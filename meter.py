#!/usr/bin/env python3
"""
Token Meter - a live cost and efficiency instrument for Claude, Codex, and Cursor.

Tails local agent logs, parses each execution as it lands, and serves a
localhost dashboard with Current and Global views. Stdlib only. Trace analysis
stays local; the optional menu-bar quota view makes read-only account-usage
requests to the signed-in Claude, Codex, and Cursor provider services.

  python3 meter.py     ->  http://localhost:8722

Claude correctness note: one API response (message.id) can be split across
several JSONL lines, one per content block, and each line repeats the same usage
block. Claude parsing dedupes by message.id so costs are not double-counted.
Codex uses token_count events instead; those are already one usage slice.
"""
import calendar
import base64
import copy
import datetime
import glob
import hashlib
import html
import json
import math
import os
import queue
import random
import re
import secrets
import selectors
import shlex
import shutil
import sqlite3
import subprocess
import statistics
import time
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")
CLAUDE_DESKTOP_DATA_ROOTS = [
    os.path.expanduser("~/Library/Application Support/Claude"),
    # Claude Desktop's third-party provider builds (including Bedrock-backed
    # Cowork) keep the same metadata and nested Claude trace layout here.
    os.path.expanduser("~/Library/Application Support/Claude-3p"),
]
CLAUDE_DESKTOP_SESSIONS = os.path.join(CLAUDE_DESKTOP_DATA_ROOTS[0], "claude-code-sessions")
CLAUDE_SETTINGS = os.path.expanduser("~/.claude/settings.json")
CLAUDE_ROOT_CONFIG = os.path.expanduser("~/.claude.json")
CODEX_SESSIONS = os.path.expanduser("~/.codex/sessions")
CODEX_INDEX = os.path.expanduser("~/.codex/session_index.jsonl")
CODEX_CONFIG = os.path.expanduser("~/.codex/config.toml")
CODEX_AUTH = os.path.expanduser("~/.codex/auth.json")
CLAUDE_CREDENTIALS = os.path.expanduser("~/.claude/.credentials.json")
CURSOR_PROJECTS = os.path.expanduser("~/.cursor/projects")
CURSOR_STATE_DB = os.path.expanduser(
    "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
)
CURSOR_REQUEST_LOGS = os.path.expanduser("~/Library/Application Support/Cursor/logs")
TOKEN_METER_SETTINGS = os.path.expanduser(
    os.environ.get("TOKEN_METER_SETTINGS", "~/.token-meter/settings.json")
)
PORT = 8722

DEFAULT_FRUSTRATION_TERMS = [
    "fuck", "fck", "fucked", "fucking", "shit", "shitty", "bullshit",
    "idiot", "stupid", "useless", "crap", "damn", "wtf",
]
MAX_FRUSTRATION_TERMS = 64
MAX_FRUSTRATION_TERM_LENGTH = 40
MODEL_PRICE_FIELDS = ("input", "output", "cache_write", "cache_read")
MODEL_PRICE_PROVIDERS = ("claude", "codex", "cursor")
MAX_CUSTOM_MODEL_PRICES = 100
MAX_MODEL_PRICE = 1_000_000.0
MODEL_PRICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,159}$")

CLAUDE_PRICE = {
    "claude-opus-4-8": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "claude-fable-5": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    # Introductory pricing through 2026-08-31; standard pricing is $3/$15 afterward.
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_write": 2.50, "cache_read": 0.20},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10},
}

# Public OpenAI API pricing, per 1M tokens. Codex subscription accounting can
# differ by plan, so the UI labels OpenAI/Codex costs as API-rate estimates.
OPENAI_PRICE = {
    # GPT-5.6 Sol / flagship pricing. Terra and Luna use lower rates.
    "gpt-5.6": {"input": 5.0, "output": 30.0, "cache_write": 6.25, "cache_read": 0.50},
    "gpt-5.5": {"input": 5.0, "output": 30.0, "cache_write": 0.0, "cache_read": 0.50},
    "gpt-5.4": {"input": 2.50, "output": 15.0, "cache_write": 0.0, "cache_read": 0.25},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50, "cache_write": 0.0, "cache_read": 0.075},
}
CURSOR_PRICE = {
    # Cursor's first-party Composer 2.5 rates. The persisted model configuration
    # distinguishes the Fast and Standard variants used for a session.
    "composer-2.5-standard": {
        "input": 0.50, "output": 2.50, "cache_write": 0.0, "cache_read": 0.0,
    },
    "composer-2.5-fast": {
        "input": 3.0, "output": 15.0, "cache_write": 0.0, "cache_read": 0.0,
    },
}
ZERO_PRICE = {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0}

DEFAULT_CLAUDE_MODEL = "claude-opus-4-8"
DEFAULT_OPENAI_MODEL = "gpt-5.5"
CHARS_PER_TOKEN = 4
TRACE_LIMIT = 220
EXEC_LIMIT = 180
MENUBAR_CONTEXT_SOFT_PCT = 0.65
MENUBAR_CONTEXT_WATCH_PCT = 0.70
MENUBAR_CONTEXT_INTERVENE_PCT = 0.85
MENUBAR_COST_SPIKE = 0.50
QUOTA_REFRESH_S = 60.0
QUOTA_STALE_S = 10 * 60.0
QUOTA_HTTP_TIMEOUT_S = 8.0
QUOTA_PROCESS_TIMEOUT_S = 8.0
LOW_YIELD_RATIO = 0.005
LOW_YIELD_COST = 0.05
LOW_YIELD_CONTEXT_PCT = 0.25
LOW_YIELD_INPUT_TOKENS = 60000
TOOL_OVERSIZED_TOKENS = 8000
PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9_.@:/-]{1,180}$")
SKILL_PATH_RE = re.compile(r"(?:^|[/\\])([^/\\\s'\"]+)[/\\]SKILL\.md(?:\b|$)", re.IGNORECASE)
DATA_URL_RE = re.compile(r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+")
BASE64_FIELD_RE = re.compile(r'("(?:data|image_url)"\s*:\s*")([A-Za-z0-9+/=]{512,})(")')

subscribers, subscribers_lock = [], threading.Lock()
STATE = {}
_xsess = {"data": None, "at": 0.0, "sessions": []}
_XSESS_TTL = 15.0
_XSESS_LIVE_REFRESH_S = 2.0
_summary_cache = {}
_model_pricing_cache = {
    "path": None, "mtime_ns": None, "overrides": {}, "effective": {},
}
_cursor_request_cache = {}
_quota_cache = {}
_quota_inflight = set()
_quota_lock = threading.Lock()
_ACTION_TOKEN = secrets.token_urlsafe(24)
AGENT_ACCESS_SERVER = "tokenmeter"
AGENT_CURRENT_MAX_AGE_S = 6 * 60 * 60


def parse_iso(ts):
    # Logs are UTC (trailing Z). calendar.timegm treats the struct as UTC, so
    # idle/elapsed line up with time.time().
    try:
        return calendar.timegm(time.strptime((ts or "").split(".")[0], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def local_dt(ts):
    return time.strftime("%Y-%m-%d %I:%M:%S %p", time.localtime(ts)).lower() if ts else ""


def local_tm(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""


def duration_label(seconds):
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _merge_execution_intervals(intervals):
    """Return wall-active seconds after collapsing overlapping execution windows."""
    clean = sorted(
        (float(start), float(end))
        for start, end in intervals
        if start is not None and end is not None and float(end) > float(start)
    )
    merged = []
    for start, end in clean:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return sum(end - start for start, end in merged)


def _claude_user_prompt(obj):
    if obj.get("type") != "user":
        return False
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict)
        and block.get("type") == "text"
        and str(block.get("text") or "").strip()
        for block in content
    )


def _is_user_input_tool(name):
    """Return whether a tool explicitly pauses the agent for human input."""
    normalized = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    return normalized in ("askuserquestion", "requestuserinput")


def _track_claude_user_pause(group, block, ts):
    if not group or not isinstance(block, dict) or block.get("type") != "tool_use":
        return
    if not _is_user_input_tool(block.get("name")):
        return
    tool_id = block.get("id")
    if tool_id:
        group.setdefault("user_pause_starts", {}).setdefault(tool_id, float(ts or 0))


def _close_claude_user_pauses(group, message, ts):
    if not group or not isinstance(message, dict):
        return
    starts = group.setdefault("user_pause_starts", {})
    for block in message.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        started = starts.pop(block.get("tool_use_id"), None)
        if started is not None and float(ts or 0) >= started:
            group["user_pause_s"] = float(group.get("user_pause_s") or 0) + float(ts or 0) - started


def _claude_effective_duration(group, duration_s, timing_basis):
    """Remove trace-visible human-response pauses from observed wall time."""
    duration_s = float(duration_s or 0)
    paused_s = float((group or {}).get("user_pause_s") or 0)
    excluded_s = min(duration_s, paused_s) if timing_basis == "observed" else 0.0
    return max(0.0, duration_s - excluded_s), excluded_s


def execution_timing(provider, objs):
    """Build trace-backed active execution time, excluding idle gaps."""
    intervals = []
    reported = observed = 0
    open_start = open_last = None

    for obj in objs:
        ts = parse_iso(obj.get("timestamp", ""))

        if provider == "claude":
            if _claude_user_prompt(obj):
                if open_start and open_last and open_last > open_start:
                    intervals.append((open_start, open_last))
                    observed += 1
                open_start = ts or open_start
                open_last = ts or open_last
                continue
            if obj.get("type") != "system" or obj.get("subtype") != "turn_duration":
                if open_start and ts and obj.get("type") == "assistant":
                    open_last = ts if open_last is None else max(open_last, ts)
                continue
            duration_ms = obj.get("durationMs")
            try:
                duration_ms = float(duration_ms or 0)
            except (TypeError, ValueError):
                duration_ms = 0
            if ts and duration_ms > 0:
                intervals.append((ts - duration_ms / 1000.0, ts))
                reported += 1
            elif open_start and ts and ts > open_start:
                intervals.append((open_start, ts))
                observed += 1
            open_start = open_last = None
            continue

        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        if ptype == "task_started":
            if open_start and open_last and open_last > open_start:
                intervals.append((open_start, open_last))
                observed += 1
            open_start = ts or open_start
            open_last = ts or open_last
            continue
        if ptype != "task_complete":
            if open_start and ts:
                open_last = ts if open_last is None else max(open_last, ts)
            continue
        duration_ms = payload.get("duration_ms")
        try:
            duration_ms = float(duration_ms or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        if ts and duration_ms > 0:
            intervals.append((ts - duration_ms / 1000.0, ts))
            reported += 1
        elif open_start and ts and ts > open_start:
            intervals.append((open_start, ts))
            observed += 1
        open_start = open_last = None

    if open_start and open_last and open_last > open_start:
        intervals.append((open_start, open_last))
        observed += 1

    duration_s = _merge_execution_intervals(intervals)
    if reported and observed:
        basis = "reported + observed"
    elif reported:
        basis = "reported"
    elif observed:
        basis = "observed"
    else:
        basis = "unavailable"
    row = {
        "duration_s": duration_s,
        "available": duration_s > 0,
        "reported_executions": reported,
        "observed_executions": observed,
        "execution_count": reported + observed,
        "basis": basis,
    }
    return row


def load(path):
    out = []
    if not path:
        return out
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return out


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
        return value
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {} if default is None else default


def atomic_write_text(path, text):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.token-meter-{os.getpid()}")
    mode = None
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        pass
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def normalize_frustration_terms(values):
    """Normalize a user-editable term list while preserving display order."""
    if isinstance(values, str):
        values = re.split(r"[,\n]", values)
    if not isinstance(values, list):
        raise ValueError("Frustration terms must be a list or comma-separated text.")
    normalized = []
    seen = set()
    for value in values:
        term = " ".join(str(value or "").strip().lower().split())
        if not term:
            continue
        if len(term) > MAX_FRUSTRATION_TERM_LENGTH:
            raise ValueError(f"Each frustration term must be {MAX_FRUSTRATION_TERM_LENGTH} characters or fewer.")
        if any(ord(char) < 32 for char in term):
            raise ValueError("Frustration terms cannot contain control characters.")
        if term not in seen:
            normalized.append(term)
            seen.add(term)
    if len(normalized) > MAX_FRUSTRATION_TERMS:
        raise ValueError(f"Use at most {MAX_FRUSTRATION_TERMS} frustration terms.")
    return normalized


def frustration_settings(path=None):
    path = path or TOKEN_METER_SETTINGS
    settings = load_json(path, {})
    if not isinstance(settings, dict):
        settings = {}
    if "frustration_terms" not in settings:
        terms = list(DEFAULT_FRUSTRATION_TERMS)
    else:
        try:
            terms = normalize_frustration_terms(settings.get("frustration_terms"))
        except ValueError:
            terms = list(DEFAULT_FRUSTRATION_TERMS)
    return {
        "terms": terms,
        "defaults": list(DEFAULT_FRUSTRATION_TERMS),
        "max_terms": MAX_FRUSTRATION_TERMS,
    }


def set_frustration_terms(values, path=None):
    """Persist the machine-wide frustration lexicon used by every session."""
    path = path or TOKEN_METER_SETTINGS
    try:
        terms = normalize_frustration_terms(values)
    except ValueError as error:
        return {"ok": False, "error": str(error)}
    settings = load_json(path, {})
    if not isinstance(settings, dict):
        settings = {}
    settings["frustration_terms"] = terms
    try:
        atomic_write_text(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    except OSError as error:
        return {"ok": False, "error": f"Token Meter could not save settings: {error}"}
    return {
        "ok": True,
        "terms": terms,
        "defaults": list(DEFAULT_FRUSTRATION_TERMS),
        "max_terms": MAX_FRUSTRATION_TERMS,
    }


def normalize_model_price_provider(provider):
    provider = str(provider or "").strip().lower()
    if provider == "openai":
        provider = "codex"
    if provider not in MODEL_PRICE_PROVIDERS:
        raise ValueError("Provider must be Claude, Codex / OpenAI, or Cursor.")
    return provider


def normalize_model_price_id(model):
    model = str(model or "").strip().lower()
    if not MODEL_PRICE_ID_RE.fullmatch(model):
        raise ValueError(
            "Model id must be 1–160 characters using letters, numbers, dots, dashes, "
            "underscores, colons, @, +, or /."
        )
    return model


def normalize_model_price(prices):
    if not isinstance(prices, dict):
        raise ValueError("Model prices must be an object.")
    normalized = {}
    for field in MODEL_PRICE_FIELDS:
        value = prices.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field.replace('_', ' ').title()} price must be a number.")
        value = float(value)
        if not math.isfinite(value) or value < 0 or value > MAX_MODEL_PRICE:
            raise ValueError(
                f"{field.replace('_', ' ').title()} price must be between 0 and "
                f"{MAX_MODEL_PRICE:,.0f}."
            )
        normalized[field] = value
    return normalized


def builtin_model_price_tables():
    return {
        "claude": CLAUDE_PRICE,
        "codex": OPENAI_PRICE,
        "cursor": CURSOR_PRICE,
    }


def _model_pricing_mtime_ns(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _load_model_price_overrides(path=None):
    """Load validated user pricing with an mtime cache for hot cost paths."""
    path = path or TOKEN_METER_SETTINGS
    mtime_ns = _model_pricing_mtime_ns(path)
    if (_model_pricing_cache["path"] == path
            and _model_pricing_cache["mtime_ns"] == mtime_ns):
        return _model_pricing_cache["overrides"]

    settings = load_json(path, {})
    raw = settings.get("model_pricing") if isinstance(settings, dict) else {}
    overrides = {provider: {} for provider in MODEL_PRICE_PROVIDERS}
    if isinstance(raw, dict):
        for raw_provider, rows in raw.items():
            try:
                provider = normalize_model_price_provider(raw_provider)
            except ValueError:
                continue
            if not isinstance(rows, dict):
                continue
            for raw_model, prices in rows.items():
                try:
                    model = normalize_model_price_id(raw_model)
                    overrides[provider][model] = normalize_model_price(prices)
                except ValueError:
                    continue
    _model_pricing_cache.update({
        "path": path,
        "mtime_ns": mtime_ns,
        "overrides": overrides,
        "effective": {},
    })
    return overrides


def effective_model_price_table(provider, path=None):
    provider = normalize_model_price_provider(provider)
    _load_model_price_overrides(path)
    cached = _model_pricing_cache["effective"].get(provider)
    if cached is not None:
        return cached
    table = {
        model: dict(prices)
        for model, prices in builtin_model_price_tables()[provider].items()
    }
    table.update(_load_model_price_overrides(path).get(provider) or {})
    _model_pricing_cache["effective"][provider] = table
    return table


def model_pricing_settings(path=None):
    """Return built-in prices plus durable user overrides for the Settings UI."""
    path = path or TOKEN_METER_SETTINGS
    overrides = _load_model_price_overrides(path)
    rows = []
    labels = {"claude": "Claude", "codex": "Codex / OpenAI", "cursor": "Cursor"}
    for provider in MODEL_PRICE_PROVIDERS:
        builtins = builtin_model_price_tables()[provider]
        for model in sorted(set(builtins) | set(overrides[provider])):
            builtin = model in builtins
            overridden = model in overrides[provider]
            prices = (overrides[provider].get(model) or builtins[model])
            rows.append({
                "provider": provider,
                "provider_label": labels[provider],
                "model": model,
                "prices": dict(prices),
                "builtin": builtin,
                "overridden": overridden,
                "custom": not builtin,
                "source": "custom" if not builtin else ("override" if overridden else "built-in"),
            })
    return {
        "models": rows,
        "currency": "USD",
        "unit": "per 1M tokens",
        "overrides": sum(
            1 for row in rows if row["builtin"] and row["overridden"]
        ),
        "custom_models": sum(1 for row in rows if row["custom"]),
        "max_custom_models": MAX_CUSTOM_MODEL_PRICES,
    }


def set_model_price(provider, model, prices=None, remove=False, path=None):
    """Persist one model price, or restore/remove it when ``remove`` is true."""
    path = path or TOKEN_METER_SETTINGS
    try:
        provider = normalize_model_price_provider(provider)
        model = normalize_model_price_id(model)
        normalized = None if remove else normalize_model_price(prices)
    except ValueError as error:
        return {"ok": False, "error": str(error)}

    overrides = copy.deepcopy(_load_model_price_overrides(path))
    custom_count = sum(
        1
        for item_provider, rows in overrides.items()
        for item_model in rows
        if item_model not in builtin_model_price_tables()[item_provider]
    )
    builtin = builtin_model_price_tables()[provider].get(model)
    changed = False
    if remove:
        changed = overrides[provider].pop(model, None) is not None
    elif builtin == normalized:
        changed = overrides[provider].pop(model, None) is not None
    else:
        is_new_custom = builtin is None and model not in overrides[provider]
        if is_new_custom and custom_count >= MAX_CUSTOM_MODEL_PRICES:
            return {
                "ok": False,
                "error": f"Use at most {MAX_CUSTOM_MODEL_PRICES} custom model prices.",
            }
        changed = overrides[provider].get(model) != normalized
        overrides[provider][model] = normalized

    settings = load_json(path, {})
    if not isinstance(settings, dict):
        settings = {}
    stored = {
        item_provider: {
            item_model: rows[item_model]
            for item_model in sorted(rows)
        }
        for item_provider, rows in overrides.items()
        if rows
    }
    if stored:
        settings["model_pricing"] = stored
    else:
        settings.pop("model_pricing", None)
    try:
        atomic_write_text(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    except OSError as error:
        return {"ok": False, "error": f"Token Meter could not save settings: {error}"}

    _model_pricing_cache.update({
        "path": None, "mtime_ns": None, "overrides": {}, "effective": {},
    })
    return {
        "ok": True,
        "changed": changed,
        "provider": provider,
        "model": model,
        "removed": bool(remove),
        "model_pricing": model_pricing_settings(path),
    }


def toml_named_sections(path, table):
    """Read simple enabled state from named TOML sections without a TOML dependency."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    header = re.compile(rf'^\[{re.escape(table)}\.(?:"([^"]+)"|([^\.\]]+))\]\s*$', re.MULTILINE)
    matches = list(header.finditer(text))
    result = {}
    for index, match in enumerate(matches):
        name = (match.group(1) or match.group(2) or "").strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end]
        enabled_match = re.search(r'^\s*enabled\s*=\s*(true|false)\s*$', body, re.MULTILINE | re.IGNORECASE)
        result[name] = {
            "enabled": enabled_match is None or enabled_match.group(1).lower() == "true",
            "start": match.start(), "body_start": match.end(), "end": end,
        }
    return result


def safe_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def home_shorten(path):
    home = os.path.expanduser("~")
    return path.replace(home, "~", 1) if path and path.startswith(home) else path


def source_runtime_label(source):
    """Return the trace runtime whose timing semantics produced this session."""
    source = source or {}
    if source.get("runtime"):
        return str(source["runtime"])
    provider = source.get("provider") or "unknown"
    if provider == "codex":
        return "Codex"
    if provider == "cursor":
        return "Cursor"
    if provider != "claude":
        return str(source.get("label") or provider)
    if source.get("client") != "claude_desktop":
        return "Claude Code"
    metadata_path = os.path.abspath(os.path.expanduser(str(source.get("metadata_path") or "")))
    third_party_root = os.path.abspath(CLAUDE_DESKTOP_DATA_ROOTS[1])
    if metadata_path == third_party_root or metadata_path.startswith(third_party_root + os.sep):
        return "Claude-3P"
    return "Claude Desktop"


def metric_availability(provider, **overrides):
    """Describe which normalized metrics are backed by the provider trace.

    Missing availability remains backward compatible elsewhere, but every new
    state carries this object so unavailable Cursor billing values cannot look
    like measured zeroes.
    """
    if provider == "cursor":
        result = {
            "cost": False,
            "tokens": False,
            "input_tokens": False,
            "output_tokens": False,
            "cache": False,
            "throughput": False,
            "context": False,
            "timing": False,
            "tool_results": False,
        }
    else:
        result = {
            "cost": True,
            "tokens": True,
            "input_tokens": True,
            "output_tokens": True,
            "cache": True,
            "throughput": True,
            "context": True,
            "timing": True,
            "tool_results": True,
        }
    result.update({key: bool(value) for key, value in overrides.items() if key in result})
    return result


def metric_available(row, metric):
    """Treat absent availability as available for legacy states and fixtures."""
    availability = (row or {}).get("availability")
    return not isinstance(availability, dict) or availability.get(metric) is not False


def make_usage_provenance(session_ids, estimated_ids=(), available_ids=None,
                          estimated_cost=0.0, estimated_tokens=0):
    """Describe evidence quality separately from metric coverage."""
    session_ids = set(session_ids or ())
    estimated_ids = set(estimated_ids or ()) & session_ids
    available_ids = session_ids if available_ids is None else set(available_ids or ()) & session_ids
    estimated_ids &= available_ids
    reported_ids = available_ids - estimated_ids
    unavailable_ids = session_ids - available_ids
    if estimated_ids and reported_ids:
        basis = "mixed"
    elif estimated_ids:
        basis = "local_estimate"
    elif reported_ids:
        basis = "reported"
    else:
        basis = "unavailable"
    return {
        "usage_basis": basis,
        "reported_sessions": len(reported_ids),
        "estimated_sessions": len(estimated_ids),
        "unavailable_sessions": len(unavailable_ids),
        "estimated_cost": float(estimated_cost or 0),
        "estimated_tokens": int(estimated_tokens or 0),
    }


def usage_provenance(rows):
    """Roll up reported versus local-estimate sessions without changing coverage."""
    rows = list(rows or [])
    ids = set()
    estimated_ids = set()
    available_ids = set()
    estimated_cost = 0.0
    estimated_tokens = 0
    for index, row in enumerate(rows):
        row = row or {}
        session_id = row.get("id") or row.get("path") or f"row-{index}"
        ids.add(session_id)
        available = metric_available(row, "cost") or metric_available(row, "tokens")
        if available:
            available_ids.add(session_id)
        if row.get("token_estimate"):
            estimated_ids.add(session_id)
            if metric_available(row, "cost"):
                estimated_cost += float(row.get("cost") or 0)
            if metric_available(row, "tokens"):
                estimated_tokens += int(row.get("tokens") or 0)
    return make_usage_provenance(
        ids, estimated_ids, available_ids, estimated_cost, estimated_tokens
    )


def decode_claude_project(name):
    user = os.environ.get("USER", "")
    prefix = "-Users-" + user
    if user and name.startswith(prefix):
        name = "~" + name[len(prefix):]
    return name.strip("-").replace("-", "/").replace("~/", "~/")


def decode_cursor_project(name):
    """Best-effort fallback for Cursor's lossy project directory encoding."""
    user = os.environ.get("USER", "")
    prefix = f"Users-{user}-" if user else ""
    if prefix and name.startswith(prefix):
        return home_shorten("/Users/" + user + "/" + name[len(prefix):].replace("-", "/"))
    return name.replace("-", "/") if name else ""


def claude_trace_cwd(path, max_lines=120):
    """Prefer Claude's recorded cwd over its lossy hyphen-encoded folder name."""
    try:
        with open(path, encoding="utf-8") as fh:
            for index, line in enumerate(fh):
                if index >= max_lines:
                    break
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except (TypeError, json.JSONDecodeError):
                    continue
                cwd = row.get("cwd") if isinstance(row, dict) else None
                if isinstance(cwd, str) and cwd.strip():
                    return cwd.strip()
    except OSError:
        pass
    return ""


def codex_id_from_path(path, meta=None):
    if meta and meta.get("session_id"):
        return meta["session_id"]
    base = os.path.basename(path).rsplit(".", 1)[0]
    match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", base)
    return match.group(1) if match else base


def normalize_dynamic_tools(dynamic_tools):
    """Flatten old function arrays and newer namespace-grouped tool catalogs."""
    out = []
    for item in dynamic_tools or []:
        if not isinstance(item, dict):
            out.append({
                "namespace": "unknown", "name": str(item) or "?", "kind": "tool",
                "defer_loading": False, "definition_tokens": 0,
            })
            continue
        children = item.get("tools")
        rows = children if isinstance(children, list) else [item]
        parent_namespace = item.get("namespace") or item.get("name") or "unknown"
        parent_deferred = bool(item.get("deferLoading"))
        for child in rows:
            if not isinstance(child, dict):
                child = {"name": str(child)}
            name = child.get("name") or "?"
            namespace = child.get("namespace") or parent_namespace or "unknown"
            raw_identity = name
            if name.startswith("mcp__"):
                ident = tool_identity(name)
                namespace = ident["namespace"]
                kind = "mcp"
            elif str(namespace).startswith("mcp__"):
                parts = str(namespace).split("__")
                namespace = parts[1] if len(parts) > 1 and parts[1] else "mcp"
                raw_identity = f"mcp__{namespace}__{name}"
                kind = "mcp"
            else:
                kind = "tool"
            definition = {
                "description": child.get("description") or "",
                "inputSchema": child.get("inputSchema") or child.get("input_schema") or {},
            }
            out.append({
                "namespace": namespace,
                "name": raw_identity,
                "kind": kind,
                "defer_loading": bool(child.get("deferLoading", parent_deferred)),
                "definition_tokens": len(json.dumps(definition, sort_keys=True)) // CHARS_PER_TOKEN,
            })
    return out[:240]


def catalog_counts(catalog):
    advertised = len(catalog or [])
    deferred = sum(1 for row in catalog or [] if row.get("defer_loading"))
    return {"advertised": advertised, "eager": max(0, advertised - deferred), "deferred": deferred}


def codex_meta(path):
    meta = {"session_id": None, "cwd": None, "model": None, "model_provider": None,
            "tools_loaded": 0, "tools_eager": 0, "tools_deferred": 0,
            "tool_catalog": [], "tool_namespaces": []}
    try:
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i > 120:
                    break
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                if obj.get("type") == "session_meta":
                    meta["session_id"] = payload.get("session_id") or payload.get("id") or meta["session_id"]
                    meta["cwd"] = payload.get("cwd") or meta["cwd"]
                    meta["model_provider"] = payload.get("model_provider") or meta["model_provider"]
                    dynamic_tools = payload.get("dynamic_tools")
                    if isinstance(dynamic_tools, list):
                        meta["tool_catalog"] = normalize_dynamic_tools(dynamic_tools)
                        counts = catalog_counts(meta["tool_catalog"])
                        meta["tools_loaded"] = counts["advertised"]
                        meta["tools_eager"] = counts["eager"]
                        meta["tools_deferred"] = counts["deferred"]
                        meta["tool_namespaces"] = sorted(set(t["namespace"] for t in meta["tool_catalog"]))
                elif obj.get("type") == "turn_context":
                    meta["cwd"] = payload.get("cwd") or meta["cwd"]
                    meta["model"] = payload.get("model") or meta["model"]
    except FileNotFoundError:
        pass
    return meta


def codex_index():
    idx = {}
    for row in load(CODEX_INDEX):
        sid = row.get("id")
        if sid:
            idx[sid] = row
    return idx


def _cursor_json(value, default=None):
    """Decode one Cursor SQLite JSON value without failing the whole session."""
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return {} if default is None else default
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value) if value else ({} if default is None else default)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {} if default is None else default
    return decoded


def _cursor_db_connection(path=None):
    """Open Cursor's live database read-only while retaining WAL visibility."""
    path = os.path.abspath(os.path.expanduser(path or CURSOR_STATE_DB))
    uri = f"file:{quote(path, safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=0.05)
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 50")
    return conn


def cursor_workspace_path(*values):
    """Extract a local workspace path from Cursor header/composer metadata."""
    for value in values:
        if not isinstance(value, dict):
            continue
        workspace = value.get("workspaceIdentifier")
        if isinstance(workspace, dict):
            uri = workspace.get("uri")
            if isinstance(uri, dict):
                path = uri.get("fsPath") or uri.get("path")
                if isinstance(path, str) and path.strip():
                    return path.strip()
        repos = value.get("trackedGitRepos")
        if isinstance(repos, list):
            for repo in repos:
                path = repo.get("repoPath") if isinstance(repo, dict) else None
                if isinstance(path, str) and path.strip():
                    return path.strip()
    return ""


def cursor_model(composer, header=None):
    config = composer.get("modelConfig") if isinstance(composer, dict) else {}
    if isinstance(config, dict) and config.get("modelName"):
        return str(config["modelName"])
    for value in (composer, header):
        if isinstance(value, dict) and value.get("model"):
            return str(value["model"])
    return "unknown"


def cursor_metadata_index(db_path=None):
    """Return top-level Cursor Composer metadata in one read-only snapshot."""
    result = {}
    try:
        with _cursor_db_connection(db_path) as conn:
            headers = conn.execute(
                "SELECT composerId, workspaceId, createdAt, lastUpdatedAt, "
                "isArchived, isSubagent, checkpointAt, value FROM composerHeaders"
            ).fetchall()
            composer_values = {
                str(key).split(":", 1)[1]: _cursor_json(value)
                for key, value in conn.execute(
                    "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
                ).fetchall()
                if str(key).startswith("composerData:")
            }
    except (OSError, sqlite3.Error):
        return result

    for composer_id, workspace_id, created_at, updated_at, archived, subagent, checkpoint_at, raw in headers:
        header = _cursor_json(raw)
        composer = composer_values.get(str(composer_id), {})
        result[str(composer_id)] = {
            "workspace_id": workspace_id,
            "created_at": int(created_at or header.get("createdAt") or composer.get("createdAt") or 0),
            "updated_at": int(updated_at or header.get("lastUpdatedAt") or composer.get("lastUpdatedAt") or 0),
            "checkpoint_at": int(checkpoint_at or 0),
            "archived": bool(archived),
            "is_subagent": bool(subagent or header.get("isBestOfNSubcomposer") or
                                composer.get("isBestOfNSubcomposer")),
            "title": compact_text(str(header.get("name") or composer.get("name") or ""), 90),
            "project": cursor_workspace_path(header, composer),
            "model": cursor_model(composer, header),
            "header": header,
            "composer": composer,
        }
    return result


def cursor_snapshot(composer_id, db_path=None):
    """Read one ordered Cursor conversation, degrading cleanly on schema drift."""
    try:
        with _cursor_db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT workspaceId, createdAt, lastUpdatedAt, isArchived, "
                "isSubagent, checkpointAt, value FROM composerHeaders WHERE composerId = ?",
                (composer_id,),
            ).fetchone()
            if not row:
                return {"available": False, "header": {}, "composer": {}, "bubbles": []}
            composer_row = conn.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?", (f"composerData:{composer_id}",)
            ).fetchone()
            header = _cursor_json(row[6])
            composer = _cursor_json(composer_row[0]) if composer_row else {}
            ordered = composer.get("fullConversationHeadersOnly") or []
            bubble_ids = [
                str(item.get("bubbleId")) for item in ordered
                if isinstance(item, dict) and item.get("bubbleId")
            ]
            values = {}
            if bubble_ids:
                keys = [f"bubbleId:{composer_id}:{bubble_id}" for bubble_id in bubble_ids]
                for offset in range(0, len(keys), 400):
                    chunk = keys[offset:offset + 400]
                    placeholders = ",".join("?" for _ in chunk)
                    values.update(conn.execute(
                        f"SELECT key, value FROM cursorDiskKV WHERE key IN ({placeholders})", chunk
                    ).fetchall())
            bubbles = []
            for bubble_id in bubble_ids:
                value = values.get(f"bubbleId:{composer_id}:{bubble_id}")
                bubble = _cursor_json(value, None)
                if isinstance(bubble, dict) and bubble:
                    bubbles.append(bubble)
            return {
                "available": True,
                "header": header,
                "composer": composer,
                "bubbles": bubbles,
                "created_at": int(row[1] or 0),
                "updated_at": int(row[2] or 0),
                "checkpoint_at": int(row[5] or 0),
                "is_subagent": bool(row[4]),
            }
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return {"available": False, "header": {}, "composer": {}, "bubbles": []}


CURSOR_TRACE_SPANS = {
    "client.ttft",
    "agent.request.attempt",
    "rpc.run",
    "ComposerChatService.submitChatMaybeAbortCurrent",
}
CURSOR_TRACE_FIELD_RE = re.compile(r'(\w+)=("[^"]*"|\S+)')


def _cursor_request_file(path):
    """Parse only bounded timing metadata from one Cursor request trace."""
    try:
        stat = os.stat(path)
    except OSError:
        return []
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _cursor_request_cache.get(path)
    if cached and cached["signature"] == signature:
        return cached["rows"]
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if " span_completed " not in line:
                    continue
                fields = {
                    key: value[1:-1] if value.startswith('"') and value.endswith('"') else value
                    for key, value in CURSOR_TRACE_FIELD_RE.findall(line)
                }
                name = fields.get("name")
                composer_id = fields.get("composerId")
                if name not in CURSOR_TRACE_SPANS or not composer_id:
                    continue
                try:
                    duration_ms = float(fields.get("durationMs") or 0)
                except (TypeError, ValueError):
                    continue
                end_ts = cursor_timestamp(line.split(" ", 1)[0])
                if not end_ts or duration_ms < 0 or duration_ms > 24 * 60 * 60 * 1000:
                    continue
                rows.append({
                    "composer_id": composer_id,
                    "request_id": fields.get("requestId") or "",
                    "trace_id": fields.get("traceId") or "",
                    "name": name,
                    "end_ts": float(end_ts),
                    "start_ts": float(end_ts) - duration_ms / 1000.0,
                    "duration_s": duration_ms / 1000.0,
                    "error": str(fields.get("error") or "").lower() == "true",
                })
    except OSError:
        rows = []
    _cursor_request_cache[path] = {"signature": signature, "rows": rows}
    return rows


def cursor_request_spans(composer_id, root=None):
    root = os.path.expanduser(root or CURSOR_REQUEST_LOGS)
    paths = glob.glob(os.path.join(root, "**", "cursor.requestTraces.log"), recursive=True)
    current = set(paths)
    for path in list(_cursor_request_cache):
        if path.startswith(root + os.sep) and path not in current:
            _cursor_request_cache.pop(path, None)
    return sorted(
        (row for path in paths for row in _cursor_request_file(path)
         if row.get("composer_id") == composer_id),
        key=lambda row: (row["end_ts"], row["name"]),
    )


def cursor_enrichment_mtime(db_path=None, log_root=None):
    db_path = os.path.expanduser(db_path or CURSOR_STATE_DB)
    log_root = os.path.expanduser(log_root or CURSOR_REQUEST_LOGS)
    values = [safe_mtime(db_path), safe_mtime(db_path + "-wal")]
    values.extend(safe_mtime(path) for path in glob.glob(
        os.path.join(log_root, "**", "cursor.requestTraces.log"), recursive=True
    ))
    return max(values, default=0)


def claude_desktop_metadata_paths(root=None):
    if root:
        return glob.glob(os.path.join(root, "**", "local_*.json"), recursive=True)
    paths = []
    for data_root in CLAUDE_DESKTOP_DATA_ROOTS:
        paths.extend(glob.glob(os.path.join(data_root, "claude-code-sessions", "*", "*", "local_*.json")))
        paths.extend(glob.glob(os.path.join(data_root, "local-agent-mode-sessions", "*", "*", "local_*.json")))
    return paths


def claude_desktop_index(root=None):
    """Map Claude Desktop metadata onto CLI trace ids."""
    idx = {}
    for path in claude_desktop_metadata_paths(root):
        try:
            with open(path, encoding="utf-8") as fh:
                row = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        if not isinstance(row, dict):
            continue
        cli_id = row.get("cliSessionId")
        if not cli_id:
            continue
        title = compact_text(row.get("title") or "", 90)
        if title.lower() in ("untitled", "untitled session"):
            title = ""
        source_kind = "agent" if f"{os.sep}local-agent-mode-sessions{os.sep}" in path else "project"
        origin_cwd = row.get("originCwd") or ""
        selected_folders = row.get("userSelectedFolders") or []
        selected_cwd = next(
            (folder for folder in selected_folders if isinstance(folder, str) and folder.strip()),
            "",
        ) if isinstance(selected_folders, list) else ""
        raw_cwd = origin_cwd or selected_cwd or row.get("cwd") or ""
        no_project = bool(
            source_kind == "agent"
            and not origin_cwd
            and not selected_cwd
            and os.path.basename(raw_cwd) == "outputs"
        )
        candidate = {
            "client": "claude_desktop",
            "label": "Claude Desktop",
            "desktop_session_id": row.get("sessionId") or os.path.basename(path).rsplit(".", 1)[0],
            "cli_session_id": cli_id,
            "cwd": raw_cwd,
            "project": "No project" if no_project else home_shorten(raw_cwd),
            "source_kind": source_kind,
            "title": title or None,
            "model": row.get("model"),
            "metadata_path": path,
            "metadata_mtime": safe_mtime(path),
            "last_activity_ms": int(row.get("lastActivityAt") or 0),
        }
        previous = idx.get(cli_id)
        if not previous or (candidate["last_activity_ms"], candidate["metadata_mtime"]) > (
                previous["last_activity_ms"], previous["metadata_mtime"]):
            idx[cli_id] = candidate
    return idx


def claude_local_agent_sources(desktop_idx):
    sources = []
    for desktop in desktop_idx.values():
        if desktop.get("source_kind") != "agent":
            continue
        metadata_path = desktop.get("metadata_path") or ""
        session_root = metadata_path.rsplit(".json", 1)[0]
        trace_pattern = os.path.join(
            session_root, ".claude", "projects", "*", f"{desktop.get('cli_session_id')}.jsonl"
        )
        for path in glob.glob(trace_pattern):
            sources.append({
                "provider": "claude", "client": "claude_desktop", "label": "Claude Desktop",
                "id": desktop.get("cli_session_id"),
                "desktop_session_id": desktop.get("desktop_session_id"),
                "session": os.path.basename(path), "path": path,
                "metadata_path": metadata_path,
                "project": desktop.get("project") or "No project",
                "mtime": max(safe_mtime(path), float(desktop.get("metadata_mtime") or 0)),
                "title": desktop.get("title"), "model": desktop.get("model"),
                "desktop_source_kind": "agent",
            })
    return sources


def all_session_sources():
    sources = []
    desktop_idx = claude_desktop_index()
    known_paths = set()

    for path in glob.glob(os.path.join(CLAUDE_PROJECTS, "*", "*.jsonl")):
        sid = os.path.basename(path).rsplit(".", 1)[0]
        project_raw = os.path.basename(os.path.dirname(path))
        desktop = desktop_idx.get(sid) or {}
        trace_cwd = claude_trace_cwd(path)
        project = desktop.get("project") or home_shorten(trace_cwd) or decode_claude_project(project_raw)
        source = {
            "provider": "claude",
            "client": desktop.get("client") or "claude_code",
            "label": desktop.get("label") or "Claude Code",
            "id": sid,
            "desktop_session_id": desktop.get("desktop_session_id"),
            "session": os.path.basename(path),
            "path": path,
            "metadata_path": desktop.get("metadata_path"),
            "project": project,
            "mtime": max(safe_mtime(path), float(desktop.get("metadata_mtime") or 0)),
            "title": desktop.get("title"),
            "model": desktop.get("model"),
        }
        sources.append(source)
        known_paths.add(path)

    for source in claude_local_agent_sources(desktop_idx):
        if source["path"] not in known_paths:
            sources.append(source)
            known_paths.add(source["path"])

    idx = codex_index()
    for path in glob.glob(os.path.join(CODEX_SESSIONS, "*", "*", "*", "*.jsonl")):
        meta = codex_meta(path)
        sid = codex_id_from_path(path, meta)
        cwd = meta.get("cwd") or os.path.dirname(path)
        sources.append({
            "provider": "codex",
            "label": "Codex",
            "id": sid,
            "session": os.path.basename(path),
            "path": path,
            "project": home_shorten(cwd),
            "mtime": safe_mtime(path),
            "title": (idx.get(sid) or {}).get("thread_name"),
            "model": meta.get("model") or DEFAULT_OPENAI_MODEL,
            "tools_loaded": meta.get("tools_loaded") or 0,
            "tools_eager": meta.get("tools_eager") or 0,
            "tools_deferred": meta.get("tools_deferred") or 0,
            "tool_catalog": meta.get("tool_catalog") or [],
            "tool_namespaces": meta.get("tool_namespaces") or [],
        })

    cursor_idx = cursor_metadata_index()
    enrichment_mtime = cursor_enrichment_mtime()
    cursor_pattern = os.path.join(
        CURSOR_PROJECTS, "*", "agent-transcripts", "*", "*.jsonl"
    )
    cursor_sources = {}
    for path in glob.glob(cursor_pattern):
        sid = os.path.basename(path).rsplit(".", 1)[0]
        if os.path.basename(os.path.dirname(path)) != sid:
            continue
        metadata = cursor_idx.get(sid) or {}
        if metadata.get("is_subagent"):
            continue
        project_dir = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path))))
        project = metadata.get("project") or decode_cursor_project(project_dir)
        metadata_mtime = max(
            float(metadata.get("updated_at") or 0) / 1000.0,
            float(metadata.get("checkpoint_at") or 0) / 1000.0,
        )
        activity_mtime = max(safe_mtime(path), metadata_mtime)
        trace_mtime = safe_mtime(path)
        candidate = {
            "provider": "cursor",
            "client": "cursor",
            "label": "Cursor",
            "runtime": "Cursor",
            "id": sid,
            "session": os.path.basename(path),
            "path": path,
            "project": home_shorten(project),
            # Activity ordering must stay session-specific. Cursor's shared DB and
            # request log are only cache-invalidation signals; using their global
            # mtimes here would make every Cursor session look equally recent.
            "mtime": activity_mtime,
            "signature_mtime": max(activity_mtime, enrichment_mtime),
            "trace_mtime": trace_mtime,
            "title": metadata.get("title") or None,
            "model": metadata.get("model") or "unknown",
        }
        previous = cursor_sources.get(sid)
        if not previous or (trace_mtime, path) > (
                float(previous.get("trace_mtime") or 0), previous.get("path") or ""):
            cursor_sources[sid] = candidate

    sources.extend(cursor_sources.values())

    return sources


def source_from_path(path):
    for source in all_session_sources():
        if source["path"] == path:
            return source
    if path and path.startswith(os.path.expanduser("~/.codex/")):
        meta = codex_meta(path)
        sid = codex_id_from_path(path, meta)
        return {
            "provider": "codex", "label": "Codex", "id": sid, "session": os.path.basename(path),
            "path": path, "project": home_shorten(meta.get("cwd") or os.path.dirname(path)),
            "mtime": safe_mtime(path), "title": None, "model": meta.get("model") or DEFAULT_OPENAI_MODEL,
            "tools_loaded": meta.get("tools_loaded") or 0,
            "tools_eager": meta.get("tools_eager") or 0,
            "tools_deferred": meta.get("tools_deferred") or 0,
            "tool_catalog": meta.get("tool_catalog") or [],
            "tool_namespaces": meta.get("tool_namespaces") or [],
        }
    if path and path.startswith(os.path.expanduser("~/.cursor/")):
        sid = os.path.basename(path).rsplit(".", 1)[0]
        metadata = cursor_metadata_index().get(sid) or {}
        project_dir = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path))))
        metadata_mtime = max(
            float(metadata.get("updated_at") or 0) / 1000.0,
            float(metadata.get("checkpoint_at") or 0) / 1000.0,
        )
        activity_mtime = max(safe_mtime(path), metadata_mtime)
        return {
            "provider": "cursor", "client": "cursor", "label": "Cursor",
            "runtime": "Cursor", "id": sid, "session": os.path.basename(path),
            "path": path,
            "project": home_shorten(metadata.get("project") or decode_cursor_project(project_dir)),
            "mtime": activity_mtime,
            "signature_mtime": max(activity_mtime, cursor_enrichment_mtime()),
            "trace_mtime": safe_mtime(path), "title": metadata.get("title") or None,
            "model": metadata.get("model") or "unknown",
        }
    sid = os.path.basename(path).rsplit(".", 1)[0]
    trace_cwd = claude_trace_cwd(path)
    return {
        "provider": "claude", "client": "claude_code", "label": "Claude Code", "id": sid,
        "session": os.path.basename(path),
        "path": path,
        "project": home_shorten(trace_cwd) or decode_claude_project(os.path.basename(os.path.dirname(path))),
        "mtime": safe_mtime(path), "title": None,
    }


def newest_source():
    sources = all_session_sources()
    return max(sources, key=lambda s: s["mtime"]) if sources else None


def find_session(sid, sources=None):
    source_pool = sources if sources is not None else all_session_sources()
    for source in source_pool:
        stem = os.path.basename(source["path"]).rsplit(".", 1)[0]
        if sid in (source["id"], source["session"], stem, source.get("desktop_session_id")):
            return source
    return None


def trash_session_log(session_id, sources=None, trash_dir=None, mover=None):
    """Move one exact, currently discovered session log to Trash."""
    session_id = str(session_id or "").strip()
    if not session_id or len(session_id) > 240:
        return {"ok": False, "error": "A valid session ID is required.", "error_code": "invalid_id"}
    source_pool = list(sources) if sources is not None else all_session_sources()
    source = find_session(session_id, sources=source_pool)
    if not source or str(source.get("id") or "") != session_id:
        return {"ok": False, "error": "Session is not in the discovered log inventory.",
                "error_code": "not_found"}
    path = str(source.get("path") or "")
    if not path.endswith(".jsonl") or not os.path.isfile(path):
        return {"ok": False, "error": "The discovered session log is not available.",
                "error_code": "not_found"}

    trash_dir = os.path.expanduser(trash_dir or "~/.Trash")
    mover = mover or shutil.move
    try:
        os.makedirs(trash_dir, exist_ok=True)
        base = f"Token Meter - {os.path.basename(path)}"
        stem, ext = os.path.splitext(base)
        destination = os.path.join(trash_dir, base)
        suffix = 2
        while os.path.exists(destination):
            destination = os.path.join(trash_dir, f"{stem} {suffix}{ext}")
            suffix += 1
        mover(path, destination)
    except OSError:
        return {"ok": False, "error": "Token Meter could not move the session log to Trash.",
                "error_code": "trash_failed"}

    _summary_cache.pop(path, None)
    _xsess["data"], _xsess["at"] = None, 0.0
    return {
        "ok": True,
        "changed": True,
        "session_id": session_id,
        "title": source.get("title") or "(untitled log)",
        "project": source.get("project") or "",
        "provider": source.get("provider") or "",
        "trash_name": os.path.basename(destination),
        "message": "Session log moved to Trash.",
    }


def _matching_price(model, table):
    if model in table:
        return table[model]
    compact = str(model or "").replace(" ", "-").lower()
    for key in sorted(table, key=len, reverse=True):
        if compact.startswith(str(key).replace(" ", "-").lower()):
            return table[key]
    return None


def cursor_model_parameters(composer, model=None):
    """Return persisted parameters for the selected Cursor model."""
    config = composer.get("modelConfig") if isinstance(composer, dict) else {}
    selected = config.get("selectedModels") if isinstance(config, dict) else []
    fallback = {}
    for row in selected or []:
        if not isinstance(row, dict):
            continue
        params = {
            str(item.get("id")): item.get("value")
            for item in (row.get("parameters") or []) if isinstance(item, dict) and item.get("id")
        }
        fallback = fallback or params
        if not model or str(row.get("modelId") or "") == str(model):
            return params
    return fallback


def cursor_price_variant(composer, model):
    compact = str(model or "").replace(" ", "-").lower()
    if not compact.startswith("composer-2.5"):
        return ""
    fast = cursor_model_parameters(composer, model).get("fast")
    if str(fast).lower() == "true":
        return "fast"
    if str(fast).lower() == "false":
        return "standard"
    return ""


def price_for(model, provider="claude", variant=None):
    if provider == "cursor":
        compact = str(model or "").replace(" ", "-").lower()
        cursor_table = effective_model_price_table("cursor")
        if compact.startswith("composer-2.5"):
            price = _matching_price(f"composer-2.5-{variant or ''}", cursor_table)
            return (price or dict(ZERO_PRICE)), True
        price = (
            _matching_price(model, cursor_table)
            or _matching_price(model, effective_model_price_table("codex"))
            or _matching_price(model, effective_model_price_table("claude"))
        )
        return (price or dict(ZERO_PRICE)), True
    if provider not in ("claude", "codex"):
        return dict(ZERO_PRICE), True
    model = model or (DEFAULT_OPENAI_MODEL if provider == "codex" else DEFAULT_CLAUDE_MODEL)
    table = effective_model_price_table(provider)
    default = DEFAULT_OPENAI_MODEL if provider == "codex" else DEFAULT_CLAUDE_MODEL
    price = _matching_price(model, table)
    if price:
        return price, False
    return table[default], True


def cost_of(u, model, provider="claude", variant=None):
    p, _ = price_for(model, provider, variant)
    return {
        "input": u.get("input_tokens", 0) * p["input"] / 1e6,
        "cache_write": u.get("cache_creation_input_tokens", 0) * p["cache_write"] / 1e6,
        "cache_read": u.get("cache_read_input_tokens", 0) * p["cache_read"] / 1e6,
        "output": u.get("output_tokens", 0) * p["output"] / 1e6,
    }


def usage_tokens(u):
    return (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            + u.get("cache_read_input_tokens", 0) + u.get("output_tokens", 0))


def usage_io_tokens(u):
    """Return total trace-reported input, including cache, and output."""
    return (
        int(u.get("input_tokens", 0) or 0)
        + int(u.get("cache_creation_input_tokens", 0) or 0)
        + int(u.get("cache_read_input_tokens", 0) or 0),
        int(u.get("output_tokens", 0) or 0),
    )


def add_model_summary(stats, model, usage, cost):
    input_tokens, output_tokens = usage_io_tokens(usage)
    row = stats.setdefault(model or "unknown", {
        "cost": 0.0, "tokens": 0, "input_tokens": 0,
        "output_tokens": 0, "executions": 0,
    })
    row["cost"] += float(cost or 0)
    row["tokens"] += input_tokens + output_tokens
    row["input_tokens"] += input_tokens
    row["output_tokens"] += output_tokens
    row["executions"] += 1
    return input_tokens, output_tokens


def add_model_daily(stats, model, usage, cost, ts):
    """Accumulate exact trace-reported model I/O into local calendar days."""
    if not ts:
        return
    input_tokens, output_tokens = usage_io_tokens(usage)
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    key = (model or "unknown", day)
    row = stats.setdefault(key, {
        "model": model or "unknown", "day": day, "cost": 0.0,
        "input_tokens": 0, "output_tokens": 0, "executions": 0,
    })
    row["cost"] += float(cost or 0)
    row["input_tokens"] += input_tokens
    row["output_tokens"] += output_tokens
    row["executions"] += 1


def claude_performance_samples(objs):
    """Return completed Claude turn samples with attributable trace timing."""
    messages = {rec["id"]: rec for rec in iter_claude_messages(objs)}
    samples = []
    current = None

    def ensure_current(ts=0):
        nonlocal current
        if current is None:
            current = {
                "message_ids": [], "seen": set(), "start_ts": ts or 0,
                "last_ts": ts or 0, "terminal": False,
                "user_pause_starts": {}, "user_pause_s": 0.0,
            }
        return current

    def close_turn(obj=None):
        nonlocal current
        duration_ms = obj.get("durationMs") if obj else 0
        try:
            duration_ms = float(duration_ms or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        group = current
        current = None
        if not group:
            return
        if duration_ms > 0:
            duration_s = duration_ms / 1000.0
            timing_basis = "turn_duration"
        else:
            duration_s = float(group.get("last_ts") or 0) - float(group.get("start_ts") or 0)
            timing_basis = "observed"
        if duration_s <= 0:
            return
        wall_duration_s = duration_s
        duration_s, user_pause_s = _claude_effective_duration(group, duration_s, timing_basis)
        if duration_s <= 0:
            return
        records = [messages[mid] for mid in group["message_ids"] if mid in messages and not messages[mid].get("side")]
        models = {rec.get("model") or DEFAULT_CLAUDE_MODEL for rec in records if rec.get("usage")}
        if len(models) != 1:
            return
        input_tokens = output_tokens = 0
        uncached_input_tokens = cache_read_tokens = cache_write_tokens = 0
        peak_input_tokens = 0
        tool_ids = set()
        for rec in records:
            usage = rec.get("usage") or {}
            in_count, out_count = usage_io_tokens(usage)
            input_tokens += in_count
            output_tokens += out_count
            uncached_input_tokens += int(usage.get("input_tokens") or 0)
            cache_read_tokens += int(usage.get("cache_read_input_tokens") or 0)
            cache_write_tokens += int(usage.get("cache_creation_input_tokens") or 0)
            peak_input_tokens = max(peak_input_tokens, in_count)
            for block in rec.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_ids.add(block.get("id") or (block.get("name"), len(tool_ids)))
        if output_tokens <= 0:
            return
        ts = (parse_iso(obj.get("timestamp", "")) if obj else 0) or group.get("last_ts") or max(
            (rec.get("last_ts") or rec.get("ts") or 0 for rec in records), default=0
        )
        samples.append({
            "provider": "claude", "model": next(iter(models)),
            "day": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
            "ts": ts or 0, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "peak_input_tokens": peak_input_tokens,
            "uncached_input_tokens": uncached_input_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "model_calls": len(records),
            "duration_s": duration_s, "generation_s": duration_s,
            "ttft_s": 0.0, "tool_calls": len(tool_ids), "timing_basis": timing_basis,
            "wall_duration_s": wall_duration_s, "user_pause_s": user_pause_s,
        })

    for obj in objs:
        otype = obj.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if otype == "user":
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            if claude_user_text(msg).strip():
                close_turn()
                current = {
                    "message_ids": [], "seen": set(), "start_ts": ts,
                    "last_ts": ts, "terminal": False,
                    "user_pause_starts": {}, "user_pause_s": 0.0,
                }
            elif current:
                _close_claude_user_pauses(current, msg, ts)
            continue
        if otype == "assistant":
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            mid = msg.get("id") or obj.get("uuid")
            group = ensure_current(ts)
            if mid not in group["seen"]:
                group["seen"].add(mid)
                group["message_ids"].append(mid)
            group["last_ts"] = max(float(group.get("last_ts") or 0), ts)
            for block in msg.get("content") or []:
                _track_claude_user_pause(group, block, ts)
            if msg.get("stop_reason") and msg.get("stop_reason") != "tool_use":
                group["terminal"] = True
            continue
        if otype == "system" and obj.get("subtype") == "turn_duration":
            close_turn(obj)
    if current and current.get("terminal"):
        close_turn()
    return samples


def codex_performance_samples(objs, default_model=None):
    """Return completed Codex task samples with model-attributable timing."""
    model = default_model or DEFAULT_OPENAI_MODEL
    samples = []
    current = None

    def ensure_current(ts=0):
        nonlocal current
        if current is None:
            current = {"started_ts": ts or 0, "usage": {}, "tool_calls": 0}
        return current

    def close_task(payload, ts):
        nonlocal current
        task = current
        current = None
        if not task or len(task["usage"]) != 1:
            return
        duration_ms = payload.get("duration_ms")
        ttft_ms = payload.get("time_to_first_token_ms")
        try:
            duration_ms = float(duration_ms or 0)
            ttft_ms = float(ttft_ms or 0)
        except (TypeError, ValueError):
            return
        if duration_ms <= 0:
            return
        sample_model, counts = next(iter(task["usage"].items()))
        output_tokens = int(counts.get("output_tokens") or 0)
        if output_tokens <= 0:
            return
        duration_s = duration_ms / 1000.0
        generation_s = (duration_ms - ttft_ms) / 1000.0 if 0 < ttft_ms < duration_ms else duration_s
        samples.append({
            "provider": "codex", "model": sample_model,
            "day": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
            "ts": ts or 0, "input_tokens": int(counts.get("input_tokens") or 0),
            "output_tokens": output_tokens, "duration_s": duration_s,
            "peak_input_tokens": int(counts.get("peak_input_tokens") or 0),
            "uncached_input_tokens": int(counts.get("uncached_input_tokens") or 0),
            "cache_read_tokens": int(counts.get("cache_read_tokens") or 0),
            "cache_write_tokens": int(counts.get("cache_write_tokens") or 0),
            "model_calls": int(counts.get("model_calls") or 0),
            "generation_s": generation_s, "ttft_s": max(0.0, ttft_ms / 1000.0),
            "tool_calls": int(task.get("tool_calls") or 0), "timing_basis": "task_complete",
        })

    for obj in objs:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if obj.get("type") == "turn_context":
            model = payload.get("model") or model
            continue
        if ptype == "task_started":
            current = {"started_ts": ts, "usage": {}, "tool_calls": 0}
            continue
        if ptype in ("function_call", "custom_tool_call", "web_search_call", "tool_search_call"):
            ensure_current(ts)["tool_calls"] += 1
            continue
        if ptype == "token_count":
            raw = ((payload.get("info") or {}).get("last_token_usage") or {})
            if not raw:
                continue
            usage = codex_usage(raw)
            task = ensure_current(ts)
            row = task["usage"].setdefault(model, {
                "input_tokens": 0, "output_tokens": 0, "peak_input_tokens": 0,
                "uncached_input_tokens": 0, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "model_calls": 0,
            })
            input_count, output_count = usage_io_tokens(usage)
            row["input_tokens"] += input_count
            row["output_tokens"] += output_count
            row["peak_input_tokens"] = max(row["peak_input_tokens"], input_count)
            row["uncached_input_tokens"] += int(usage.get("input_tokens") or 0)
            row["cache_read_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
            row["cache_write_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)
            row["model_calls"] += 1
            continue
        if ptype == "task_complete":
            close_task(payload, ts)
    return samples


def _wait_sample(group, end_ts, duration_s, timing_basis, provider,
                 wall_duration_s=None, user_pause_s=0.0):
    """Return one completed prompt-to-response wall-clock sample."""
    if not group or duration_s <= 0:
        return None
    models = sorted(name for name in (group.get("models") or set()) if name)
    model = models[0] if len(models) == 1 else ("mixed" if models else "unknown")
    return {
        "provider": provider,
        "model": model,
        "day": time.strftime("%Y-%m-%d", time.localtime(end_ts)) if end_ts else "",
        "ts": end_ts or 0,
        "start_ts": float(group.get("start_ts") or 0),
        "duration_s": float(duration_s),
        "tool_calls": int(group.get("tool_calls") or 0),
        "output_tokens": int(group.get("output_tokens") or 0),
        "timing_basis": timing_basis,
        "wall_duration_s": float(wall_duration_s if wall_duration_s is not None else duration_s),
        "user_pause_s": float(user_pause_s or 0),
    }


def claude_wait_samples(objs):
    """Return completed Claude prompt-to-response wait samples.

    Wait time is deliberately end to end: reasoning, tool use, and model output
    all count because the user is still waiting for the turn to finish.
    """
    samples = []
    current = None

    def close_turn(end_ts=0, duration_ms=0, allow_observed=False):
        nonlocal current
        group = current
        current = None
        if not group:
            return
        try:
            duration_ms = float(duration_ms or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        if duration_ms > 0:
            duration_s = duration_ms / 1000.0
            basis = "reported"
        elif allow_observed:
            end_ts = end_ts or group.get("last_ts") or 0
            duration_s = float(end_ts or 0) - float(group.get("start_ts") or 0)
            basis = "observed"
        else:
            return
        wall_duration_s = duration_s
        duration_s, user_pause_s = _claude_effective_duration(group, duration_s, basis)
        sample = _wait_sample(group, end_ts or group.get("last_ts") or 0,
                              duration_s, basis, "claude", wall_duration_s, user_pause_s)
        if sample:
            samples.append(sample)

    for obj in objs:
        otype = obj.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if _claude_user_prompt(obj):
            close_turn(allow_observed=bool(current and current.get("terminal")))
            current = {
                "start_ts": ts, "last_ts": ts, "models": set(),
                "tool_ids": set(), "tool_calls": 0, "output_tokens": 0,
                "seen_usage": set(), "terminal": False,
                "user_pause_starts": {}, "user_pause_s": 0.0,
            }
            continue
        if otype == "user" and current:
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            _close_claude_user_pauses(current, msg, ts)
            current["last_ts"] = max(float(current.get("last_ts") or 0), ts)
            continue
        if otype == "assistant" and current:
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            model = msg.get("model")
            if model:
                current["models"].add(model)
            current["last_ts"] = max(float(current.get("last_ts") or 0), ts)
            message_id = msg.get("id") or obj.get("uuid")
            if message_id not in current["seen_usage"]:
                current["seen_usage"].add(message_id)
                _, output_tokens = usage_io_tokens(msg.get("usage") or {})
                current["output_tokens"] += output_tokens
            for block in msg.get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool_id = block.get("id") or (block.get("name"), len(current["tool_ids"]))
                current["tool_ids"].add(tool_id)
                _track_claude_user_pause(current, block, ts)
            current["tool_calls"] = len(current["tool_ids"])
            if msg.get("stop_reason") and msg.get("stop_reason") != "tool_use":
                current["terminal"] = True
            continue
        if otype == "system" and obj.get("subtype") == "turn_duration":
            close_turn(ts, obj.get("durationMs"), allow_observed=True)
    if current and current.get("terminal"):
        close_turn(current.get("last_ts") or 0, allow_observed=True)
    return samples


def codex_wait_samples(objs, default_model=None):
    """Return completed Codex task prompt-to-response wait samples."""
    samples = []
    model = default_model or DEFAULT_OPENAI_MODEL
    current = None

    def close_task(end_ts=0, duration_ms=0, allow_observed=False):
        nonlocal current
        group = current
        current = None
        if not group:
            return
        try:
            duration_ms = float(duration_ms or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        if duration_ms > 0:
            duration_s = duration_ms / 1000.0
            basis = "reported"
        elif allow_observed:
            end_ts = end_ts or group.get("last_ts") or 0
            duration_s = float(end_ts or 0) - float(group.get("start_ts") or 0)
            basis = "observed"
        else:
            return
        sample = _wait_sample(group, end_ts or group.get("last_ts") or 0,
                              duration_s, basis, "codex")
        if sample:
            samples.append(sample)

    for obj in objs:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if obj.get("type") == "turn_context":
            model = payload.get("model") or model
            if current:
                current["models"].add(model)
            continue
        if ptype == "task_started":
            close_task(allow_observed=False)
            current = {
                "start_ts": ts, "last_ts": ts, "models": {model},
                "tool_calls": 0, "output_tokens": 0,
            }
            continue
        if not current:
            continue
        current["last_ts"] = max(float(current.get("last_ts") or 0), ts)
        if ptype in ("function_call", "custom_tool_call", "web_search_call", "tool_search_call"):
            current["tool_calls"] += 1
            continue
        if ptype == "token_count":
            raw = ((payload.get("info") or {}).get("last_token_usage") or {})
            if raw:
                current["output_tokens"] += usage_io_tokens(codex_usage(raw))[1]
            continue
        if ptype == "task_complete":
            close_task(ts, payload.get("duration_ms"), allow_observed=True)
    return samples


def wait_time_summary(samples):
    """Summarize completed prompt-to-response wall-clock waits."""
    rows = [row for row in (samples or []) if float(row.get("duration_s") or 0) > 0]
    durations = sorted(float(row.get("duration_s") or 0) for row in rows)
    count = len(durations)
    total = sum(durations)
    p95_index = min(count - 1, max(0, math.ceil(count * 0.95) - 1)) if count else 0
    return {
        "available": bool(count),
        "total_s": total,
        "avg_s": total / count if count else 0,
        "median_s": statistics.median(durations) if count else 0,
        "p95_s": durations[p95_index] if count else 0,
        "max_s": durations[-1] if count else 0,
        "sample_count": count,
        "reported_samples": sum(row.get("timing_basis") == "reported" for row in rows),
        "observed_samples": sum(row.get("timing_basis") == "observed" for row in rows),
        "user_pause_s": sum(float(row.get("user_pause_s") or 0) for row in rows),
    }


def performance_summary(samples, total_output_tokens=0):
    """Summarize weighted observed output throughput without averaging rates."""
    timed = [row for row in (samples or []) if row.get("output_tokens", 0) > 0 and row.get("duration_s", 0) > 0]
    tool_free = [row for row in timed if int(row.get("tool_calls") or 0) == 0]
    selected = tool_free or timed
    basis = "tool_free" if tool_free else ("end_to_end" if timed else "unavailable")

    def seconds(row):
        if basis == "tool_free":
            return float(row.get("generation_s") or row.get("duration_s") or 0)
        return float(row.get("duration_s") or 0)

    measured_seconds = sum(seconds(row) for row in selected)
    measured_output = sum(int(row.get("output_tokens") or 0) for row in selected)
    latest = max(selected, key=lambda row: row.get("ts") or 0) if selected else None
    latest_seconds = seconds(latest) if latest else 0
    ttft_rows = [float(row.get("ttft_s") or 0) for row in selected if row.get("ttft_s", 0) > 0]
    denominator = int(total_output_tokens or 0)
    return {
        "available": bool(measured_seconds > 0 and measured_output > 0),
        "output_tps": (measured_output / measured_seconds) if measured_seconds > 0 else 0,
        "latest_output_tps": ((latest.get("output_tokens") or 0) / latest_seconds) if latest_seconds > 0 else 0,
        "basis": basis,
        "sample_count": len(selected),
        "timed_samples": len(timed),
        "tool_free_samples": len(tool_free),
        "measured_output_tokens": measured_output,
        "measured_seconds": measured_seconds,
        "timing_coverage": (measured_output / denominator) if denominator > 0 else 0,
        "avg_ttft_ms": (sum(ttft_rows) * 1000 / len(ttft_rows)) if ttft_rows else 0,
    }


def codex_usage(raw):
    raw = raw or {}
    input_total = int(raw.get("input_tokens") or 0)
    cached = int(raw.get("cached_input_tokens") or 0)
    return {
        "input_tokens": max(0, input_total - cached),
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cached,
        "output_tokens": int(raw.get("output_tokens") or 0),
        "reasoning_output_tokens": int(raw.get("reasoning_output_tokens") or 0),
        "total_tokens": int(raw.get("total_tokens") or 0),
    }


def tool_identity(name):
    raw = name or "?"
    clean = raw
    kind = "tool"
    if raw.startswith("mcp__"):
        parts = raw.split("__")
        namespace = parts[1] if len(parts) > 1 and parts[1] else "mcp"
        clean = parts[2] if len(parts) > 2 and parts[2] else raw
        kind = "mcp"
    elif "." in raw:
        namespace = raw.split(".", 1)[0]
        clean = raw.split(".")[-1]
    elif raw in ("exec_command", "write_stdin"):
        namespace = "shell"
    elif raw == "apply_patch":
        namespace = "files"
    elif raw.startswith("web_") or raw.startswith("web"):
        namespace = "web"
    elif raw.startswith("multi_tool"):
        namespace = "orchestration"
    elif raw.startswith("tool_search"):
        namespace = "tool_search"
    else:
        namespace = raw.split("_", 1)[0] if raw and raw != "?" else "unknown"
    return {"name": raw, "display": clean, "namespace": namespace, "kind": kind}


def trace_event(ts, kind, label, detail="", execution=None, tool=None, tokens=0, cost=0.0, severity="neutral", **meta):
    event = {
        "ts": ts or 0,
        "time": local_tm(ts),
        "local": local_dt(ts),
        "kind": kind,
        "label": label,
        "detail": detail,
        "execution": execution,
        "tool": tool,
        "tokens": int(tokens or 0),
        "cost": round(cost or 0.0, 6),
        "severity": severity,
    }
    for key, value in meta.items():
        if value is not None:
            event[key] = value
    return event


def trim_trace(trace):
    return trace[-TRACE_LIMIT:]


def compact_text(s, limit=72):
    s = " ".join((s or "").split())
    return s[:limit - 1] + "…" if len(s) > limit else s


def text_from_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for block in content:
            if isinstance(block, dict):
                pieces.append(block.get("text") or block.get("content") or "")
        return " ".join(p for p in pieces if isinstance(p, str))
    return ""


def claude_user_text(msg):
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    pieces = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in (None, "text"):
            pieces.append(block.get("text") or block.get("content") or "")
    return " ".join(p for p in pieces if isinstance(p, str))


def frustration_term_counts(text, terms):
    """Return exact configured term hits using word-safe, case-insensitive matching."""
    text = str(text or "")
    counts = {}
    for term in terms or []:
        escaped = re.escape(term).replace(r"\ ", r"\s+")
        matches = re.findall(rf"(?<!\w){escaped}(?!\w)", text, flags=re.IGNORECASE)
        if matches:
            counts[term] = len(matches)
    return counts


def week_start(day):
    if not day:
        return ""
    try:
        value = datetime.date.fromisoformat(day)
    except (TypeError, ValueError):
        return ""
    return (value - datetime.timedelta(days=value.weekday())).isoformat()


def _claude_human_text(obj):
    """Return human-authored Claude text, None for tool/meta/user-shaped records."""
    if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain"):
        return None
    if obj.get("sourceToolAssistantUUID") or obj.get("toolUseResult") is not None:
        return None
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, list):
        text_blocks = [
            block.get("text") or block.get("content") or ""
            for block in content
            if isinstance(block, dict) and block.get("type") in (None, "text")
        ]
        if not text_blocks and any(
            isinstance(block, dict) and block.get("type") == "tool_result" for block in content
        ):
            return None
        text = " ".join(value for value in text_blocks if isinstance(value, str))
    elif isinstance(content, str):
        text = content
    else:
        text = ""
    stripped = text.strip()
    meta_prefixes = (
        "<local-command-caveat>", "<local-command-stdout>",
        "<command-name>", "<system-reminder>",
    )
    if stripped.startswith(meta_prefixes):
        return None
    return text


def _dedupe_user_turns(turns, window_seconds=2.0):
    result = []
    for turn in turns:
        fingerprint = " ".join(str(turn.get("text") or "").lower().split())
        previous = result[-1] if result else None
        if previous:
            previous_fingerprint = " ".join(str(previous.get("text") or "").lower().split())
            delta = abs(float(turn.get("ts") or 0) - float(previous.get("ts") or 0))
            if fingerprint == previous_fingerprint and delta <= window_seconds:
                continue
        result.append(turn)
    return result


def claude_user_turns(objs, default_model=None):
    turns = []
    pending = []
    current_model = default_model or DEFAULT_CLAUDE_MODEL
    for obj in objs or []:
        text = _claude_human_text(obj)
        if text is not None:
            turn = {
                "ts": parse_iso(obj.get("timestamp", "")) or 0,
                "text": text,
                "model": None,
            }
            turns.append(turn)
            pending.append(turn)
            continue
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        model = msg.get("model") or current_model
        current_model = model
        if pending:
            for turn in pending:
                turn["model"] = model
            pending = []
    for turn in pending:
        turn["model"] = current_model
    return _dedupe_user_turns(turns)


def _codex_fallback_user_text(payload):
    if payload.get("type") != "message" or payload.get("role") != "user":
        return None
    text = text_from_content(payload.get("content"))
    stripped = text.strip()
    if stripped.startswith(("# AGENTS.md instructions", "<environment_context>")):
        return None
    return text


def codex_user_turns(objs, default_model=None):
    """Prefer canonical user_message events; fall back for older Codex logs."""
    current_model = default_model or DEFAULT_OPENAI_MODEL
    event_turns = []
    fallback_turns = []
    for obj in objs or []:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        if obj.get("type") == "turn_context":
            current_model = payload.get("model") or current_model
            continue
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if payload.get("type") == "user_message":
            event_turns.append({"ts": ts, "text": payload.get("message") or "", "model": current_model})
            continue
        text = _codex_fallback_user_text(payload)
        if text is not None:
            fallback_turns.append({"ts": ts, "text": text, "model": current_model})
    return _dedupe_user_turns(event_turns or fallback_turns)


def cursor_user_turns(objs, default_model=None):
    """Extract human turns from Cursor's durable transcript without wrappers."""
    turns = []
    for row in objs or []:
        if not isinstance(row, dict) or row.get("role") != "user":
            continue
        text = text_from_content(cursor_message_content(row))
        match = re.search(r"<user_query>\s*(.*?)\s*</user_query>", text, flags=re.DOTALL)
        turns.append({"ts": 0, "text": match.group(1) if match else text,
                      "model": default_model or "unknown"})
    return turns


def _new_frustration_bucket(**identity):
    return {
        **identity,
        "user_turns": 0,
        "utterances": 0,
        "matches": 0,
        "term_counts": defaultdict(int),
    }


def _add_frustration_event(bucket, event):
    bucket["user_turns"] += 1
    bucket["utterances"] += int(bool(event.get("utterance")))
    bucket["matches"] += int(event.get("matches") or 0)
    for term, count in (event.get("term_counts") or {}).items():
        bucket["term_counts"][term] += int(count or 0)


def _finish_frustration_bucket(bucket):
    row = dict(bucket)
    term_counts = row.pop("term_counts", {})
    row["rate"] = (row["utterances"] / row["user_turns"]) if row["user_turns"] else 0.0
    row["terms"] = sorted(
        ({"term": term, "count": count} for term, count in term_counts.items() if count),
        key=lambda item: (-item["count"], item["term"]),
    )
    return row


def rollup_frustration_events(events):
    total = _new_frustration_bucket()
    days = {}
    weeks = {}
    models = {}
    for event in events or []:
        _add_frustration_event(total, event)
        day = event.get("day") or ""
        week = event.get("week") or ""
        model = event.get("model") or "unknown"
        runtime = event.get("runtime") or ""
        model_id = event.get("model_id") or model
        if day:
            _add_frustration_event(days.setdefault(day, _new_frustration_bucket(day=day)), event)
        if week:
            _add_frustration_event(weeks.setdefault(week, _new_frustration_bucket(week=week)), event)
        model_row = models.setdefault(model_id, {
            "total": _new_frustration_bucket(
                id=model_id, model=model, runtime=runtime
            ), "daily": {}, "weekly": {},
        })
        _add_frustration_event(model_row["total"], event)
        if day:
            _add_frustration_event(
                model_row["daily"].setdefault(day, _new_frustration_bucket(day=day)), event
            )
        if week:
            _add_frustration_event(
                model_row["weekly"].setdefault(week, _new_frustration_bucket(week=week)), event
            )
    result = _finish_frustration_bucket(total)
    result["daily"] = [
        _finish_frustration_bucket(days[key]) for key in sorted(days)
    ]
    result["weekly"] = [
        _finish_frustration_bucket(weeks[key]) for key in sorted(weeks)
    ]
    result["models"] = []
    for model_id in sorted(models):
        model_data = models[model_id]
        row = _finish_frustration_bucket(model_data["total"])
        row["daily"] = [
            _finish_frustration_bucket(model_data["daily"][key])
            for key in sorted(model_data["daily"])
        ]
        row["weekly"] = [
            _finish_frustration_bucket(model_data["weekly"][key])
            for key in sorted(model_data["weekly"])
        ]
        result["models"].append(row)
    result["models"].sort(key=lambda row: (
        -row["utterances"], -row["user_turns"], row["model"], row.get("runtime") or ""
    ))
    return result


def analyze_frustration(provider, objs, terms=None, default_model=None):
    terms = list(frustration_settings()["terms"] if terms is None else terms)
    if provider == "codex":
        turns = codex_user_turns(objs, default_model)
    elif provider == "cursor":
        turns = cursor_user_turns(objs, default_model)
    else:
        turns = claude_user_turns(objs, default_model)
    events = []
    for turn in turns:
        ts = turn.get("ts") or 0
        day = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""
        term_counts = frustration_term_counts(turn.get("text"), terms)
        events.append({
            "ts": ts,
            "day": day,
            "week": week_start(day),
            "model": turn.get("model") or default_model or "unknown",
            "utterance": bool(term_counts),
            "matches": sum(term_counts.values()),
            "term_counts": term_counts,
        })
    return rollup_frustration_events(events), events


def user_prompt_preview(texts, limit=220):
    seen = set()
    unique = []
    for text in texts:
        key = " ".join((text or "").split())
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(text)
    return compact_text(" / ".join(unique), limit)


def execution_low_yield(execution):
    tokens = execution.get("tokens") or {}
    input_tokens = int(tokens.get("input") or execution.get("context_tokens") or 0)
    output_tokens = int(tokens.get("output") or 0)
    cost = float(execution.get("cost") or 0)
    if input_tokens <= 0:
        return False
    return (output_tokens / input_tokens) < LOW_YIELD_RATIO and cost > LOW_YIELD_COST


def low_yield_should_warn(executions, context_pct=0):
    if not executions:
        return False
    latest = executions[-1]
    if not execution_low_yield(latest):
        return False

    latest_tokens = latest.get("tokens") or {}
    latest_input = int(latest_tokens.get("input") or latest.get("context_tokens") or 0)
    consecutive = 0
    for execution in reversed(executions):
        if not execution_low_yield(execution):
            break
        consecutive += 1

    return (
        (context_pct or 0) >= LOW_YIELD_CONTEXT_PCT
        or latest_input >= LOW_YIELD_INPUT_TOKENS
        or consecutive >= 2
    )


def is_operational_warning(insight):
    key = insight.get("key") or ""
    return (
        key == "context-high"
        or key == "low-yield-latest" and insight.get("kind") == "warn"
    )


INSIGHT_CATEGORY_ORDER = {
    "Context": 0,
    "Yield": 1,
    "Spend": 2,
    "Tools": 3,
    "Cache": 4,
    "Reasoning": 5,
    "Flow": 6,
    "Pricing": 7,
}
INSIGHT_KIND_SCORE = {"warn": 0, "good": 1, "neutral": 2}


def insight(key, kind, category, title, text, detail="", action="", priority=50):
    row = {
        "key": key,
        "kind": kind,
        "category": category,
        "title": title,
        "text": text,
        "priority": priority,
    }
    if detail:
        row["detail"] = detail
    if action:
        row["action"] = action
    return row


def insight_sort_key(row):
    return (
        INSIGHT_KIND_SCORE.get(row.get("kind"), 2),
        row.get("priority", 50),
        INSIGHT_CATEGORY_ORDER.get(row.get("category"), 99),
        row.get("key") or "",
    )


def normalize_insights(rows, limit=12):
    normalized = []
    seen = set()
    for row in rows or []:
        key = row.get("key") or row.get("text") or ""
        if key in seen:
            continue
        seen.add(key)
        normalized.append(row)
    return sorted(normalized, key=insight_sort_key)[:limit]


def claude_user_events(objs):
    events = []
    for obj in objs:
        if obj.get("type") != "user":
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        txt = compact_text(claude_user_text(msg), 220)
        if txt:
            events.append({"ts": parse_iso(obj.get("timestamp", "")) or 0, "text": txt})
    return sorted(events, key=lambda e: e["ts"])


def tool_summary(executions):
    by_name = {}
    by_namespace = {}
    by_skill = defaultdict(int)
    by_execution = []

    for ex in executions:
        row_tools = {}
        for tool in ex.get("tools", []):
            name = tool.get("name") or "?"
            namespace = tool.get("namespace") or "unknown"
            kind = tool.get("kind") or "tool"
            tokens = int(tool.get("output_tokens") or 0)
            output_chars = int(tool.get("output_chars") or 0)
            args_chars = int(tool.get("args_chars") or 0)
            for skill_name in tool.get("skills") or []:
                if skill_name:
                    by_skill[str(skill_name)] += 1

            n = by_name.setdefault(name, {
                "name": name,
                "display": tool.get("display") or name,
                "namespace": namespace,
                "kind": kind,
                "calls": 0,
                "output_tokens": 0,
                "output_chars": 0,
                "args_chars": 0,
                "errors": 0,
                "executions": set(),
            })
            n["calls"] += 1
            n["output_tokens"] += tokens
            n["output_chars"] += output_chars
            n["args_chars"] += args_chars
            n["errors"] += 1 if tool.get("error") else 0
            n["executions"].add(ex["idx"])

            ns = by_namespace.setdefault(namespace, {
                "namespace": namespace,
                "kind": kind,
                "calls": 0,
                "output_tokens": 0,
                "errors": 0,
                "executions": set(),
            })
            ns["calls"] += 1
            ns["output_tokens"] += tokens
            ns["errors"] += 1 if tool.get("error") else 0
            ns["executions"].add(ex["idx"])

            key = (namespace, name)
            rt = row_tools.setdefault(key, {
                "name": name,
                "display": tool.get("display") or name,
                "namespace": namespace,
                "kind": kind,
                "calls": 0,
                "output_tokens": 0,
                "output_chars": 0,
                "args_chars": 0,
                "errors": 0,
            })
            rt["calls"] += 1
            rt["output_tokens"] += tokens
            rt["output_chars"] += output_chars
            rt["args_chars"] += args_chars
            rt["errors"] += 1 if tool.get("error") else 0
        if row_tools:
            rows = sorted(row_tools.values(), key=lambda r: (-r["output_tokens"], -r["calls"], r["name"]))
            by_execution.append({
                "execution": ex["idx"],
                "cost": ex.get("cost", 0.0),
                "tokens": ex.get("tokens", {}).get("total", 0),
                "model": ex.get("model"),
                "context_tokens": ex.get("tokens", {}).get("input", 0),
                "tool_calls": sum(r["calls"] for r in rows),
                "unique_tools": len(rows),
                "namespaces": sorted(set(r["namespace"] for r in rows)),
                "tools": rows,
            })

    by_name_rows = []
    for row in by_name.values():
        row = dict(row)
        row["executions"] = sorted(row["executions"])
        row["execution_count"] = len(row["executions"])
        by_name_rows.append(row)

    by_namespace_rows = []
    for row in by_namespace.values():
        row = dict(row)
        row["executions"] = sorted(row["executions"])
        row["execution_count"] = len(row["executions"])
        by_namespace_rows.append(row)

    total_calls = sum(r["calls"] for r in by_name_rows)
    peak_calls = max((row["tool_calls"] for row in by_execution), default=0)
    peak_unique = max((row["unique_tools"] for row in by_execution), default=0)
    shown_execution_rows = by_execution[-80:]
    return {
        "total_calls": total_calls,
        "total_output_tokens": sum(r["output_tokens"] for r in by_name_rows),
        "total_errors": sum(r["errors"] for r in by_name_rows),
        "unique_used": len(by_name_rows),
        "namespaces_used": len(by_namespace_rows),
        "peak_calls_per_execution": peak_calls,
        "peak_tools_per_execution": peak_unique,
        "execution_rows_total": len(by_execution),
        "execution_rows_shown": len(shown_execution_rows),
        "execution_rows_truncated": len(shown_execution_rows) < len(by_execution),
        "execution_calls_shown": sum(row["tool_calls"] for row in shown_execution_rows),
        "skills": [{"name": name, "activations": activations}
                   for name, activations in sorted(by_skill.items(), key=lambda item: (-item[1], item[0]))],
        "activity": {
            "scope": "session",
            "observed_unique": len(by_name_rows),
            "total_calls": total_calls,
            "peak_calls_per_execution": peak_calls,
            "namespaces_used": len(by_namespace_rows),
        },
        "by_name": sorted(by_name_rows, key=lambda r: (-r["output_tokens"], -r["calls"], r["name"]))[:16],
        "by_namespace": sorted(by_namespace_rows, key=lambda r: (-r["output_tokens"], -r["calls"], r["namespace"]))[:12],
        "by_execution": shown_execution_rows,
    }


def iter_claude_messages(objs):
    """Yield one logical Claude assistant message per message.id."""
    by_id = {}
    order = []
    for obj in objs:
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        mid = msg.get("id") or obj.get("uuid")
        rec = by_id.get(mid)
        if rec is None:
            rec = {
                "id": mid,
                "model": msg.get("model", DEFAULT_CLAUDE_MODEL),
                "usage": msg.get("usage") or {},
                "stop_reason": msg.get("stop_reason"),
                "ts": parse_iso(obj.get("timestamp", "")),
                "last_ts": parse_iso(obj.get("timestamp", "")),
                "side": bool(obj.get("isSidechain")),
                "content": [],
            }
            by_id[mid] = rec
            order.append(mid)
        content = msg.get("content")
        if isinstance(content, list):
            rec["content"].extend(b for b in content if isinstance(b, dict))
        if (msg.get("usage") or {}).get("output_tokens", 0) >= rec["usage"].get("output_tokens", 0):
            rec["usage"] = msg.get("usage") or rec["usage"]
        if msg.get("stop_reason"):
            rec["stop_reason"] = msg.get("stop_reason")
        rec["last_ts"] = max(rec.get("last_ts") or 0, parse_iso(obj.get("timestamp", "")) or 0)
    return [by_id[i] for i in order]


def tool_result_is_error(value, explicit=False):
    if explicit:
        return True
    if isinstance(value, dict):
        if value.get("is_error") is True or value.get("success") is False:
            return True
        status = str(value.get("status") or "").lower()
        if status in ("error", "failed", "failure"):
            return True
        exit_code = value.get("exit_code", value.get("exitCode"))
        if exit_code not in (None, 0, "0"):
            return True
        if value.get("error") not in (None, "", False):
            return True
        return any(tool_result_is_error(v) for v in value.values() if isinstance(v, (dict, list)))
    if isinstance(value, list):
        return any(tool_result_is_error(v) for v in value)
    text = str(value or "").strip().lower()
    return text.startswith(("error:", "failed:", "tool error", "process exited with code"))


def observable_output_chars(value):
    """Count trace-visible text while excluding embedded image/base64 bytes."""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value or "")
    text = DATA_URL_RE.sub("<image-data>", text)
    text = BASE64_FIELD_RE.sub(r'\1<binary-data>\3', text)
    return len(text)


def argument_fingerprint(value):
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value or "")
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16] if text else ""


def skill_names_from_value(value):
    """Infer skill activations only when a trace argument references SKILL.md."""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value or "")
    return sorted(set(match.group(1) for match in SKILL_PATH_RE.finditer(text)))


def claude_tool_results(objs):
    chars_by_id = defaultdict(int)
    ts_by_id = {}
    errors_by_id = defaultdict(bool)
    for obj in objs:
        if obj.get("type") != "user":
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = block.get("tool_use_id")
                chars_by_id[tid] += observable_output_chars(block.get("content", ""))
                ts_by_id[tid] = parse_iso(obj.get("timestamp", ""))
                errors_by_id[tid] = errors_by_id[tid] or tool_result_is_error(
                    block.get("content"), block.get("is_error") is True
                )
    return chars_by_id, ts_by_id, errors_by_id


CURSOR_TOOL_IDENTITIES = {
    "read_file_v2": ("Read", "files"),
    "ripgrep_raw_search": ("Grep", "search"),
    "glob_file_search": ("Glob", "search"),
    "run_terminal_command_v2": ("Shell", "shell"),
    "edit_file_v2": ("Edit", "files"),
    "apply_patch": ("Apply patch", "files"),
    "todo_write": ("Todo", "planning"),
    "web_search": ("Web search", "web"),
    "web_fetch": ("Web fetch", "web"),
    "delete_file": ("Delete", "files"),
    "await": ("Await", "orchestration"),
}
CURSOR_TOOL_ALIASES = {
    "read": "read_file_v2", "readfile": "read_file_v2", "read_file": "read_file_v2",
    "grep": "ripgrep_raw_search", "rg": "ripgrep_raw_search",
    "glob": "glob_file_search", "shell": "run_terminal_command_v2",
    "edit": "edit_file_v2", "applypatch": "apply_patch",
    "todowrite": "todo_write", "websearch": "web_search", "webfetch": "web_fetch",
    "delete": "delete_file", "deletefile": "delete_file",
}


def cursor_tool_identity(name):
    raw = str(name or "?")
    alias = re.sub(r"[^a-z0-9_]", "", raw.lower())
    canonical = CURSOR_TOOL_ALIASES.get(alias, raw)
    display, namespace = CURSOR_TOOL_IDENTITIES.get(canonical, (canonical, "cursor"))
    return {"name": canonical, "display": display, "namespace": namespace, "kind": "tool"}


def cursor_timestamp(value):
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return float(parse_iso(value) or 0)
    return 0.0


def cursor_message_content(row):
    message = row.get("message") if isinstance(row, dict) else {}
    content = message.get("content") if isinstance(message, dict) else []
    return content if isinstance(content, list) else []


def cursor_transcript_groups(rows, source):
    """Reconstruct coarse turns when Cursor SQLite enrichment is unavailable."""
    groups = []
    current = None
    for row in rows or []:
        role = row.get("role") if isinstance(row, dict) else None
        if role == "user":
            if current:
                groups.append(current)
            user_text = text_from_content(cursor_message_content(row))
            query = re.search(r"<user_query>\s*(.*?)\s*</user_query>", user_text, flags=re.DOTALL)
            current = {
                "user_text": query.group(1) if query else user_text,
                "model": source.get("model") or "unknown", "bubbles": [],
                "completed": False,
            }
            continue
        if role == "assistant":
            if current is None:
                current = {"user_text": "", "model": source.get("model") or "unknown",
                           "bubbles": [], "completed": False}
            current["bubbles"].append({"type": 2, "content": cursor_message_content(row)})
            continue
        if row.get("type") == "turn_ended" and current:
            current["completed"] = row.get("status") in (None, "success", "completed")
            groups.append(current)
            current = None
    if current:
        groups.append(current)
    return groups


def cursor_enriched_groups(snapshot):
    groups = []
    current = None
    for bubble in snapshot.get("bubbles") or []:
        if bubble.get("type") == 1:
            if current:
                groups.append(current)
            model_info = bubble.get("modelInfo") if isinstance(bubble.get("modelInfo"), dict) else {}
            current = {
                "user_text": str(bubble.get("text") or ""),
                "model": model_info.get("modelName") or
                         cursor_model(snapshot.get("composer") or {}, snapshot.get("header") or {}),
                "request_id": bubble.get("requestId") or "",
                "start_ts": cursor_timestamp(bubble.get("createdAt")),
                "context": bubble.get("contextWindowStatusAtCreation")
                           if isinstance(bubble.get("contextWindowStatusAtCreation"), dict) else {},
                "bubbles": [], "completed": False,
            }
            continue
        if bubble.get("type") != 2:
            continue
        if current is None:
            current = {
                "user_text": "", "model": cursor_model(
                    snapshot.get("composer") or {}, snapshot.get("header") or {}
                ), "request_id": "", "start_ts": 0, "context": {}, "bubbles": [],
                "completed": False,
            }
        current["bubbles"].append(bubble)
        if bubble.get("turnDurationMs") is not None:
            current["completed"] = True
            current["turn_duration_ms"] = bubble.get("turnDurationMs")
    if current:
        groups.append(current)
    return groups


def cursor_turn_timing(spans, start_ts, next_start_ts=0, terminal_ts=0, turn_duration_ms=0):
    boundary = float(next_start_ts or (terminal_ts + 24 * 60 * 60) or float("inf"))
    matches = [
        row for row in spans or []
        if row.get("end_ts", 0) >= float(start_ts or 0) - 1
        and row.get("start_ts", 0) < boundary
        and row.get("end_ts", 0) <= boundary + 1
    ]
    submits = [row for row in matches if row.get("name") == "ComposerChatService.submitChatMaybeAbortCurrent"]
    attempts = [row for row in matches if row.get("name") == "agent.request.attempt"]
    rpc_errors = {
        row.get("request_id") for row in matches
        if row.get("name") == "rpc.run" and row.get("error") and row.get("request_id")
    }
    ttfts = [row for row in matches if row.get("name") == "client.ttft" and row.get("duration_s", 0) > 0]
    final_ts = max((row["end_ts"] for row in submits), default=float(terminal_ts or 0))
    intervals = [(row["start_ts"], row["end_ts"]) for row in attempts]
    try:
        fallback_s = max(0.0, float(turn_duration_ms or 0) / 1000.0)
    except (TypeError, ValueError):
        fallback_s = 0.0
    if not intervals and fallback_s and terminal_ts:
        intervals = [(max(0.0, float(terminal_ts) - fallback_s), float(terminal_ts))]
    active_s = _merge_execution_intervals(intervals)
    if final_ts and start_ts and final_ts >= start_ts:
        wait_s = final_ts - start_ts
    else:
        wait_s = fallback_s
        final_ts = float(terminal_ts or (start_ts + fallback_s if start_ts else 0))
    failed = sum(bool(row.get("error") or row.get("request_id") in rpc_errors) for row in attempts)
    return {
        "end_ts": final_ts,
        "wait_s": max(0.0, wait_s),
        "active_s": max(0.0, active_s),
        "active_intervals": intervals,
        "timing_basis": "request_trace" if (submits or attempts) else
                        ("turn_duration" if fallback_s else "unavailable"),
        "ttft_s": min((row["duration_s"] for row in ttfts), default=0.0),
        "attempts": len(attempts),
        "failed_attempts": failed,
        "retries": max(0, len(attempts) - 1),
    }


def cursor_prompt_breakdown(composer):
    raw = composer.get("promptTokenBreakdown") if isinstance(composer, dict) else {}
    categories = raw.get("categories") if isinstance(raw, dict) else []
    return [
        {
            "id": str(row.get("id") or "unknown"),
            "label": str(row.get("label") or row.get("id") or "Unknown"),
            "estimated_tokens": int(row.get("estimatedTokens") or 0),
        }
        for row in (categories or []) if isinstance(row, dict)
    ]


def cursor_context_estimates(groups, latest_tokens=0, latest_window=0):
    """Fill sparse Cursor context checkpoints without calling them billable input."""
    rows = []
    for group in groups or []:
        context = group.get("context") if isinstance(group.get("context"), dict) else {}
        rows.append({
            "tokens": max(0, int(context.get("tokensUsed") or 0)),
            "window": max(0, int(context.get("tokenLimit") or 0)),
            "interpolated": False,
        })
    if not rows:
        return rows
    if latest_tokens:
        rows[-1]["tokens"] = max(0, int(latest_tokens))
    if latest_window:
        rows[-1]["window"] = max(0, int(latest_window))

    known_tokens = [index for index, row in enumerate(rows) if row["tokens"] > 0]
    for index, row in enumerate(rows):
        if row["tokens"] > 0:
            continue
        previous = max((known for known in known_tokens if known < index), default=None)
        following = min((known for known in known_tokens if known > index), default=None)
        if following is not None and previous is not None:
            span = following - previous
            share = (index - previous) / span
            start = rows[previous]["tokens"]
            row["tokens"] = max(1, round(start + (rows[following]["tokens"] - start) * share))
        elif following is not None:
            row["tokens"] = max(1, round(rows[following]["tokens"] * (index + 1) / (following + 1)))
        elif previous is not None:
            row["tokens"] = rows[previous]["tokens"]
        row["interpolated"] = bool(row["tokens"])

    known_windows = [index for index, row in enumerate(rows) if row["window"] > 0]
    for index, row in enumerate(rows):
        if row["window"] > 0:
            continue
        nearest = min(known_windows, key=lambda known: abs(known - index)) if known_windows else None
        if nearest is not None:
            row["window"] = rows[nearest]["window"]
    return rows


def cursor_visible_output(bubbles):
    """Estimate only model-authored text that Cursor persisted in its bubbles."""
    assistant_chars = 0
    reasoning_chars = 0
    for bubble in bubbles or []:
        if not isinstance(bubble, dict):
            continue
        seen = set()
        text = bubble.get("text")
        if isinstance(text, str) and text.strip():
            seen.add(text)
            assistant_chars += len(text)
        for block in bubble.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            value = block.get("text")
            if isinstance(value, str) and value.strip() and value not in seen:
                seen.add(value)
                assistant_chars += len(value)
        thinking = bubble.get("thinking")
        if isinstance(thinking, str) and thinking.strip() and thinking not in seen:
            reasoning_chars += len(thinking)
    return {
        "assistant_chars": assistant_chars,
        "reasoning_chars": reasoning_chars,
        "assistant_tokens": math.ceil(assistant_chars / CHARS_PER_TOKEN) if assistant_chars else 0,
        "reasoning_tokens": math.ceil(reasoning_chars / CHARS_PER_TOKEN) if reasoning_chars else 0,
    }


def cursor_model_call_count(bubbles):
    call_ids = set()
    has_model_activity = False
    for bubble in bubbles or []:
        if not isinstance(bubble, dict):
            continue
        tool_data = bubble.get("toolFormerData")
        if isinstance(tool_data, dict):
            call_id = tool_data.get("modelCallId")
            if call_id:
                call_ids.add(str(call_id))
            has_model_activity = has_model_activity or bool(tool_data.get("name"))
        has_model_activity = has_model_activity or bool(str(bubble.get("text") or "").strip())
        has_model_activity = has_model_activity or any(
            isinstance(block, dict) and block.get("type") in ("text", "tool_use")
            for block in (bubble.get("content") or [])
        )
    return max(len(call_ids), int(has_model_activity))


def cursor_pricing_note(model, variant, supported):
    basis = "one context snapshot per execution plus trace-visible model text"
    if not supported:
        return f"Local Cursor token estimate ({basis}); no configured public rate for {model}."
    if str(model or "").replace(" ", "-").lower().startswith("composer-2.5"):
        rate = f"Composer 2.5 {variant.title()} public rates"
    else:
        rate = "selected-model public API rates"
    return f"Local Cursor estimate ({basis}), priced with {rate}; cache and hidden model work are excluded."


def recompute_cursor(source):
    """Build explicitly estimated usage from Cursor's locally persisted evidence."""
    transcript = load(source.get("path"))
    snapshot = cursor_snapshot(source.get("id"))
    enriched = bool(snapshot.get("available") and snapshot.get("bubbles"))
    groups = cursor_enriched_groups(snapshot) if enriched else cursor_transcript_groups(transcript, source)
    if not groups:
        return None
    spans = cursor_request_spans(source.get("id")) if enriched else []
    composer = snapshot.get("composer") or {}
    latest_context = int(composer.get("contextTokensUsed") or 0)
    latest_window = int(composer.get("contextTokenLimit") or 0)
    context_rows = cursor_context_estimates(groups, latest_context, latest_window)
    tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    cost = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
    model_tok, model_cost = defaultdict(int), defaultdict(float)
    series, executions, trace = [], [], []
    wait_samples, performance_samples, active_intervals = [], [], []
    first_ts = last_ts = 0.0

    for position, group in enumerate(groups):
        idx = position + 1
        start_ts = float(group.get("start_ts") or 0)
        next_start = float(groups[position + 1].get("start_ts") or 0) if position + 1 < len(groups) else 0
        bubbles = group.get("bubbles") or []
        bubble_ts = [cursor_timestamp(bubble.get("createdAt")) for bubble in bubbles
                     if isinstance(bubble, dict) and bubble.get("createdAt")]
        terminal_ts = max(bubble_ts, default=start_ts)
        turn_duration_ms = group.get("turn_duration_ms") or max(
            (bubble.get("turnDurationMs") or 0 for bubble in bubbles if isinstance(bubble, dict)),
            default=0,
        )
        timing = cursor_turn_timing(spans, start_ts, next_start, terminal_ts, turn_duration_ms)
        end_ts = timing.get("end_ts") or terminal_ts or start_ts
        active_intervals.extend(timing.get("active_intervals") or [])
        model = str(group.get("model") or source.get("model") or "unknown")
        user_text = compact_text(str(group.get("user_text") or ""), 220)
        context_row = context_rows[position]
        context_tokens = int(context_row.get("tokens") or 0)
        context_window = int(context_row.get("window") or 0)
        visible = cursor_visible_output(bubbles)
        assistant_tokens = int(visible["assistant_tokens"])
        reasoning_tokens = int(visible["reasoning_tokens"])
        output_tokens = assistant_tokens + reasoning_tokens
        model_calls = cursor_model_call_count(bubbles)
        variant = cursor_price_variant(composer, model)
        price, _ = price_for(model, "cursor", variant)
        pricing_supported = any(float(value or 0) > 0 for value in price.values())
        usage = {"input_tokens": context_tokens, "output_tokens": output_tokens}
        cost_available = bool(context_tokens and pricing_supported)
        cost_breakdown = cost_of(usage, model, "cursor", variant) if cost_available else dict(ZERO_PRICE)
        execution_cost = sum(cost_breakdown.values())
        tools = []
        reasoning_ms = 0.0
        assistant_text = ""
        if user_text:
            trace.append(trace_event(start_ts, "user", "User message", compact_text(user_text, 84),
                                     idx, severity="start", model=model))

        for bubble in bubbles:
            if not isinstance(bubble, dict):
                continue
            ts = cursor_timestamp(bubble.get("createdAt")) or terminal_ts
            thinking = bubble.get("thinking")
            try:
                thinking_ms = float(bubble.get("thinkingDurationMs") or 0)
            except (TypeError, ValueError):
                thinking_ms = 0.0
            if thinking is not None or thinking_ms > 0:
                reasoning_ms += max(0.0, thinking_ms)
                trace.append(trace_event(
                    ts, "reasoning", "Reasoning",
                    duration_label(thinking_ms / 1000.0) if thinking_ms else "Trace-visible reasoning",
                    idx, severity="reasoning", model=model, duration_ms=thinking_ms or None,
                ))
            tool_data = bubble.get("toolFormerData")
            if isinstance(tool_data, dict) and tool_data.get("name"):
                ident = cursor_tool_identity(tool_data.get("name"))
                arguments = tool_data.get("params") or tool_data.get("rawArgs") or {}
                result_value = tool_data.get("result")
                if result_value in (None, "", [], {}):
                    result_value = tool_data.get("additionalData")
                result_present = result_value not in (None, "", [], {})
                output_chars = observable_output_chars(result_value) if result_present else 0
                status = str(tool_data.get("status") or "").lower()
                errored = bool(status in ("error", "failed", "failure") or
                               tool_data.get("error") not in (None, "", False) or
                               tool_result_is_error(result_value, False))
                tool = {
                    **ident,
                    "id": tool_data.get("toolCallId") or bubble.get("bubbleId"),
                    "call_id": tool_data.get("toolCallId") or bubble.get("bubbleId"),
                    "args_chars": len(str(arguments or "")),
                    "args_fingerprint": argument_fingerprint(arguments),
                    "output_chars": output_chars,
                    "output_tokens": output_chars // CHARS_PER_TOKEN,
                    "result_available": result_present,
                    "error": errored,
                    "skills": skill_names_from_value(arguments),
                }
                tools.append(tool)
                trace.append(trace_event(
                    ts, "tool_call", ident["display"], ident["namespace"], idx,
                    tool=ident["name"], severity="tool", model=model,
                    args_chars=tool["args_chars"], tool_kind=ident["kind"],
                ))
                if result_present or errored:
                    trace.append(trace_event(
                        ts, "tool_result", ident["display"],
                        f"~{tool['output_tokens']:,} returned tokens" if result_present else "Tool error",
                        idx, tool=ident["name"], tokens=tool["output_tokens"],
                        severity="warn" if errored else "retrieval", model=model,
                        output_chars=output_chars, retrieval_tokens=tool["output_tokens"], error=errored,
                    ))
            text = bubble.get("text")
            if isinstance(text, str) and text.strip():
                assistant_text = compact_text(text, 84)
                trace.append(trace_event(ts, "message", "Assistant message", assistant_text,
                                         idx, model=model))
            for block in bubble.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and str(block.get("text") or "").strip():
                    assistant_text = compact_text(str(block.get("text")), 84)
                    trace.append(trace_event(ts, "message", "Assistant message", assistant_text,
                                             idx, model=model))
                elif block.get("type") == "tool_use":
                    ident = cursor_tool_identity(block.get("name"))
                    arguments = block.get("input") or {}
                    tool = {
                        **ident, "id": block.get("id"), "call_id": block.get("id"),
                        "args_chars": len(str(arguments or "")),
                        "args_fingerprint": argument_fingerprint(arguments),
                        "output_chars": 0, "output_tokens": 0,
                        "result_available": False, "error": False,
                        "skills": skill_names_from_value(arguments),
                    }
                    tools.append(tool)
                    trace.append(trace_event(ts, "tool_call", ident["display"], ident["namespace"],
                                             idx, tool=ident["name"], severity="tool", model=model,
                                             args_chars=tool["args_chars"], tool_kind="tool"))

        if timing.get("retries"):
            trace.append(trace_event(
                end_ts, "retry", "Request retried",
                f"{timing['attempts']} attempts · {timing['failed_attempts']} failed",
                idx, severity="warn", model=model, attempts=timing["attempts"],
                failed_attempts=timing["failed_attempts"], retries=timing["retries"],
            ))
        trace.append(trace_event(
            end_ts, "complete", "Execution complete",
            duration_label(timing.get("wait_s") or 0) if timing.get("wait_s") else "",
            idx, severity="good" if group.get("completed") else "neutral", model=model,
            duration_ms=(timing.get("wait_s") or 0) * 1000 or None,
            cost=execution_cost if cost_available else None,
        ))

        retrieval = sum(int(tool.get("output_tokens") or 0) for tool in tools)
        context_pct = context_tokens / context_window if context_window else None
        output_available = bool(bubbles)
        token_available = bool(context_tokens or output_available)
        execution_availability = metric_availability(
            "cursor", cost=cost_available, tokens=token_available,
            input_tokens=bool(context_tokens), output_tokens=output_available,
            throughput=bool(output_tokens and timing.get("active_s")),
            context=bool(context_window), timing=bool(timing.get("wait_s")),
            tool_results=any(tool.get("result_available") for tool in tools),
        )
        series.append({
            "i": idx, "in": context_tokens, "out": output_tokens, "cost": execution_cost,
            "fresh_input": context_tokens, "cache": 0, "cache_read": 0, "cache_write": 0,
            "think": bool(reasoning_tokens or reasoning_ms), "tools": len(tools), "side": False,
            "reasoning": reasoning_tokens, "reasoning_ms": reasoning_ms,
            "context_pct": context_pct, "context_tokens": context_tokens,
            "user_message": user_text, "user_input": user_text,
            "availability": execution_availability,
        })
        execution = {
            "id": f"{source['id']}:{idx}", "idx": idx, "ts": end_ts or start_ts,
            "time": local_tm(end_ts or start_ts), "model": model,
            "tokens": {"input": context_tokens, "output": output_tokens,
                       "reasoning": reasoning_tokens, "retrieval": retrieval,
                       "fresh_input": context_tokens, "cache": 0, "cache_read": 0,
                       "cache_write": 0, "total": context_tokens + output_tokens},
            "cost": execution_cost, "cost_breakdown": cost_breakdown, "tools": tools,
            "tool_count": len(tools), "model_calls": model_calls,
            "reasoning_tokens": reasoning_tokens, "reasoning_duration_ms": reasoning_ms,
            "context_tokens": context_tokens, "context_window": context_window,
            "context_interpolated": bool(context_row.get("interpolated")),
            "context_pct": context_pct, "duration_ms": (timing.get("active_s") or 0) * 1000 or None,
            "wait_duration_ms": (timing.get("wait_s") or 0) * 1000 or None,
            "ttft_ms": (timing.get("ttft_s") or 0) * 1000 or None,
            "attempts": timing.get("attempts") or 0,
            "failed_attempts": timing.get("failed_attempts") or 0,
            "retries": timing.get("retries") or 0,
            "timing_basis": timing.get("timing_basis"), "pricing_variant": variant,
            "summary": (f"Execution {idx}: {len(tools)} tools · ${execution_cost:.3f} local estimate"
                        if cost_available else f"Execution {idx}: {len(tools)} tools · cost unavailable"),
            "user_message": user_text, "user_input": user_text,
            "availability": execution_availability,
        }
        executions.append(execution)
        tot["input"] += context_tokens
        tot["output"] += output_tokens
        if cost_available:
            for key in cost:
                cost[key] += float(cost_breakdown.get(key) or 0)
        model_tok[model] += context_tokens + output_tokens
        model_cost[model] += execution_cost
        if timing.get("wait_s"):
            wait_samples.append({
                "provider": "cursor", "model": model,
                "day": time.strftime("%Y-%m-%d", time.localtime(end_ts)) if end_ts else "",
                "ts": end_ts, "start_ts": start_ts, "duration_s": timing["wait_s"],
                "tool_calls": len(tools), "output_tokens": output_tokens,
                "context_tokens": context_tokens, "model_calls": model_calls,
                "timing_basis": timing.get("timing_basis") or "observed",
                "ttft_s": timing.get("ttft_s") or 0,
                "attempts": timing.get("attempts") or 0,
                "failed_attempts": timing.get("failed_attempts") or 0,
                "retries": timing.get("retries") or 0,
            })
        if output_tokens and timing.get("active_s"):
            performance_samples.append({
                "provider": "cursor", "model": model,
                "day": time.strftime("%Y-%m-%d", time.localtime(end_ts)) if end_ts else "",
                "ts": end_ts, "input_tokens": context_tokens, "output_tokens": output_tokens,
                "peak_input_tokens": context_tokens, "uncached_input_tokens": context_tokens,
                "cache_read_tokens": 0, "cache_write_tokens": 0, "model_calls": model_calls,
                "duration_s": timing["active_s"], "generation_s": timing["active_s"],
                "ttft_s": timing.get("ttft_s") or 0, "tool_calls": len(tools),
                "timing_basis": timing.get("timing_basis") or "observed",
            })
        if start_ts:
            first_ts = min(first_ts or start_ts, start_ts)
        if end_ts:
            last_ts = max(last_ts, end_ts)

    total_tokens = sum(tot.values())
    total_cost = sum(cost.values())
    tool_data = tool_summary(executions)
    model_names = [execution.get("model") or "unknown" for execution in executions]
    primary_model = max(set(model_names), key=model_names.count) if model_names else source.get("model") or "unknown"
    reasoning_total = sum(int(execution.get("reasoning_tokens") or 0) for execution in executions)
    reasoning_cost = sum(
        float((execution.get("cost_breakdown") or {}).get("output") or 0)
        * int(execution.get("reasoning_tokens") or 0)
        / max(1, int((execution.get("tokens") or {}).get("output") or 0))
        for execution in executions
    )
    analyses = analysis_block(
        tot, total_cost, reasoning_total,
        sum(bool(execution.get("reasoning_tokens") or execution.get("reasoning_duration_ms"))
            for execution in executions),
        reasoning_cost, model_tok, model_cost, tool_data, 0.0, 0,
        sum(bool(group.get("completed")) for group in groups),
    )
    active_s = _merge_execution_intervals(active_intervals)
    if not active_s:
        active_s = sum(float(execution.get("duration_ms") or 0) / 1000.0 for execution in executions)
    source = dict(source)
    primary_variant = cursor_price_variant(composer, primary_model)
    primary_price, _ = price_for(primary_model, "cursor", primary_variant)
    pricing_supported = any(float(value or 0) > 0 for value in primary_price.values())
    pricing_note = cursor_pricing_note(primary_model, primary_variant, pricing_supported)
    source.update({
        "context_latest": latest_context or (executions[-1].get("context_tokens") if executions else 0),
        "context_window": latest_window or max((e.get("context_window") or 0 for e in executions), default=0),
        "context_breakdown": cursor_prompt_breakdown(composer),
        "token_estimate": True,
        "estimate_basis": "context_proxy_and_visible_output",
        "pricing_variant": primary_variant,
    })
    input_available = bool(executions and all(metric_available(e, "input_tokens") for e in executions))
    output_available = bool(executions and all(metric_available(e, "output_tokens") for e in executions))
    cost_available = bool(executions and all(metric_available(e, "cost") for e in executions))
    throughput = performance_summary(performance_samples, tot["output"])
    availability = metric_availability(
        "cursor", cost=cost_available, tokens=bool(input_available or output_available),
        input_tokens=input_available, output_tokens=output_available,
        throughput=bool(throughput.get("available")),
        context=bool(source["context_window"]), timing=bool(active_s or wait_samples),
        tool_results=any(tool.get("result_available") for e in executions for tool in e.get("tools") or []),
    )
    biggest = max(
        ({"cost": execution.get("cost") or 0, "idx": execution.get("idx")} for execution in executions),
        key=lambda row: row["cost"], default=None,
    )
    state = build_state(
        source, tot, cost, total_tokens, total_cost, series, executions, trace,
        {"reasoning": reasoning_total, "output": max(0, tot["output"] - reasoning_total),
         "retrieval": tool_data["total_output_tokens"], "coordination": 0},
        analyses, [], first_ts, last_ts,
        (time.time() - last_ts) if last_ts else 1e9, biggest, 0, cost_available,
        primary_model, pricing_note,
        {"duration_s": active_s, "available": bool(active_s),
         "reported_executions": sum(bool(e.get("duration_ms")) for e in executions),
         "observed_executions": 0, "execution_count": len(executions),
         "basis": "request trace" if spans else ("turn duration" if active_s else "unavailable")},
        wait_samples, availability=availability,
    )
    state["throughput"] = throughput
    state["token_estimate"] = True
    state["estimation"] = {
        "basis": "one context snapshot per execution plus trace-visible model text",
        "input": "context proxy",
        "output": "visible text estimate",
        "excluded": ["cache accounting", "hidden reasoning", "repeated internal model-call input"],
    }
    state["context"]["breakdown"] = source.get("context_breakdown") or []
    state["context"]["estimated"] = True
    state["cursor_enrichment"] = {
        "database": enriched,
        "request_traces": bool(spans),
        "transcript_fallback": not enriched,
    }
    return state


def recompute(source):
    if isinstance(source, str):
        source = source_from_path(source)
    if not source:
        return None
    if source["provider"] == "codex":
        return recompute_codex(source)
    if source["provider"] == "claude":
        return recompute_claude(source)
    if source["provider"] == "cursor":
        return recompute_cursor(source)
    return None


def recompute_claude(source):
    path = source["path"]
    objs = load(path)
    if not objs:
        return None

    msgs = iter_claude_messages(objs)
    user_events = claude_user_events(objs)
    user_event_idx = 0
    pending_user_texts = []
    result_chars, result_ts, result_errors = claude_tool_results(objs)
    tool_name_by_id = {}
    for rec in msgs:
        for block in rec["content"]:
            if block.get("type") == "tool_use":
                tool_name_by_id[block.get("id")] = block.get("name") or "?"

    tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    cost = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
    first_ts = last_ts = None
    biggest = None
    series, executions, trace = [], [], []
    think_turns = think_out = routine_out = think_cost = 0
    completed = 0
    model_tok, model_cost = defaultdict(int), defaultdict(float)
    side_cost = side_turns = 0
    approx_cost = False

    for rec in msgs:
        usage = rec["usage"]
        if not usage:
            continue
        idx = len(series) + 1
        model = rec["model"]
        ts = rec["ts"]
        if ts:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
            while user_event_idx < len(user_events) and user_events[user_event_idx]["ts"] <= ts:
                pending_user_texts.append(user_events[user_event_idx]["text"])
                user_event_idx += 1
        user_input = user_prompt_preview(pending_user_texts)
        pending_user_texts = []

        c = cost_of(usage, model, "claude")
        _, approx = price_for(model, "claude")
        approx_cost = approx_cost or approx
        tc = sum(c.values())
        for key in cost:
            cost[key] += c[key]

        in_tok = (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                  + usage.get("cache_creation_input_tokens", 0))
        out_tok = usage.get("output_tokens", 0)
        total = usage_tokens(usage)
        tot["input"] += usage.get("input_tokens", 0)
        tot["cache_write"] += usage.get("cache_creation_input_tokens", 0)
        tot["cache_read"] += usage.get("cache_read_input_tokens", 0)
        tot["output"] += out_tok
        model_tok[model] += total
        model_cost[model] += tc

        has_think = any(block.get("type") == "thinking" for block in rec["content"])
        tool_blocks = [b for b in rec["content"] if b.get("type") == "tool_use"]
        tools = []
        for block in tool_blocks:
            ident = tool_identity(block.get("name") or "?")
            tid = block.get("id")
            out_chars = result_chars.get(tid, 0)
            tool = {
                **ident,
                "id": tid,
                "call_id": tid,
                "args_chars": len(json.dumps(block.get("input", ""))),
                "output_chars": out_chars,
                "output_tokens": out_chars // CHARS_PER_TOKEN,
                "error": bool(result_errors.get(tid)),
                "skills": skill_names_from_value(block.get("input")),
            }
            tools.append(tool)

        if has_think:
            think_turns += 1
            think_out += out_tok
            think_cost += out_tok * price_for(model, "claude")[0]["output"] / 1e6
            trace.append(trace_event(ts, "reasoning", "Reasoning", f"thinking turn #{idx}", idx,
                                     tokens=out_tok, cost=think_cost, severity="reasoning",
                                     model=model, output_tokens=out_tok))
        else:
            routine_out += out_tok
        if rec["stop_reason"] == "end_turn":
            completed += 1
        if rec["side"]:
            side_cost += tc
            side_turns += 1
            trace.append(trace_event(ts, "coordination", "Subagent turn", f"execution #{idx}", idx,
                                     tokens=out_tok, cost=tc, severity="coordination",
                                     model=model, output_tokens=out_tok))

        cache_tokens = usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        fresh_input_tokens = usage.get("input_tokens", 0)
        cache_read_tokens = usage.get("cache_read_input_tokens", 0)
        cache_write_tokens = usage.get("cache_creation_input_tokens", 0)
        trace.append(trace_event(
            ts, "message", "Assistant turn",
            f"{out_tok:,} out / {in_tok:,} in",
            idx, tokens=total, cost=tc, severity="usage",
            model=model, input_tokens=in_tok, output_tokens=out_tok,
            cache_tokens=cache_tokens, context_tokens=in_tok,
            fresh_input_tokens=fresh_input_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            tool_count=len(tools), reasoning_tokens=out_tok if has_think else 0,
        ))
        for tool in tools:
            trace.append(trace_event(ts, "tool_call", tool["display"], tool["namespace"], idx,
                                     tool=tool["name"], severity="tool",
                                     model=model, args_chars=tool["args_chars"]))
            if tool["output_tokens"]:
                trace.append(trace_event(result_ts.get(tool["id"]) or ts, "tool_result", tool["display"],
                                         f"~{tool['output_tokens']:,} returned tokens", idx,
                                         tool=tool["name"], tokens=tool["output_tokens"],
                                         severity="warn" if tool.get("error") else "retrieval",
                                         model=model, output_chars=tool["output_chars"],
                                         retrieval_tokens=tool["output_tokens"], error=tool.get("error")))

        series.append({
            "i": idx,
            "in": in_tok,
            "out": out_tok,
            "cost": round(tc, 4),
            "fresh_input": fresh_input_tokens,
            "cache": cache_tokens,
            "cache_read": cache_read_tokens,
            "cache_write": cache_write_tokens,
            "think": has_think,
            "tools": len(tools),
            "side": rec["side"],
            "reasoning": out_tok if has_think else 0,
            "user_message": user_input,
            "user_input": user_input,
        })
        executions.append({
            "id": rec["id"],
            "idx": idx,
            "ts": ts or 0,
            "time": time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "",
            "model": model,
            "tokens": {"input": in_tok, "output": out_tok, "reasoning": out_tok if has_think else 0,
                       "retrieval": sum(t["output_tokens"] for t in tools), "fresh_input": fresh_input_tokens,
                       "cache": cache_tokens, "cache_read": cache_read_tokens, "cache_write": cache_write_tokens,
                       "total": total},
            "cost": round(tc, 6),
            "cost_breakdown": {k: round(v, 6) for k, v in c.items()},
            "tools": tools,
            "tool_count": len(tools),
            "reasoning_tokens": out_tok if has_think else 0,
            "context_tokens": in_tok,
            "context_window": None,
            "context_pct": None,
            "duration_ms": None,
            "summary": f"Turn {idx}: {out_tok:,} out / {in_tok:,} in",
            "user_message": user_input,
            "user_input": user_input,
        })
        if biggest is None or tc > biggest["cost"]:
            biggest = {"cost": tc, "idx": idx}

    tool_data = tool_summary(executions)
    retrieval_tokens = tool_data["total_output_tokens"]
    total_tokens = sum(tot.values())
    total_cost = sum(cost.values())
    elapsed = (last_ts - first_ts) if (first_ts and last_ts) else 0
    minutes = max(elapsed / 60.0, 1e-9)
    cache_in = tot["cache_read"] + tot["cache_write"]
    cache_ratio = (tot["cache_read"] / cache_in) if cache_in else 0.0
    idle = (time.time() - last_ts) if last_ts else 1e9
    side_out = sum(s["out"] for s in series if s["side"])
    semantic = {
        "reasoning": think_out,
        "output": max(0, routine_out),
        "retrieval": retrieval_tokens,
        "coordination": side_out,
    }
    primary_model = max(model_tok, key=model_tok.get) if model_tok else DEFAULT_CLAUDE_MODEL
    analyses = analysis_block(tot, total_cost, think_out, think_turns, think_cost, model_tok, model_cost,
                              tool_data, side_cost, side_turns, completed)
    insights = build_insights(tot, cost, total_cost, cache_ratio, biggest, len(series), analyses,
                              "claude", primary_model, approx_cost)

    wait_samples = claude_wait_samples(objs)
    state = build_state(source, tot, cost, total_tokens, total_cost, series, executions, trace, semantic,
                        analyses, insights, first_ts, last_ts, idle, biggest, side_turns, approx_cost,
                        primary_model, "exact Claude API-rate estimate", execution_timing("claude", objs),
                        wait_samples)
    state["throughput"] = performance_summary(claude_performance_samples(objs), tot["output"])
    return state


def analysis_block(tot, total_cost, think_out, think_turns, think_cost, model_tok, model_cost,
                   tool_data, side_cost, side_turns, completed):
    tool_bloat = [{
        "name": row["name"],
        "calls": row["calls"],
        "tokens": row["output_tokens"],
        "chars": row["output_chars"],
        "namespace": row["namespace"],
        "kind": row["kind"],
    } for row in tool_data["by_name"][:8]]
    return {
        "reasoning": {
            "share": (think_out / tot["output"]) if tot["output"] else 0.0,
            "think_turns": think_turns,
            "tokens": think_out,
            "cost": think_cost,
        },
        "model_mix": sorted(
            [{"model": k, "tokens": model_tok[k], "cost": model_cost[k]} for k in model_tok],
            key=lambda x: -x["cost"]),
        "tool_bloat": tool_bloat,
        "coordination": {
            "share": (side_cost / total_cost) if total_cost else 0.0,
            "turns": side_turns,
            "cost": side_cost,
        },
        "cost_per_task": {
            "completed": completed,
            "per_task": (total_cost / completed) if completed else 0.0,
        },
    }


def new_codex_pending():
    return {"trace": [], "calls": {}, "has_reasoning": False, "start_ts": None,
            "context_window": None, "user_inputs": []}


def codex_approval_policy_label(value):
    """Return a compact label for legacy strings and structured Codex policies."""
    if isinstance(value, dict):
        value = next(iter(value), "")
    return str(value or "").replace("_", " ")


def recompute_codex(source):
    path = source["path"]
    objs = load(path)
    if not objs:
        return None

    model = source.get("model") or DEFAULT_OPENAI_MODEL
    meta_cwd = source.get("project")
    tot = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    cost = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
    model_tok, model_cost = defaultdict(int), defaultdict(float)
    series, executions, trace = [], [], []
    pending = new_codex_pending()
    call_map = {}
    first_ts = last_ts = None
    biggest = None
    completed = 0
    approx_cost = True
    task_start_ts = None
    context_window = None
    tools_loaded = int(source.get("tools_loaded") or 0)
    tools_eager = int(source.get("tools_eager") or 0)
    tools_deferred = int(source.get("tools_deferred") or 0)
    tool_catalog = list(source.get("tool_catalog") or [])
    tool_namespaces = list(source.get("tool_namespaces") or [])

    for obj in objs:
        ts = parse_iso(obj.get("timestamp", ""))
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        otype = obj.get("type")

        if otype == "session_meta":
            meta_cwd = home_shorten(payload.get("cwd") or meta_cwd)
            dynamic_tools = payload.get("dynamic_tools")
            if isinstance(dynamic_tools, list):
                tool_catalog = normalize_dynamic_tools(dynamic_tools)
                counts = catalog_counts(tool_catalog)
                tools_loaded = counts["advertised"]
                tools_eager = counts["eager"]
                tools_deferred = counts["deferred"]
                tool_namespaces = sorted(set(t["namespace"] for t in tool_catalog))
            continue
        if otype == "turn_context":
            model = payload.get("model") or model
            meta_cwd = home_shorten(payload.get("cwd") or meta_cwd)
            detail = " · ".join(x for x in [
                model,
                payload.get("effort"),
                codex_approval_policy_label(payload.get("approval_policy")),
            ] if x)
            pending["trace"].append(trace_event(
                ts, "context", "Run context", detail, severity="neutral",
                model=model, tools_loaded=tools_loaded or None,
                tool_namespaces=tool_namespaces[:6],
            ))
            continue

        if ptype == "task_started":
            task_start_ts = ts or task_start_ts
            pending["start_ts"] = pending["start_ts"] or ts
            context_window = payload.get("model_context_window") or context_window
            pending["context_window"] = context_window
            pending["trace"].append(trace_event(ts, "start", "Execution started",
                                                payload.get("collaboration_mode_kind", ""), severity="start",
                                                model=model, context_window=context_window,
                                                tools_loaded=tools_loaded or None, trace_id=payload.get("trace_id"),
                                                turn_id=payload.get("turn_id")))
            continue

        if ptype == "user_message":
            txt = compact_text(payload.get("message") or "", 100)
            if txt:
                pending["user_inputs"].append(compact_text(payload.get("message") or "", 220))
                pending["trace"].append(trace_event(ts, "user", "User message", txt,
                                                    severity="start", model=model))
            continue

        if ptype == "agent_message":
            txt = compact_text(payload.get("message") or "", 120)
            if txt:
                pending["trace"].append(trace_event(ts, "message", "Agent update", txt,
                                                    severity="neutral", model=model,
                                                    phase=payload.get("phase")))
            continue

        if ptype == "context_compacted":
            pending["trace"].append(trace_event(ts, "context", "Context compacted", "",
                                                severity="warn", model=model))
            continue

        if ptype == "thread_goal_updated":
            goal = payload.get("goal") or {}
            txt = compact_text(goal.get("objective") if isinstance(goal, dict) else str(goal), 120)
            pending["trace"].append(trace_event(ts, "goal", "Goal updated", txt,
                                                severity="neutral", model=model))
            continue

        if ptype == "reasoning":
            pending["has_reasoning"] = True
            summary = payload.get("summary")
            pending["trace"].append(trace_event(ts, "reasoning", "Reasoning",
                                                compact_text(str(summary or "encrypted reasoning"), 90),
                                                severity="reasoning", model=model))
            continue

        if ptype == "message":
            role = payload.get("role")
            content = payload.get("content")
            if role == "assistant":
                txt = compact_text(text_from_content(content), 84)
                pending["trace"].append(trace_event(ts, "message", "Assistant message", txt, model=model))
            elif role == "user":
                txt = compact_text(text_from_content(content), 84)
                if txt:
                    pending["trace"].append(trace_event(ts, "user", "User message", txt, severity="start", model=model))
            continue

        if ptype in ("function_call", "custom_tool_call", "web_search_call", "tool_search_call"):
            name = payload.get("name") or ("web.search" if ptype == "web_search_call" else ptype.replace("_call", ""))
            call_id = payload.get("call_id") or payload.get("id") or f"call-{len(call_map) + 1}"
            ident = tool_identity(name)
            arguments = payload.get("arguments") or payload.get("input")
            tool = {
                **ident,
                "id": payload.get("id"),
                "call_id": call_id,
                "args_chars": len(str(arguments or "")),
                "output_chars": 0,
                "output_tokens": 0,
                "error": False,
                "skills": skill_names_from_value(arguments),
            }
            pending["calls"][call_id] = tool
            call_map[call_id] = tool
            pending["trace"].append(trace_event(ts, "tool_call", ident["display"], ident["namespace"],
                                                tool=name, severity="tool", model=model,
                                                args_chars=tool["args_chars"], tool_kind=ident["kind"]))
            continue

        if ptype in ("function_call_output", "custom_tool_call_output", "web_search_end", "tool_search_output", "patch_apply_end"):
            call_id = payload.get("call_id") or payload.get("id") or payload.get("callId")
            tool = pending["calls"].get(call_id) or call_map.get(call_id)
            if tool is None:
                ident = tool_identity(payload.get("name") or ptype)
                tool = {**ident, "id": payload.get("id"), "call_id": call_id,
                        "args_chars": 0, "output_chars": 0, "output_tokens": 0, "error": False,
                        "skills": []}
                pending["calls"][call_id] = tool
                call_map[call_id] = tool
            output = payload.get("output") if "output" in payload else payload
            out_chars = observable_output_chars(output)
            tool["output_chars"] += out_chars
            tool["output_tokens"] = tool["output_chars"] // CHARS_PER_TOKEN
            tool["error"] = bool(tool.get("error") or tool_result_is_error(output, payload.get("status") == "failed"))
            pending["trace"].append(trace_event(ts, "tool_result", tool["display"],
                                                f"~{tool['output_tokens']:,} returned tokens",
                                                tool=tool["name"], tokens=tool["output_tokens"],
                                                severity="warn" if tool.get("error") else "retrieval", model=model,
                                                output_chars=tool["output_chars"],
                                                retrieval_tokens=tool["output_tokens"], error=tool.get("error")))
            continue

        if ptype == "task_complete":
            completed += 1
            duration = payload.get("duration_ms") or payload.get("time_to_first_token_ms")
            if executions:
                executions[-1]["duration_ms"] = duration
                trace.append(trace_event(ts, "complete", "Execution complete",
                                         f"{duration}ms" if duration else "",
                                         executions[-1]["idx"], severity="good",
                                         model=model, duration_ms=duration,
                                         turn_id=payload.get("turn_id")))
            else:
                pending["trace"].append(trace_event(ts, "complete", "Execution complete", "",
                                                    severity="good", model=model,
                                                    duration_ms=duration,
                                                    turn_id=payload.get("turn_id")))
            continue

        if ptype == "token_count":
            info = payload.get("info") or {}
            raw = (info.get("last_token_usage") or {})
            if not raw:
                continue
            context_window = info.get("model_context_window") or context_window or pending.get("context_window")
            usage = codex_usage(raw)
            idx = len(series) + 1
            c = cost_of(usage, model, "codex")
            _, missing_price = price_for(model, "codex")
            approx_cost = approx_cost or missing_price
            tc = sum(c.values())
            for key in cost:
                cost[key] += c[key]
            in_tok = usage["input_tokens"] + usage["cache_read_input_tokens"]
            out_tok = usage["output_tokens"]
            reasoning = min(out_tok, usage.get("reasoning_output_tokens", 0))
            total = usage_tokens(usage)
            context_pct = (in_tok / context_window) if context_window else None
            fresh_input_tokens = usage["input_tokens"]
            cache_read_tokens = usage["cache_read_input_tokens"]
            cache_write_tokens = usage["cache_creation_input_tokens"]
            cache_tokens = cache_read_tokens + cache_write_tokens
            tot["input"] += usage["input_tokens"]
            tot["cache_read"] += usage["cache_read_input_tokens"]
            tot["cache_write"] += usage["cache_creation_input_tokens"]
            tot["output"] += out_tok
            model_tok[model] += total
            model_cost[model] += tc
            first_ts = ts if first_ts is None else min(first_ts, ts or first_ts)
            last_ts = ts if ts else last_ts

            tools = [dict(t) for t in pending["calls"].values()]
            user_input = user_prompt_preview(pending.get("user_inputs") or [])
            observed_tools_loaded = tools_loaded or len(set(t.get("name") for t in call_map.values() if t.get("name")))
            for ev in pending["trace"]:
                ev["execution"] = idx if ev.get("execution") is None else ev["execution"]
                trace.append(ev)
            trace.append(trace_event(
                ts, "usage", "Token count",
                f"{out_tok:,} out / {in_tok:,} in",
                idx, tokens=total, cost=tc, severity="usage",
                model=model, input_tokens=in_tok, output_tokens=out_tok,
                cache_tokens=cache_tokens,
                context_tokens=in_tok, context_window=context_window,
                context_pct=context_pct, tool_count=len(tools),
                fresh_input_tokens=fresh_input_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                reasoning_tokens=reasoning, tools_loaded=observed_tools_loaded or None,
            ))

            series.append({
                "i": idx,
                "in": in_tok,
                "out": out_tok,
                "cost": round(tc, 4),
                "fresh_input": fresh_input_tokens,
                "cache": cache_tokens,
                "cache_read": cache_read_tokens,
                "cache_write": cache_write_tokens,
                "think": bool(reasoning or pending["has_reasoning"]),
                "tools": len(tools),
                "side": False,
                "reasoning": reasoning,
                "context_pct": context_pct,
                "user_message": user_input,
                "user_input": user_input,
            })
            executions.append({
                "id": f"{source['id']}:{idx}",
                "idx": idx,
                "ts": ts or 0,
                "time": time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "",
                "model": model,
                "tokens": {"input": in_tok, "output": out_tok, "reasoning": reasoning,
                           "retrieval": sum(t["output_tokens"] for t in tools),
                           "fresh_input": fresh_input_tokens, "cache": cache_tokens,
                           "cache_read": cache_read_tokens, "cache_write": cache_write_tokens,
                           "total": total},
                "cost": round(tc, 6),
                "cost_breakdown": {k: round(v, 6) for k, v in c.items()},
                "tools": tools,
                "tool_count": len(tools),
                "reasoning_tokens": reasoning,
                "context_tokens": in_tok,
                "context_window": context_window,
                "context_pct": context_pct,
                "duration_ms": None,
                "summary": f"Execution {idx}: {out_tok:,} out / {in_tok:,} in",
                "user_message": user_input,
                "user_input": user_input,
            })
            if biggest is None or tc > biggest["cost"]:
                biggest = {"cost": tc, "idx": idx}
            pending = new_codex_pending()
            task_start_ts = None

    tool_data = tool_summary(executions)
    total_tokens = sum(tot.values())
    total_cost = sum(cost.values())
    if not first_ts:
        first_ts = min((parse_iso(o.get("timestamp", "")) for o in objs if parse_iso(o.get("timestamp", ""))), default=None)
    if not last_ts:
        last_ts = max((parse_iso(o.get("timestamp", "")) for o in objs if parse_iso(o.get("timestamp", ""))), default=None)
    elapsed = (last_ts - first_ts) if (first_ts and last_ts) else 0
    idle = (time.time() - last_ts) if last_ts else 1e9
    reasoning_tokens = sum(e["reasoning_tokens"] for e in executions)
    output_tokens = max(0, tot["output"] - reasoning_tokens)
    coord_execs = [e for e in executions if any(t["namespace"] in ("orchestration", "workspace-agents") or "agent" in t["name"] for t in e["tools"])]
    coord_cost = sum(e["cost"] for e in coord_execs)
    semantic = {
        "reasoning": reasoning_tokens,
        "output": output_tokens,
        "retrieval": tool_data["total_output_tokens"],
        "coordination": sum(e["tokens"]["output"] for e in coord_execs),
    }
    primary_model = max(model_tok, key=model_tok.get) if model_tok else model
    p, _ = price_for(primary_model, "codex")
    think_cost = reasoning_tokens * p["output"] / 1e6
    analyses = analysis_block(tot, total_cost, reasoning_tokens, sum(1 for e in executions if e["reasoning_tokens"]),
                              think_cost, model_tok, model_cost, tool_data, coord_cost,
                              len(coord_execs), completed or len(executions))
    cache_in = tot["cache_read"] + tot["cache_write"]
    cache_ratio = (tot["cache_read"] / cache_in) if cache_in else 0.0
    insights = build_insights(tot, cost, total_cost, cache_ratio, biggest, len(series), analyses,
                              "codex", primary_model, True)

    source = dict(source)
    source["project"] = meta_cwd or source.get("project")
    source["tools_loaded"] = tools_loaded
    source["tools_eager"] = tools_eager
    source["tools_deferred"] = tools_deferred
    source["tool_catalog"] = tool_catalog
    source["tool_namespaces"] = tool_namespaces
    wait_samples = codex_wait_samples(objs, source.get("model"))
    state = build_state(source, tot, cost, total_tokens, total_cost, series, executions, trace, semantic,
                        analyses, insights, first_ts, last_ts, idle, biggest, len(coord_execs), True,
                        primary_model, "estimated with public OpenAI API rates", execution_timing("codex", objs),
                        wait_samples)
    state["throughput"] = performance_summary(codex_performance_samples(objs, source.get("model")), tot["output"])
    return state


def build_state(source, tot, cost, total_tokens, total_cost, series, executions, trace, semantic,
                analyses, insights, first_ts, last_ts, idle, biggest, side_turns, approx_cost,
                primary_model, pricing_note, active_timing=None, wait_samples=None,
                availability=None):
    elapsed = (last_ts - first_ts) if (first_ts and last_ts) else 0
    active_timing = active_timing or {}
    wait_samples = wait_samples or []
    wait_time = wait_time_summary(wait_samples)
    active_seconds = float(active_timing.get("duration_s") or 0)
    active_available = bool(active_timing.get("available") and active_seconds > 0)
    minutes = max(active_seconds / 60.0, 1e-9)
    cache_in = tot["cache_read"] + tot["cache_write"]
    cache_ratio = (tot["cache_read"] / cache_in) if cache_in else 0.0
    cache = cache_block(tot, cost, executions, source["provider"], primary_model)
    tool_data = tool_summary(executions)
    context_window = (source.get("context_window") or
                      max((e.get("context_window") or 0 for e in executions), default=0) or None)
    context_peak = max(
        int(source.get("context_latest") or 0),
        max((e.get("context_tokens") or e.get("tokens", {}).get("input", 0)
             for e in executions), default=0),
    )
    context_latest = int(source.get("context_latest") or
                         (executions[-1].get("context_tokens", 0) if executions else 0))
    context_pct = (context_latest / context_window) if context_window else None
    context_peak_pct = (context_peak / context_window) if context_window else None
    tools_loaded = int(source.get("tools_loaded") or 0)
    loaded_known = bool(tools_loaded)
    if not tools_loaded:
        tools_loaded = tool_data["unique_used"]
    tool_catalog = list(source.get("tool_catalog") or [])[:240]
    counts = catalog_counts(tool_catalog)
    advertised = counts["advertised"] if loaded_known else 0
    eager = int(source.get("tools_eager") or counts["eager"] or 0)
    deferred = int(source.get("tools_deferred") or counts["deferred"] or 0)
    catalog_names = {row.get("name") for row in tool_catalog if row.get("name")}
    used_names = {row.get("name") for row in tool_data.get("by_name", []) if row.get("name")}
    catalog_coverage = "unavailable"
    if loaded_known:
        catalog_coverage = "reported" if used_names.issubset(catalog_names) else "partial"
    tool_data["loaded"] = tools_loaded
    tool_data["loaded_known"] = loaded_known
    tool_data["advertised"] = advertised
    tool_data["eager"] = eager
    tool_data["deferred"] = deferred
    tool_data["catalog_coverage"] = catalog_coverage
    tool_data["loaded_namespaces"] = list(source.get("tool_namespaces") or [])
    tool_data["catalog"] = tool_catalog[:80]
    availability = availability or metric_availability(
        source["provider"], context=bool(context_window),
        timing=bool(active_available or wait_samples),
        tool_results=True,
    )
    cache["available"] = bool(availability.get("cache"))
    tool_data["results_available"] = bool(availability.get("tool_results"))
    insights = enrich_insights(insights, executions, tool_data, context_window, context_latest, context_peak,
                               source["provider"])
    source_obj = {
        "provider": source["provider"],
        "client": source.get("client") or source["provider"],
        "runtime": source_runtime_label(source),
        "label": source["label"],
        "id": source["id"],
        "desktop_session_id": source.get("desktop_session_id"),
        "path": source["path"],
        "project": source.get("project") or "",
        "pricing_note": pricing_note,
        "approximate_cost": bool(approx_cost),
        "token_estimate": bool(source.get("token_estimate")),
        "estimate_basis": source.get("estimate_basis") or "",
        "pricing_variant": source.get("pricing_variant") or "",
        "tools_loaded": tools_loaded,
        "tools_loaded_known": loaded_known,
        "tools_advertised": advertised,
        "tools_eager": eager,
        "tools_deferred": deferred,
        "tool_catalog_coverage": catalog_coverage,
        "availability": availability,
    }
    return {
        "provider": source["provider"],
        "client": source.get("client") or source["provider"],
        "source": source_obj,
        "availability": availability,
        "session": source["session"],
        "project": source.get("project") or "",
        "tokens": tot,
        "cost": cost,
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "cost_approx": bool(approx_cost),
        "primary_model": primary_model,
        "turns": len(series),
        "subagent_turns": side_turns,
        "cache_ratio": cache_ratio,
        "cache_saved": cache["saved"],
        "cache": cache,
        "burn_tok_min": total_tokens / minutes if active_available else 0,
        "burn_usd_min": total_cost / minutes if active_available else 0,
        "timing": {
            "start_ts": first_ts or 0,
            "end_ts": last_ts or 0,
            "start_local": local_dt(first_ts),
            "end_local": local_dt(last_ts),
            "duration_s": int(round(active_seconds)),
            "duration": duration_label(active_seconds) if active_available else "--",
            "duration_available": active_available,
            "duration_basis": active_timing.get("basis") or "unavailable",
            "execution_count": int(active_timing.get("execution_count") or 0),
            "reported_executions": int(active_timing.get("reported_executions") or 0),
            "observed_executions": int(active_timing.get("observed_executions") or 0),
            "wall_duration_s": int(elapsed),
            "timezone": time.tzname[time.localtime().tm_isdst > 0],
            "end_label": "Last activity",
        },
        "wait_time": {
            **wait_time,
            "samples": [
                {
                    "i": index,
                    "duration_s": row["duration_s"],
                    "model": row.get("model") or "unknown",
                    "tool_calls": int(row.get("tool_calls") or 0),
                    "model_calls": int(row.get("model_calls") or 0),
                    "output_tokens": int(row.get("output_tokens") or 0),
                    "context_tokens": int(row.get("context_tokens") or 0),
                    "timing_basis": row.get("timing_basis") or "observed",
                    "ts": row.get("ts") or 0,
                    "ttft_s": float(row.get("ttft_s") or 0),
                    "attempts": int(row.get("attempts") or 0),
                    "failed_attempts": int(row.get("failed_attempts") or 0),
                    "retries": int(row.get("retries") or 0),
                }
                for index, row in enumerate(wait_samples, 1)
            ],
        },
        "context": {
            "window": context_window,
            "latest": context_latest,
            "peak": context_peak,
            "latest_pct": context_pct,
            "peak_pct": context_peak_pct,
        },
        "elapsed_s": int(elapsed),
        "active_elapsed_s": int(round(active_seconds)),
        "idle_s": int(idle),
        "idle": idle > 90,
        "ended": False,
        "biggest_turn": biggest,
        "last_turn_cost": series[-1]["cost"] if series else 0,
        "series": series,
        "chart": {"series": series, "scale_hint": "linear"},
        "executions": executions[-EXEC_LIMIT:],
        "trace": trim_trace(sorted(trace, key=lambda e: (e.get("ts") or 0, e.get("execution") or 0))),
        "trace_truncated": len(trace) > TRACE_LIMIT,
        "tools": tool_data,
        "semantic": semantic,
        "analyses": analyses,
        "insights": insights,
        "ts": time.strftime("%H:%M:%S"),
    }


def enrich_insights(insights, executions, tool_data, context_window, context_latest, context_peak, provider):
    out = list(insights or [])
    if context_window:
        latest_pct = context_latest / context_window if context_window else 0
        peak_pct = context_peak / context_window if context_window else 0
        if latest_pct >= MENUBAR_CONTEXT_INTERVENE_PCT:
            out.insert(0, insight(
                "context-high", "warn", "Context", "Compact now",
                f"Context is {latest_pct * 100:.0f}% of the model window.",
                detail="The next execution is close to the model limit and will replay a large prompt.",
                action="Summarize, compact, or narrow tool output before continuing.",
                priority=0,
            ))
        elif latest_pct >= MENUBAR_CONTEXT_WATCH_PCT:
            out.append(insight(
                "context-watch", "warn", "Context", "Prepare to compact",
                f"Context is {latest_pct * 100:.0f}% of the model window.",
                detail="The run is entering the range where summary quality and tool selectivity start to matter.",
                action="Prepare a summary before the context reaches 85%.",
                priority=8,
            ))
        elif peak_pct > MENUBAR_CONTEXT_SOFT_PCT:
            out.append(insight(
                "context-peak", "neutral", "Context", "Context peak",
                f"Context peaked at {peak_pct * 100:.0f}% of the model window.",
                detail="This is historical pressure, not necessarily the latest state.",
                priority=55,
            ))
    loaded = tool_data.get("advertised") or tool_data.get("loaded") or 0
    unique_used = tool_data.get("unique_used") or 0
    if loaded and tool_data.get("loaded_known"):
        ratio = unique_used / loaded if loaded else 0
        kind = "neutral" if ratio >= 0.25 else "warn"
        out.append(insight(
            "tools-loaded", kind, "Tools", "Tool surface",
            f"Runtime reported {loaded} tools; {unique_used} were used in this log.",
            detail=(f"{tool_data.get('eager', 0)} eager and {tool_data.get('deferred', 0)} deferred; "
                    f"catalog coverage is {tool_data.get('catalog_coverage', 'reported')}."),
            action="Review rarely used tools or keep them deferred." if kind == "warn" else "",
            priority=34 if kind == "warn" else 68,
        ))
    if executions:
        latest = executions[-1]
        if low_yield_should_warn(executions, latest_pct if context_window else 0):
            out.append(insight(
                "low-yield-latest", "warn", "Yield", "Low-yield execution",
                "Latest execution replayed large context for a small output.",
                detail="The run is paying to replay a large prompt without producing much new work.",
                action="Summarize or restart with a tighter request.",
                priority=6,
            ))
        elif execution_low_yield(latest):
            out.append(insight(
                "low-yield-latest", "neutral", "Yield", "Low-yield execution",
                "Latest execution produced little output from its input.",
                detail="This is notable, but not yet actionable under the current thresholds.",
                priority=60,
            ))
    return normalize_insights(out)


def cache_savings(tot, provider, model):
    p, _ = price_for(model, provider)
    return tot["cache_read"] * max(0, p["input"] - p["cache_read"]) / 1e6


def cache_block(tot, cost, executions, provider, model):
    fresh = int(tot.get("input", 0) or 0)
    read = int(tot.get("cache_read", 0) or 0)
    write = int(tot.get("cache_write", 0) or 0)
    cached = read + write
    input_total = fresh + cached
    latest_tokens = (executions[-1].get("tokens") if executions else {}) or {}
    latest_input = int(latest_tokens.get("input", 0) or 0)
    latest_cache = int(latest_tokens.get("cache", 0) or 0)
    latest_read = int(latest_tokens.get("cache_read", latest_cache) or 0)
    latest_write = int(latest_tokens.get("cache_write", 0) or 0)
    return {
        "fresh": fresh,
        "read": read,
        "write": write,
        "total": cached,
        "input_total": input_total,
        "hit_ratio": (read / cached) if cached else 0.0,
        "input_share": (cached / input_total) if input_total else 0.0,
        "saved": cache_savings(tot, provider, model),
        "cost": (cost.get("cache_read", 0.0) or 0.0) + (cost.get("cache_write", 0.0) or 0.0),
        "read_cost": cost.get("cache_read", 0.0) or 0.0,
        "write_cost": cost.get("cache_write", 0.0) or 0.0,
        "latest": {
            "tokens": latest_cache,
            "read": latest_read,
            "write": latest_write,
            "input": latest_input,
            "share": (latest_cache / latest_input) if latest_input else 0.0,
        },
    }


def build_insights(tot, cost, total_cost, cache_ratio, biggest, n_turns, an, provider, model, cost_approx):
    out = []
    if total_cost <= 0:
        return out
    labels = {"input": "uncached input", "cache_write": "cache writes",
              "cache_read": "cached input", "output": "output"}
    top = max(cost, key=cost.get)
    top_share = cost[top] / total_cost if total_cost else 0
    top_kind = "warn" if top_share >= 0.75 and cost[top] >= 0.25 else "neutral"
    out.append(insight(
        f"top:{top}", top_kind, "Spend", "Spend driver",
        f"{labels[top]} is {top_share * 100:.0f}% of spend (${cost[top]:.2f}).",
        detail="This points to the part of the run that is actually moving cost.",
        action="Reduce this bucket first if you need to lower spend." if top_kind == "warn" else "",
        priority=22 if top_kind == "warn" else 46,
    ))

    saved = cache_savings(tot, provider, model)
    fresh = int(tot.get("input", 0) or 0)
    read = int(tot.get("cache_read", 0) or 0)
    write = int(tot.get("cache_write", 0) or 0)
    cached = read + write
    input_total = fresh + cached
    cached_share = cached / input_total if input_total else 0
    if saved > 0.01:
        out.append(insight(
            "cache-saved", "good", "Cache", "Cache leverage",
            f"Caching saved ~${saved:.2f}.",
            detail=f"Cache read hit ratio is {cache_ratio * 100:.0f}% across {cached:,} cached input tokens.",
            priority=28,
        ))
    elif input_total >= 50000 and cached_share < 0.15:
        out.append(insight(
            "cache-low", "warn", "Cache", "Low cache leverage",
            f"Only {cached_share * 100:.0f}% of input was cached.",
            detail=f"{fresh:,} tokens were billed as fresh input in this log.",
            action="Reuse a live thread or trim large repeated context before the next request.",
            priority=30,
        ))

    rs = an["reasoning"]["share"]
    if rs > 0.6 and an["reasoning"]["think_turns"]:
        out.append(insight(
            "reasoning-high", "warn", "Reasoning", "Reasoning load",
            f"{rs * 100:.0f}% of output came from reasoning turns.",
            detail=f"{an['reasoning']['tokens']:,} reasoning tokens across {an['reasoning']['think_turns']} executions.",
            action="Split exploratory work from implementation, or ask for a narrower next step.",
            priority=26,
        ))
    elif rs > 0.25 and an["reasoning"]["think_turns"]:
        out.append(insight(
            "reasoning-mix", "neutral", "Reasoning", "Reasoning mix",
            f"{rs * 100:.0f}% of output was reasoning.",
            detail="This is expected for complex code work, but it is worth watching on long runs.",
            priority=72,
        ))

    co = an["coordination"]
    if co["share"] > 0.30:
        out.append(insight(
            "coordination-high", "warn", "Flow", "Coordination tax",
            f"Coordination tax is {co['share'] * 100:.0f}% of spend.",
            detail=f"{co['turns']} coordination executions cost ${co['cost']:.2f}.",
            action="Use fewer subagents or collapse exploration into one pass.",
            priority=32,
        ))
    elif co["share"] > 0.10 and co["turns"]:
        out.append(insight(
            "coordination-mix", "neutral", "Flow", "Coordination mix",
            f"Coordination used {co['share'] * 100:.0f}% of spend.",
            detail=f"{co['turns']} coordination executions were detected.",
            priority=74,
        ))

    if cost_approx:
        out.append(insight(
            "cost-approx", "neutral", "Pricing", "Pricing basis",
            f"Cost uses {model} public API rates.",
            detail="Subscription billing, discounts, and non-public pricing can differ.",
            priority=90,
        ))

    if biggest and biggest["cost"] > 0:
        biggest_share = biggest["cost"] / total_cost if total_cost else 0
        kind = "warn" if biggest["cost"] >= MENUBAR_COST_SPIKE or (n_turns > 1 and biggest_share >= 0.55) else "neutral"
        out.append(insight(
            "biggest", kind, "Spend", "Largest execution",
            f"Priciest execution was ${biggest['cost']:.2f} (#{biggest['idx']} of {n_turns}).",
            detail=f"It accounts for {biggest_share * 100:.0f}% of this log's spend.",
            action="Inspect that execution before continuing if it was unexpected." if kind == "warn" else "",
            priority=24 if kind == "warn" else 70,
        ))
    return normalize_insights(out)


def summarize_tool_evidence(calls, catalog=None):
    by_name = {}
    by_skill = {}
    previous_key = None
    previous_ts = 0
    day_tokens = defaultdict(int)
    day_flagged = defaultdict(int)
    totals = {
        "total_calls": 0,
        "total_output_tokens": 0,
        "flagged_tokens": 0,
        "oversized_calls": 0,
        "oversized_tokens": 0,
        "repeat_calls": 0,
        "repeat_tokens": 0,
        "errors": 0,
        "error_tokens": 0,
    }
    for call in calls or []:
        name = call.get("name") or "?"
        tokens = int(call.get("output_tokens") or 0)
        error = bool(call.get("error"))
        oversized = tokens >= TOOL_OVERSIZED_TOKENS
        ts = int(call.get("ts") or 0)
        args_fingerprint = call.get("args_fingerprint") or ""
        call_key = (name, args_fingerprint) if args_fingerprint else None
        repeated = bool(call_key and previous_key == call_key and
                        (not ts or not previous_ts or 0 <= ts - previous_ts <= 300))
        previous_key, previous_ts = call_key, ts
        flagged = bool(oversized or repeated or error)
        day = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""
        if day:
            day_tokens[day] += tokens
            if flagged:
                day_flagged[day] += tokens

        row = by_name.setdefault(name, {
            "name": name,
            "display": call.get("display") or name,
            "namespace": call.get("namespace") or "unknown",
            "kind": call.get("kind") or "tool",
            "calls": 0,
            "output_tokens": 0,
            "flagged_tokens": 0,
            "errors": 0,
            "oversized_calls": 0,
            "repeat_calls": 0,
            "last_ts": 0,
        })
        row["calls"] += 1
        row["output_tokens"] += tokens
        row["flagged_tokens"] += tokens if flagged else 0
        row["errors"] += 1 if error else 0
        row["oversized_calls"] += 1 if oversized else 0
        row["repeat_calls"] += 1 if repeated else 0
        row["last_ts"] = max(row["last_ts"], ts)

        totals["total_calls"] += 1
        totals["total_output_tokens"] += tokens
        totals["flagged_tokens"] += tokens if flagged else 0
        totals["oversized_calls"] += 1 if oversized else 0
        totals["oversized_tokens"] += tokens if oversized else 0
        totals["repeat_calls"] += 1 if repeated else 0
        totals["repeat_tokens"] += tokens if repeated else 0
        totals["errors"] += 1 if error else 0
        totals["error_tokens"] += tokens if error else 0

        for skill_name in call.get("skills") or []:
            skill = by_skill.setdefault(skill_name, {"name": skill_name, "activations": 0, "last_ts": 0})
            skill["activations"] += 1
            skill["last_ts"] = max(skill["last_ts"], ts)

    totals["tools"] = sorted(by_name.values(), key=lambda r: (-r["output_tokens"], -r["calls"], r["name"]))
    totals["skills"] = sorted(by_skill.values(), key=lambda r: (-r["activations"], r["name"]))
    totals["day_tokens"] = dict(day_tokens)
    totals["day_flagged"] = dict(day_flagged)
    totals["catalog"] = list(catalog or [])
    used_names = set(by_name)
    totals["definition_tokens"] = sum(int(row.get("definition_tokens") or 0) for row in totals["catalog"])
    totals["eager_definition_tokens"] = sum(
        int(row.get("definition_tokens") or 0) for row in totals["catalog"] if not row.get("defer_loading")
    )
    totals["deferred_definition_tokens"] = totals["definition_tokens"] - totals["eager_definition_tokens"]
    totals["unused_eager_definition_tokens"] = sum(
        int(row.get("definition_tokens") or 0) for row in totals["catalog"]
        if not row.get("defer_loading") and row.get("name") not in used_names
    )
    return totals


def claude_tool_call_evidence(objs, msgs=None):
    msgs = msgs if msgs is not None else iter_claude_messages(objs)
    result_chars, result_ts, result_errors = claude_tool_results(objs)
    calls = []
    for rec in msgs:
        for block in rec.get("content") or []:
            if block.get("type") != "tool_use":
                continue
            ident = tool_identity(block.get("name") or "?")
            tid = block.get("id")
            calls.append({
                **ident,
                "output_tokens": int(result_chars.get(tid, 0)) // CHARS_PER_TOKEN,
                "error": bool(result_errors.get(tid)),
                "ts": result_ts.get(tid) or rec.get("ts") or 0,
                "args_fingerprint": argument_fingerprint(block.get("input")),
                "skills": skill_names_from_value(block.get("input")),
            })
    return calls


def codex_tool_call_evidence(objs):
    calls = {}
    order = []
    for obj in objs:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        ptype = payload.get("type")
        ts = parse_iso(obj.get("timestamp", "")) or 0
        if ptype in ("function_call", "custom_tool_call", "web_search_call", "tool_search_call"):
            name = payload.get("name") or ("web.search" if ptype == "web_search_call" else ptype.replace("_call", ""))
            call_id = payload.get("call_id") or payload.get("id") or f"call-{len(order) + 1}"
            if call_id not in calls:
                arguments = payload.get("arguments") or payload.get("input")
                calls[call_id] = {
                    **tool_identity(name), "output_chars": 0, "output_tokens": 0,
                    "error": False, "ts": ts,
                    "args_fingerprint": argument_fingerprint(arguments),
                    "skills": skill_names_from_value(arguments),
                }
                order.append(call_id)
            continue
        if ptype not in ("function_call_output", "custom_tool_call_output", "web_search_end", "tool_search_output", "patch_apply_end"):
            continue
        call_id = payload.get("call_id") or payload.get("id") or payload.get("callId")
        if call_id not in calls:
            name = payload.get("name") or ptype
            calls[call_id] = {
                **tool_identity(name), "output_chars": 0, "output_tokens": 0,
                "error": False, "ts": ts, "args_fingerprint": "", "skills": [],
            }
            order.append(call_id)
        row = calls[call_id]
        output = payload.get("output") if "output" in payload else payload
        row["output_chars"] += observable_output_chars(output)
        row["output_tokens"] = row["output_chars"] // CHARS_PER_TOKEN
        row["error"] = bool(row.get("error") or tool_result_is_error(output, payload.get("status") == "failed"))
        row["ts"] = ts or row.get("ts") or 0
    return [calls[call_id] for call_id in order]


def claude_summary(source, objs):
    msgs = iter_claude_messages(objs)
    cost = 0.0
    tokens = 0
    first_ts = last_ts = None
    models = set()
    model_cost, model_tok = defaultdict(float), defaultdict(int)
    model_stats = {}
    model_daily = {}
    input_tokens = output_tokens = 0
    day_cost = defaultdict(float)
    approx = False
    for rec in msgs:
        usage = rec["usage"]
        if not usage:
            continue
        c = sum(cost_of(usage, rec["model"], "claude").values())
        _, missing = price_for(rec["model"], "claude")
        approx = approx or missing
        toks = usage_tokens(usage)
        cost += c
        tokens += toks
        models.add(rec["model"].replace("claude-", ""))
        model_cost[rec["model"]] += c
        model_tok[rec["model"]] += toks
        input_count, output_count = add_model_summary(model_stats, rec["model"], usage, c)
        add_model_daily(model_daily, rec["model"], usage, c, rec["ts"])
        input_tokens += input_count
        output_tokens += output_count
        if rec["ts"]:
            first_ts = rec["ts"] if first_ts is None else min(first_ts, rec["ts"])
            last_ts = rec["ts"] if last_ts is None else max(last_ts, rec["ts"])
            day = time.strftime("%Y-%m-%d", time.localtime(rec["ts"]))
            day_cost[day] += c

    title = source.get("title")
    if not title:
        for obj in objs:
            if obj.get("type") == "user":
                msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
                txt = text_from_content(msg.get("content")).strip()
                if txt and not txt.startswith("<") and "command-" not in txt[:20]:
                    title = compact_text(txt, 60)
                    break

    performance = claude_performance_samples(objs)
    wait_samples = claude_wait_samples(objs)
    row = summary_row(source, title, cost, tokens, len(msgs), models, first_ts, last_ts, model_cost, model_tok, day_cost, approx,
                      execution_timing("claude", objs), input_tokens, output_tokens, model_stats,
                      list(model_daily.values()), performance, wait_samples)
    row["frustration"], row["_frustration_events"] = analyze_frustration(
        "claude", objs, default_model=source.get("model") or DEFAULT_CLAUDE_MODEL
    )
    row["_tool_evidence"] = summarize_tool_evidence(claude_tool_call_evidence(objs, msgs))
    return row


def codex_summary(source, objs):
    model = source.get("model") or DEFAULT_OPENAI_MODEL
    cost = 0.0
    tokens = 0
    turns = 0
    first_ts = last_ts = None
    models = set()
    model_cost, model_tok = defaultdict(float), defaultdict(int)
    model_stats = {}
    model_daily = {}
    input_tokens = output_tokens = 0
    day_cost = defaultdict(float)
    approx = True

    for obj in objs:
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        if obj.get("type") == "turn_context":
            model = payload.get("model") or model
        if payload.get("type") != "token_count":
            continue
        raw = ((payload.get("info") or {}).get("last_token_usage") or {})
        if not raw:
            continue
        usage = codex_usage(raw)
        c = sum(cost_of(usage, model, "codex").values())
        toks = usage_tokens(usage)
        turns += 1
        cost += c
        tokens += toks
        models.add(model)
        model_cost[model] += c
        model_tok[model] += toks
        input_count, output_count = add_model_summary(model_stats, model, usage, c)
        ts = parse_iso(obj.get("timestamp", ""))
        add_model_daily(model_daily, model, usage, c, ts)
        input_tokens += input_count
        output_tokens += output_count
        if ts:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
            day = time.strftime("%Y-%m-%d", time.localtime(ts))
            day_cost[day] += c

    title = source.get("title")
    if not title:
        for obj in objs:
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            if payload.get("type") == "user_message":
                title = compact_text(payload.get("message") or "", 60)
                break
    performance = codex_performance_samples(objs, source.get("model"))
    wait_samples = codex_wait_samples(objs, source.get("model"))
    row = summary_row(source, title, cost, tokens, turns, models, first_ts, last_ts, model_cost, model_tok, day_cost, approx,
                      execution_timing("codex", objs), input_tokens, output_tokens, model_stats,
                      list(model_daily.values()), performance, wait_samples)
    row["frustration"], row["_frustration_events"] = analyze_frustration(
        "codex", objs, default_model=source.get("model") or DEFAULT_OPENAI_MODEL
    )
    row["_tool_evidence"] = summarize_tool_evidence(codex_tool_call_evidence(objs), source.get("tool_catalog") or [])
    return row


def summary_row(source, title, cost, tokens, turns, models, first_ts, last_ts, model_cost, model_tok, day_cost, approx,
                active_timing=None, input_tokens=0, output_tokens=0, model_stats=None,
                model_daily=None, performance_samples=None, wait_samples=None,
                availability=None):
    active_timing = active_timing or {}
    model_stats = model_stats or {}
    model_daily = model_daily or []
    performance_samples = performance_samples or []
    wait_samples = wait_samples or []
    availability = availability or metric_availability(source.get("provider"))
    wall_duration = (last_ts - first_ts) if (first_ts and last_ts) else 0
    return {
        "id": source["id"],
        "path": source["path"],
        "provider": source["provider"],
        "client": source.get("client") or source["provider"],
        "runtime": source_runtime_label(source),
        "label": source["label"],
        "desktop_session_id": source.get("desktop_session_id"),
        "project": source.get("project") or "",
        "title": title or source.get("title") or "(untitled log)",
        "cost": cost,
        "cost_approx": bool(approx),
        "availability": availability,
        "tokens": tokens,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "turns": turns,
        "models": sorted(models),
        "model_stats": sorted([
            {
                "model": model,
                "cost": float(values.get("cost") or 0),
                "tokens": int(values.get("tokens") or 0),
                "input_tokens": int(values.get("input_tokens") or 0),
                "output_tokens": int(values.get("output_tokens") or 0),
                "executions": int(values.get("executions") or 0),
                "availability": values.get("availability") or availability,
            }
            for model, values in model_stats.items()
        ], key=lambda row: (-row["cost"], -row["tokens"], row["model"])),
        "throughput": performance_summary(performance_samples, output_tokens),
        "wait_time": wait_time_summary(wait_samples),
        "mtime": source["mtime"],
        "start": time.strftime("%Y-%m-%d %H:%M", time.localtime(first_ts)) if first_ts else "",
        "last": time.strftime("%Y-%m-%d %H:%M", time.localtime(last_ts)) if last_ts else "",
        "duration_s": int(round(active_timing.get("duration_s") or 0)),
        "duration_available": bool(active_timing.get("available")),
        "duration_basis": active_timing.get("basis") or "unavailable",
        "wall_duration_s": int(wall_duration),
        "_model_cost": dict(model_cost),
        "_model_tok": dict(model_tok),
        "_day_cost": dict(day_cost),
        "_model_daily": model_daily,
        "_performance_samples": performance_samples,
        "_wait_samples": wait_samples,
    }
    row["provenance"] = usage_provenance([row])
    row["usage_basis"] = row["provenance"]["usage_basis"]
    return row


def cursor_summary(source, objs=None):
    """Build a cross-session Cursor row from the same local-estimate contract."""
    state = recompute_cursor(source)
    if not state:
        availability = metric_availability("cursor")
        return summary_row(
            source, source.get("title"), 0.0, 0, 0, set(), None, None,
            {}, {}, {}, False, availability=availability,
        )
    executions = state.get("executions") or []
    availability = state.get("availability") or metric_availability("cursor")
    model_stats = {}
    model_daily = {}
    model_cost = defaultdict(float)
    model_tok = defaultdict(int)
    day_cost = defaultdict(float)
    performance_samples = []
    wait_samples = []
    for execution in executions:
        model = execution.get("model") or "unknown"
        token_data = execution.get("tokens") or {}
        input_tokens = int(token_data.get("input") or 0)
        output_tokens = int(token_data.get("output") or 0)
        execution_cost = float(execution.get("cost") or 0)
        stats = model_stats.setdefault(model, {
            "cost": 0.0, "tokens": 0, "input_tokens": 0,
            "output_tokens": 0, "executions": 0, "availability": availability,
        })
        stats["cost"] += execution_cost
        stats["tokens"] += input_tokens + output_tokens
        stats["input_tokens"] += input_tokens
        stats["output_tokens"] += output_tokens
        stats["executions"] += 1
        model_cost[model] += execution_cost
        model_tok[model] += input_tokens + output_tokens
        ts = float(execution.get("ts") or 0)
        if ts:
            day = time.strftime("%Y-%m-%d", time.localtime(ts))
            day_cost[day] += execution_cost
            daily = model_daily.setdefault((model, day), {
                "model": model, "day": day, "cost": 0.0,
                "input_tokens": 0, "output_tokens": 0, "executions": 0,
                "availability": availability,
            })
            daily["cost"] += execution_cost
            daily["input_tokens"] += input_tokens
            daily["output_tokens"] += output_tokens
            daily["executions"] += 1
        duration_s = float(execution.get("duration_ms") or 0) / 1000.0
        if output_tokens and duration_s:
            performance_samples.append({
                "provider": "cursor", "model": model,
                "day": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
                "ts": ts, "input_tokens": input_tokens, "output_tokens": output_tokens,
                "peak_input_tokens": int(execution.get("context_tokens") or input_tokens),
                "uncached_input_tokens": input_tokens, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "model_calls": int(execution.get("model_calls") or 0),
                "duration_s": duration_s, "generation_s": duration_s,
                "ttft_s": float(execution.get("ttft_ms") or 0) / 1000.0,
                "tool_calls": int(execution.get("tool_count") or 0),
                "timing_basis": execution.get("timing_basis") or "observed",
            })
    for sample in (state.get("wait_time") or {}).get("samples") or []:
        ts = float(sample.get("ts") or 0)
        wait_samples.append({
            **sample,
            "provider": "cursor",
            "day": time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "",
        })
    first_ts = float((state.get("timing") or {}).get("start_ts") or 0) or None
    last_ts = float((state.get("timing") or {}).get("end_ts") or 0) or None
    active = {
        "duration_s": float((state.get("timing") or {}).get("duration_s") or 0),
        "available": bool((state.get("timing") or {}).get("duration_available")),
        "basis": (state.get("timing") or {}).get("duration_basis") or "unavailable",
    }
    row = summary_row(
        source, source.get("title"), float(state.get("total_cost") or 0),
        int(state.get("total_tokens") or 0), len(executions), set(model_stats),
        first_ts, last_ts, model_cost, model_tok, day_cost, bool(state.get("cost_approx")), active,
        int((state.get("tokens") or {}).get("input") or 0),
        int((state.get("tokens") or {}).get("output") or 0),
        model_stats, list(model_daily.values()), performance_samples, wait_samples,
        availability,
    )
    row["token_estimate"] = bool(state.get("token_estimate"))
    row["provenance"] = usage_provenance([row])
    row["usage_basis"] = row["provenance"]["usage_basis"]
    terms = frustration_settings()["terms"]
    events = []
    for execution in executions:
        ts = float(execution.get("ts") or 0)
        day = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""
        term_counts = frustration_term_counts(execution.get("user_input") or "", terms)
        events.append({
            "ts": ts, "day": day, "week": week_start(day),
            "model": execution.get("model") or "unknown",
            "utterance": bool(term_counts), "matches": sum(term_counts.values()),
            "term_counts": term_counts,
        })
    row["frustration"] = rollup_frustration_events(events)
    row["_frustration_events"] = events
    calls = []
    for execution in executions:
        for tool in execution.get("tools") or []:
            calls.append({**tool, "ts": execution.get("ts") or 0})
    row["_tool_evidence"] = summarize_tool_evidence(calls)
    row["context"] = state.get("context") or {}
    row["tool_calls"] = int((state.get("tools") or {}).get("total_calls") or 0)
    row["tool_errors"] = int((state.get("tools") or {}).get("total_errors") or 0)
    return row


def session_summary(source):
    cached = _summary_cache.get(source["path"])
    mtime = source.get("signature_mtime") or source.get("mtime") or safe_mtime(source["path"])
    if cached and cached.get("mtime") == mtime:
        return cached["row"]
    objs = load(source["path"])
    if source["provider"] == "codex":
        row = codex_summary(source, objs)
    elif source["provider"] == "claude":
        row = claude_summary(source, objs)
    elif source["provider"] == "cursor":
        row = cursor_summary(source, objs)
    else:
        row = summary_row(source, source.get("title"), 0.0, 0, 0, set(), None, None,
                          {}, {}, {}, False, availability=metric_availability("unknown"))
    _summary_cache[source["path"]] = {"mtime": mtime, "row": row}
    return row


def global_tool_waste(session_rows):
    by_name = {}
    by_namespace = {}
    by_skill = {}
    day_tokens = defaultdict(int)
    day_flagged = defaultdict(int)
    totals = {
        "total_calls": 0, "total_output_tokens": 0, "flagged_tokens": 0,
        "oversized_calls": 0, "oversized_tokens": 0,
        "repeat_calls": 0, "repeat_tokens": 0,
        "errors": 0, "error_tokens": 0,
        "definition_tokens": 0, "eager_definition_tokens": 0,
        "deferred_definition_tokens": 0, "unused_eager_definition_tokens": 0,
    }
    advertised_names = set()
    used_advertised_names = set()

    def evidence_key(name, kind, provider):
        # Keep the existing cross-runtime MCP view unchanged. Plain tools are
        # runtime-owned, so identical Cursor/Codex/Claude names stay separate.
        return name if kind == "mcp" else f"{provider}::{name}"

    for session in session_rows:
        evidence = session.get("_tool_evidence") or {}
        project = session.get("project") or "local"
        provider = session.get("provider") or "unknown"
        runtime = session.get("runtime") or source_runtime_label(session)
        session_id = session.get("id") or session.get("path")
        for key in ("total_calls", "total_output_tokens", "flagged_tokens", "oversized_calls",
                    "oversized_tokens", "repeat_calls", "repeat_tokens", "errors", "error_tokens",
                    "definition_tokens", "eager_definition_tokens", "deferred_definition_tokens",
                    "unused_eager_definition_tokens"):
            totals[key] += int(evidence.get(key) or 0)
        for day, value in (evidence.get("day_tokens") or {}).items():
            day_tokens[day] += int(value or 0)
        for day, value in (evidence.get("day_flagged") or {}).items():
            day_flagged[day] += int(value or 0)

        for item in evidence.get("tools") or []:
            name = item.get("name") or "?"
            kind = item.get("kind") or "tool"
            key = evidence_key(name, kind, provider)
            row = by_name.setdefault(key, {
                "id": key,
                "name": name,
                "display": item.get("display") or name,
                "namespace": item.get("namespace") or "unknown",
                "kind": kind, "runtime": runtime,
                "calls": 0, "output_tokens": 0, "flagged_tokens": 0,
                "errors": 0, "oversized_calls": 0, "repeat_calls": 0,
                "last_ts": 0, "sessions": set(), "projects": set(),
                "project_calls": defaultdict(int), "providers": set(),
                "advertised_sessions": set(), "eager_sessions": set(), "deferred_sessions": set(),
                "definition_tokens": 0, "eager_definition_tokens": 0,
                "deferred_definition_tokens": 0, "unused_eager_definition_tokens": 0,
            })
            for key in ("calls", "output_tokens", "flagged_tokens", "errors", "oversized_calls", "repeat_calls"):
                row[key] += int(item.get(key) or 0)
            row["last_ts"] = max(row["last_ts"], int(item.get("last_ts") or 0))
            row["sessions"].add(session_id)
            row["projects"].add(project)
            row["project_calls"][project] += int(item.get("calls") or 0)
            row["providers"].add(provider)

            namespace = row["namespace"]
            namespace_key = evidence_key(namespace, row["kind"], provider)
            ns = by_namespace.setdefault(namespace_key, {
                "id": namespace_key, "namespace": namespace, "kind": row["kind"],
                "runtime": runtime, "providers": set(), "calls": 0,
                "output_tokens": 0, "flagged_tokens": 0, "errors": 0,
                "sessions": set(), "projects": set(),
            })
            ns["calls"] += int(item.get("calls") or 0)
            ns["output_tokens"] += int(item.get("output_tokens") or 0)
            ns["flagged_tokens"] += int(item.get("flagged_tokens") or 0)
            ns["errors"] += int(item.get("errors") or 0)
            ns["sessions"].add(session_id)
            ns["projects"].add(project)
            ns["providers"].add(provider)

        for item in evidence.get("skills") or []:
            name = item.get("name") or "?"
            row = by_skill.setdefault(name, {
                "name": name, "activations": 0, "last_ts": 0,
                "sessions": set(), "projects": set(), "providers": set(),
            })
            row["activations"] += int(item.get("activations") or 0)
            row["last_ts"] = max(row["last_ts"], int(item.get("last_ts") or 0))
            row["sessions"].add(session_id)
            row["projects"].add(project)
            row["providers"].add(provider)

        session_used = {item.get("name") for item in evidence.get("tools") or []}
        for item in evidence.get("catalog") or []:
            name = item.get("name") or "?"
            kind = item.get("kind") or "tool"
            key = evidence_key(name, kind, provider)
            advertised_names.add(name)
            if name in session_used:
                used_advertised_names.add(name)
            row = by_name.setdefault(key, {
                "id": key,
                "name": name, "display": name,
                "namespace": item.get("namespace") or "unknown",
                "kind": kind, "runtime": runtime,
                "calls": 0, "output_tokens": 0, "flagged_tokens": 0,
                "errors": 0, "oversized_calls": 0, "repeat_calls": 0,
                "last_ts": 0, "sessions": set(), "projects": set(),
                "project_calls": defaultdict(int), "providers": set(),
                "advertised_sessions": set(), "eager_sessions": set(), "deferred_sessions": set(),
                "definition_tokens": 0, "eager_definition_tokens": 0,
                "deferred_definition_tokens": 0, "unused_eager_definition_tokens": 0,
            })
            row["advertised_sessions"].add(session_id)
            definition_tokens = int(item.get("definition_tokens") or 0)
            row["definition_tokens"] += definition_tokens
            if item.get("defer_loading"):
                row["deferred_sessions"].add(session_id)
                row["deferred_definition_tokens"] += definition_tokens
            else:
                row["eager_sessions"].add(session_id)
                row["eager_definition_tokens"] += definition_tokens
                if name not in session_used:
                    row["unused_eager_definition_tokens"] += definition_tokens

    total_sessions = len(session_rows)
    tool_rows = []
    for row in by_name.values():
        sessions_used = len(row["sessions"])
        advertised_sessions = len(row["advertised_sessions"])
        project_calls = dict(row["project_calls"])
        top_project = max(project_calls, key=project_calls.get) if project_calls else ""
        top_project_calls = project_calls.get(top_project, 0)
        project_share = top_project_calls / row["calls"] if row["calls"] else 0.0
        diagnostic = bool(row["kind"] == "mcp" and (
            row["namespace"] == "tokenmeter" or str(row["name"]).startswith("mcp__tokenmeter__")
        ))
        recommendation = "keep"
        reason = "Observed usage does not cross a trace-waste threshold."
        if diagnostic:
            reason = "Token Meter diagnostic overhead is retained for accounting but excluded from cleanup advice."
        elif row["kind"] == "mcp" and advertised_sessions >= 5 and sessions_used == 0:
            recommendation = "disable"
            reason = f"Reported in {advertised_sessions} sessions and never called."
        elif row["errors"] >= 3 and row["errors"] / max(1, row["calls"]) >= 0.5:
            recommendation = "fix_or_disable"
            reason = f"{row['errors']} of {row['calls']} calls were errors."
        elif row["oversized_calls"] or row["output_tokens"] >= 25000:
            recommendation = "narrow_results"
            reason = f"Returned ~{row['output_tokens']:,} tokens across {row['calls']} calls."
        elif row["repeat_calls"] >= 3:
            recommendation = "reduce_repeats"
            reason = f"Repeated the same arguments in {row['repeat_calls']} consecutive calls."
        elif row["kind"] == "mcp" and sessions_used <= max(1, int(total_sessions * 0.05)):
            recommendation = "scope"
            reason = f"Used in {sessions_used} of {total_sessions} sessions."
        elif project_share >= 0.8 and row["calls"] >= 5 and len(row["projects"]) > 0:
            recommendation = "scope"
            reason = f"{project_share * 100:.0f}% of calls came from {top_project}."

        tool_rows.append({
            "id": row["id"], "name": row["name"], "display": row["display"],
            "namespace": row["namespace"], "kind": row["kind"],
            "runtime": row["runtime"],
            "calls": row["calls"], "output_tokens": row["output_tokens"],
            "flagged_tokens": row["flagged_tokens"], "errors": row["errors"],
            "oversized_calls": row["oversized_calls"], "repeat_calls": row["repeat_calls"],
            "sessions_used": sessions_used, "advertised_sessions": advertised_sessions,
            "eager_sessions": len(row["eager_sessions"]), "deferred_sessions": len(row["deferred_sessions"]),
            "definition_tokens": row["definition_tokens"],
            "eager_definition_tokens": row["eager_definition_tokens"],
            "deferred_definition_tokens": row["deferred_definition_tokens"],
            "unused_eager_definition_tokens": row["unused_eager_definition_tokens"],
            "projects": sorted(row["projects"]), "top_project": top_project,
            "project_share": project_share, "providers": sorted(row["providers"]),
            "last_ts": row["last_ts"],
            "last_used": time.strftime("%Y-%m-%d", time.localtime(row["last_ts"])) if row["last_ts"] else "Never",
            "recommendation": recommendation, "reason": reason,
            "mcp_server": row["namespace"] if row["kind"] == "mcp" else "",
            "diagnostic": diagnostic,
        })

    tool_rows.sort(key=lambda r: (-r["output_tokens"], -r["calls"], r["name"]))
    namespace_rows = []
    for row in by_namespace.values():
        namespace_rows.append({
            "id": row["id"], "namespace": row["namespace"], "kind": row["kind"],
            "runtime": row["runtime"], "providers": sorted(row["providers"]),
            "calls": row["calls"], "output_tokens": row["output_tokens"],
            "flagged_tokens": row["flagged_tokens"], "errors": row["errors"],
            "sessions_used": len(row["sessions"]), "projects": sorted(row["projects"]),
        })
    namespace_rows.sort(key=lambda r: (-r["output_tokens"], -r["calls"], r["namespace"]))

    trend = [
        {"day": day, "tokens": day_tokens[day], "flagged_tokens": day_flagged.get(day, 0)}
        for day in sorted(set(day_tokens) | set(day_flagged))
    ][-14:]

    insights = []
    actionable_rows = [row for row in tool_rows if not row.get("diagnostic")]
    disable_candidates = [row for row in actionable_rows if row["recommendation"] == "disable"]
    if disable_candidates:
        candidate = disable_candidates[0]
        insights.append(insight(
            f"global-disable:{candidate['name']}", "warn", "Tools", "MCP disable candidate",
            f"{candidate['display']} was advertised in {candidate['advertised_sessions']} sessions and never called.",
            detail=f"Runtime-reported namespace: {candidate['namespace']}.",
            action="Review and disable the MCP server if it is no longer needed.", priority=8,
        ))

    catalog_count = len(advertised_names)
    catalog_used = len(used_advertised_names)
    if totals["unused_eager_definition_tokens"]:
        eager = totals["eager_definition_tokens"]
        share = totals["unused_eager_definition_tokens"] / max(1, eager)
        insights.append(insight(
            "global-eager-tax", "warn" if share >= 0.5 else "neutral", "Tools", "Unused eager schema tax",
            f"~{totals['unused_eager_definition_tokens']:,} eager tool-definition tokens were loaded in sessions that did not call those tools.",
            detail=f"That is {share * 100:.0f}% of {eager:,} eager definition tokens across runtime-reported catalogs.",
            action="Move rarely used capabilities to deferred loading or disable their provider." if share >= 0.5 else "",
            priority=9,
        ))

    skill_rows = [{
        "name": row["name"], "activations": row["activations"],
        "sessions_used": len(row["sessions"]), "projects": sorted(row["projects"]),
        "providers": sorted(row["providers"]), "last_ts": row["last_ts"],
        "last_used": time.strftime("%Y-%m-%d", time.localtime(row["last_ts"])) if row["last_ts"] else "Never",
    } for row in by_skill.values()]
    skill_rows.sort(key=lambda row: (-row["activations"], row["name"]))

    return {
        **dict(totals),
        "total_sessions": total_sessions,
        "sessions_with_tools": sum(1 for row in session_rows if (row.get("_tool_evidence") or {}).get("total_calls")),
        "by_name": (tool_rows[:20] + [
            row for row in tool_rows[20:] if row["recommendation"] in ("disable", "fix_or_disable")
        ])[:24],
        "inventory_tools": tool_rows[:240],
        "by_namespace": namespace_rows[:16],
        "skills": skill_rows[:80],
        "catalog_unique": catalog_count,
        "catalog_used_unique": catalog_used,
        "catalog_utilization": (catalog_used / catalog_count) if catalog_count else 0.0,
        "trend": trend,
        "insights": normalize_insights(insights, limit=8),
    }


def codex_mcp_states():
    return {name: bool(row.get("enabled")) for name, row in toml_named_sections(CODEX_CONFIG, "mcp_servers").items()}


def claude_mcp_states():
    states = {}
    desktop_configs = [os.path.join(root, "claude_desktop_config.json") for root in CLAUDE_DESKTOP_DATA_ROOTS]
    for path in (CLAUDE_ROOT_CONFIG, *desktop_configs):
        data = load_json(path, {})
        for name in (data.get("mcpServers") or {}) if isinstance(data, dict) else {}:
            states[name] = True
    return states


def codex_plugin_states():
    return {name: bool(row.get("enabled")) for name, row in toml_named_sections(CODEX_CONFIG, "plugins").items()}


def claude_plugin_installations():
    data = load_json(os.path.expanduser("~/.claude/plugins/installed_plugins.json"), {})
    plugins = data.get("plugins") if isinstance(data, dict) else {}
    result = {}
    for plugin_id, installs in (plugins or {}).items():
        if not isinstance(installs, list) or not installs:
            continue
        valid = [row for row in installs if isinstance(row, dict) and row.get("installPath")]
        if valid:
            result[plugin_id] = valid[-1]
    return result


def skill_identity(runtime, name, origin_id, plugin_id=""):
    """Return a stable identity that cannot collide across runtimes or origins."""
    runtime_key = re.sub(r"[^a-z0-9]+", "-", str(runtime or "unknown").lower()).strip("-") or "unknown"
    owner = str(plugin_id or origin_id or "unknown").strip()
    return f"skill:{runtime_key}:{owner}:{name}"


def discovered_skills(skill_usage=None):
    usage = {str(row.get("name") or "").lower(): row for row in (skill_usage or [])}
    rows = []

    def add(path, runtime, source, origin_id, origin="user", enabled=True, plugin_id="",
            mutable=False, control_scope="", reviewable=False, setting_path=""):
        name = os.path.basename(os.path.dirname(path))
        used = usage.get(name.lower()) or {}
        providers = {str(provider).lower() for provider in used.get("providers") or []}
        expected_provider = "codex" if runtime == "Codex" else "claude"
        if providers and expected_provider not in providers:
            used = {}
        rows.append({
            "id": skill_identity(runtime, name, origin_id, plugin_id),
            "type": "skill", "name": name, "runtime": runtime, "source": source,
            "path": home_shorten(path), "enabled": bool(enabled), "plugin_id": plugin_id,
            "mutable": bool(mutable), "control_scope": control_scope,
            "origin": origin, "origin_id": origin_id, "reviewable": bool(reviewable),
            "setting_path": home_shorten(setting_path) if setting_path else "",
            "used": bool(used), "activations": int(used.get("activations") or 0),
            "sessions_used": int(used.get("sessions_used") or 0), "last_used": used.get("last_used") or "Never",
        })

    codex_skills_root = os.path.expanduser("~/.codex/skills")
    codex_system_root = os.path.join(codex_skills_root, ".system") + os.sep
    for path in glob.glob(os.path.join(codex_system_root, "**", "SKILL.md"), recursive=True):
        add(path, "Codex", "Codex built-in", "codex:built-in", "built_in", True)
    for path in glob.glob(os.path.join(codex_skills_root, "**", "SKILL.md"), recursive=True):
        if "/plugins/" not in path and not path.startswith(codex_system_root):
            add(path, "Codex", "User installed", "codex:user", "user", True)

    codex_plugins = codex_plugin_states()
    codex_cache = os.path.expanduser("~/.codex/plugins/cache")
    for path in glob.glob(os.path.join(codex_cache, "*", "*", "*", "skills", "*", "SKILL.md")):
        rel = os.path.relpath(path, codex_cache).split(os.sep)
        if len(rel) < 6:
            continue
        market, plugin = rel[0], rel[1]
        plugin_id = f"{plugin}@{market}"
        configured = plugin_id in codex_plugins
        bundled = market in ("openai-bundled", "openai-primary-runtime", "openai-curated-remote")
        add(path, "Codex", "Codex runtime pack" if bundled else "User-installed plugin",
            f"codex:plugin:{market}", "runtime_pack" if bundled else "user_plugin",
            codex_plugins.get(plugin_id, True), plugin_id, configured,
            "plugin pack" if configured else "", configured and not bundled, CODEX_CONFIG)

    claude_settings = load_json(CLAUDE_SETTINGS, {})
    claude_enabled = claude_settings.get("enabledPlugins") if isinstance(claude_settings, dict) else {}
    for plugin_id, install in claude_plugin_installations().items():
        root = install.get("installPath") or ""
        marketplace = plugin_id.rsplit("@", 1)[-1] if "@" in plugin_id else "unknown"
        runtime_pack = marketplace in ("claude-plugins-official", "openai-codex")
        for path in glob.glob(os.path.join(root, "skills", "*", "SKILL.md")):
            add(path, "Claude", "Claude runtime pack" if runtime_pack else "User-installed plugin",
                f"claude:plugin:{marketplace}", "runtime_pack" if runtime_pack else "user_plugin",
                bool((claude_enabled or {}).get(plugin_id)), plugin_id, True, "plugin pack",
                not runtime_pack, CLAUDE_SETTINGS)

    for data_root in CLAUDE_DESKTOP_DATA_ROOTS:
        desktop_root = os.path.join(data_root, "local-agent-mode-sessions", "skills-plugin")
        for path in glob.glob(os.path.join(desktop_root, "**", "skills", "*", "SKILL.md"), recursive=True):
            add(path, "Claude Desktop", "Cowork built-in", "claude-desktop:built-in", "built_in", True)

    deduped = {}
    for row in rows:
        deduped[row["id"]] = row
    return sorted(deduped.values(), key=lambda row: (row["runtime"], row["name"], row["source"]))


def capability_control_groups(_mcp_items, skill_items):
    """Return user-installed skill packs that the dashboard can disable.

    MCP servers remain visible as read-only evidence because their configuration
    mechanisms differ across clients and installations.
    """
    groups = []
    packs = {}
    for row in skill_items or []:
        if not row.get("mutable") or not row.get("plugin_id"):
            continue
        key = (row.get("runtime") or "unknown", row.get("plugin_id"))
        pack = packs.setdefault(key, {
            "id": f"skill_pack:{key[0]}:{key[1]}", "control_type": "skill_pack",
            "item_id": row.get("id"), "type": "skill", "name": key[1], "runtime": key[0],
            "plugin_id": key[1], "enabled": False, "used": False,
            "mutable": True, "calls": 0, "activations": 0, "members": set(),
            "returned_tokens": 0,
            "used_member_names": set(), "last_used": "Never",
            "definition_tokens": 0, "eager_definition_tokens": 0,
            "deferred_definition_tokens": 0, "unused_eager_definition_tokens": 0,
            "origin": row.get("origin") or "user_plugin",
            "origin_id": row.get("origin_id") or key[1],
            "source": row.get("source") or key[1],
            "reviewable": bool(row.get("reviewable", True)),
            "setting_path": row.get("setting_path") or "",
            "member_ids": set(),
        })
        name = row.get("name") or "?"
        pack["members"].add(name)
        pack["member_ids"].add(row.get("id"))
        pack["enabled"] = pack["enabled"] or bool(row.get("enabled"))
        pack["used"] = pack["used"] or bool(row.get("used"))
        if row.get("used"):
            pack["used_member_names"].add(name)
        pack["activations"] += int(row.get("activations") or 0)
        last_used = row.get("last_used") or "Never"
        if last_used != "Never" and (pack["last_used"] == "Never" or last_used > pack["last_used"]):
            pack["last_used"] = last_used
    for pack in packs.values():
        members = sorted(pack.pop("members"))
        used_members = pack.pop("used_member_names")
        pack["members"] = members
        pack["member_ids"] = sorted(value for value in pack["member_ids"] if value)
        pack["member_count"] = len(members)
        pack["used_members"] = len(used_members)
        groups.append(pack)
    return sorted(groups, key=lambda row: (row["control_type"], row["runtime"], row["name"]))


def optional_capability_summary(control_groups):
    enabled = [row for row in (control_groups or [])
               if row.get("enabled") and row.get("mutable") and row.get("reviewable", True)]
    used = [row for row in enabled if row.get("used")]
    unused = [row for row in enabled if not row.get("used")]
    mcp_enabled = [row for row in enabled if row.get("control_type") == "mcp"]
    skill_enabled = [row for row in enabled if row.get("control_type") == "skill_pack"]
    avoidable_tokens = sum(int(row.get("unused_eager_definition_tokens") or 0) for row in unused)
    eager_unused = [row for row in unused if int(row.get("unused_eager_definition_tokens") or 0) > 0]
    deferred_unused = [row for row in unused if int(row.get("deferred_definition_tokens") or 0) > 0
                       and int(row.get("unused_eager_definition_tokens") or 0) == 0]
    return {
        "scope": "all_sessions", "enabled": len(enabled), "used": len(used), "unused": len(unused),
        "utilization": len(used) / len(enabled) if enabled else 0.0,
        "mcp_enabled": len(mcp_enabled),
        "mcp_used": sum(1 for row in mcp_enabled if row.get("used")),
        "skill_packs_enabled": len(skill_enabled),
        "skill_packs_used": sum(1 for row in skill_enabled if row.get("used")),
        "review_candidates": [row["id"] for row in unused],
        "review_candidate_names": [row["name"] for row in unused],
        "avoidable_eager_definition_tokens": avoidable_tokens,
        "overhead_measured_groups": sum(1 for row in enabled if int(row.get("definition_tokens") or 0) > 0),
        "eager_unused_groups": len(eager_unused),
        "deferred_unused_groups": len(deferred_unused),
        "unmeasured_unused_groups": sum(1 for row in unused
                                         if not row.get("definition_tokens")),
    }


def capability_inventory(waste=None):
    waste = waste or {}
    tool_evidence = waste.get("inventory_tools") or waste.get("by_name") or []
    tool_items = []
    for row in tool_evidence:
        if row.get("kind") == "mcp":
            continue
        advertised = int(row.get("advertised_sessions") or 0)
        eager = int(row.get("eager_sessions") or 0)
        deferred = int(row.get("deferred_sessions") or 0)
        state = "Observed only"
        if advertised:
            state = "Eager" if eager and not deferred else ("Deferred" if deferred and not eager else "Mixed")
        tool_items.append({
            "id": f"tool:{row.get('id') or row.get('name')}", "type": "tool",
            "name": row.get("display") or row.get("name"),
            "identity": row.get("name"), "runtime": row.get("runtime") or "Trace",
            "source": row.get("namespace") or "unknown", "state": state,
            "enabled": True, "mutable": False, "used": bool(row.get("calls")),
            "calls": int(row.get("calls") or 0), "returned_tokens": int(row.get("output_tokens") or 0),
            "advertised_sessions": advertised, "eager_sessions": eager, "deferred_sessions": deferred,
            "last_used": row.get("last_used") or "Never", "recommendation": row.get("recommendation") or "keep",
        })

    codex_states, claude_states = codex_mcp_states(), claude_mcp_states()
    mcp_usage = defaultdict(lambda: {
        "calls": 0, "tokens": 0, "last_used": "Never", "used": False,
        "definition_tokens": 0, "eager_definition_tokens": 0,
        "deferred_definition_tokens": 0, "unused_eager_definition_tokens": 0,
    })
    for row in tool_evidence:
        if row.get("kind") != "mcp":
            continue
        name = row.get("mcp_server") or row.get("namespace") or "mcp"
        u = mcp_usage[name]
        u["calls"] += int(row.get("calls") or 0)
        u["tokens"] += int(row.get("output_tokens") or 0)
        u["used"] = u["used"] or bool(row.get("calls"))
        for key in ("definition_tokens", "eager_definition_tokens", "deferred_definition_tokens",
                    "unused_eager_definition_tokens"):
            u[key] += int(row.get(key) or 0)
        if row.get("last_ts") and row.get("last_used"):
            u["last_used"] = row["last_used"]
    all_mcp_names = set(codex_states) | set(claude_states) | set(mcp_usage)
    mcp_items = []
    for name in sorted(all_mcp_names):
        codex_on = bool(codex_states.get(name))
        claude_on = bool(claude_states.get(name))
        usage_row = mcp_usage[name]
        enabled = codex_on or claude_on
        mcp_items.append({
            "id": f"mcp:{name}", "type": "mcp", "name": name, "runtime": "Codex + Claude",
            "source": "trace/config",
            "state": "Enabled" if enabled else "Disabled", "enabled": enabled,
            "mutable": False,
            "codex_enabled": codex_on, "claude_enabled": claude_on, "used": usage_row["used"],
            "calls": usage_row["calls"], "returned_tokens": usage_row["tokens"], "last_used": usage_row["last_used"],
            "definition_tokens": usage_row["definition_tokens"],
            "eager_definition_tokens": usage_row["eager_definition_tokens"],
            "deferred_definition_tokens": usage_row["deferred_definition_tokens"],
            "unused_eager_definition_tokens": usage_row["unused_eager_definition_tokens"],
        })

    skill_items = discovered_skills(waste.get("skills") or [])
    control_groups = capability_control_groups(mcp_items, skill_items)
    optional_summary = optional_capability_summary(control_groups)
    optional_summary["scanned_sessions"] = int(waste.get("total_sessions") or 0)
    review_ids = set(optional_summary["review_candidates"])
    for row in mcp_items:
        row["control_id"] = row["id"]
        row["review_candidate"] = row["control_id"] in review_ids
    for row in skill_items:
        row["control_id"] = f"skill_pack:{row.get('runtime')}:{row.get('plugin_id')}" if row.get("plugin_id") else ""
        row["review_candidate"] = bool(row["control_id"] and row["control_id"] in review_ids)
    tool_reported = sum(1 for row in tool_items if row["advertised_sessions"])
    tool_reported_used = sum(1 for row in tool_items if row["advertised_sessions"] and row["used"])
    mcp_enabled = sum(1 for row in mcp_items if row["enabled"])
    mcp_enabled_used = sum(1 for row in mcp_items if row["enabled"] and row["used"])
    skills_enabled = sum(1 for row in skill_items if row["enabled"])
    skills_used = sum(1 for row in skill_items if row["enabled"] and row["used"])
    desktop_index = claude_desktop_index()
    local_agents = [row for row in desktop_index.values() if row.get("source_kind") == "agent"]
    traceable_agents = claude_local_agent_sources(desktop_index)
    latest_desktop = max((row.get("metadata_mtime") or 0 for row in local_agents), default=0)
    summary = {
        "tools": {"available": len(tool_items), "reported": tool_reported, "enabled": tool_reported,
                  "used": tool_reported_used, "utilization": tool_reported_used / tool_reported if tool_reported else 0.0,
                  "observed_only": sum(1 for row in tool_items if not row["advertised_sessions"])},
        "mcps": {"available": len(mcp_items), "enabled": mcp_enabled, "used": mcp_enabled_used,
                 "historically_used": sum(1 for row in mcp_items if row["used"]),
                 "utilization": mcp_enabled_used / mcp_enabled if mcp_enabled else 0.0},
        "skills": {"available": len(skill_items), "enabled": skills_enabled, "used": skills_used,
                   "utilization": skills_used / skills_enabled if skills_enabled else 0.0},
        "optional": optional_summary,
        "definitions": {key: int(waste.get(key) or 0) for key in (
            "definition_tokens", "eager_definition_tokens", "deferred_definition_tokens", "unused_eager_definition_tokens"
        )},
    }
    return {
        "summary": summary, "items": tool_items + mcp_items + skill_items,
        "control_groups": control_groups,
        "actions": capability_action_capability(),
        "claude_desktop": {
            "local_agent_sessions": len(local_agents),
            "traceable_agent_sessions": len(traceable_agents),
            "latest_local_agent": local_dt(latest_desktop) if latest_desktop else "Never",
            "cloud_trace_available": False,
            "roots": [home_shorten(root) for root in CLAUDE_DESKTOP_DATA_ROOTS if os.path.isdir(root)],
            "note": "Scanning local Claude Desktop Agent/Cowork traces.",
        },
        "generated_at": int(time.time()),
    }


def session_optional_capabilities(state, capabilities):
    """Summarize enabled removable groups for one selected session."""
    state = state or {}
    tools = state.get("tools") or {}
    provider = state.get("provider") or (state.get("source") or {}).get("provider") or ""
    skill_activations = {row.get("name"): int(row.get("activations") or 0)
                         for row in tools.get("skills") or [] if row.get("name")}
    groups = []
    for source_group in (capabilities or {}).get("control_groups") or []:
        group = dict(source_group)
        if (not group.get("enabled") or not group.get("mutable") or
                not group.get("reviewable", True)):
            continue
        attached = ((provider == "codex" and group.get("runtime") == "Codex") or
                    (provider == "claude" and group.get("runtime") == "Claude"))
        active_members = set(group.get("members") or []) & set(skill_activations)
        current_used = bool(active_members)
        activations = sum(skill_activations[name] for name in active_members)
        if not attached:
            continue
        group.update({
            "current_used": current_used, "current_activations": activations,
            "current_eager_definition_tokens": 0,
            "current_deferred_definition_tokens": 0,
            "current_unused_eager_definition_tokens": 0,
            "overhead_measured": False,
        })
        groups.append(group)

    used = [row for row in groups if row.get("current_used")]
    unused = [row for row in groups if not row.get("current_used")]
    avoidable_tokens = sum(int(row.get("current_unused_eager_definition_tokens") or 0) for row in unused)
    return {
        "scope": "session", "enabled": len(groups), "used": len(used), "unused": len(unused),
        "utilization": len(used) / len(groups) if groups else 0.0,
        "mcp_enabled": sum(1 for row in groups if row.get("control_type") == "mcp"),
        "mcp_used": sum(1 for row in used if row.get("control_type") == "mcp"),
        "skill_packs_enabled": sum(1 for row in groups if row.get("control_type") == "skill_pack"),
        "skill_packs_used": sum(1 for row in used if row.get("control_type") == "skill_pack"),
        "avoidable_eager_definition_tokens": avoidable_tokens,
        "overhead_measured_groups": sum(1 for row in groups if row.get("overhead_measured")),
        "eager_unused_groups": sum(1 for row in unused
                                    if int(row.get("current_unused_eager_definition_tokens") or 0) > 0),
        "deferred_unused_groups": sum(1 for row in unused
                                       if int(row.get("current_deferred_definition_tokens") or 0) > 0
                                       and int(row.get("current_unused_eager_definition_tokens") or 0) == 0),
        "unmeasured_unused_groups": sum(1 for row in unused if not row.get("overhead_measured")),
        "global_review_candidates": list((((capabilities or {}).get("summary") or {}).get("optional") or {}).get("review_candidate_names") or []),
        "groups": groups,
    }


def attach_cross_session(state, cross=None):
    if not state:
        return state
    cross = cross or cross_session()
    state["xsession"] = cross
    state["optional_capabilities"] = session_optional_capabilities(state, cross.get("capabilities") or {})
    return state


def capability_action_capability():
    return {
        "token": _ACTION_TOKEN,
        "skill_pack_toggle": True,
    }


def session_action_capability():
    return {
        "available": True,
        "token": _ACTION_TOKEN,
        "recoverable": True,
        "destination": "macOS Trash",
    }


def agent_access_launcher():
    root = os.path.dirname(os.path.realpath(__file__))
    candidates = [
        os.path.join(root, "scripts", "run-token-meter-mcp"),
        os.path.join(root, "bin", "token-meter-mcp"),
        "/Library/Application Support/Token Meter/bin/token-meter-mcp",
    ]
    return next((path for path in candidates if os.path.isfile(path) and os.access(path, os.X_OK)), candidates[0])


def agent_client_executable(client, which=None):
    which = which or shutil.which
    direct = which(client)
    if direct:
        return direct
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".local", "bin", client),
        os.path.join(home, ".volta", "bin", client),
        os.path.join(home, ".asdf", "shims", client),
        os.path.join(home, ".npm-global", "bin", client),
        os.path.join(home, "bin", client),
        os.path.join("/opt/homebrew/bin", client),
        os.path.join("/usr/local/bin", client),
    ]
    nvm = glob.glob(os.path.join(home, ".nvm", "versions", "node", "*", "bin", client))
    candidates.extend(sorted(nvm, key=safe_mtime, reverse=True))
    return next((path for path in candidates if os.path.isfile(path) and os.access(path, os.X_OK)), None)


def agent_client_environment(cli_path):
    """Give env-based Node wrappers their sibling runtime under a LaunchAgent."""
    env = os.environ.copy()
    current = [value for value in str(env.get("PATH") or "").split(os.pathsep) if value]
    # Keep the wrapper's directory, not the resolved script target. NVM's
    # `codex` is a symlink whose sibling `node` binary is required by env(1).
    preferred = [os.path.dirname(os.path.abspath(cli_path))] if cli_path else []
    for value in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if value not in preferred:
            preferred.append(value)
    env["PATH"] = os.pathsep.join(dict.fromkeys(preferred + current))
    env.setdefault("HOME", os.path.expanduser("~"))
    return env


def agent_access_command(client, enabled, launcher=None, cli_path=None):
    launcher = launcher or agent_access_launcher()
    client = str(client or "").strip().lower()
    if client == "codex":
        cli_path = cli_path or agent_client_executable("codex") or "codex"
        if enabled:
            return [cli_path, "mcp", "add", "--env", "TOKEN_METER_CALLER=codex",
                    AGENT_ACCESS_SERVER, "--", launcher]
        return [cli_path, "mcp", "remove", AGENT_ACCESS_SERVER]
    if client == "claude":
        cli_path = cli_path or agent_client_executable("claude") or "claude"
        if enabled:
            return [cli_path, "mcp", "add", "--transport", "stdio", "--scope", "user",
                    AGENT_ACCESS_SERVER, "--env", "TOKEN_METER_CALLER=claude", "--", launcher]
        return [cli_path, "mcp", "remove", AGENT_ACCESS_SERVER, "--scope", "user"]
    raise ValueError("Unsupported agent client.")


def agent_access_command_display(command):
    command = list(command or [])
    if command:
        command[0] = os.path.basename(command[0])
    return shlex.join(command)


def agent_cli_error_detail(completed):
    raw = str(getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or "")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return ""
    return compact_text(lines[-1].replace(os.path.expanduser("~"), "~"), 220)


def _agent_access_matches(command, args, env, launcher, runtime):
    try:
        command_match = os.path.realpath(os.path.expanduser(str(command or ""))) == os.path.realpath(launcher)
    except OSError:
        command_match = False
    return bool(command_match and list(args or []) == [] and str((env or {}).get("TOKEN_METER_CALLER") or "") == runtime)


def agent_access_client_status(client, launcher=None, runner=None, which=None, claude_config_path=None):
    client = str(client or "").strip().lower()
    if client not in ("codex", "claude"):
        raise ValueError("Unsupported agent client.")
    launcher = launcher or agent_access_launcher()
    runner = runner or subprocess.run
    which = which or shutil.which
    cli_path = agent_client_executable(client, which=which)
    configured = False
    connected = False
    conflict = False
    actual_enabled = True
    command, args, env = "", [], {}
    if cli_path and client == "codex":
        try:
            completed = runner([cli_path, "mcp", "get", AGENT_ACCESS_SERVER, "--json"],
                               capture_output=True, text=True, timeout=15, check=False,
                               env=agent_client_environment(cli_path))
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if completed and completed.returncode == 0:
            try:
                row = json.loads(completed.stdout or "{}")
            except json.JSONDecodeError:
                row = {}
            transport = row.get("transport") if isinstance(row, dict) else {}
            if isinstance(transport, dict) and transport.get("type") == "stdio":
                configured = True
                command = transport.get("command") or ""
                args = transport.get("args") or []
                env = transport.get("env") or {}
                actual_enabled = row.get("enabled") is not False
    elif cli_path:
        config = load_json(claude_config_path or CLAUDE_ROOT_CONFIG, {})
        servers = config.get("mcpServers") if isinstance(config, dict) else {}
        row = (servers or {}).get(AGENT_ACCESS_SERVER) if isinstance(servers, dict) else None
        if isinstance(row, dict):
            configured = True
            command = row.get("command") or ""
            args = row.get("args") or []
            env = row.get("env") or {}

    exact = configured and _agent_access_matches(command, args, env, launcher,
                                                  "codex" if client == "codex" else "claude")
    connected = bool(exact and actual_enabled)
    conflict = bool(configured and not connected)
    add_command = agent_access_command(client, True, launcher=launcher, cli_path=cli_path or client)
    remove_command = agent_access_command(client, False, launcher=launcher, cli_path=cli_path or client)
    return {
        "id": client,
        "label": "Codex" if client == "codex" else "Claude Code",
        "detected": bool(cli_path),
        "available": bool(cli_path and os.path.isfile(launcher) and os.access(launcher, os.X_OK)),
        "configured": configured,
        "connected": connected,
        "conflict": conflict,
        "status": "Connected" if connected else ("Needs attention" if conflict else
                  ("Ready to connect" if cli_path else "Client not found")),
        "connect_command": agent_access_command_display(add_command),
        "disconnect_command": agent_access_command_display(remove_command),
        "restart_note": "Start a new agent session after changing this connection.",
    }


def agent_access_status(**kwargs):
    launcher = kwargs.pop("launcher", None) or agent_access_launcher()
    clients = [agent_access_client_status(client, launcher=launcher, **kwargs)
               for client in ("codex", "claude")]
    return {
        "ok": True,
        "server": AGENT_ACCESS_SERVER,
        "launcher_ready": bool(os.path.isfile(launcher) and os.access(launcher, os.X_OK)),
        "clients": clients,
        "any_detected": any(row["detected"] for row in clients),
        "any_connected": any(row["connected"] for row in clients),
        "access": {
            "current_run": "Detailed cost, context, execution, and safe tool labels for the matched run.",
            "history": "Aggregate spend, model, runtime, and tool categories without run or project names.",
            "capabilities": "Named user-installed skill packs only when capability review is requested.",
            "never": "Prompts, messages, reasoning, tool arguments or results, paths, credentials, and config values.",
            "processing": "Returned metrics enter the connected agent context and may be processed by its model provider.",
            "mutation": False,
        },
    }


def set_agent_access(client, enabled, runner=None, status_getter=None):
    client = str(client or "").strip().lower()
    if client not in ("codex", "claude") or enabled not in (True, False):
        return {"ok": False, "error": "A supported client and explicit connection state are required."}
    runner = runner or subprocess.run
    status_getter = status_getter or agent_access_client_status
    before = status_getter(client)
    if not before.get("detected"):
        return {"ok": False, "error": f"{before.get('label') or client} CLI was not found."}
    if not before.get("available"):
        return {"ok": False, "error": "The Token Meter MCP launcher is not executable. Reinstall Token Meter."}
    if enabled and before.get("connected"):
        return {"ok": True, "changed": False, "client": before, "restart_required": False}
    if before.get("conflict"):
        return {"ok": False, "conflict": True,
                "error": "A different tokenmeter MCP entry already exists. Remove or rename it before connecting Token Meter."}
    if not enabled and not before.get("configured"):
        return {"ok": True, "changed": False, "client": before, "restart_required": False}
    cli_path = agent_client_executable(client)
    command = agent_access_command(client, enabled, cli_path=cli_path)
    try:
        completed = runner(command, capture_output=True, text=True, timeout=45, check=False,
                           env=agent_client_environment(cli_path))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "The agent client timed out while changing the connection."}
    except OSError:
        return {"ok": False, "error": "The agent client could not change the connection."}
    if completed.returncode != 0:
        label = before.get("label") or client
        detail = agent_cli_error_detail(completed)
        message = f"{label} rejected the connection change."
        if detail:
            message = f"{message} {detail}"
        return {"ok": False, "error": message}
    after = status_getter(client)
    verified = bool(after.get("connected")) if enabled else not bool(after.get("configured"))
    if not verified:
        return {"ok": False, "error": "The connection command completed, but the saved configuration could not be verified."}
    return {
        "ok": True, "changed": True, "client": after, "restart_required": True,
        "message": f"{after.get('label') or client} {'connected' if enabled else 'disconnected'}. Start a new agent session.",
    }


def set_codex_plugin_enabled(plugin_id, enabled):
    if not PLUGIN_ID_RE.fullmatch(str(plugin_id or "")):
        return {"ok": False, "error": "Invalid plugin id."}
    states = codex_plugin_states()
    if plugin_id not in states:
        return {"ok": False, "error": "Codex plugin is not configured."}
    try:
        with open(CODEX_CONFIG, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return {"ok": False, "error": compact_text(str(exc), 240)}
    quoted = re.escape(plugin_id)
    header = re.search(rf'^\[plugins\."{quoted}"\]\s*$', text, re.MULTILINE)
    if not header:
        header = re.search(rf'^\[plugins\.{quoted}\]\s*$', text, re.MULTILINE)
    if not header:
        return {"ok": False, "error": "Codex plugin section was not found."}
    next_header = re.search(r'^\[', text[header.end():], re.MULTILINE)
    end = header.end() + next_header.start() if next_header else len(text)
    body = text[header.end():end]
    value = "true" if enabled else "false"
    if re.search(r'^\s*enabled\s*=\s*(?:true|false)\s*$', body, re.MULTILINE | re.IGNORECASE):
        body = re.sub(r'(^\s*enabled\s*=\s*)(?:true|false)(\s*$)', rf'\g<1>{value}\g<2>', body,
                      count=1, flags=re.MULTILINE | re.IGNORECASE)
    else:
        body = f"\nenabled = {value}" + body
    try:
        atomic_write_text(CODEX_CONFIG, text[:header.end()] + body + text[end:])
    except OSError as exc:
        return {"ok": False, "error": compact_text(str(exc), 240)}
    verified_state = codex_plugin_states().get(plugin_id)
    if verified_state is not bool(enabled):
        return {"ok": False, "error": "Codex setting was written but could not be verified."}
    _xsess["data"], _xsess["at"] = None, 0.0
    return {
        "ok": True, "plugin_id": plugin_id, "runtime": "Codex", "enabled": bool(enabled),
        "verified": True, "setting_path": home_shorten(CODEX_CONFIG), "restart_required": True,
    }


def set_claude_plugin_enabled(plugin_id, enabled):
    if not PLUGIN_ID_RE.fullmatch(str(plugin_id or "")):
        return {"ok": False, "error": "Invalid plugin id."}
    if plugin_id not in claude_plugin_installations():
        return {"ok": False, "error": "Claude plugin is not installed."}
    settings = load_json(CLAUDE_SETTINGS, {})
    if not isinstance(settings, dict):
        settings = {}
    enabled_plugins = settings.setdefault("enabledPlugins", {})
    enabled_plugins[plugin_id] = bool(enabled)
    try:
        atomic_write_text(CLAUDE_SETTINGS, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    except OSError as exc:
        return {"ok": False, "error": compact_text(str(exc), 240)}
    verified = load_json(CLAUDE_SETTINGS, {})
    verified_state = ((verified.get("enabledPlugins") or {}).get(plugin_id)
                      if isinstance(verified, dict) else None)
    if verified_state is not bool(enabled):
        return {"ok": False, "error": "Claude setting was written but could not be verified."}
    _xsess["data"], _xsess["at"] = None, 0.0
    return {
        "ok": True, "plugin_id": plugin_id, "runtime": "Claude", "enabled": bool(enabled),
        "verified": True, "setting_path": home_shorten(CLAUDE_SETTINGS), "restart_required": True,
    }


def set_skill_pack_enabled(runtime, plugin_id, enabled):
    runtime = str(runtime or "").strip().lower()
    if runtime == "codex":
        result = set_codex_plugin_enabled(plugin_id, enabled)
    elif runtime == "claude":
        result = set_claude_plugin_enabled(plugin_id, enabled)
    else:
        return {"ok": False, "error": "Only Codex and Claude plugin packs can be changed."}
    return result


def set_capability_control_enabled(control, enabled):
    if (control or {}).get("control_type") == "skill_pack":
        return set_skill_pack_enabled(control.get("runtime"), control.get("plugin_id"), enabled)
    return {"ok": False, "error": "Unsupported capability control."}


def disable_capability_controls(control_ids, capabilities=None, setter=None):
    """Disable an exact set of current review candidates with partial-failure reporting."""
    if not isinstance(control_ids, list) or not control_ids or len(control_ids) > 100:
        return {"ok": False, "error": "Select between 1 and 100 unused capability groups."}
    requested = list(dict.fromkeys(str(value or "").strip() for value in control_ids))
    if any(not value for value in requested):
        return {"ok": False, "error": "Capability control ids must be non-empty strings."}
    capabilities = capabilities or (cross_session().get("capabilities") or {})
    candidate_ids = set((((capabilities.get("summary") or {}).get("optional") or {}).get("review_candidates") or []))
    groups = {row.get("id"): row for row in capabilities.get("control_groups") or []}
    invalid = [control_id for control_id in requested if (
        control_id not in candidate_ids or control_id not in groups or
        not groups[control_id].get("enabled") or groups[control_id].get("used") or
        not groups[control_id].get("mutable") or not groups[control_id].get("reviewable", True)
    )]
    if invalid:
        return {
            "ok": False, "error": "One or more controls are no longer unused review candidates.",
            "invalid_control_ids": invalid,
        }

    setter = setter or set_capability_control_enabled
    changed, failures, results = [], [], []
    for control_id in requested:
        control = groups[control_id]
        item = setter(control, False)
        result = {
            "control_id": control_id, "name": control.get("name"),
            "control_type": control.get("control_type"), "ok": bool(item.get("ok")),
        }
        if item.get("ok"):
            changed.append(control_id)
            result["verified"] = bool(item.get("verified", control.get("control_type") == "mcp"))
        else:
            result["error"] = item.get("error") or "Capability change failed."
            failures.append(result)
        results.append(result)
    return {
        "ok": not failures, "partial": bool(changed and failures),
        "requested": len(requested), "changed": len(changed),
        "changed_control_ids": changed, "failures": failures, "results": results,
        "restart_required": bool(changed),
    }


def refresh_capability_state():
    """Rebuild and publish capabilities after a verified configuration change."""
    cross = cross_session()
    if STATE:
        publish(attach_cross_session(dict(STATE), cross))
    return cross.get("capabilities") or {}


def daily_summaries(session_rows, limit=30):
    """Aggregate daily spend, usage provenance, and completed wait time."""
    days = {}

    def day_row(day):
        return days.setdefault(day, {
            "day": day, "cost": 0.0, "tokens": 0,
            "input_tokens": 0, "output_tokens": 0, "providers": {},
            "sessions": {}, "projects": set(), "wait_s": 0.0,
            "wait_samples": 0, "longest_wait_s": 0.0,
            "session_ids": set(), "cost_covered_ids": set(), "token_covered_ids": set(),
            "cache_covered_ids": set(), "estimated_ids": set(),
            "estimated_cost": 0.0, "estimated_tokens": 0,
        })

    def provider_row(row, provider):
        return row["providers"].setdefault(provider, {
            "provider": provider, "cost": 0.0, "tokens": 0,
            "input_tokens": 0, "output_tokens": 0,
            "wait_s": 0.0, "wait_samples": 0,
            "session_ids": set(), "cost_covered_ids": set(), "token_covered_ids": set(),
            "cache_covered_ids": set(), "estimated_ids": set(),
            "estimated_cost": 0.0, "estimated_tokens": 0,
        })

    def session_row(row, session, provider, project):
        session_id = session.get("id") or session.get("path") or "unknown"
        return row["sessions"].setdefault(session_id, {
            "id": session_id, "title": session.get("title") or session_id,
            "project": project, "provider": provider,
            "label": session.get("label") or provider, "cost": 0.0,
            "tokens": 0, "input_tokens": 0, "output_tokens": 0,
            "wait_s": 0.0, "wait_samples": 0, "longest_wait_s": 0.0,
            "availability": session.get("availability") or metric_availability(provider),
            "token_estimate": bool(session.get("token_estimate")),
        })

    for session in session_rows or []:
        provider = session.get("provider") or "unknown"
        project = session.get("project") or "local"
        session_id = session.get("id") or session.get("path") or "unknown"
        estimated = bool(session.get("token_estimate"))

        def mark_coverage(row):
            row["session_ids"].add(session_id)
            if metric_available(session, "cost"):
                row["cost_covered_ids"].add(session_id)
            if metric_available(session, "tokens"):
                row["token_covered_ids"].add(session_id)
            if metric_available(session, "cache"):
                row["cache_covered_ids"].add(session_id)
            if estimated:
                row["estimated_ids"].add(session_id)

        for day, value in (session.get("_day_cost") or {}).items():
            cost = float(value or 0)
            row = day_row(day)
            row["cost"] += cost
            runtime = provider_row(row, provider)
            runtime["cost"] += cost
            if estimated:
                row["estimated_cost"] += cost
                runtime["estimated_cost"] += cost
            mark_coverage(row)
            mark_coverage(runtime)
            row["projects"].add(project)
            session_row(row, session, provider, project)["cost"] += cost
        for stats in session.get("_model_daily") or []:
            day = stats.get("day") or ""
            if not day:
                continue
            input_tokens = int(stats.get("input_tokens") or 0)
            output_tokens = int(stats.get("output_tokens") or 0)
            tokens = input_tokens + output_tokens
            row = day_row(day)
            runtime = provider_row(row, provider)
            mark_coverage(row)
            mark_coverage(runtime)
            row["tokens"] += tokens
            row["input_tokens"] += input_tokens
            row["output_tokens"] += output_tokens
            runtime["tokens"] += tokens
            runtime["input_tokens"] += input_tokens
            runtime["output_tokens"] += output_tokens
            if estimated:
                row["estimated_tokens"] += tokens
                runtime["estimated_tokens"] += tokens
            row["projects"].add(project)
            daily_session = session_row(row, session, provider, project)
            daily_session["tokens"] += tokens
            daily_session["input_tokens"] += input_tokens
            daily_session["output_tokens"] += output_tokens
        for sample in session.get("_wait_samples") or []:
            day = sample.get("day") or ""
            duration_s = float(sample.get("duration_s") or 0)
            if not day or duration_s <= 0:
                continue
            row = day_row(day)
            mark_coverage(row)
            row["wait_s"] += duration_s
            row["wait_samples"] += 1
            row["longest_wait_s"] = max(row["longest_wait_s"], duration_s)
            row["projects"].add(project)
            runtime = provider_row(row, provider)
            mark_coverage(runtime)
            runtime["wait_s"] += duration_s
            runtime["wait_samples"] += 1
            daily_session = session_row(row, session, provider, project)
            daily_session["wait_s"] += duration_s
            daily_session["wait_samples"] += 1
            daily_session["longest_wait_s"] = max(daily_session["longest_wait_s"], duration_s)

    result = []
    for day in sorted((value for value in days if value), reverse=True)[:limit]:
        row = days[day]
        sessions = []
        for daily_session in row["sessions"].values():
            session_id = daily_session["id"]
            available = set()
            if metric_available(daily_session, "cost"):
                available.add(session_id)
            if metric_available(daily_session, "tokens"):
                available.add(session_id)
            estimated_ids = {session_id} if daily_session.get("token_estimate") else set()
            daily_session["provenance"] = make_usage_provenance(
                {session_id}, estimated_ids, available,
                daily_session["cost"] if estimated_ids else 0,
                daily_session["tokens"] if estimated_ids else 0,
            )
            daily_session["usage_basis"] = daily_session["provenance"]["usage_basis"]
            sessions.append(daily_session)
        sessions.sort(key=lambda value: (-value["cost"], value["title"]))
        providers = []
        for provider in row["providers"].values():
            session_ids = provider.pop("session_ids")
            cost_ids = provider.pop("cost_covered_ids")
            token_ids = provider.pop("token_covered_ids")
            cache_ids = provider.pop("cache_covered_ids")
            estimated_ids = provider.pop("estimated_ids")
            total_sessions = len(session_ids)
            cost_covered = len(cost_ids)
            token_covered = len(token_ids)
            cache_covered = len(cache_ids)
            provider_coverage = {
                "cost": {"covered_sessions": cost_covered, "total_sessions": total_sessions,
                         "complete": cost_covered == total_sessions},
                "tokens": {"covered_sessions": token_covered, "total_sessions": total_sessions,
                           "complete": token_covered == total_sessions},
                "cache": {"covered_sessions": cache_covered, "total_sessions": total_sessions,
                          "complete": cache_covered == total_sessions},
            }
            provider["coverage"] = provider_coverage
            provider["availability"] = {
                "cost": cost_covered > 0, "tokens": token_covered > 0,
                "input_tokens": token_covered > 0, "output_tokens": token_covered > 0,
                "cache": cache_covered > 0, "throughput": token_covered > 0,
                "context": True, "timing": bool(provider["wait_samples"]), "tool_results": True,
            }
            provider["provenance"] = make_usage_provenance(
                session_ids, estimated_ids, cost_ids | token_ids,
                provider.pop("estimated_cost"), provider.pop("estimated_tokens"),
            )
            provider["usage_basis"] = provider["provenance"]["usage_basis"]
            providers.append(provider)
        providers = sorted(providers,
                           key=lambda value: (-value["cost"], -value["wait_s"], value["provider"]))
        session_ids = row.pop("session_ids")
        cost_ids = row.pop("cost_covered_ids")
        token_ids = row.pop("token_covered_ids")
        cache_ids = row.pop("cache_covered_ids")
        estimated_ids = row.pop("estimated_ids")
        total_sessions = len(session_ids)
        cost_covered = len(cost_ids)
        token_covered = len(token_ids)
        cache_covered = len(cache_ids)
        coverage = {
            "cost": {"covered_sessions": cost_covered, "total_sessions": total_sessions,
                     "complete": cost_covered == total_sessions},
            "tokens": {"covered_sessions": token_covered, "total_sessions": total_sessions,
                       "complete": token_covered == total_sessions},
            "cache": {"covered_sessions": cache_covered, "total_sessions": total_sessions,
                      "complete": cache_covered == total_sessions},
        }
        provenance = make_usage_provenance(
            session_ids, estimated_ids, cost_ids | token_ids,
            row.pop("estimated_cost"), row.pop("estimated_tokens"),
        )
        result.append({
            "day": day, "cost": row["cost"], "tokens": row["tokens"],
            "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
            "sessions": len(sessions),
            "projects": len(row["projects"]), "providers": providers,
            "coverage": coverage, "provenance": provenance,
            "usage_basis": provenance["usage_basis"],
            "availability": {
                "cost": cost_covered > 0, "tokens": token_covered > 0,
                "input_tokens": token_covered > 0, "output_tokens": token_covered > 0,
                "cache": cache_covered > 0, "throughput": token_covered > 0,
                "context": True, "timing": bool(row["wait_samples"]), "tool_results": True,
            },
            "wait_time": {
                "available": bool(row["wait_samples"]),
                "total_s": row["wait_s"],
                "avg_s": row["wait_s"] / row["wait_samples"] if row["wait_samples"] else 0,
                "max_s": row["longest_wait_s"],
                "sample_count": row["wait_samples"],
            },
            "top_sessions": sessions[:8],
        })
    return result


MATCHED_PACE_MIN_PAIRS = 20
MATCHED_PACE_MIN_COVERAGE = 0.30


def _pace_log_distance(left, right, max_distance):
    left = max(1.0, float(left or 0))
    right = max(1.0, float(right or 0))
    distance = abs(math.log(left / right, 2))
    return None if distance > max_distance else distance / max_distance


def pace_match_distance(left, right):
    """Return workload distance, or None when two completed turns are not comparable."""
    left_tools = int(left.get("tool_calls") or 0)
    right_tools = int(right.get("tool_calls") or 0)
    if bool(left_tools) != bool(right_tools):
        return None
    dimensions = [
        (_pace_log_distance(
            left.get("peak_input_tokens") or left.get("input_tokens"),
            right.get("peak_input_tokens") or right.get("input_tokens"), 2.0,
        ), 0.28),
        (_pace_log_distance(left.get("input_tokens"), right.get("input_tokens"), 3.0), 0.17),
        (_pace_log_distance(left.get("output_tokens"), right.get("output_tokens"), 2.0), 0.22),
        (_pace_log_distance(left.get("model_calls") or 1, right.get("model_calls") or 1, 2.0), 0.16),
    ]
    if left_tools:
        dimensions.append((_pace_log_distance(left_tools, right_tools, 2.0), 0.12))
    if any(distance is None for distance, _ in dimensions):
        return None
    left_input = max(1, int(left.get("input_tokens") or 0))
    right_input = max(1, int(right.get("input_tokens") or 0))
    left_cache = min(1.0, float(left.get("cache_read_tokens") or 0) / left_input)
    right_cache = min(1.0, float(right.get("cache_read_tokens") or 0) / right_input)
    cache_distance = abs(left_cache - right_cache)
    if cache_distance > 0.60:
        return None
    score = sum(distance * weight for distance, weight in dimensions)
    score += (cache_distance / 0.60) * 0.05
    recency_days = abs(float(left.get("ts") or 0) - float(right.get("ts") or 0)) / 86400.0
    score += min(1.0, recency_days / 90.0) * 0.03
    return score


def _percentile(values, quantile):
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    return ordered[min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))]


def _bootstrap_median_interval(values, seed_key, repetitions=400):
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return float(values[0]), float(values[0])
    seed = int(hashlib.sha256(str(seed_key).encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    medians = []
    for _ in range(repetitions):
        medians.append(statistics.median(rng.choice(values) for _ in values))
    return _percentile(medians, 0.025), _percentile(medians, 0.975)


def matched_pace_comparison(a_id, a_samples, b_id, b_samples):
    """Compare two model-runtime histories using deterministic workload matching."""
    def usable(sample):
        return (
            float(sample.get("duration_s") or 0) > 0
            and int(sample.get("input_tokens") or 0) > 0
            and int(sample.get("output_tokens") or 0) > 0
        )

    a_rows = [sample for sample in (a_samples or []) if usable(sample)]
    b_rows = [sample for sample in (b_samples or []) if usable(sample)]
    result = {
        "a_id": a_id, "b_id": b_id,
        "a_samples": len(a_rows), "b_samples": len(b_rows),
        "matched_pairs": 0, "coverage": 0.0, "pace_ratio": 0.0,
        "ci_low": 0.0, "ci_high": 0.0, "available": False,
    }
    smaller = min(len(a_rows), len(b_rows))
    if smaller < MATCHED_PACE_MIN_PAIRS:
        result["reason"] = f"needs {MATCHED_PACE_MIN_PAIRS} timed turns per runtime"
        return result

    candidates = []
    for a_index, left in enumerate(a_rows):
        for b_index, right in enumerate(b_rows):
            distance = pace_match_distance(left, right)
            if distance is not None:
                candidates.append((distance, abs(float(left.get("ts") or 0) - float(right.get("ts") or 0)),
                                   a_index, b_index))
    candidates.sort()
    used_a = set()
    used_b = set()
    ratios = []
    for _, _, a_index, b_index in candidates:
        if a_index in used_a or b_index in used_b:
            continue
        used_a.add(a_index)
        used_b.add(b_index)
        left_duration = float(a_rows[a_index].get("duration_s") or 0)
        right_duration = float(b_rows[b_index].get("duration_s") or 0)
        ratios.append(right_duration / left_duration)

    matched_pairs = len(ratios)
    coverage = matched_pairs / smaller if smaller else 0.0
    result["matched_pairs"] = matched_pairs
    result["coverage"] = coverage
    if ratios:
        result["pace_ratio"] = statistics.median(ratios)
    if matched_pairs >= MATCHED_PACE_MIN_PAIRS:
        result["ci_low"], result["ci_high"] = _bootstrap_median_interval(
            ratios, f"{a_id}|{b_id}|{matched_pairs}"
        )
    if matched_pairs < MATCHED_PACE_MIN_PAIRS:
        result["reason"] = f"only {matched_pairs} comparable turns; needs {MATCHED_PACE_MIN_PAIRS}"
    elif coverage < MATCHED_PACE_MIN_COVERAGE:
        result["reason"] = f"only {round(coverage * 100)}% of the smaller history overlaps"
    else:
        result["available"] = True
        result["reason"] = ""
    return result


def matched_pace_windows(sample_groups, now_ts=None):
    """Build pairwise matched-pace comparisons for every dashboard history window."""
    today = datetime.date.fromtimestamp(float(now_ts if now_ts is not None else time.time()))
    windows = {"7": 7, "30": 30, "90": 90, "all": None}
    result = {}
    ids = sorted(sample_groups)
    for window, days in windows.items():
        cutoff = (today - datetime.timedelta(days=days - 1)).isoformat() if days else ""
        groups = {
            row_id: [sample for sample in sample_groups[row_id]
                     if not cutoff or str(sample.get("day") or "") >= cutoff]
            for row_id in ids
        }
        comparisons = []
        for a_index, a_id in enumerate(ids):
            for b_id in ids[a_index + 1:]:
                comparisons.append(matched_pace_comparison(a_id, groups[a_id], b_id, groups[b_id]))
        result[window] = comparisons
    return {
        "method": "nearest workload match on context, input, output, cache, model calls, tools, and recency",
        "min_pairs": MATCHED_PACE_MIN_PAIRS,
        "min_coverage": MATCHED_PACE_MIN_COVERAGE,
        "windows": result,
    }


def _finalize_throughput_fields(row):
    """Add weighted speed and coverage fields to a model or model/day row."""
    tool_free_samples = int(row.get("tool_free_samples") or 0)
    timed_samples = int(row.get("timed_samples") or 0)
    if tool_free_samples and row.get("tool_free_seconds", 0) > 0:
        speed_output = int(row.get("tool_free_output_tokens") or 0)
        speed_seconds = float(row.get("tool_free_seconds") or 0)
        basis = "tool_free"
        sample_count = tool_free_samples
    elif timed_samples and row.get("timed_seconds", 0) > 0:
        speed_output = int(row.get("timed_output_tokens") or 0)
        speed_seconds = float(row.get("timed_seconds") or 0)
        basis = "end_to_end"
        sample_count = timed_samples
    else:
        speed_output = 0
        speed_seconds = 0.0
        basis = "unavailable"
        sample_count = 0
    total_output = int(row.get("output_tokens") or 0)
    row["output_tps"] = (speed_output / speed_seconds) if speed_seconds > 0 else 0
    row["throughput_basis"] = basis
    row["throughput_samples"] = sample_count
    row["timing_coverage"] = (speed_output / total_output) if total_output > 0 else 0
    ttft_samples = int(row.get("ttft_samples") or 0)
    row["avg_ttft_ms"] = (float(row.get("ttft_total_s") or 0) * 1000 / ttft_samples) if ttft_samples else 0
    wait_samples = int(row.get("wait_samples") or 0)
    row["avg_wait_s"] = (float(row.get("wait_seconds") or 0) / wait_samples) if wait_samples else 0
    durations = sorted(
        float(value) for value in (row.get("wait_durations_s") or [])
        if float(value or 0) > 0
    )
    p95_index = min(len(durations) - 1, max(0, math.ceil(len(durations) * 0.95) - 1)) if durations else 0
    row["median_wait_s"] = statistics.median(durations) if durations else 0
    row["p95_wait_s"] = durations[p95_index] if durations else 0
    workload_fields = {
        "workload_peak_inputs": "median_peak_input_tokens",
        "workload_outputs": "median_workload_output_tokens",
        "workload_tool_calls": "median_tool_calls",
        "workload_model_calls": "median_model_calls",
        "workload_cache_ratios": "median_cache_ratio",
    }
    for source_key, target_key in workload_fields.items():
        values = [float(value) for value in (row.get(source_key) or []) if float(value or 0) >= 0]
        row[target_key] = statistics.median(values) if values else 0
    return row


def aggregate_model_stats(session_rows):
    """Aggregate model I/O by runtime and build workload-matched pace comparisons."""
    models = {}
    pace_groups = defaultdict(list)

    def model_row(name, runtime):
        name = name or "unknown"
        runtime = runtime or "unknown runtime"
        row_id = f"{name}::{runtime}"
        return models.setdefault(row_id, {
            "id": row_id, "model": name, "runtime": runtime,
            "providers": set(), "logs": 0,
            "executions": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0,
            "timed_output_tokens": 0, "timed_seconds": 0.0, "timed_samples": 0,
            "tool_free_output_tokens": 0, "tool_free_seconds": 0.0, "tool_free_samples": 0,
            "ttft_total_s": 0.0, "ttft_samples": 0, "last_ts": 0, "daily": {},
            "wait_seconds": 0.0, "wait_samples": 0, "max_wait_s": 0.0,
            "wait_durations_s": [], "user_pause_seconds": 0.0,
            "workload_peak_inputs": [], "workload_outputs": [],
            "workload_tool_calls": [], "workload_model_calls": [],
            "workload_cache_ratios": [],
            "_log_ids": set(), "_cost_covered_ids": set(), "_token_covered_ids": set(),
            "_cache_covered_ids": set(), "_estimated_ids": set(),
            "_estimated_cost": 0.0, "_estimated_tokens": 0,
        })

    def daily_row(parent, day):
        return parent["daily"].setdefault(day, {
            "day": day, "input_tokens": 0, "output_tokens": 0,
            "executions": 0, "cost": 0.0,
            "timed_output_tokens": 0, "timed_seconds": 0.0, "timed_samples": 0,
            "tool_free_output_tokens": 0, "tool_free_seconds": 0.0, "tool_free_samples": 0,
            "ttft_total_s": 0.0, "ttft_samples": 0,
            "wait_seconds": 0.0, "wait_samples": 0, "max_wait_s": 0.0,
            "wait_durations_s": [], "user_pause_seconds": 0.0,
            "workload_peak_inputs": [], "workload_outputs": [],
            "workload_tool_calls": [], "workload_model_calls": [],
            "workload_cache_ratios": [],
            "_log_ids": set(), "_cost_covered_ids": set(), "_token_covered_ids": set(),
            "_cache_covered_ids": set(), "_estimated_ids": set(),
            "_estimated_cost": 0.0, "_estimated_tokens": 0,
        })

    def mark_model_coverage(target, session, session_id, availability=None):
        availability = availability or session.get("availability") or {}
        target["_log_ids"].add(session_id)
        if availability.get("cost") is not False and metric_available(session, "cost"):
            target["_cost_covered_ids"].add(session_id)
        if availability.get("tokens") is not False and metric_available(session, "tokens"):
            target["_token_covered_ids"].add(session_id)
        if availability.get("cache") is not False and metric_available(session, "cache"):
            target["_cache_covered_ids"].add(session_id)
        if session.get("token_estimate"):
            target["_estimated_ids"].add(session_id)

    for session in session_rows or []:
        provider = session.get("provider") or "unknown"
        runtime = session.get("runtime") or source_runtime_label(session)
        session_id = session.get("id") or session.get("path") or f"session-{id(session)}"
        for stats in session.get("model_stats") or []:
            row = model_row(stats.get("model"), runtime)
            row["providers"].add(provider)
            mark_model_coverage(row, session, session_id, stats.get("availability"))
            row["executions"] += int(stats.get("executions") or 0)
            row["input_tokens"] += int(stats.get("input_tokens") or 0)
            row["output_tokens"] += int(stats.get("output_tokens") or 0)
            row["cost"] += float(stats.get("cost") or 0)
            if session.get("token_estimate"):
                row["_estimated_cost"] += float(stats.get("cost") or 0)
                row["_estimated_tokens"] += int(stats.get("tokens") or 0)
        for stats in session.get("_model_daily") or []:
            day = stats.get("day") or ""
            if not day:
                continue
            row = model_row(stats.get("model"), runtime)
            row["providers"].add(provider)
            daily = daily_row(row, day)
            mark_model_coverage(row, session, session_id, stats.get("availability"))
            mark_model_coverage(daily, session, session_id, stats.get("availability"))
            for key in ("input_tokens", "output_tokens", "executions"):
                daily[key] += int(stats.get(key) or 0)
            daily["cost"] += float(stats.get("cost") or 0)
            if session.get("token_estimate"):
                daily["_estimated_cost"] += float(stats.get("cost") or 0)
                daily["_estimated_tokens"] += int(stats.get("input_tokens") or 0) + int(stats.get("output_tokens") or 0)
        for sample in session.get("_performance_samples") or []:
            row = model_row(sample.get("model"), runtime)
            row["providers"].add(provider)
            mark_model_coverage(row, session, session_id)
            day = sample.get("day") or ""
            targets = [row]
            if day:
                daily_target = daily_row(row, day)
                mark_model_coverage(daily_target, session, session_id)
                targets.append(daily_target)
            output_tokens = int(sample.get("output_tokens") or 0)
            duration_s = float(sample.get("duration_s") or 0)
            generation_s = float(sample.get("generation_s") or duration_s)
            ttft_s = float(sample.get("ttft_s") or 0)
            input_tokens = int(sample.get("input_tokens") or 0)
            peak_input_tokens = int(sample.get("peak_input_tokens") or input_tokens)
            tool_calls = int(sample.get("tool_calls") or 0)
            model_calls = max(1, int(sample.get("model_calls") or 1))
            cache_read_tokens = int(sample.get("cache_read_tokens") or 0)
            cache_ratio = min(1.0, cache_read_tokens / input_tokens) if input_tokens else 0.0
            for target in targets:
                target["timed_output_tokens"] += output_tokens
                target["timed_seconds"] += duration_s
                target["timed_samples"] += 1
                if int(sample.get("tool_calls") or 0) == 0:
                    target["tool_free_output_tokens"] += output_tokens
                    target["tool_free_seconds"] += generation_s
                    target["tool_free_samples"] += 1
                if ttft_s > 0:
                    target["ttft_total_s"] += ttft_s
                    target["ttft_samples"] += 1
                context_tokens = int(sample.get("context_tokens") or 0)
                if context_tokens > 0:
                    target["workload_peak_inputs"].append(context_tokens)
                    target["workload_tool_calls"].append(int(sample.get("tool_calls") or 0))
                    target["workload_model_calls"].append(
                        max(1, int(sample.get("model_calls") or 1))
                    )
                target["workload_peak_inputs"].append(peak_input_tokens)
                target["workload_outputs"].append(output_tokens)
                target["workload_tool_calls"].append(tool_calls)
                target["workload_model_calls"].append(model_calls)
                if metric_available(session, "cache"):
                    target["workload_cache_ratios"].append(cache_ratio)
            if (not session.get("token_estimate") and duration_s > 0
                    and input_tokens > 0 and output_tokens > 0):
                pace_groups[row["id"]].append({
                    "day": day, "ts": float(sample.get("ts") or 0),
                    "duration_s": duration_s, "input_tokens": input_tokens,
                    "peak_input_tokens": peak_input_tokens,
                    "output_tokens": output_tokens, "tool_calls": tool_calls,
                    "model_calls": model_calls, "cache_read_tokens": cache_read_tokens,
                })
            row["last_ts"] = max(float(row.get("last_ts") or 0), float(sample.get("ts") or 0))
        for sample in session.get("_wait_samples") or []:
            model = sample.get("model") or ""
            if not model or model in ("mixed", "unknown"):
                continue
            row = model_row(model, runtime)
            row["providers"].add(provider)
            mark_model_coverage(row, session, session_id)
            day = sample.get("day") or ""
            targets = [row]
            if day:
                daily_target = daily_row(row, day)
                mark_model_coverage(daily_target, session, session_id)
                targets.append(daily_target)
            duration_s = float(sample.get("duration_s") or 0)
            if duration_s <= 0:
                continue
            for target in targets:
                target["wait_seconds"] += duration_s
                target["wait_samples"] += 1
                target["max_wait_s"] = max(float(target.get("max_wait_s") or 0), duration_s)
                target["wait_durations_s"].append(duration_s)
                target["user_pause_seconds"] += float(sample.get("user_pause_s") or 0)
                ttft_s = float(sample.get("ttft_s") or 0)
                if ttft_s > 0:
                    target["ttft_total_s"] += ttft_s
                    target["ttft_samples"] += 1
                context_tokens = int(sample.get("context_tokens") or 0)
                if context_tokens > 0:
                    target["workload_peak_inputs"].append(context_tokens)
                    target["workload_tool_calls"].append(int(sample.get("tool_calls") or 0))
                    target["workload_model_calls"].append(
                        max(1, int(sample.get("model_calls") or 1))
                    )

    def finalize_coverage(row):
        log_ids = set(row.pop("_log_ids", set()))
        total_logs = len(log_ids)
        cost_ids = set(row.pop("_cost_covered_ids", set()))
        token_ids = set(row.pop("_token_covered_ids", set()))
        cache_ids = set(row.pop("_cache_covered_ids", set()))
        estimated_ids = set(row.pop("_estimated_ids", set()))
        covered_cost = len(cost_ids)
        covered_tokens = len(token_ids)
        covered_cache = len(cache_ids)
        row["logs"] = total_logs
        row["coverage"] = {
            "cost": {"covered_sessions": covered_cost, "total_sessions": total_logs,
                     "complete": covered_cost == total_logs},
            "tokens": {"covered_sessions": covered_tokens, "total_sessions": total_logs,
                       "complete": covered_tokens == total_logs},
            "cache": {"covered_sessions": covered_cache, "total_sessions": total_logs,
                      "complete": covered_cache == total_logs},
        }
        row["provenance"] = make_usage_provenance(
            log_ids, estimated_ids, cost_ids | token_ids,
            row.pop("_estimated_cost", 0), row.pop("_estimated_tokens", 0),
        )
        row["usage_basis"] = row["provenance"]["usage_basis"]
        row["availability"] = {
            "cost": covered_cost > 0,
            "tokens": covered_tokens > 0,
            "input_tokens": covered_tokens > 0,
            "output_tokens": covered_tokens > 0,
            "cache": covered_cache > 0,
            "throughput": covered_tokens > 0 and int(row.get("throughput_samples") or 0) > 0,
            "context": True,
            "timing": int(row.get("wait_samples") or 0) > 0,
            "tool_results": True,
        }
        return row

    result = []
    for row in models.values():
        if (not row.get("input_tokens") and not row.get("output_tokens")
                and (not row.get("executions") and not row.get("wait_samples")
                     or len(row.get("_token_covered_ids") or ()) == len(row.get("_log_ids") or ()))):
            continue
        daily = [finalize_coverage(_finalize_throughput_fields(item))
                 for item in row.pop("daily").values()]
        daily.sort(key=lambda item: item["day"])
        row["providers"] = sorted(row["providers"])
        row["daily"] = daily
        result.append(finalize_coverage(_finalize_throughput_fields(row)))
    result.sort(key=lambda row: (-row["output_tokens"], -row["input_tokens"],
                                 -row["executions"], -row["wait_samples"],
                                 row["model"], row["runtime"]))
    valid_ids = {row["id"] for row in result}
    return {
        "models": result,
        "total_models": len(result),
        "total_model_names": len({row["model"] for row in result}),
        "first_day": min((day["day"] for row in result for day in row["daily"]), default=""),
        "last_day": max((day["day"] for row in result for day in row["daily"]), default=""),
        "matched_pace": matched_pace_windows({
            row_id: samples for row_id, samples in pace_groups.items() if row_id in valid_ids
        }),
    }


def aggregate_frustration(session_rows, terms=None):
    """Aggregate lexical frustration evidence without retaining message content."""
    settings = frustration_settings()
    configured_terms = list(settings["terms"] if terms is None else terms)
    events = []
    for session in session_rows or []:
        runtime = session.get("runtime") or source_runtime_label(session)
        for source_event in session.get("_frustration_events") or []:
            event = dict(source_event)
            model = event.get("model") or "unknown"
            event["runtime"] = runtime
            event["model_id"] = f"{model}::{runtime}"
            events.append(event)
    result = rollup_frustration_events(events)
    result.update({
        "configured_terms": configured_terms,
        "default_terms": list(settings["defaults"]),
        "max_terms": settings["max_terms"],
        "affected_sessions": sum(
            1 for session in (session_rows or [])
            if ((session.get("frustration") or {}).get("utterances") or 0) > 0
        ),
        "sessions_with_user_turns": sum(
            1 for session in (session_rows or [])
            if ((session.get("frustration") or {}).get("user_turns") or 0) > 0
        ),
        "method": "case-insensitive whole-term match; one matched user turn equals one utterance",
    })
    return result


def metric_coverage(rows, metric):
    rows = list(rows or [])
    covered = sum(1 for row in rows if metric_available(row, metric))
    return {
        "covered_sessions": covered,
        "total_sessions": len(rows),
        "complete": covered == len(rows),
    }


def cross_session():
    now = time.time()
    if _xsess["data"] and (now - _xsess["at"] < _XSESS_TTL):
        return _xsess["data"]

    sessions = []
    internal_rows = []
    model_name_cost = defaultdict(float)
    model_mix_rows = {}
    day_cost = defaultdict(float)
    provider_cost, provider_sessions = defaultdict(float), defaultdict(int)
    provider_rows = defaultdict(list)

    for source in all_session_sources():
        row = session_summary(source)
        if row["turns"] == 0:
            continue
        internal_rows.append(row)
        sessions.append({key: value for key, value in row.items() if not key.startswith("_")})
        provider_cost[row["provider"]] += row["cost"]
        provider_sessions[row["provider"]] += 1
        provider_rows[row["provider"]].append(row)
        runtime = row.get("runtime") or source_runtime_label(row)
        row_model_cost = row.get("_model_cost", {})
        row_model_tokens = row.get("_model_tok", {})
        for model in set(row_model_cost) | set(row_model_tokens):
            key = f"{model}::{runtime}"
            item = model_mix_rows.setdefault(key, {
                "id": key, "model": model, "runtime": runtime,
                "providers": set(), "cost": 0.0, "tokens": 0,
                "session_ids": set(), "cost_ids": set(), "token_ids": set(),
                "estimated_ids": set(), "estimated_cost": 0.0, "estimated_tokens": 0,
            })
            cost_value = float(row_model_cost.get(model) or 0)
            token_value = int(row_model_tokens.get(model) or 0)
            item["providers"].add(row.get("provider") or "unknown")
            item["cost"] += cost_value
            item["tokens"] += token_value
            item["session_ids"].add(row["id"])
            if metric_available(row, "cost"):
                item["cost_ids"].add(row["id"])
            if metric_available(row, "tokens"):
                item["token_ids"].add(row["id"])
            if row.get("token_estimate"):
                item["estimated_ids"].add(row["id"])
                item["estimated_cost"] += cost_value
                item["estimated_tokens"] += token_value
            model_name_cost[model] += cost_value
        for day, val in row.get("_day_cost", {}).items():
            day_cost[day] += val

    sessions.sort(key=lambda s: -s["mtime"])
    _xsess["sessions"] = sessions
    mm = []
    for item in model_mix_rows.values():
        provenance = make_usage_provenance(
            item.pop("session_ids"), item.pop("estimated_ids"),
            item.pop("cost_ids") | item.pop("token_ids"),
            item.pop("estimated_cost"), item.pop("estimated_tokens"),
        )
        item["providers"] = sorted(item["providers"])
        item["provenance"] = provenance
        item["usage_basis"] = provenance["usage_basis"]
        mm.append(item)
    mm.sort(key=lambda item: (-item["cost"], -item["tokens"], item["id"]))
    daily = daily_summaries(internal_rows)
    daily_by_day = {row["day"]: row for row in daily}
    days = sorted(day_cost)
    trend = []
    for day in days[-14:]:
        daily_row = daily_by_day.get(day) or {}
        provenance = daily_row.get("provenance") or make_usage_provenance((), (), ())
        estimated_cost = float(provenance.get("estimated_cost") or 0)
        trend.append({
            "day": day, "cost": day_cost[day],
            "reported_cost": max(0.0, day_cost[day] - estimated_cost),
            "estimated_cost": estimated_cost,
            "provenance": provenance,
            "usage_basis": provenance.get("usage_basis") or "unavailable",
        })
    alert_costs = [item["reported_cost"] for item in trend]
    med = sorted(alert_costs)[len(alert_costs) // 2] if alert_costs else 0
    for item in trend:
        item["anomaly"] = bool(med and item["reported_cost"] > 2.5 * med)
        item["anomaly_basis"] = "reported_only"

    total = sum(item["cost"] for item in mm)
    premium = (model_name_cost.get("claude-opus-4-8", 0)
               + model_name_cost.get("claude-fable-5", 0)
               + model_name_cost.get("gpt-5.5", 0))
    tool_waste = global_tool_waste(internal_rows)
    coverage = {
        "cost": metric_coverage(internal_rows, "cost"),
        "tokens": metric_coverage(internal_rows, "tokens"),
        "cache": metric_coverage(internal_rows, "cache"),
    }
    provenance = usage_provenance(internal_rows)
    global_wait = wait_time_summary([
        sample
        for row in internal_rows
        for sample in (row.get("_wait_samples") or [])
    ])
    data = {
        "generated_at": int(now),
        "sessions": sessions[:60],
        "model_mix": mm,
        "trend": trend,
        "total_cost": total,
        "reported_cost": max(0.0, total - provenance["estimated_cost"]),
        "estimated_cost": provenance["estimated_cost"],
        "total_sessions": len(sessions),
        "total_executions": sum(int(row.get("turns") or 0) for row in internal_rows),
        "total_tokens": sum(int(row.get("tokens") or 0) for row in internal_rows),
        "coverage": coverage, "provenance": provenance,
        "usage_basis": provenance["usage_basis"],
        "availability": {
            "cost": coverage["cost"]["covered_sessions"] > 0,
            "tokens": coverage["tokens"]["covered_sessions"] > 0,
            "input_tokens": coverage["tokens"]["covered_sessions"] > 0,
            "output_tokens": coverage["tokens"]["covered_sessions"] > 0,
            "cache": coverage["cache"]["covered_sessions"] > 0,
            "throughput": coverage["tokens"]["covered_sessions"] > 0,
            "context": True, "timing": bool(global_wait.get("available")), "tool_results": True,
        },
        "wait_time": global_wait,
        "opus_share": (premium / total) if total else 0.0,
        "premium_share": (premium / total) if total else 0.0,
        "providers": sorted([
            {
                "provider": k, "cost": provider_cost[k], "sessions": provider_sessions[k],
                "coverage": {
                    "cost": metric_coverage(provider_rows[k], "cost"),
                    "tokens": metric_coverage(provider_rows[k], "tokens"),
                },
                "availability": {
                    "cost": any(metric_available(row, "cost") for row in provider_rows[k]),
                    "tokens": any(metric_available(row, "tokens") for row in provider_rows[k]),
                },
                "provenance": usage_provenance(provider_rows[k]),
                "usage_basis": usage_provenance(provider_rows[k])["usage_basis"],
            }
            for k in provider_cost
        ], key=lambda r: -r["cost"]),
        "model_stats": aggregate_model_stats(internal_rows),
        "frustration": aggregate_frustration(internal_rows),
        "model_pricing": model_pricing_settings(),
        "tool_waste": tool_waste,
        "daily": daily,
        "capabilities": capability_inventory(tool_waste),
        "session_actions": session_action_capability(),
    }
    _xsess["data"], _xsess["at"] = data, now
    return data


def log_sessions_state():
    """Return the complete lightweight log inventory outside the polled state payload."""
    cross = cross_session()
    sessions = list(_xsess.get("sessions") or cross.get("sessions") or [])
    return {
        "generated_at": cross.get("generated_at"),
        "sessions": sessions,
        "total_sessions": int(cross.get("total_sessions") or len(sessions)),
    }


def enqueue_latest(q_, data):
    """Keep a slow SSE client subscribed by replacing queued stale snapshots."""
    try:
        q_.put_nowait(data)
        return True
    except queue.Full:
        try:
            while True:
                q_.get_nowait()
        except queue.Empty:
            pass
        try:
            q_.put_nowait(data)
        except queue.Full:
            pass
        return True
    except Exception:
        return False


def publish(state):
    global STATE
    STATE = state
    data = "data: " + json.dumps(state) + "\n\n"
    with subscribers_lock:
        dead = []
        for q_ in subscribers:
            if not enqueue_latest(q_, data):
                dead.append(q_)
        for d in dead:
            subscribers.remove(d)


def source_mtime_signature(sources):
    """Track additions, removals, and updates across every discovered log."""
    return tuple(sorted(
        (str(source.get("path") or ""),
         float(source.get("signature_mtime") or source.get("mtime") or 0))
        for source in (sources or [])
        if source.get("path")
    ))


def refresh_cross_session_state(state=None, builder=None, publisher=None):
    """Force a fresh cross-log snapshot and publish it with the live state."""
    builder = builder or cross_session
    publisher = publisher or publish
    _xsess["data"], _xsess["at"] = None, 0.0
    cross = builder()
    base = dict(state or STATE or {})
    if base:
        publisher(attach_cross_session(base, cross))
    return cross


def publish_after_session_delete():
    """Publish a fresh remaining-session state immediately after deletion."""
    sources = all_session_sources()
    next_source = max(sources, key=lambda row: row.get("mtime") or 0) if sources else None
    cross = cross_session()
    if next_source:
        state = recompute(next_source)
        if state:
            publish(attach_cross_session(state, cross))
            return next_source.get("id")
    publish({
        "ok": False,
        "message": "No Claude, Codex, or Cursor logs found yet.",
        "source": {},
        "total_cost": 0,
        "total_tokens": 0,
        "turns": 0,
        "xsession": cross,
    })
    return None


def current_state():
    if STATE:
        return STATE
    source = newest_source()
    st = recompute(source) if source else None
    if st:
        return attach_cross_session(st)
    return {
        "ok": False,
        "message": "No Claude, Codex, or Cursor logs found yet.",
        "source": {},
        "total_cost": 0,
        "total_tokens": 0,
        "turns": 0,
        "context": {},
        "insights": [],
    }


AGENT_RESULT_MAX_CHARS = 8000
AGENT_CHECK_FOCUS = {"continue", "cost", "context", "tools", "next_phase"}
AGENT_USAGE_WINDOWS = {"today": 1, "7d": 7, "14d": 14}
AGENT_USAGE_FOCUS = {"spend", "models", "tools", "changes"}


def agent_as_of():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def agent_project_key(value):
    value = str(value or "").strip()
    if not value or value == "No project":
        return ""
    return os.path.normcase(os.path.realpath(os.path.expanduser(value)))


def agent_project_name(value):
    value = str(value or "").strip().rstrip("/\\")
    if not value or value == "No project":
        return "No project"
    return compact_text(value.replace("\\", "/").rsplit("/", 1)[-1], 52)


def agent_provider(value):
    value = str(value or "").strip().lower()
    if value.startswith("claude"):
        return "claude"
    if value.startswith("codex"):
        return "codex"
    if value.startswith("cursor"):
        return "cursor"
    return ""


def resolve_agent_source(session_id=None, caller=None, sources=None):
    """Resolve a safe current-run target without crossing a caller's runtime/project."""
    sources = list(sources if sources is not None else all_session_sources())
    requested = str(session_id or "").strip()
    if requested:
        source = find_session(requested, sources=sources)
        if not source:
            return None, "The requested Token Meter session was not found."
        return source, "explicit"

    caller = caller or {}
    provider = agent_provider(caller.get("runtime") or caller.get("provider"))
    project = agent_project_key(caller.get("project") or caller.get("cwd"))
    candidates = [row for row in sources if not provider or row.get("provider") == provider]
    if project:
        exact_matches = []
        ancestor_matches = []
        for row in candidates:
            candidate = agent_project_key(row.get("project"))
            if not candidate:
                continue
            if candidate == project:
                exact_matches.append(row)
            elif project.startswith(candidate + os.sep):
                ancestor_matches.append((candidate, row))
        matches = exact_matches
        if not matches and ancestor_matches:
            nearest_length = max(len(candidate) for candidate, _ in ancestor_matches)
            matches = [row for candidate, row in ancestor_matches if len(candidate) == nearest_length]
        if not matches:
            runtime = ("Codex" if provider == "codex" else "Claude" if provider == "claude"
                       else "Cursor" if provider == "cursor" else "agent")
            return None, f"No {runtime} run matched the caller's current project."
        candidates = matches
    if not candidates:
        return None, "No matching Claude, Codex, or Cursor run was found."
    selected = max(candidates, key=lambda row: float(row.get("mtime") or 0))
    mtime = float(selected.get("mtime") or 0)
    if not mtime or time.time() - mtime > AGENT_CURRENT_MAX_AGE_S:
        runtime = ("Codex" if provider == "codex" else "Claude" if provider == "claude"
                   else "Cursor" if provider == "cursor" else "agent")
        return None, f"No recent {runtime} run matched the caller's current project."
    return selected, "matched"


def agent_session_summary(source):
    return {
        "id": source.get("id"),
        "provider": source.get("provider"),
        "client": source.get("client") or source.get("provider"),
        "project": agent_project_name(source.get("project")),
    }


def agent_dashboard_url(session_id=None, panel="summary"):
    base = f"http://127.0.0.1:{PORT}"
    if session_id:
        return f"{base}/sessions/{quote(str(session_id), safe='')}#{panel}"
    return f"{base}/#{panel}"


def compact_agent_value(value, depth=0):
    if depth > 5:
        return None
    if isinstance(value, str):
        return compact_text(value, 500)
    if isinstance(value, list):
        return [compact_agent_value(item, depth + 1) for item in value[:10]]
    if isinstance(value, dict):
        return {str(key): compact_agent_value(item, depth + 1)
                for key, item in list(value.items())[:50]}
    return value


def bounded_agent_result(result):
    """Keep a model-facing result useful even if an upstream label grows unexpectedly."""
    result = dict(result or {})
    result["evidence"] = list(result.get("evidence") or [])[:3]
    if isinstance(result.get("candidates"), list):
        result["candidates"] = result["candidates"][:5]
    result.setdefault("truncated", False)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= AGENT_RESULT_MAX_CHARS:
        return result
    result = compact_agent_value(result)
    result["truncated"] = True
    result["evidence"] = list(result.get("evidence") or [])[:2]
    if isinstance(result.get("candidates"), list):
        result["candidates"] = result["candidates"][:3]
    if isinstance(result.get("categories"), list):
        result["categories"] = result["categories"][:3]
    for key in ("answer", "caveat", "recommended_action"):
        if isinstance(result.get(key), str):
            result[key] = compact_text(result[key], 240)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > AGENT_RESULT_MAX_CHARS:
        result.pop("categories", None)
        result.pop("candidates", None)
        result.pop("execution", None)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > AGENT_RESULT_MAX_CHARS:
        result["evidence"] = []
    return result


def agent_no_session(message, panel="summary"):
    return bounded_agent_result({
        "ok": False,
        "answer": message,
        "verdict": {"key": "unavailable", "label": "Run not matched", "severity": "idle"},
        "evidence": [],
        "recommended_action": "Open Token Meter and choose the intended run, then ask again with its session id.",
        "caveat": "Token Meter did not fall back to another project because that could expose the wrong run.",
        "dashboard_url": agent_dashboard_url(panel=panel),
        "as_of": agent_as_of(),
        "data_scope": "matched_current_run",
        "approximate_fields": [],
    })


def safe_execution_trace(state, execution_idx, limit=5):
    allowed = {"tool_call", "tool_result", "usage", "complete", "context", "reasoning", "coordination", "start"}
    out = []
    for event in state.get("trace") or []:
        if event.get("execution") != execution_idx or event.get("kind") not in allowed:
            continue
        item = {"kind": event.get("kind"), "label": compact_text(event.get("label") or "Activity", 64)}
        if event.get("kind") == "tool_result" and event.get("tokens") is not None:
            item["returned_tokens"] = int(event.get("tokens") or 0)
        if event.get("cost"):
            item["cost"] = round(float(event.get("cost") or 0), 4)
        out.append(item)
    return out[:limit]


def agent_check(focus="continue", execution=None, session_id=None, caller=None):
    focus = str(focus or "continue").strip().lower()
    if focus not in AGENT_CHECK_FOCUS:
        raise ValueError(f"focus must be one of: {', '.join(sorted(AGENT_CHECK_FOCUS))}")
    if execution is not None:
        try:
            execution = int(execution)
        except (TypeError, ValueError):
            raise ValueError("execution must be a positive integer")
        if execution < 1:
            raise ValueError("execution must be a positive integer")

    source, resolution = resolve_agent_source(session_id=session_id, caller=caller)
    if not source:
        return agent_no_session(resolution)
    state = recompute(source)
    if not state:
        return agent_no_session("Token Meter found the run but could not read its metrics.")

    recommendation = menubar_recommendation(state)
    verdict = menubar_verdict(state, recommendation)
    context = state.get("context") or {}
    tools = state.get("tools") or {}
    executions = state.get("executions") or []
    last_execution = executions[-1] if executions else {}
    requested_execution = None
    if execution is not None:
        requested_execution = next((row for row in executions if int(row.get("idx") or 0) == execution), None)
        if requested_execution is None:
            raise ValueError(f"execution {execution} is not available in the retained run history")
        last_execution = requested_execution

    total_cost = round(float(state.get("total_cost") or 0), 4)
    availability = state.get("availability") or {}
    cost_available = metric_available(state, "cost")
    tokens_available = metric_available(state, "tokens")
    tool_results_available = metric_available(state, "tool_results")
    context_pct = context.get("latest_pct")
    context_text = f"{context_pct * 100:.0f}%" if context_pct is not None else "not reported"
    tool_tokens = int(tools.get("total_output_tokens") or 0)
    flagged_tokens = int(tools.get("flagged_tokens") or 0)
    selected_tool_tokens = int((last_execution.get("tokens") or {}).get("retrieval") or 0)
    selected_execution_label = "Selected execution" if requested_execution is not None else "Latest execution"
    cursor_estimate = source.get("provider") == "cursor"
    evidence_pool = {
        "cost": ({"label": "Local run cost estimate" if cursor_estimate else "Estimated run cost",
                  "value": total_cost, "unit": "USD"}
                 if cost_available else
                 {"label": "Run cost", "value": "unavailable",
                  "reason": "No supported rate or input-context evidence"}),
        "last_cost": ({"label": f"{selected_execution_label} cost" +
                                 (" estimate" if cursor_estimate else ""),
                       "value": round(float(last_execution.get("cost") or 0), 4), "unit": "USD"}
                      if cost_available else
                      {"label": f"{selected_execution_label} cost", "value": "unavailable"}),
        "context": {"label": "Current context use", "value": context_text},
        "latest_tools": ({"label": f"{selected_execution_label} tool results",
                          "value": selected_tool_tokens, "unit": "estimated tokens"}
                         if tool_results_available else
                         {"label": f"{selected_execution_label} tool results", "value": "unavailable"}),
        "run_tools": ({"label": "Run-wide trace-observed tool results", "value": tool_tokens,
                       "unit": "estimated tokens", "flagged_tokens": flagged_tokens}
                      if tool_results_available else
                      {"label": "Run-wide tool results", "value": "unavailable"}),
        "turns": {"label": "Executions", "value": int(state.get("turns") or len(executions))},
    }
    order = {
        "continue": ("context", "last_cost", "latest_tools"),
        "cost": ("cost", "last_cost", "turns"),
        "context": ("context", "turns", "cost"),
        "tools": ("run_tools", "latest_tools", "context"),
        "next_phase": ("context", "cost", "run_tools"),
    }[focus]
    evidence = [evidence_pool[key] for key in order]
    action = recommendation.get("label") or "Review the selected run"
    if recommendation.get("detail"):
        action = f"{action}: {recommendation['detail']}"
    selected = agent_session_summary(source)
    answer = verdict.get("detail") or recommendation.get("detail") or "Token Meter found no immediate intervention signal."
    result = {
        "ok": True,
        "answer": answer,
        "verdict": {key: verdict.get(key) for key in ("key", "label", "severity", "detail")},
        "evidence": evidence,
        "recommended_action": compact_text(action, 220),
        "caveat": ("Cursor input is a local one-context-snapshot-per-execution proxy; output uses trace-visible model text. Cost applies the persisted model variant's public rate. Cache, hidden reasoning, repeated internal model-call input, and authoritative dashboard billing are excluded."
                   if cursor_estimate else
                   "Costs are estimates based on public API rates." if state.get("cost_approx") else
                   "Tool-result volume is trace-observed and may not include content the client did not log."),
        "dashboard_url": agent_dashboard_url(source.get("id"), recommendation.get("target") or "summary"),
        "as_of": agent_as_of(),
        "data_scope": "matched_current_run",
        "approximate_fields": (["cost"] if state.get("cost_approx") and cost_available else []) +
                              (["input_tokens", "output_tokens", "output_pace"] if cursor_estimate and tokens_available else []) +
                              (["tool_result_tokens"] if cursor_estimate and tool_results_available else []),
        "availability": availability,
        "selected_session": selected,
        "selection": resolution,
    }
    if requested_execution is not None:
        result["execution"] = {
            "index": execution,
            "cost": round(float(requested_execution.get("cost") or 0), 4) if cost_available else None,
            "tokens": ({
                "input": int((requested_execution.get("tokens") or {}).get("input") or 0),
                "output": int((requested_execution.get("tokens") or {}).get("output") or 0),
                "retrieval": int((requested_execution.get("tokens") or {}).get("retrieval") or 0),
            } if tokens_available else {"available": False,
                                        "retrieval_estimate": selected_tool_tokens if tool_results_available else None}),
            "context_pct": requested_execution.get("context_pct"),
            "activity": safe_execution_trace(state, execution),
        }
        if cursor_estimate:
            result["execution"]["estimated"] = True
    return bounded_agent_result(result)


def agent_usage(window="7d", focus="changes"):
    window = str(window or "7d").strip().lower()
    focus = str(focus or "changes").strip().lower()
    if window not in AGENT_USAGE_WINDOWS:
        raise ValueError("window must be one of: today, 7d, 14d")
    if focus not in AGENT_USAGE_FOCUS:
        raise ValueError(f"focus must be one of: {', '.join(sorted(AGENT_USAGE_FOCUS))}")
    cross = cross_session()
    days = sorted(cross.get("daily") or [], key=lambda row: row.get("day") or "", reverse=True)
    today = time.strftime("%Y-%m-%d", time.localtime())
    if window == "today":
        selected = [row for row in days if row.get("day") == today]
    else:
        selected = days[:AGENT_USAGE_WINDOWS[window]]
    total_cost = sum(float(row.get("cost") or 0) for row in selected)
    sessions = sum(int(row.get("sessions") or 0) for row in selected)
    tool_tokens = sum(int(row.get("tool_tokens") or 0) for row in selected)
    flagged_tokens = sum(int(row.get("flagged_tokens") or 0) for row in selected)
    providers = defaultdict(float)
    for row in selected:
        for provider in row.get("providers") or []:
            if metric_available(provider, "cost"):
                providers[provider.get("provider") or "unknown"] += float(provider.get("cost") or 0)
    provider_rank = sorted(providers.items(), key=lambda item: (-item[1], item[0]))[:5]
    model_rank = [
        {"model": row.get("model"), "cost": round(float(row.get("cost") or 0), 4), "tokens": int(row.get("tokens") or 0)}
        for row in (cross.get("model_mix") or [])[:5]
    ]
    tool_rank = [
        {"name": row.get("display") or row.get("name"), "namespace": row.get("namespace"),
         "returned_tokens": int(row.get("output_tokens") or 0), "calls": int(row.get("calls") or 0)}
        for row in ((cross.get("tool_waste") or {}).get("by_name") or [])
        if not row.get("diagnostic")
    ][:5]

    newest = selected[0] if selected else {}
    previous = selected[1] if len(selected) > 1 else {}
    newest_cost = float(newest.get("cost") or 0)
    previous_cost = float(previous.get("cost") or 0)
    delta = ((newest_cost - previous_cost) / previous_cost) if previous_cost else None
    cost_complete = all(((row.get("coverage") or {}).get("cost") or {}).get("complete", True)
                        for row in selected)
    evidence_pool = {
        "spend": {"label": f"Estimated spend ({window})" + ("" if cost_complete else " · partial coverage"),
                  "value": round(total_cost, 4), "unit": "USD", "complete": cost_complete},
        "sessions": {"label": "Daily run count summed", "value": sessions},
        "tools": {"label": "Trace-observed tool results", "value": tool_tokens, "unit": "tokens", "flagged_tokens": flagged_tokens},
        "change": {"label": "Latest day vs prior day", "value": f"{delta * 100:+.0f}%" if delta is not None else "No prior-day baseline"},
        "provider": {"label": "Largest runtime by spend", "value": provider_rank[0][0] if provider_rank else "No data",
                     "cost": round(provider_rank[0][1], 4) if provider_rank else 0},
    }
    order = {
        "spend": ("spend", "change", "provider"),
        "models": ("spend", "provider", "change"),
        "tools": ("tools", "spend", "change"),
        "changes": ("change", "spend", "tools"),
    }[focus]
    if not selected:
        answer = f"Token Meter has no aggregate usage for {window}."
        action = "Run Claude, Codex, or Cursor, then ask again after Token Meter observes a local trace."
        assessment = "No data"
    elif delta is not None and delta >= 0.25:
        answer = f"The latest day is {delta * 100:.0f}% more expensive than the prior recorded day."
        action = "Review the Daily view and the largest model or tool category before the next phase."
        assessment = "Spend increased"
    elif flagged_tokens and flagged_tokens / max(1, tool_tokens) >= 0.25:
        answer = "Tool-result volume is the clearest efficiency signal in this window."
        action = "Review the largest returned-token category and narrow repeated or oversized results."
        assessment = "Tool output needs review"
    else:
        answer = (f"Estimated spend among cost-covered logs is ${total_cost:.2f} across the selected {window} window"
                  + ("; one or more traces lack pricing evidence." if not cost_complete else ", with no strong change signal."))
        action = "Keep the current approach and compare again after another recorded day."
        assessment = "Stable"
    approximate = any(bool(row.get("cost_approx")) for row in (cross.get("sessions") or []))
    result = {
        "ok": bool(selected),
        "answer": answer,
        "assessment": assessment,
        "evidence": [evidence_pool[key] for key in order],
        "recommended_action": action,
        "caveat": ("History is aggregate-only; run titles, project names, session ids, and paths are omitted. "
                   + ("Cursor values are local context-and-visible-output proxies, not dashboard billing. "
                      if any(row.get("provider") == "cursor" for row in (cross.get("sessions") or [])) else "")
                   + ("Spend is partial because one or more traces lack pricing evidence."
                      if not cost_complete else "")),
        "coverage": {"cost_complete": cost_complete},
        "dashboard_url": agent_dashboard_url(panel="daily"),
        "as_of": agent_as_of(),
        "data_scope": "anonymous_aggregate_history",
        "approximate_fields": ["cost"] if approximate else [],
        "window": window,
        "days_observed": len(selected),
    }
    if focus == "models":
        result["categories"] = model_rank
    elif focus == "tools":
        result["categories"] = tool_rank
    else:
        result["categories"] = [{"provider": name, "cost": round(value, 4)} for name, value in provider_rank]
    return bounded_agent_result(result)


def agent_capabilities(scope="current", limit=5, caller=None):
    scope = str(scope or "current").strip().lower()
    if scope not in ("current", "all"):
        raise ValueError("scope must be one of: current, all")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer from 1 to 5")
    if limit < 1 or limit > 5:
        raise ValueError("limit must be an integer from 1 to 5")
    cross = cross_session()
    capabilities = cross.get("capabilities") or {}
    selected = None
    if scope == "current":
        source, resolution = resolve_agent_source(caller=caller)
        if not source:
            return agent_no_session(resolution, panel="capabilities")
        state = recompute(source)
        if not state:
            return agent_no_session("Token Meter found the run but could not read its capability evidence.", panel="capabilities")
        summary = session_optional_capabilities(state, capabilities)
        groups = summary.get("groups") or []
        selected = agent_session_summary(source)
    else:
        summary = ((capabilities.get("summary") or {}).get("optional") or {})
        groups = capabilities.get("control_groups") or []
    groups = [row for row in groups if row.get("name") != "tokenmeter" and row.get("namespace") != "tokenmeter"]
    groups.sort(key=lambda row: (
        bool(row.get("current_used") if scope == "current" else row.get("used")),
        -int(row.get("current_unused_eager_definition_tokens") or row.get("unused_eager_definition_tokens") or 0),
        str(row.get("name") or ""),
    ))
    candidates = []
    for row in groups[:limit]:
        used = bool(row.get("current_used") if scope == "current" else row.get("used"))
        candidate = {
            "name": compact_text(row.get("name") or "Unknown", 80),
            "type": row.get("control_type") or "capability",
            "runtime": row.get("runtime") or "",
            "used": used,
            "observed_uses": int(row.get("current_activations") or row.get("activations") or row.get("calls") or 0),
        }
        overhead = int(row.get("current_unused_eager_definition_tokens") or row.get("unused_eager_definition_tokens") or 0)
        if overhead:
            candidate["avoidable_eager_tokens"] = overhead
        candidates.append(candidate)
    enabled = int(summary.get("enabled") or 0)
    unused = int(summary.get("unused") or 0)
    if unused:
        answer = f"{unused} of {enabled} removable capability groups have no observed use in this scope."
        action = "Review the named candidates in Tools & Skills; only disable a group after confirming you do not need it."
        assessment = "Review available"
    else:
        answer = "No unused removable capability group was found in this scope."
        action = "Keep the current setup and review again after more representative work."
        assessment = "No cleanup needed"
    result = {
        "ok": True,
        "answer": answer,
        "assessment": assessment,
        "evidence": [
            {"label": "Enabled removable groups", "value": enabled},
            {"label": "Groups without observed use", "value": unused},
        ],
        "candidates": candidates,
        "recommended_action": action,
        "caveat": "Capability evidence names user-installed skill packs but never returns configuration values, environment variables, credentials, tool arguments, or tool results.",
        "dashboard_url": agent_dashboard_url(panel="capabilities"),
        "as_of": agent_as_of(),
        "data_scope": "named_capability_evidence",
        "approximate_fields": [],
        "scope": scope,
    }
    if selected:
        result["selected_session"] = selected
    return bounded_agent_result(result)


def compact_duration_ms(ms):
    try:
        seconds = max(0, int(ms) / 1000.0)
    except Exception:
        return ""
    if seconds < 1:
        return f"{int(seconds * 1000)}ms"
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{int(round(seconds))}s"
    minutes, sec = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def menubar_activity(st):
    trace = st.get("trace") or []
    preferred = {
        "tool_call", "tool_result", "message", "reasoning", "complete",
        "context", "goal", "start", "user",
    }
    event = None
    for ev in reversed(trace[-24:]):
        if ev.get("kind") in preferred:
            event = ev
            break
    if event is None and trace:
        event = trace[-1]
    if not event:
        return {
            "kind": "idle",
            "title": "Waiting for activity",
            "detail": "No trace events yet.",
            "time": "",
            "execution": None,
        }

    kind = event.get("kind") or "activity"
    label = event.get("label") or kind.replace("_", " ").title()
    detail = event.get("detail") or ""
    tool = event.get("tool")
    execution = event.get("execution")
    duration_ms = event.get("duration_ms")

    if kind == "tool_call":
        title = f"Running {label}"
    elif kind == "tool_result":
        title = f"Received {label}"
    elif kind == "reasoning":
        title = "Reasoning"
    elif kind == "message":
        title = label if label != "Agent update" else "Agent update"
    elif kind == "complete":
        title = f"Completed #{execution}" if execution else "Execution complete"
    elif kind == "context":
        title = label
    elif kind == "user":
        title = "User message"
    else:
        title = label

    bits = []
    duration = compact_duration_ms(duration_ms)
    if not duration and isinstance(detail, str) and detail.endswith("ms") and detail[:-2].isdigit():
        duration = compact_duration_ms(detail[:-2])
    if duration:
        bits.append(f"in {duration}" if kind == "complete" else duration)
    elif detail:
        bits.append(detail)
    if execution:
        if kind == "complete":
            pass
        else:
            bits.append(f"#{execution}")
    if tool and tool not in title:
        bits.append(tool)
    if event.get("cost"):
        bits.append(f"${event['cost']:.3f}")
    detail = " · ".join(bits)
    return {
        "kind": kind,
        "title": compact_text(title, 64),
        "detail": compact_text(detail, 120),
        "time": event.get("time") or "",
        "execution": execution,
        "tool": tool,
    }


def menubar_recommendation(st):
    context = st.get("context") or {}
    pct = context.get("latest_pct") or 0
    insights = st.get("insights") or []
    warn = next((i for i in insights if i.get("kind") == "warn"), None)
    last_cost = st.get("last_turn_cost") or 0
    cost_available = metric_available(st, "cost") and not st.get("token_estimate")
    low_yield_actionable = (not st.get("token_estimate") and
                            low_yield_should_warn(st.get("executions") or [], pct))

    if st.get("ended"):
        return {
            "label": "Pinned log",
            "detail": "This is a frozen log view; return to live to follow newest activity.",
            "severity": "idle",
            "target": "summary",
        }
    if pct >= MENUBAR_CONTEXT_INTERVENE_PCT:
        return {
            "label": "Compact now",
            "detail": f"Context is {pct * 100:.0f}% of the model window.",
            "severity": "bad",
            "target": "activity",
        }
    if cost_available and last_cost >= MENUBAR_COST_SPIKE:
        return {
            "label": "Review spike",
            "detail": f"Last execution cost ${last_cost:.2f}.",
            "severity": "bad",
            "target": "activity",
        }
    if low_yield_actionable:
        return {
            "label": "Summarize soon",
            "detail": "Latest execution replayed large context for low output.",
            "severity": "warn",
            "target": "activity",
        }
    if pct >= MENUBAR_CONTEXT_WATCH_PCT:
        return {
            "label": "Summarize soon",
            "detail": f"Context is {pct * 100:.0f}%; prepare before 85%.",
            "severity": "warn",
            "target": "activity",
        }
    if pct >= MENUBAR_CONTEXT_SOFT_PCT:
        return {
            "label": "Watch context",
            "detail": f"Context is {pct * 100:.0f}% of the model window.",
            "severity": "idle",
            "target": "summary",
        }
    if warn:
        return {
            "label": "Check signal",
            "detail": warn.get("text") or "A warning signal is active.",
            "severity": "warn",
            "target": "insights",
        }
    return {
        "label": "Let it run",
        "detail": "No immediate intervention needed.",
        "severity": "good",
        "target": "summary",
    }


def menubar_verdict(st, recommendation):
    context = st.get("context") or {}
    reported_pct = context.get("latest_pct")
    pct = reported_pct or 0
    last_cost = st.get("last_turn_cost") or 0
    cost_available = metric_available(st, "cost") and not st.get("token_estimate")
    insights = st.get("insights") or []
    operational_warn = next((i for i in insights if i.get("kind") == "warn" and is_operational_warning(i)), None)

    def payload(key, detail):
        labels = {
            "healthy": ("Healthy", "TM", "good"),
            "watch": ("Watch closely", "TM !", "warn"),
            "intervene": ("Intervene now", "TM !!", "bad"),
            "idle": ("Idle", "TM idle", "idle"),
        }
        label, prefix, severity = labels[key]
        return {"key": key, "label": label, "prefix": prefix, "severity": severity, "detail": detail}

    if st.get("ended"):
        return payload("idle", "This is a frozen log view; return to live to follow newest activity.")
    if pct >= MENUBAR_CONTEXT_INTERVENE_PCT:
        return payload(
            "intervene",
            f"Context is {pct * 100:.0f}% of the model window; compact now.",
        )
    if cost_available and last_cost >= MENUBAR_COST_SPIKE:
        return payload(
            "intervene",
            f"Last execution cost ${last_cost:.2f}; review the spike before continuing.",
        )
    if pct >= MENUBAR_CONTEXT_WATCH_PCT:
        return payload(
            "watch",
            f"Context is {pct * 100:.0f}% of the model window; prepare to summarize before 85%.",
        )
    if operational_warn:
        detail = operational_warn.get("text") or recommendation.get("detail") or "An operational warning is active."
        return payload("watch", detail)

    detail = (f"Context is {pct * 100:.0f}% and no operational warning needs intervention."
              if reported_pct is not None else
              "Context percentage is not reported; no operational warning needs intervention.")
    return payload("healthy", detail)


class QuotaUnavailable(Exception):
    """A safe, user-facing reason that a provider quota cannot be read."""


def quota_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def quota_timestamp(value):
    number = quota_number(value)
    if number is not None:
        if number > 10_000_000_000:
            number /= 1000.0
        return number if number > 0 else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.timestamp()
    except (ValueError, OverflowError):
        return None


def quota_compact_duration(seconds):
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h" + (f" {minutes}m" if minutes else "")
    days, hours = divmod(hours, 24)
    return f"{days}d" + (f" {hours}h" if hours else "")


def quota_pace(window, now=None):
    """Compare provider-reported use with even consumption across a real window."""
    now = float(now if now is not None else time.time())
    used = quota_number((window or {}).get("used_percent"))
    duration = quota_number((window or {}).get("window_seconds"))
    reset_at = quota_timestamp((window or {}).get("reset_at"))
    if used is None or duration is None or duration <= 0 or reset_at is None:
        return None
    remaining_time = reset_at - now
    if remaining_time <= 0 or remaining_time > duration:
        return None
    elapsed = duration - remaining_time
    if elapsed <= 0 or elapsed / duration < 0.03:
        return None

    actual = max(0.0, min(100.0, used))
    expected = max(0.0, min(100.0, elapsed / duration * 100.0))
    delta = actual - expected
    if delta > 4:
        state = "deficit"
        lead = f"{abs(delta):.0f}% in deficit"
    elif delta < -4:
        state = "reserve"
        lead = f"{abs(delta):.0f}% in reserve"
    else:
        state = "on_pace"
        lead = "on pace"

    runs_out_at = None
    will_last = False
    if actual >= 100:
        summary = "exhausted"
        runs_out_at = now
    elif actual <= 0:
        will_last = True
        summary = f"{lead} · lasts until reset"
    else:
        rate = actual / elapsed
        run_out_in = (100.0 - actual) / rate if rate > 0 else None
        if run_out_in is None or run_out_in >= remaining_time:
            will_last = True
            summary = f"{lead} · lasts until reset"
        else:
            runs_out_at = now + run_out_in
            summary = f"{lead} · runs out in {quota_compact_duration(run_out_in)}"
    return {
        "state": state,
        "expected_used_percent": round(expected, 1),
        "delta_percent": round(delta, 1),
        "will_last_to_reset": will_last,
        "runs_out_at": round(runs_out_at, 3) if runs_out_at is not None else None,
        "summary": summary,
    }


def quota_window(provider, window_id, kind, label, used_percent,
                 window_seconds=None, reset_at=None, now=None):
    used = quota_number(used_percent)
    if used is None:
        return None
    duration = quota_number(window_seconds)
    reset = quota_timestamp(reset_at)
    row = {
        "id": str(window_id or f"{provider}-{kind}"),
        "kind": str(kind or "extra"),
        "label": str(label or kind or "Quota"),
        "used_percent": round(max(0.0, min(100.0, used)), 2),
        "window_seconds": int(duration) if duration is not None and duration > 0 else None,
        "reset_at": round(reset, 3) if reset is not None else None,
    }
    row["pace"] = quota_pace(row, now=now)
    return row


def quota_provider(provider, label, status, source, windows=None, plan="", error="",
                   coverage_note=""):
    return {
        "id": provider,
        "label": label,
        "status": status,
        "plan": str(plan or ""),
        "source": source,
        "provenance": "provider_reported" if windows else "unavailable",
        "windows": [row for row in (windows or []) if row],
        "error": compact_text(str(error or ""), 180),
        "coverage_note": compact_text(str(coverage_note or ""), 180),
    }


def quota_coverage_note(provider, missing, qualifier=""):
    missing = [str(value).strip() for value in (missing or []) if str(value).strip()]
    if not missing:
        return ""
    names = missing[0] if len(missing) == 1 else " and ".join(missing)
    subject = " ".join(value for value in (str(qualifier or "").strip(), names) if value)
    subject = subject[:1].upper() + subject[1:]
    noun = "limit" if len(missing) == 1 else "limits"
    verb = "was" if len(missing) == 1 else "were"
    return f"{subject} {noun} {verb} not reported by {provider}; missing does not mean 0%."


def quota_slug(value):
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug[:64] or "quota"


def provider_cli_path(name):
    resolved = shutil.which(name)
    if resolved:
        return resolved
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".local", "bin", name),
        os.path.join(home, ".volta", "bin", name),
        os.path.join(home, ".asdf", "shims", name),
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
    ]
    candidates.extend(sorted(
        glob.glob(os.path.join(home, ".nvm", "versions", "node", "*", "bin", name)),
        key=safe_mtime, reverse=True,
    ))
    return next((path for path in candidates if os.path.isfile(path) and os.access(path, os.X_OK)), None)


def quota_http_json(url, headers=None, timeout=QUOTA_HTTP_TIMEOUT_S, opener=None):
    request = Request(url, headers=headers or {}, method="GET")
    try:
        response = (opener or urlopen)(request, timeout=timeout)
        with response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise QuotaUnavailable("Provider quota response was too large.")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise QuotaUnavailable("Provider returned an invalid quota response.")
        return value
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise QuotaUnavailable("Provider authentication needs to be refreshed.") from None
        if exc.code == 429:
            raise QuotaUnavailable("Provider quota service is rate limited; Token Meter will retry.") from None
        raise QuotaUnavailable(f"Provider quota request failed (HTTP {exc.code}).") from None
    except (URLError, TimeoutError):
        raise QuotaUnavailable("Provider quota request could not connect.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise QuotaUnavailable("Provider returned an invalid quota response.") from None


def _rpc_read_response(process, selector, request_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = selector.select(max(0.0, deadline - time.monotonic()))
        if not events:
            break
        line = process.stdout.readline()
        if not line:
            break
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") != request_id:
            continue
        if message.get("error"):
            raise QuotaUnavailable("Codex could not read account quotas.")
        return message.get("result") or {}
    raise QuotaUnavailable("Codex quota request timed out.")


def codex_app_server_rate_limits(timeout=QUOTA_PROCESS_TIMEOUT_S):
    executable = provider_cli_path("codex")
    if not executable:
        raise QuotaUnavailable("Codex CLI is not installed.")
    try:
        process = subprocess.Popen(
            [executable, "-s", "read-only", "-a", "untrusted", "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, env=agent_client_environment(executable),
        )
    except OSError:
        raise QuotaUnavailable("Codex could not start its account quota service.") from None
    selector = selectors.DefaultSelector()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)

        def send(message):
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()

        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"clientInfo": {"name": "token-meter", "version": "0.1"}},
        })
        _rpc_read_response(process, selector, 1, timeout)
        send({"jsonrpc": "2.0", "method": "initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"})
        return _rpc_read_response(process, selector, 2, timeout)
    except (BrokenPipeError, OSError):
        raise QuotaUnavailable("Codex could not start its account quota service.") from None
    finally:
        selector.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)


def _codex_limit_label(value):
    text = str(value or "").strip()
    if not text:
        return "Additional"
    if "spark" in text.lower():
        return "Spark"
    text = re.sub(r"(?i)^gpt[- ]?[\w.]+[- ]?codex[- ]?", "", text)
    text = re.sub(r"(?i)[- ]preview$", "", text).replace("-", " ").strip()
    return compact_text(text.title() or "Additional", 32)


def _codex_window(provider_id, raw, kind, label, now=None):
    if not isinstance(raw, dict):
        return None
    duration = quota_number(raw.get("limit_window_seconds"))
    if duration is None:
        minutes = quota_number(raw.get("windowDurationMins") or raw.get("window_duration_mins"))
        duration = minutes * 60 if minutes is not None else None
    return quota_window(
        "codex", f"codex-{provider_id}-{kind}", kind, label,
        raw.get("used_percent") if "used_percent" in raw else raw.get("usedPercent"),
        window_seconds=duration,
        reset_at=raw.get("reset_at") if "reset_at" in raw else raw.get("resetsAt"),
        now=now,
    )


def parse_codex_quota(payload, source="Codex app-server", now=None):
    root = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    main = root.get("rateLimits") or root.get("rate_limit") or root.get("rateLimitsByLimitId", {}).get("codex") or {}
    plan = main.get("planType") or main.get("plan_type") or root.get("plan_type") or ""
    windows, seen = [], set()

    def add(row):
        if row and row["id"] not in seen:
            seen.add(row["id"])
            windows.append(row)

    main_session = _codex_window(
        "main", main.get("primary") or main.get("primary_window"), "session", "Session", now=now,
    )
    main_weekly = _codex_window(
        "main", main.get("secondary") or main.get("secondary_window"), "weekly", "Weekly", now=now,
    )
    add(main_session)
    add(main_weekly)
    missing_main = [
        label for label, window in (("Session", main_session), ("Weekly", main_weekly)) if not window
    ]
    coverage_note = quota_coverage_note("Codex", missing_main, qualifier="regular")

    additional = []
    by_id = root.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        additional.extend((key, value) for key, value in by_id.items() if key != "codex" and isinstance(value, dict))
    for idx, value in enumerate(root.get("additional_rate_limits") or []):
        if isinstance(value, dict):
            additional.append((value.get("metered_feature") or f"extra-{idx}", value))

    for limit_id, value in additional:
        nested = value.get("rate_limit") if isinstance(value.get("rate_limit"), dict) else value
        prefix = _codex_limit_label(value.get("limitName") or value.get("limit_name") or limit_id)
        stable = quota_slug(limit_id)
        add(_codex_window(stable, nested.get("primary") or nested.get("primary_window"),
                          "session", f"{prefix} session", now=now))
        add(_codex_window(stable, nested.get("secondary") or nested.get("secondary_window"),
                          "weekly", f"{prefix} weekly", now=now))

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


def codex_oauth_quota(now=None, opener=None):
    try:
        with open(CODEX_AUTH, encoding="utf-8") as fh:
            auth = json.load(fh)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        raise QuotaUnavailable("Codex is not signed in locally.") from None
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    if not access_token:
        raise QuotaUnavailable("Codex is not signed in locally.")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "TokenMeter/0.1",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = str(account_id)
    payload = quota_http_json(
        "https://chatgpt.com/backend-api/wham/usage", headers=headers, opener=opener,
    )
    return parse_codex_quota(payload, source="Codex OAuth API", now=now)


def load_codex_quota(now=None):
    try:
        return parse_codex_quota(codex_app_server_rate_limits(), now=now)
    except QuotaUnavailable:
        return codex_oauth_quota(now=now)


def claude_auth_status(timeout=3.0):
    executable = provider_cli_path("claude")
    if not executable:
        return {}
    try:
        result = subprocess.run(
            [executable, "auth", "status", "--json"], capture_output=True,
            text=True, timeout=timeout, check=False,
            env=agent_client_environment(executable),
        )
        value = json.loads(result.stdout or "{}")
        return value if isinstance(value, dict) else {}
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return {}


def claude_oauth_credentials(auth_status=None, timeout=3.0):
    data = None
    try:
        with open(CLAUDE_CREDENTIALS, "rb") as fh:
            data = fh.read(1024 * 1024 + 1)
    except (FileNotFoundError, OSError):
        pass
    if data is None:
        status = auth_status or {}
        if not status.get("loggedIn") or str(status.get("authMethod") or "").lower() == "third_party":
            raise QuotaUnavailable("Claude OAuth credentials are not available.")
        try:
            result = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, timeout=timeout, check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            raise QuotaUnavailable("Claude OAuth credentials are not available.") from None
        if result.returncode != 0:
            raise QuotaUnavailable("Claude OAuth credentials are not available.")
        data = result.stdout
    if len(data) > 1024 * 1024:
        raise QuotaUnavailable("Claude OAuth credentials are invalid.")
    try:
        root = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise QuotaUnavailable("Claude OAuth credentials are invalid.") from None
    oauth = root.get("claudeAiOauth") if isinstance(root, dict) else None
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        raise QuotaUnavailable("Claude OAuth credentials are not available.")
    expires_at = quota_timestamp(oauth.get("expiresAt"))
    if expires_at is not None and expires_at <= time.time() + 60:
        raise QuotaUnavailable("Claude OAuth credentials need to be refreshed.")
    scopes = oauth.get("scopes") or []
    if scopes and "user:profile" not in scopes:
        raise QuotaUnavailable("Claude OAuth credentials do not include quota access.")
    return oauth


def _claude_window(field, raw, kind, label, duration, now=None):
    if not isinstance(raw, dict):
        return None
    return quota_window(
        "claude", f"claude-{field.replace('_', '-')}", kind, label,
        raw.get("utilization"), window_seconds=duration,
        reset_at=raw.get("resets_at"), now=now,
    )


def parse_claude_quota(payload, credentials=None, now=None):
    windows, seen = [], set()

    def add(row):
        if row and row["id"] not in seen:
            seen.add(row["id"])
            windows.append(row)

    main_session = _claude_window(
        "five_hour", payload.get("five_hour"), "session", "Session", 5 * 60 * 60, now=now,
    )
    main_weekly = _claude_window(
        "seven_day", payload.get("seven_day"), "weekly", "Weekly", 7 * 24 * 60 * 60, now=now,
    )
    add(main_session)
    add(main_weekly)
    missing_main = [
        label for label, window in (("Session", main_session), ("Weekly", main_weekly)) if not window
    ]
    coverage_note = quota_coverage_note("Claude", missing_main)
    named = [
        ("seven_day_opus", "Opus weekly"),
        ("seven_day_sonnet", "Sonnet weekly"),
        ("seven_day_routines", "Routines weekly"),
        ("seven_day_cowork", "Routines weekly"),
    ]
    for field, label in named:
        add(_claude_window(field, payload.get(field), "weekly", label, 7 * 24 * 60 * 60, now=now))
    for idx, limit in enumerate(payload.get("limits") or []):
        if not isinstance(limit, dict) or limit.get("is_active") is False:
            continue
        if limit.get("kind") != "weekly_scoped" and limit.get("group") != "weekly":
            continue
        scope = limit.get("scope") if isinstance(limit.get("scope"), dict) else {}
        model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
        name = model.get("display_name") or model.get("id") or f"Scoped {idx + 1}"
        add(quota_window(
            "claude", f"claude-weekly-{quota_slug(name)}", "weekly", f"{name} weekly",
            limit.get("percent"), window_seconds=7 * 24 * 60 * 60,
            reset_at=limit.get("resets_at"), now=now,
        ))
    meta = credentials or {}
    plan = meta.get("subscriptionType") or meta.get("rateLimitTier") or ""
    if not windows:
        return quota_provider(
            "claude", "Claude", "unavailable", "Claude OAuth API", plan=plan,
            error="This Claude account does not report quota windows.",
            coverage_note=coverage_note,
        )
    return quota_provider(
        "claude", "Claude", "ok", "Claude OAuth API", windows=windows, plan=plan,
        coverage_note=coverage_note,
    )


def load_claude_quota(now=None, opener=None):
    status = claude_auth_status()
    if str(status.get("authMethod") or "").lower() == "third_party":
        provider = str(status.get("apiProvider") or "third-party")
        return quota_provider(
            "claude", "Claude", "unavailable", "Claude third-party authentication",
            plan=provider.title(), error="Claude account quotas are not exposed for this provider.",
            coverage_note=quota_coverage_note("Claude", ["Session", "Weekly"]),
        )
    credentials = claude_oauth_credentials(auth_status=status)
    payload = quota_http_json(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization": f"Bearer {credentials['accessToken']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "TokenMeter/0.1",
        },
        opener=opener,
    )
    return parse_claude_quota(payload, credentials=credentials, now=now)


def cursor_auth_session(now=None, db_path=None):
    try:
        with _cursor_db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM ItemTable WHERE key = ? LIMIT 1",
                ("cursorAuth/accessToken",),
            ).fetchone()
    except (sqlite3.Error, OSError):
        raise QuotaUnavailable("Cursor is not signed in locally.") from None
    if not row:
        raise QuotaUnavailable("Cursor is not signed in locally.")
    access_token = row[0]
    if isinstance(access_token, memoryview):
        access_token = access_token.tobytes()
    if isinstance(access_token, bytes):
        access_token = access_token.decode("utf-8", "replace")
    if not isinstance(access_token, str) or not access_token.strip():
        raise QuotaUnavailable("Cursor is not signed in locally.")
    try:
        parts = access_token.split(".")
        encoded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
        user_id = str(payload.get("sub") or "").split("|")[-1]
        expires_at = quota_timestamp(payload.get("exp"))
    except (IndexError, ValueError, UnicodeError, json.JSONDecodeError):
        raise QuotaUnavailable("Cursor local authentication is invalid.") from None
    if not user_id or not re.fullmatch(r"[A-Za-z0-9._-]+", user_id):
        raise QuotaUnavailable("Cursor local authentication is invalid.")
    if expires_at is None or expires_at <= float(now if now is not None else time.time()) + 60:
        raise QuotaUnavailable("Cursor authentication needs to be refreshed.")
    return access_token, user_id


def parse_cursor_quota(payload, now=None):
    individual = payload.get("individualUsage") if isinstance(payload.get("individualUsage"), dict) else {}
    team = payload.get("teamUsage") if isinstance(payload.get("teamUsage"), dict) else {}
    candidates = [
        (individual.get("plan"), "Plan"),
        (individual.get("overall"), "Plan"),
        (team.get("pooled"), "Team plan"),
    ]
    selected, label = None, "Plan"
    for value, candidate_label in candidates:
        if not isinstance(value, dict):
            continue
        used, limit = quota_number(value.get("used")), quota_number(value.get("limit"))
        if used is not None and limit is not None and limit > 0:
            selected, label = (used, limit), candidate_label
            break
    plan = str(payload.get("membershipType") or payload.get("limitType") or "")
    coverage_note = quota_coverage_note("Cursor", ["Session", "Weekly"])
    if not selected:
        reason = ("Cursor reports an unlimited plan without a usage cap."
                  if payload.get("isUnlimited") else
                  "This Cursor account does not report a capped plan window.")
        return quota_provider(
            "cursor", "Cursor", "unavailable", "Cursor account API", plan=plan, error=reason,
            coverage_note=coverage_note,
        )
    start = quota_timestamp(payload.get("billingCycleStart"))
    end = quota_timestamp(payload.get("billingCycleEnd"))
    duration = end - start if start is not None and end is not None and end > start else None
    used, limit = selected
    window = quota_window(
        "cursor", "cursor-plan", "monthly", label, used / limit * 100.0,
        window_seconds=duration, reset_at=end, now=now,
    )
    return quota_provider(
        "cursor", "Cursor", "ok", "Cursor account API", windows=[window], plan=plan,
        coverage_note=coverage_note,
    )


def load_cursor_quota(now=None, opener=None):
    access_token, user_id = cursor_auth_session(now=now)
    payload = quota_http_json(
        "https://cursor.com/api/usage-summary",
        headers={
            "Accept": "application/json",
            "Cookie": f"WorkosCursorSessionToken={user_id}%3A%3A{access_token}",
            "User-Agent": "TokenMeter/0.1",
        },
        opener=opener,
    )
    return parse_cursor_quota(payload, now=now)


def _quota_loading_row(provider):
    labels = {"claude": "Claude", "codex": "Codex", "cursor": "Cursor"}
    return quota_provider(
        provider, labels[provider], "loading", "Provider account", error="Loading provider quotas.",
    )


def _quota_failure_row(provider, error, now):
    safe_error = str(error) if isinstance(error, QuotaUnavailable) else "Provider quota refresh failed."
    with _quota_lock:
        previous = copy.deepcopy(_quota_cache.get(provider))
    if previous and previous.get("windows"):
        previous["status"] = "error"
        previous["error"] = compact_text(safe_error, 180)
        previous["attempted_at"] = now
        return previous
    labels = {"claude": "Claude", "codex": "Codex", "cursor": "Cursor"}
    row = quota_provider(provider, labels[provider], "error", "Provider account", error=safe_error)
    row["fetched_at"] = now
    row["attempted_at"] = now
    return row


def refresh_provider_quota(provider, loader, now=None):
    now = float(now if now is not None else time.time())
    try:
        row = loader(now=now)
        if not isinstance(row, dict):
            raise QuotaUnavailable("Provider returned an invalid quota response.")
        row = copy.deepcopy(row)
        row["fetched_at"] = now
        row["attempted_at"] = now
        row.setdefault("error", "")
    except Exception as exc:
        row = _quota_failure_row(provider, exc, now)
    with _quota_lock:
        _quota_cache[provider] = row
        _quota_inflight.discard(provider)
    return copy.deepcopy(row)


def _quota_refresh_worker(provider, loader):
    refresh_provider_quota(provider, loader)


def provider_quota_snapshots(now=None, loaders=None, start_refresh=True):
    now = float(now if now is not None else time.time())
    loaders = loaders or {
        "claude": load_claude_quota,
        "codex": load_codex_quota,
        "cursor": load_cursor_quota,
    }
    to_start = []
    with _quota_lock:
        for provider, loader in loaders.items():
            cached = _quota_cache.get(provider)
            last_attempt = (cached or {}).get("attempted_at") or (cached or {}).get("fetched_at") or 0
            if start_refresh and provider not in _quota_inflight and now - last_attempt >= QUOTA_REFRESH_S:
                _quota_inflight.add(provider)
                to_start.append((provider, loader))
        cached_rows = {provider: copy.deepcopy(_quota_cache.get(provider)) for provider in loaders}

    for provider, loader in to_start:
        threading.Thread(
            target=_quota_refresh_worker, args=(provider, loader),
            name=f"token-meter-quota-{provider}", daemon=True,
        ).start()

    out = []
    for provider in ("claude", "codex", "cursor"):
        if provider not in loaders:
            continue
        row = cached_rows.get(provider) or _quota_loading_row(provider)
        fetched_at = quota_timestamp(row.get("fetched_at"))
        age = max(0.0, now - fetched_at) if fetched_at is not None else None
        row["age_seconds"] = int(age) if age is not None else None
        row["stale"] = bool(age is not None and age >= QUOTA_STALE_S)
        if row["stale"] and row.get("windows"):
            row["status"] = "stale"
        for window in row.get("windows") or []:
            window["pace"] = quota_pace(window, now=now)
        out.append(row)
    return out


def reset_provider_quota_cache():
    with _quota_lock:
        _quota_cache.clear()
        _quota_inflight.clear()


def menubar_session_name(source):
    title = compact_text(source.get("title") or "", 52).strip()
    if title and title.lower() not in ("untitled", "untitled session"):
        return title
    project = str(source.get("project") or "").rstrip("/\\")
    if project and project != "No project":
        return compact_text(project.replace("\\", "/").rsplit("/", 1)[-1], 52)
    return str(source.get("id") or "session")[:12]


def menubar_recent_sessions(sources, selected_id=None, limit=5):
    ordered, seen = [], set()
    for source in sorted(sources or [], key=lambda row: -(row.get("mtime") or 0)):
        sid = str(source.get("id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        ordered.append(source)

    selected = next((row for row in ordered if row.get("id") == selected_id), None)
    choices = ordered[:max(0, limit)]
    if selected and selected not in choices and limit > 0:
        choices = [selected] + [row for row in ordered if row is not selected][:limit - 1]

    return [{
        "id": row.get("id"),
        "provider": row.get("provider"),
        "client": row.get("client") or row.get("provider"),
        "label": row.get("label"),
        "name": menubar_session_name(row),
        "project": row.get("project") or "",
        "mtime": row.get("mtime") or 0,
    } for row in choices]


def menubar_state(session_id=None):
    requested_id = str(session_id or "").strip()
    sources = all_session_sources()
    selected_source = find_session(requested_id, sources=sources) if requested_id else None
    missing = bool(requested_id and not selected_source)
    # The watcher owns the first full recompute. Calling current_state() here
    # before it publishes would make every two-second native poll independently
    # rebuild all cross-session history and starve the cold-start worker.
    st = recompute(selected_source) if selected_source else (STATE or {
        "ok": False,
        "message": "Token Meter is loading local session history.",
        "source": {},
        "context": {},
        "cache": {},
        "throughput": {},
        "executions": [],
        "insights": [],
    })
    if selected_source and not st:
        missing = True
        selected_source = None
        st = current_state()
    source = st.get("source") or {}
    context = st.get("context") or {}
    cache = st.get("cache") or {}
    throughput = st.get("throughput") or {}
    activity = menubar_activity(st)
    recommendation = menubar_recommendation(st)
    verdict = menubar_verdict(st, recommendation)
    availability = st.get("availability") or metric_availability(st.get("provider"))
    selected_id = source.get("id")
    model = next((row.get("model") for row in reversed(st.get("executions") or [])
                  if row.get("model")), None) or source.get("model") or "unknown"
    return {
        "ok": bool(st.get("source")),
        "provider": st.get("provider"),
        "availability": availability,
        "model": model,
        "source": {
            "label": source.get("label"),
            "id": source.get("id"),
            "project": source.get("project"),
            "pricing_note": source.get("pricing_note"),
            "approximate_cost": source.get("approximate_cost"),
            "token_estimate": source.get("token_estimate"),
            "availability": source.get("availability") or availability,
        },
        "session": st.get("session"),
        "project": st.get("project") or source.get("project"),
        "total_cost": st.get("total_cost", 0),
        "cost_approx": st.get("cost_approx", False),
        "total_tokens": st.get("total_tokens", 0),
        "turns": st.get("turns", 0),
        "throughput": {
            "available": bool(throughput.get("available")),
            "output_tps": throughput.get("output_tps", 0),
            "basis": throughput.get("basis") or "unavailable",
            "sample_count": throughput.get("sample_count", 0),
            "timing_coverage": throughput.get("timing_coverage", 0),
        },
        "cache": {
            "available": bool(availability.get("cache", True)),
            "fresh": cache.get("fresh", 0),
            "read": cache.get("read", 0),
            "write": cache.get("write", 0),
            "total": cache.get("total", 0),
            "input_total": cache.get("input_total", 0),
            "hit_ratio": cache.get("hit_ratio", 0),
            "input_share": cache.get("input_share", 0),
            "saved": cache.get("saved", 0),
            "cost": cache.get("cost", 0),
            "latest": cache.get("latest") or {},
        },
        "context": {
            "latest": context.get("latest"),
            "window": context.get("window"),
            "latest_pct": context.get("latest_pct"),
        },
        "last_turn_cost": st.get("last_turn_cost", 0),
        "idle_s": st.get("idle_s", 0),
        "ended": st.get("ended", False),
        "activity": activity,
        "recommendation": recommendation,
        "verdict": verdict,
        "insights": (st.get("insights") or [])[:4],
        "selection": {
            "requested_id": requested_id or None,
            "selected_id": selected_id,
            "pinned": bool(requested_id and selected_source),
            "missing": missing,
        },
        "recent_sessions": menubar_recent_sessions(
            sources, selected_id=requested_id if selected_source else selected_id, limit=5
        ),
        "provider_quotas": provider_quota_snapshots(),
        "ts": st.get("ts"),
    }


def watcher():
    cur, last_sig = None, None
    last_sources_sig = None
    cross_dirty = False
    last_cross_refresh = 0.0
    while True:
        sources = all_session_sources()
        nf = max(sources, key=lambda source: source["mtime"]) if sources else None
        sources_sig = source_mtime_signature(sources)
        if sources_sig != last_sources_sig:
            cross_dirty = True
            last_sources_sig = sources_sig
        if nf and (not cur or nf["path"] != cur["path"]):
            cur, last_sig = nf, None
        elif nf and cur and nf["path"] == cur["path"]:
            cur = nf
        updated_state = None
        if cur:
            sig = float(cur.get("signature_mtime") or cur.get("mtime") or safe_mtime(cur["path"]))
            if not sig:
                cur = None
                time.sleep(0.5)
                continue
            if sig != last_sig:
                last_sig = sig
                updated_state = recompute(cur)
                if updated_state:
                    cache_at = _xsess.get("at") or 0.0
                    publish(attach_cross_session(updated_state))
                    if (_xsess.get("at") or 0.0) > cache_at:
                        cross_dirty = False
                        last_cross_refresh = time.monotonic()
        now = time.monotonic()
        if cross_dirty and now - last_cross_refresh >= _XSESS_LIVE_REFRESH_S:
            refresh_cross_session_state(updated_state or STATE)
            cross_dirty = False
            last_cross_refresh = now
        time.sleep(0.5)


def page_candidates():
    paths = []
    explicit = os.environ.get("TOKEN_METER_PAGE")
    if explicit:
        paths.append(os.path.abspath(os.path.expanduser(explicit)))
    paths.extend([
        os.path.join(os.path.dirname(os.path.realpath(__file__)), "page.html"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html"),
        os.path.join(os.getcwd(), "page.html"),
    ])

    out, seen = [], set()
    for path in paths:
        if path not in seen:
            out.append(path)
            seen.add(path)
    return out


PAGE_CANDIDATES = page_candidates()


def page_path():
    for path in PAGE_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def is_dashboard_page_path(req_path):
    return req_path == "/" or bool(re.fullmatch(r"/sessions/[^/]{1,240}/?", req_path or ""))


def missing_page_html():
    candidates = "\n".join(
        f"<li><code>{html.escape(path)}</code></li>" for path in PAGE_CANDIDATES
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Token Meter setup error</title>
  <style>
    body {{ font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; max-width: 760px; }}
    code {{ background: #f3f4f6; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>page.html is missing</h1>
  <p>Token Meter needs the dashboard file <code>page.html</code> alongside <code>meter.py</code>, or in the directory where you start the server.</p>
  <p>Run from a full repository clone with <code>./scripts/start-token-meter</code>, or copy <code>page.html</code> from the repo into the same folder as <code>meter.py</code>.</p>
  <p>Looked in:</p>
  <ul>{candidates}</ul>
</body>
</html>"""


class H(BaseHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", status=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        req_path = urlparse(self.path).path
        if is_dashboard_page_path(req_path):
            path = page_path()
            body = b"" if path else missing_page_html().encode()
            self.send_response(200 if path else 503)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(os.path.getsize(path) if path else len(body)))
            self.end_headers()
        else:
            self.send_error(404)

    def do_POST(self):
        req_path = urlparse(self.path).path
        if req_path not in ("/capability/toggle", "/capability/disable-unused",
                            "/agent-access/toggle", "/session/delete",
                            "/settings/frustration", "/settings/model-pricing"):
            self.send_error(404)
            return
        origin = self.headers.get("Origin") or ""
        if origin and (urlparse(origin).hostname or "") not in ("localhost", "127.0.0.1", "::1"):
            self._send(json.dumps({"ok": False, "error": "Local dashboard origin required."}),
                       "application/json", status=403)
            return
        content_type = self.headers.get("Content-Type") or ""
        if not content_type.startswith("application/json"):
            self._send(json.dumps({"ok": False, "error": "JSON request required."}),
                       "application/json", status=415)
            return
        action_token = self.headers.get("X-Token-Meter-Action") or ""
        if not action_token or not secrets.compare_digest(action_token, _ACTION_TOKEN):
            self._send(json.dumps({"ok": False, "error": "Invalid action token."}),
                       "application/json", status=403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 4096:
            self._send(json.dumps({"ok": False, "error": "Invalid request size."}),
                       "application/json", status=400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(json.dumps({"ok": False, "error": "Invalid JSON."}),
                       "application/json", status=400)
            return
        if req_path == "/settings/frustration":
            result = set_frustration_terms(payload.get("terms"))
            if result.get("ok"):
                _summary_cache.clear()
                cross = refresh_cross_session_state()
                result["frustration"] = cross.get("frustration") or {}
            self._send(json.dumps(result), "application/json",
                       status=200 if result.get("ok") else 400)
            return
        if req_path == "/settings/model-pricing":
            result = set_model_price(
                payload.get("provider"),
                payload.get("model"),
                payload.get("prices"),
                remove=payload.get("remove") is True,
            )
            if result.get("ok"):
                _summary_cache.clear()
                _xsess["data"], _xsess["at"] = None, 0.0
                current_id = ((STATE.get("source") or {}).get("id") if STATE else "")
                source = find_session(current_id) if current_id else newest_source()
                updated = recompute(source) if source else None
                cross = refresh_cross_session_state(updated or STATE)
                result["model_pricing"] = cross.get("model_pricing") or result["model_pricing"]
            self._send(json.dumps(result), "application/json",
                       status=200 if result.get("ok") else 400)
            return
        if req_path == "/agent-access/toggle":
            result = set_agent_access(payload.get("client"), payload.get("enabled"))
            status = 200 if result.get("ok") else (409 if result.get("conflict") else 400)
            self._send(json.dumps(result), "application/json", status=status)
            return
        if req_path == "/session/delete":
            result = trash_session_log(payload.get("session_id"))
            if result.get("ok"):
                result["next_session_id"] = publish_after_session_delete()
            status = 200 if result.get("ok") else (404 if result.get("error_code") == "not_found" else
                     (500 if result.get("error_code") == "trash_failed" else 400))
            self._send(json.dumps(result), "application/json", status=status)
            return
        if req_path == "/capability/disable-unused":
            capabilities = cross_session().get("capabilities") or {}
            result = disable_capability_controls(payload.get("control_ids"), capabilities)
        else:
            capability_type = str(payload.get("type") or "").strip().lower()
            control_id = str(payload.get("control_id") or "").strip()
            enabled = payload.get("enabled") is True
            control = next((row for row in capability_inventory().get("control_groups") or []
                            if row.get("id") == control_id and row.get("control_type") == "skill_pack"
                            and row.get("mutable")), None)
            if not control:
                result = {"ok": False, "error": "Capability control is not in the discovered inventory."}
            elif capability_type == "skill":
                result = set_skill_pack_enabled(control.get("runtime"), control.get("plugin_id"), enabled)
            else:
                result = {"ok": False, "error": "Unsupported capability type."}
        if result.get("ok") or result.get("changed"):
            result["capabilities"] = refresh_capability_state()
        status = 200 if result.get("ok") else (409 if result.get("partial") else
                 (503 if "not available" in result.get("error", "") else 400))
        self._send(json.dumps(result), "application/json", status=status)

    def do_GET(self):
        parsed = urlparse(self.path)
        req_path = parsed.path
        if is_dashboard_page_path(req_path):
            path = page_path()
            if path:
                self._send(open(path, encoding="utf-8").read())
            else:
                self._send(missing_page_html(), status=503)
        elif req_path == "/session":
            sid = (parse_qs(parsed.query).get("id") or [""])[0]
            source = find_session(sid)
            st = recompute(source) if source else None
            if st:
                attach_cross_session(st)
                st["ended"] = True
                if st.get("timing"):
                    st["timing"]["end_label"] = "Last activity"
            self._send(json.dumps(st or {}), "application/json")
        elif req_path == "/state":
            self._send(json.dumps(current_state()), "application/json")
        elif req_path == "/logs":
            self._send(json.dumps(log_sessions_state()), "application/json")
        elif req_path == "/agent-access/status":
            self._send(json.dumps(agent_access_status()), "application/json")
        elif req_path == "/menubar":
            sid = (parse_qs(parsed.query).get("session") or [""])[0][:240]
            self._send(json.dumps(menubar_state(sid)), "application/json")
        elif req_path == "/health":
            path = page_path()
            sources = all_session_sources()
            clients = defaultdict(int)
            for source in sources:
                clients[source.get("client") or source.get("provider") or "unknown"] += 1
            self._send(json.dumps({
                "ok": bool(path),
                "state_ready": bool(STATE),
                "sources": len(sources),
                "source_clients": dict(clients),
                "port": PORT,
                "page_ready": bool(path),
                "page_path": path,
                "page_candidates": PAGE_CANDIDATES,
            }), "application/json", status=200 if path else 503)
        elif req_path == "/events":
            # Older dashboard builds used EventSource and can keep reconnecting
            # even after Chromium replaces the visible tab with an error page.
            # A 204 response explicitly tells EventSource clients to stop.
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
        else:
            self.send_error(404)


class TokenMeterHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64


if __name__ == "__main__":
    threading.Thread(target=watcher, daemon=True).start()
    srv = TokenMeterHTTPServer(("127.0.0.1", PORT), H)
    print(f"Token Meter live -> http://localhost:{PORT}")
    print("Auto-following newest ~/.claude, ~/.codex, and ~/.cursor log. Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
