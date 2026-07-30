#!/usr/bin/env python3
"""Linux StatusNotifier/AppIndicator companion for Token Meter."""
import json
import os
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    import gi
    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
    except (ValueError, ImportError):
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as AppIndicator
    from gi.repository import GLib, Gtk
except (ImportError, ValueError) as exc:
    print(
        "Token Meter's Linux tray needs GTK 3, PyGObject, and Ayatana AppIndicator.\n"
        "Debian/Ubuntu: sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1\n"
        "Fedora: sudo dnf install python3-gobject libayatana-appindicator-gtk3\n"
        "Arch: sudo pacman -S python-gobject libayatana-appindicator",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

BASE_URL = os.environ.get("TOKEN_METER_URL", "http://127.0.0.1:8722").rstrip("/")
STATE_URL = BASE_URL + "/menubar"
CONFIG_HOME = os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config"))
STATE_PATH = os.path.join(CONFIG_HOME, "token-meter", "tray.json")
MAX_RECENT_SESSIONS = 10
MAX_PROVIDERS = 5
MAX_PROVIDER_WINDOWS = 4


def compact_number(value):
    value = int(value or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def money(value):
    value = float(value or 0)
    return f"${value:.4f}" if value < 0.01 else f"${value:.2f}"


def percentage(value):
    if value is None:
        return "--"
    return f"{float(value):.0f}%"


def reset_label(reset_at):
    if not reset_at:
        return "Reset time unavailable"
    remaining = max(0, int(float(reset_at) - time.time()))
    if remaining < 60:
        return "resets in <1m"
    if remaining < 3600:
        return f"resets in {remaining / 60:.0f}m"
    if remaining < 86400:
        return f"resets in {remaining / 3600:.1f}h"
    return f"resets in {remaining / 86400:.1f}d"


class TokenMeterTray:
    """Grouped GTK menu built once and updated in place.

    KDE Plasma breaks when indicator.set_menu() replaces an open menu tree.
    Submenus are attached once at startup; polling only updates labels and
    visibility on the existing widgets.
    """

    def __init__(self):
        self.pinned_session = self.load_state().get("pinned_session")
        self.snapshot = {}
        self.error = "Waiting for Token Meter"
        self.menu_open = False
        self.pending_refresh = False
        self.indicator = AppIndicator.Indicator.new(
            "token-meter", "utilities-system-monitor",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Token Meter")
        self.menu = Gtk.Menu()
        self.menu.connect("show", self._on_menu_show)
        self.menu.connect("hide", self._on_menu_hide)
        self.connection_item = self._metric_item("")
        self.run_items = {
            key: self._metric_item("")
            for key in ("run", "model", "cost", "context", "tokens")
        }
        self.sessions_separator = Gtk.SeparatorMenuItem()
        self.sessions_item, self.sessions_menu = self._submenu_item("Recent sessions")
        self.follow_latest_item = self._action_item("Follow latest", lambda *_: self.set_pinned(None))
        self.sessions_menu.append(self.follow_latest_item)
        self.session_items = []
        for _ in range(MAX_RECENT_SESSIONS):
            item = Gtk.MenuItem(label="")
            item.token_meter_session_id = None
            item.connect("activate", self._on_session_activate)
            self.session_items.append(item)
            self.sessions_menu.append(item)
        self.limits_separator = Gtk.SeparatorMenuItem()
        self.limits_item, self.limits_menu = self._submenu_item("Provider limits")
        self.provider_slots = []
        for _ in range(MAX_PROVIDERS):
            slot_item, slot_menu = self._submenu_item("")
            slot = {
                "item": slot_item,
                "menu": slot_menu,
                "status": self._metric_item(""),
                "empty": self._metric_item(""),
                "coverage": self._metric_item(""),
                "windows": [],
            }
            slot_menu.append(slot["status"])
            slot_menu.append(slot["empty"])
            for _window in range(MAX_PROVIDER_WINDOWS):
                window = {
                    "separator": Gtk.SeparatorMenuItem(),
                    "quota": self._metric_item(""),
                    "reset": self._metric_item(""),
                    "pace": self._metric_item(""),
                }
                slot_menu.append(window["separator"])
                slot_menu.append(window["quota"])
                slot_menu.append(window["reset"])
                slot_menu.append(window["pace"])
                slot["windows"].append(window)
            slot_menu.append(slot["coverage"])
            self.limits_menu.append(slot_item)
            self.provider_slots.append(slot)
        self.actions_separator = Gtk.SeparatorMenuItem()
        self.action_items = {
            label: self._action_item(label, lambda _item, route=route: self.open_url(route))
            for label, route in (
                ("Open Dashboard", "#summary"),
                ("Open Daily Brief", "#daily"),
                ("Open Trace", "#activity"),
                ("Open Tools", "#capabilities"),
                ("Settings", "#settings"),
            )
        }
        self.quit_separator = Gtk.SeparatorMenuItem()
        self.quit_item = self._action_item("Quit Token Meter Tray", lambda *_: Gtk.main_quit())
        self._assemble_menu()
        self.indicator.set_menu(self.menu)
        self.poll()
        GLib.timeout_add_seconds(2, self.poll)

    def _submenu_item(self, label):
        item = Gtk.MenuItem(label=label)
        item.set_reserve_indicator(True)
        submenu = Gtk.Menu()
        item.set_submenu(submenu)
        return item, submenu

    def _metric_item(self, label):
        row = Gtk.MenuItem(label=label)
        row.set_sensitive(False)
        return row

    def _action_item(self, label, callback):
        row = Gtk.MenuItem(label=label)
        row.connect("activate", callback)
        return row

    def _assemble_menu(self):
        self.menu.append(self.connection_item)
        for key in ("run", "model", "cost", "context", "tokens"):
            self.menu.append(self.run_items[key])
        self.menu.append(self.sessions_separator)
        self.menu.append(self.sessions_item)
        self.menu.append(self.limits_separator)
        self.menu.append(self.limits_item)
        self.menu.append(self.actions_separator)
        for item in self.action_items.values():
            self.menu.append(item)
        self.menu.append(self.quit_separator)
        self.menu.append(self.quit_item)
        self.menu.show_all()

    @staticmethod
    def _set_visible(widget, visible):
        if visible:
            widget.show()
        else:
            widget.hide()

    def _on_menu_show(self, _menu):
        self.menu_open = True

    def _on_menu_hide(self, _menu):
        self.menu_open = False
        if self.pending_refresh:
            self.pending_refresh = False
            GLib.idle_add(self._apply_menu_content)

    def load_state(self):
        try:
            with open(STATE_PATH, encoding="utf-8") as handle:
                value = json.load(handle)
                return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self):
        try:
            os.makedirs(os.path.dirname(STATE_PATH), mode=0o700, exist_ok=True)
            temporary = STATE_PATH + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump({"pinned_session": self.pinned_session}, handle)
            os.chmod(temporary, 0o600)
            os.replace(temporary, STATE_PATH)
        except OSError as exc:
            print(f"Token Meter could not save tray state: {exc}", file=sys.stderr)

    def fetch(self):
        url = STATE_URL
        if self.pinned_session:
            url += "?session=" + quote(self.pinned_session, safe="")
        request = Request(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
        with urlopen(request, timeout=5) as response:
            if response.status != 200:
                raise URLError(f"HTTP {response.status}")
            return json.load(response)

    def poll(self):
        try:
            payload = self.fetch()
            if not isinstance(payload, dict):
                raise ValueError("unreadable response")
            if (payload.get("selection") or {}).get("missing"):
                self.pinned_session = None
                self.save_state()
            self.snapshot = payload
            self.error = ""
        except (OSError, ValueError, URLError) as exc:
            self.snapshot = {}
            self.error = str(exc)
        self.update_tray_label()
        self.refresh_menu_content()
        return True

    def update_tray_label(self):
        if self.error:
            title = "Token Meter · offline"
            self.indicator.set_label("offline", "offline")
        else:
            state = self.snapshot
            availability = state.get("availability") or {}
            context = state.get("context") or {}
            cost = money(state.get("total_cost")) if availability.get("cost", True) else "--"
            context_pct = context.get("latest_pct")
            context_label = percentage(
                (float(context_pct) * 100)
                if context_pct is not None and float(context_pct) <= 1
                else context_pct
            )
            title = f"{cost} · {context_label}"
            self.indicator.set_label(title, "$999.99 · 100%")
        self.indicator.set_title(title)

    def open_url(self, path=""):
        pinned_route = path in ("#summary", "#activity") and self.pinned_session
        if pinned_route:
            url = BASE_URL + "/sessions/" + quote(self.pinned_session, safe="") + path
        else:
            url = BASE_URL + "/" + path.lstrip("/")
        try:
            subprocess.Popen(
                ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            print(f"Token Meter could not open {url}: {exc}", file=sys.stderr)

    def set_pinned(self, session_id):
        self.pinned_session = session_id
        self.save_state()
        self.menu_open = False
        self.pending_refresh = False
        self.poll()

    def _on_session_activate(self, item):
        session_id = getattr(item, "token_meter_session_id", None)
        if session_id:
            self.set_pinned(session_id)

    def _set_item_label(self, item, label):
        item.set_label(label)

    def refresh_menu_content(self):
        if self.menu_open:
            self.pending_refresh = True
            return
        self._apply_menu_content()

    def _apply_menu_content(self):
        if self.error:
            self._set_visible(self.connection_item, True)
            self._set_item_label(self.connection_item, f"Connection:  {self.error}")
            for item in self.run_items.values():
                self._set_visible(item, False)
            self._set_visible(self.sessions_separator, False)
            self._set_visible(self.sessions_item, False)
            self._set_visible(self.limits_separator, False)
            self._set_visible(self.limits_item, False)
            return False

        self._set_visible(self.connection_item, False)
        state = self.snapshot
        source = state.get("source") or {}
        availability = state.get("availability") or {}
        context = state.get("context") or {}
        cost = money(state.get("total_cost")) if availability.get("cost", True) else "--"
        context_pct = context.get("latest_pct")
        context_label = percentage(
            (float(context_pct) * 100)
            if context_pct is not None and float(context_pct) <= 1
            else context_pct
        )
        tokens = compact_number(state.get("total_tokens")) if availability.get("tokens", True) else "--"
        run_labels = {
            "run": f"Run:  {source.get('label') or state.get('provider') or 'Current session'} · {source.get('project') or 'Current session'}",
            "model": f"Model:  {state.get('model') or source.get('model') or 'unknown'}",
            "cost": f"Cost:  {cost}{' est' if state.get('cost_approx') else ''}",
            "context": f"Context:  {context_label} · {compact_number(context.get('latest'))} / {compact_number(context.get('window'))}",
            "tokens": f"Tokens:  {tokens} · {int(state.get('turns') or 0)} execs",
        }
        for key, label in run_labels.items():
            self._set_visible(self.run_items[key], True)
            self._set_item_label(self.run_items[key], label)

        sessions = state.get("recent_sessions") or []
        show_sessions = bool(sessions)
        self._set_visible(self.sessions_separator, show_sessions)
        self._set_visible(self.sessions_item, show_sessions)
        self._set_visible(self.follow_latest_item, show_sessions)
        for index, item in enumerate(self.session_items):
            if not show_sessions or index >= len(sessions[:MAX_RECENT_SESSIONS]):
                item.token_meter_session_id = None
                self._set_visible(item, False)
                continue
            session = sessions[index]
            session_id = str(session.get("id") or "")
            if not session_id:
                item.token_meter_session_id = None
                self._set_visible(item, False)
                continue
            label = session.get("name") or session.get("project") or session_id
            provider = session.get("label") or str(session.get("provider") or "").title()
            marker = "✓ " if session_id == self.pinned_session else ""
            self._set_item_label(item, f"{marker}{provider} · {label}")
            item.token_meter_session_id = session_id
            self._set_visible(item, True)

        providers = state.get("provider_quotas") or []
        show_limits = bool(providers)
        self._set_visible(self.limits_separator, show_limits)
        self._set_visible(self.limits_item, show_limits)
        for index, slot in enumerate(self.provider_slots):
            if not show_limits or index >= len(providers[:MAX_PROVIDERS]):
                self._set_visible(slot["item"], False)
                continue
            self._update_provider_slot(slot, providers[index])
            self._set_visible(slot["item"], True)
        return False

    def _update_provider_slot(self, slot, provider):
        label = provider.get("label") or str(provider.get("id") or "Provider").title()
        plan = str(provider.get("plan") or "").strip()
        status = provider.get("status") or "unavailable"
        windows = provider.get("windows") or []
        self._set_item_label(slot["item"], label)
        self._set_visible(slot["status"], True)
        self._set_item_label(slot["status"], f"Status:  {status}{' · ' + plan if plan else ''}")
        self._set_visible(slot["empty"], not windows)
        if not windows:
            self._set_item_label(slot["empty"], f"Quota:  {provider.get('error') or 'Not reported'}")
        coverage = provider.get("coverage_note")
        self._set_visible(slot["coverage"], bool(coverage))
        if coverage:
            self._set_item_label(slot["coverage"], f"Coverage:  {coverage}")
        for index, window in enumerate(slot["windows"]):
            if index >= len(windows[:MAX_PROVIDER_WINDOWS]):
                for key in ("separator", "quota", "reset", "pace"):
                    self._set_visible(window[key], False)
                continue
            data = windows[index]
            pace = data.get("pace") or {}
            self._set_visible(window["separator"], True)
            self._set_visible(window["quota"], True)
            self._set_item_label(
                window["quota"],
                f"{data.get('label') or 'Quota'}:  {percentage(data.get('used_percent'))} used",
            )
            self._set_visible(window["reset"], True)
            self._set_item_label(window["reset"], f"Reset:  {reset_label(data.get('reset_at'))}")
            show_pace = bool(pace.get("summary"))
            self._set_visible(window["pace"], show_pace)
            if show_pace:
                self._set_item_label(window["pace"], f"Pace:  {pace['summary']}")


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(0)
    TokenMeterTray()
    Gtk.main()
