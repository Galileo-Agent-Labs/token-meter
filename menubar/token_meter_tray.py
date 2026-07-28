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
    def __init__(self):
        self.pinned_session = self.load_state().get("pinned_session")
        self.snapshot = {}
        self.error = "Waiting for Token Meter"
        self.indicator = AppIndicator.Indicator.new(
            "token-meter", "utilities-system-monitor",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Token Meter")
        self.refresh_menu()
        self.poll()
        GLib.timeout_add_seconds(2, self.poll)

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
        self.refresh_menu()
        return True

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

    def item(self, label, callback=None, sensitive=True):
        row = Gtk.MenuItem(label=label)
        row.set_sensitive(sensitive)
        if callback:
            row.connect("activate", callback)
        return row

    def metric(self, menu, label, value):
        menu.append(self.item(f"{label}:  {value}", sensitive=False))

    def provider_submenu(self, provider):
        label = provider.get("label") or str(provider.get("id") or "Provider").title()
        top = self.item(label)
        menu = Gtk.Menu()
        plan = str(provider.get("plan") or "").strip()
        status = provider.get("status") or "unavailable"
        self.metric(menu, "Status", f"{status}{' · ' + plan if plan else ''}")
        windows = provider.get("windows") or []
        for window in windows:
            menu.append(Gtk.SeparatorMenuItem())
            self.metric(menu, window.get("label") or "Quota", percentage(window.get("used_percent")) + " used")
            self.metric(menu, "Reset", reset_label(window.get("reset_at")))
            pace = window.get("pace") or {}
            if pace.get("summary"):
                self.metric(menu, "Pace", pace["summary"])
        if not windows:
            self.metric(menu, "Quota", provider.get("error") or "Not reported")
        if provider.get("coverage_note"):
            menu.append(Gtk.SeparatorMenuItem())
            self.metric(menu, "Coverage", provider["coverage_note"])
        top.set_submenu(menu)
        return top

    def set_pinned(self, session_id):
        self.pinned_session = session_id
        self.save_state()
        self.poll()

    def session_submenu(self, sessions):
        top = self.item("Recent sessions")
        menu = Gtk.Menu()
        latest = self.item("Follow latest")
        latest.connect("activate", lambda *_: self.set_pinned(None))
        menu.append(latest)
        for session in sessions[:10]:
            session_id = str(session.get("id") or "")
            if not session_id:
                continue
            label = session.get("name") or session.get("project") or session_id
            provider = session.get("label") or str(session.get("provider") or "").title()
            marker = "✓ " if session_id == self.pinned_session else ""
            row = self.item(f"{marker}{provider} · {label}")
            row.connect("activate", lambda _item, sid=session_id: self.set_pinned(sid))
            menu.append(row)
        top.set_submenu(menu)
        return top

    def refresh_menu(self):
        menu = Gtk.Menu()
        state = self.snapshot
        if self.error:
            title = "Token Meter · offline"
            self.indicator.set_label("offline", "offline")
            self.metric(menu, "Connection", self.error)
        else:
            source = state.get("source") or {}
            availability = state.get("availability") or {}
            context = state.get("context") or {}
            cost = money(state.get("total_cost")) if availability.get("cost", True) else "--"
            context_pct = context.get("latest_pct")
            context_label = percentage((float(context_pct) * 100) if context_pct is not None and float(context_pct) <= 1 else context_pct)
            title = f"{cost} · {context_label}"
            self.indicator.set_label(title, "$999.99 · 100%")
            self.metric(menu, source.get("label") or state.get("provider") or "Run", source.get("project") or "Current session")
            self.metric(menu, "Model", state.get("model") or source.get("model") or "unknown")
            self.metric(menu, "Cost", cost + (" est" if state.get("cost_approx") else ""))
            self.metric(menu, "Context", f"{context_label} · {compact_number(context.get('latest'))} / {compact_number(context.get('window'))}")
            tokens = compact_number(state.get("total_tokens")) if availability.get("tokens", True) else "--"
            self.metric(menu, "Tokens", f"{tokens} · {int(state.get('turns') or 0)} execs")
            sessions = state.get("recent_sessions") or []
            if sessions:
                menu.append(Gtk.SeparatorMenuItem())
                menu.append(self.session_submenu(sessions))
            providers = state.get("provider_quotas") or []
            if providers:
                menu.append(Gtk.SeparatorMenuItem())
                limits = self.item("Provider limits")
                limits_menu = Gtk.Menu()
                for provider in providers:
                    limits_menu.append(self.provider_submenu(provider))
                limits.set_submenu(limits_menu)
                menu.append(limits)
        self.indicator.set_title(title)
        menu.append(Gtk.SeparatorMenuItem())
        for label, route in (("Open Dashboard", "#summary"), ("Open Daily Brief", "#daily"),
                             ("Open Trace", "#activity"), ("Open Tools", "#capabilities"),
                             ("Settings", "#settings")):
            row = self.item(label)
            row.connect("activate", lambda _item, target=route: self.open_url(target))
            menu.append(row)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = self.item("Quit Token Meter Tray")
        quit_item.connect("activate", lambda *_: Gtk.main_quit())
        menu.append(quit_item)
        menu.show_all()
        self.indicator.set_menu(menu)


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(0)
    TokenMeterTray()
    Gtk.main()
