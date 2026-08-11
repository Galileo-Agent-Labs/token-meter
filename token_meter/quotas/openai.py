"""OpenAI account quota parsing and bounded acquisition."""

import json
import re

from .base import QuotaUnavailable
from .common import (
    quota_coverage_note,
    quota_number,
    quota_provider,
    quota_slug,
    quota_window,
)


def _compact_text(value, limit):
    value = " ".join(str(value or "").split())
    return value[:limit - 1] + "…" if len(value) > limit else value


def limit_label(value):
    text = str(value or "").strip()
    if not text:
        return "Additional"
    if "spark" in text.lower():
        return "Spark"
    text = re.sub(r"(?i)^gpt[- ]?[\w.]+[- ]?codex[- ]?", "", text)
    text = re.sub(r"(?i)[- ]preview$", "", text).replace("-", " ").strip()
    return _compact_text(text.title() or "Additional", 32)


def _window(provider_id, raw, kind, label, now=None):
    if not isinstance(raw, dict):
        return None
    duration = quota_number(raw.get("limit_window_seconds"))
    if duration is None:
        minutes = quota_number(
            raw.get("windowDurationMins") or raw.get("window_duration_mins")
        )
        duration = minutes * 60 if minutes is not None else None
    return quota_window(
        "codex", "codex-{}-{}".format(provider_id, kind), kind, label,
        raw.get("used_percent") if "used_percent" in raw else raw.get("usedPercent"),
        window_seconds=duration,
        reset_at=raw.get("reset_at") if "reset_at" in raw else raw.get("resetsAt"),
        now=now,
    )


def parse_quota(payload, source="Codex app-server", now=None):
    root = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    main = (
        root.get("rateLimits")
        or root.get("rate_limit")
        or root.get("rateLimitsByLimitId", {}).get("codex")
        or {}
    )
    plan = main.get("planType") or main.get("plan_type") or root.get("plan_type") or ""
    windows, seen = [], set()

    def add(row):
        if row and row["id"] not in seen:
            seen.add(row["id"])
            windows.append(row)

    main_session = _window(
        "main", main.get("primary") or main.get("primary_window"),
        "session", "Session", now=now,
    )
    main_weekly = _window(
        "main", main.get("secondary") or main.get("secondary_window"),
        "weekly", "Weekly", now=now,
    )
    add(main_session)
    add(main_weekly)
    missing_main = [
        label for label, window in (("Session", main_session), ("Weekly", main_weekly))
        if not window
    ]
    coverage_note = quota_coverage_note("Codex", missing_main, qualifier="regular")

    additional = []
    by_id = root.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        additional.extend(
            (key, value) for key, value in by_id.items()
            if key != "codex" and isinstance(value, dict)
        )
    for index, value in enumerate(root.get("additional_rate_limits") or []):
        if isinstance(value, dict):
            additional.append((value.get("metered_feature") or "extra-{}".format(index), value))

    for limit_id, value in additional:
        nested = value.get("rate_limit") if isinstance(value.get("rate_limit"), dict) else value
        prefix = limit_label(value.get("limitName") or value.get("limit_name") or limit_id)
        stable = quota_slug(limit_id)
        add(_window(
            stable, nested.get("primary") or nested.get("primary_window"),
            "session", "{} session".format(prefix), now=now,
        ))
        add(_window(
            stable, nested.get("secondary") or nested.get("secondary_window"),
            "weekly", "{} weekly".format(prefix), now=now,
        ))

    if not windows:
        return quota_provider(
            "codex", "Codex", "unavailable", source, plan=plan,
            error="This Codex account does not report quota windows.",
            coverage_note=coverage_note,
        )
    return quota_provider(
        "codex", "Codex", "ok", source, windows=windows, plan=plan,
        coverage_note=coverage_note,
    )


def oauth_quota(auth_path, http_json, now=None, opener=None):
    try:
        with open(auth_path, encoding="utf-8") as stream:
            auth = json.load(stream)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        raise QuotaUnavailable("Codex is not signed in locally.") from None
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    if not access_token:
        raise QuotaUnavailable("Codex is not signed in locally.")
    headers = {
        "Authorization": "Bearer {}".format(access_token),
        "Accept": "application/json",
        "User-Agent": "TokenMeter/0.1",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = str(account_id)
    payload = http_json(
        "https://chatgpt.com/backend-api/wham/usage", headers=headers, opener=opener,
    )
    return parse_quota(payload, source="Codex OAuth API", now=now)


def app_server_rate_limits(provider_cli_path, agent_environment, rpc_reader,
                           subprocess_module, selectors_module, timeout):
    executable = provider_cli_path("codex")
    if not executable:
        raise QuotaUnavailable("Codex CLI is not installed.")
    try:
        process = subprocess_module.Popen(
            [executable, "-s", "read-only", "-a", "untrusted", "app-server"],
            stdin=subprocess_module.PIPE,
            stdout=subprocess_module.PIPE,
            stderr=subprocess_module.DEVNULL,
            text=True,
            bufsize=1,
            env=agent_environment(executable),
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        raise QuotaUnavailable(
            "Codex could not start its account quota service."
        ) from None
    selector = selectors_module.DefaultSelector()
    try:
        selector.register(process.stdout, selectors_module.EVENT_READ)

        def send(message):
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()

        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"clientInfo": {"name": "token-meter", "version": "0.1"}},
        })
        rpc_reader(process, selector, 1, timeout)
        send({"jsonrpc": "2.0", "method": "initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"})
        return rpc_reader(process, selector, 2, timeout)
    except (BrokenPipeError, OSError):
        raise QuotaUnavailable(
            "Codex could not start its account quota service."
        ) from None
    finally:
        selector.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess_module.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)


def load_quota(app_server_loader, oauth_loader, now=None):
    try:
        return parse_quota(app_server_loader(), now=now)
    except QuotaUnavailable:
        return oauth_loader(now=now)
