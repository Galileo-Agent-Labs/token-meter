#!/usr/bin/env python3
"""
Token Meter - a live cost and efficiency instrument for Claude Code and Codex.

Tails local agent logs, parses each execution as it lands, and serves a
localhost dashboard over SSE with Current and Global views. Stdlib only; nothing
leaves your machine.

  python3 meter.py     ->  http://localhost:8722

Claude correctness note: one API response (message.id) can be split across
several JSONL lines, one per content block, and each line repeats the same usage
block. Claude parsing dedupes by message.id so costs are not double-counted.
Codex uses token_count events instead; those are already one usage slice.
"""
import calendar
import glob
import hashlib
import html
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import time
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")
CLAUDE_DESKTOP_DATA_ROOTS = [
    os.path.expanduser("~/Library/Application Support/Claude"),
    os.path.expanduser("~/Library/Application Support/Claude-3p"),
]
CLAUDE_DESKTOP_SESSIONS = os.path.join(CLAUDE_DESKTOP_DATA_ROOTS[0], "claude-code-sessions")
CLAUDE_SETTINGS = os.path.expanduser("~/.claude/settings.json")
CLAUDE_ROOT_CONFIG = os.path.expanduser("~/.claude.json")
CODEX_SESSIONS = os.path.expanduser("~/.codex/sessions")
CODEX_INDEX = os.path.expanduser("~/.codex/session_index.jsonl")
CODEX_CONFIG = os.path.expanduser("~/.codex/config.toml")
GHOST_MCP_ROOT = os.path.expanduser("~/.config/ghost/mcp-servers")
PORT = 8722

CLAUDE_PRICE = {
    "claude-opus-4-8": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "claude-fable-5": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10},
}

# Public OpenAI API pricing, per 1M tokens. Codex subscription accounting can
# differ by plan, so the UI labels OpenAI/Codex costs as API-rate estimates.
OPENAI_PRICE = {
    "gpt-5.5": {"input": 5.0, "output": 30.0, "cache_write": 0.0, "cache_read": 0.50},
    "gpt-5.4": {"input": 2.50, "output": 15.0, "cache_write": 0.0, "cache_read": 0.25},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50, "cache_write": 0.0, "cache_read": 0.075},
}

DEFAULT_CLAUDE_MODEL = "claude-opus-4-8"
DEFAULT_OPENAI_MODEL = "gpt-5.5"
CHARS_PER_TOKEN = 4
TRACE_LIMIT = 220
EXEC_LIMIT = 180
MENUBAR_CONTEXT_SOFT_PCT = 0.65
MENUBAR_CONTEXT_WATCH_PCT = 0.70
MENUBAR_CONTEXT_INTERVENE_PCT = 0.85
MENUBAR_COST_SPIKE = 0.50
LOW_YIELD_RATIO = 0.005
LOW_YIELD_COST = 0.05
LOW_YIELD_CONTEXT_PCT = 0.25
LOW_YIELD_INPUT_TOKENS = 60000
TOOL_OVERSIZED_TOKENS = 8000
MCP_SERVER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9_.@:/-]{1,180}$")
SKILL_PATH_RE = re.compile(r"(?:^|[/\\])([^/\\\s'\"]+)[/\\]SKILL\.md(?:\b|$)", re.IGNORECASE)
DATA_URL_RE = re.compile(r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+")
BASE64_FIELD_RE = re.compile(r'("(?:data|image_url)"\s*:\s*")([A-Za-z0-9+/=]{512,})(")')

subscribers, subscribers_lock = [], threading.Lock()
STATE = {}
_xsess = {"data": None, "at": 0.0}
_XSESS_TTL = 15.0
_summary_cache = {}
_ACTION_TOKEN = secrets.token_urlsafe(24)
_mcp_action_log = []
_ghost_catalog_cache = {"rows": {}, "at": 0.0}


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
    return {
        "duration_s": duration_s,
        "available": duration_s > 0,
        "reported_executions": reported,
        "observed_executions": observed,
        "execution_count": reported + observed,
        "basis": basis,
    }


def load(path):
    out = []
    if not path:
        return out
    try:
        for line in open(path, encoding="utf-8").read().splitlines():
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


def ghost_executable():
    found = shutil.which("ghost")
    if found:
        return found
    for path in (
        os.path.expanduser("~/.local/bin/ghost"),
        "/opt/homebrew/bin/ghost",
        "/usr/local/bin/ghost",
    ):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def decode_claude_project(name):
    user = os.environ.get("USER", "")
    prefix = "-Users-" + user
    if user and name.startswith(prefix):
        name = "~" + name[len(prefix):]
    return name.strip("-").replace("-", "/").replace("~/", "~/")


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


def claude_desktop_metadata_paths(root=None):
    if root:
        return glob.glob(os.path.join(root, "**", "local_*.json"), recursive=True)
    paths = []
    for data_root in CLAUDE_DESKTOP_DATA_ROOTS:
        paths.extend(glob.glob(os.path.join(data_root, "claude-code-sessions", "*", "*", "local_*.json")))
        paths.extend(glob.glob(os.path.join(data_root, "local-agent-mode-sessions", "*", "*", "local_*.json")))
    return paths


def claude_desktop_index(root=None):
    """Map standard and enterprise Claude Desktop metadata onto CLI trace ids."""
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
        raw_cwd = origin_cwd or row.get("cwd") or ""
        no_project = bool(source_kind == "agent" and not origin_cwd and os.path.basename(raw_cwd) == "outputs")
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
        project = desktop.get("project") or decode_claude_project(project_raw)
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
    sid = os.path.basename(path).rsplit(".", 1)[0]
    return {
        "provider": "claude", "client": "claude_code", "label": "Claude Code", "id": sid,
        "session": os.path.basename(path),
        "path": path, "project": decode_claude_project(os.path.basename(os.path.dirname(path))),
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


def price_for(model, provider="claude"):
    model = model or (DEFAULT_OPENAI_MODEL if provider == "codex" else DEFAULT_CLAUDE_MODEL)
    table = OPENAI_PRICE if provider == "codex" else CLAUDE_PRICE
    default = DEFAULT_OPENAI_MODEL if provider == "codex" else DEFAULT_CLAUDE_MODEL
    if model in table:
        return table[model], False
    compact = model.replace(" ", "-").lower()
    for key, price in table.items():
        if compact.startswith(key):
            return price, False
    return table[default], True


def cost_of(u, model, provider="claude"):
    p, _ = price_for(model, provider)
    return {
        "input": u.get("input_tokens", 0) * p["input"] / 1e6,
        "cache_write": u.get("cache_creation_input_tokens", 0) * p["cache_write"] / 1e6,
        "cache_read": u.get("cache_read_input_tokens", 0) * p["cache_read"] / 1e6,
        "output": u.get("output_tokens", 0) * p["output"] / 1e6,
    }


def usage_tokens(u):
    return (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            + u.get("cache_read_input_tokens", 0) + u.get("output_tokens", 0))


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
        or key.startswith("tool-bloat:")
        or key.startswith("namespace-bloat:")
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

    return {
        "total_calls": sum(r["calls"] for r in by_name_rows),
        "total_output_tokens": sum(r["output_tokens"] for r in by_name_rows),
        "total_errors": sum(r["errors"] for r in by_name_rows),
        "unique_used": len(by_name_rows),
        "namespaces_used": len(by_namespace_rows),
        "by_name": sorted(by_name_rows, key=lambda r: (-r["output_tokens"], -r["calls"], r["name"]))[:16],
        "by_namespace": sorted(by_namespace_rows, key=lambda r: (-r["output_tokens"], -r["calls"], r["namespace"]))[:12],
        "by_execution": by_execution[-80:],
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


def recompute(source):
    if isinstance(source, str):
        source = source_from_path(source)
    if not source:
        return None
    if source["provider"] == "codex":
        return recompute_codex(source)
    return recompute_claude(source)


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

    return build_state(source, tot, cost, total_tokens, total_cost, series, executions, trace, semantic,
                       analyses, insights, first_ts, last_ts, idle, biggest, side_turns, approx_cost,
                       primary_model, "exact Claude API-rate estimate", execution_timing("claude", objs))


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
                (payload.get("approval_policy") or "").replace("_", " "),
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
            tool = {
                **ident,
                "id": payload.get("id"),
                "call_id": call_id,
                "args_chars": len(str(payload.get("arguments") or payload.get("input") or "")),
                "output_chars": 0,
                "output_tokens": 0,
                "error": False,
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
                        "args_chars": 0, "output_chars": 0, "output_tokens": 0, "error": False}
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
    return build_state(source, tot, cost, total_tokens, total_cost, series, executions, trace, semantic,
                       analyses, insights, first_ts, last_ts, idle, biggest, len(coord_execs), True,
                       primary_model, "estimated with public OpenAI API rates", execution_timing("codex", objs))


def build_state(source, tot, cost, total_tokens, total_cost, series, executions, trace, semantic,
                analyses, insights, first_ts, last_ts, idle, biggest, side_turns, approx_cost,
                primary_model, pricing_note, active_timing=None):
    elapsed = (last_ts - first_ts) if (first_ts and last_ts) else 0
    active_timing = active_timing or {}
    active_seconds = float(active_timing.get("duration_s") or 0)
    active_available = bool(active_timing.get("available") and active_seconds > 0)
    minutes = max(active_seconds / 60.0, 1e-9)
    cache_in = tot["cache_read"] + tot["cache_write"]
    cache_ratio = (tot["cache_read"] / cache_in) if cache_in else 0.0
    cache = cache_block(tot, cost, executions, source["provider"], primary_model)
    tool_data = tool_summary(executions)
    context_window = max((e.get("context_window") or 0 for e in executions), default=0) or None
    context_peak = max((e.get("context_tokens") or e.get("tokens", {}).get("input", 0) for e in executions), default=0)
    context_latest = executions[-1].get("context_tokens", 0) if executions else 0
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
    insights = enrich_insights(insights, executions, tool_data, context_window, context_latest, context_peak,
                               source["provider"])
    source_obj = {
        "provider": source["provider"],
        "client": source.get("client") or source["provider"],
        "label": source["label"],
        "id": source["id"],
        "desktop_session_id": source.get("desktop_session_id"),
        "path": source["path"],
        "project": source.get("project") or "",
        "pricing_note": pricing_note,
        "approximate_cost": bool(approx_cost),
        "tools_loaded": tools_loaded,
        "tools_loaded_known": loaded_known,
        "tools_advertised": advertised,
        "tools_eager": eager,
        "tools_deferred": deferred,
        "tool_catalog_coverage": catalog_coverage,
    }
    return {
        "provider": source["provider"],
        "client": source.get("client") or source["provider"],
        "source": source_obj,
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
    if tool_data.get("by_namespace"):
        top_ns = tool_data["by_namespace"][0]
        if top_ns.get("output_tokens", 0) > 25000:
            out.append(insight(
                f"namespace-bloat:{top_ns['namespace']}", "warn", "Tools", "Namespace payload",
                f"{top_ns['namespace']} tools returned ~{top_ns['output_tokens']:,} tokens.",
                detail="Tool result text is reintroduced into context and can dominate later turns.",
                action="Open the Tools tab and narrow high-volume calls.",
                priority=18,
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

    if an["tool_bloat"] and an["tool_bloat"][0]["tokens"] > 8000:
        b = an["tool_bloat"][0]
        out.append(insight(
            f"tool-bloat:{b['name']}", "warn", "Tools", "Tool payload",
            f"{b['name']} returned ~{b['tokens']:,} tokens.",
            detail=f"{b['calls']} calls from the {b['namespace']} namespace produced the largest tool payload.",
            action="Open Tools and inspect whether that output can be narrowed.",
            priority=16,
        ))
    elif an["tool_bloat"] and an["tool_bloat"][0]["tokens"] > 2500:
        b = an["tool_bloat"][0]
        out.append(insight(
            f"tool-heavy:{b['name']}", "neutral", "Tools", "Tool payload",
            f"{b['name']} returned ~{b['tokens']:,} tokens.",
            detail="This is the largest tool result stream in the log.",
            priority=64,
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

    row = summary_row(source, title, cost, tokens, len(msgs), models, first_ts, last_ts, model_cost, model_tok, day_cost, approx,
                      execution_timing("claude", objs))
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
        ts = parse_iso(obj.get("timestamp", ""))
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
    row = summary_row(source, title, cost, tokens, turns, models, first_ts, last_ts, model_cost, model_tok, day_cost, approx,
                      execution_timing("codex", objs))
    row["_tool_evidence"] = summarize_tool_evidence(codex_tool_call_evidence(objs), source.get("tool_catalog") or [])
    return row


def summary_row(source, title, cost, tokens, turns, models, first_ts, last_ts, model_cost, model_tok, day_cost, approx,
                active_timing=None):
    active_timing = active_timing or {}
    wall_duration = (last_ts - first_ts) if (first_ts and last_ts) else 0
    return {
        "id": source["id"],
        "path": source["path"],
        "provider": source["provider"],
        "client": source.get("client") or source["provider"],
        "label": source["label"],
        "desktop_session_id": source.get("desktop_session_id"),
        "project": source.get("project") or "",
        "title": title or source.get("title") or "(untitled log)",
        "cost": cost,
        "cost_approx": bool(approx),
        "tokens": tokens,
        "turns": turns,
        "models": sorted(models),
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
    }


def session_summary(source):
    cached = _summary_cache.get(source["path"])
    mtime = source.get("mtime") or safe_mtime(source["path"])
    if cached and cached.get("mtime") == mtime:
        return cached["row"]
    objs = load(source["path"])
    if source["provider"] == "codex":
        row = codex_summary(source, objs)
    else:
        row = claude_summary(source, objs)
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

    for session in session_rows:
        evidence = session.get("_tool_evidence") or {}
        project = session.get("project") or "local"
        provider = session.get("provider") or "unknown"
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
            row = by_name.setdefault(name, {
                "name": name,
                "display": item.get("display") or name,
                "namespace": item.get("namespace") or "unknown",
                "kind": item.get("kind") or "tool",
                "calls": 0, "output_tokens": 0, "flagged_tokens": 0,
                "errors": 0, "oversized_calls": 0, "repeat_calls": 0,
                "last_ts": 0, "sessions": set(), "projects": set(),
                "project_calls": defaultdict(int), "providers": set(),
                "advertised_sessions": set(), "eager_sessions": set(), "deferred_sessions": set(),
            })
            for key in ("calls", "output_tokens", "flagged_tokens", "errors", "oversized_calls", "repeat_calls"):
                row[key] += int(item.get(key) or 0)
            row["last_ts"] = max(row["last_ts"], int(item.get("last_ts") or 0))
            row["sessions"].add(session_id)
            row["projects"].add(project)
            row["project_calls"][project] += int(item.get("calls") or 0)
            row["providers"].add(provider)

            namespace = row["namespace"]
            ns = by_namespace.setdefault(namespace, {
                "namespace": namespace, "kind": row["kind"], "calls": 0,
                "output_tokens": 0, "flagged_tokens": 0, "errors": 0,
                "sessions": set(), "projects": set(),
            })
            ns["calls"] += int(item.get("calls") or 0)
            ns["output_tokens"] += int(item.get("output_tokens") or 0)
            ns["flagged_tokens"] += int(item.get("flagged_tokens") or 0)
            ns["errors"] += int(item.get("errors") or 0)
            ns["sessions"].add(session_id)
            ns["projects"].add(project)

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
            advertised_names.add(name)
            if name in session_used:
                used_advertised_names.add(name)
            row = by_name.setdefault(name, {
                "name": name, "display": name,
                "namespace": item.get("namespace") or "unknown",
                "kind": item.get("kind") or "tool",
                "calls": 0, "output_tokens": 0, "flagged_tokens": 0,
                "errors": 0, "oversized_calls": 0, "repeat_calls": 0,
                "last_ts": 0, "sessions": set(), "projects": set(),
                "project_calls": defaultdict(int), "providers": set(),
                "advertised_sessions": set(), "eager_sessions": set(), "deferred_sessions": set(),
            })
            row["advertised_sessions"].add(session_id)
            if item.get("defer_loading"):
                row["deferred_sessions"].add(session_id)
            else:
                row["eager_sessions"].add(session_id)

    total_sessions = len(session_rows)
    tool_rows = []
    for row in by_name.values():
        sessions_used = len(row["sessions"])
        advertised_sessions = len(row["advertised_sessions"])
        project_calls = dict(row["project_calls"])
        top_project = max(project_calls, key=project_calls.get) if project_calls else ""
        top_project_calls = project_calls.get(top_project, 0)
        project_share = top_project_calls / row["calls"] if row["calls"] else 0.0
        recommendation = "keep"
        reason = "Observed usage does not cross a trace-waste threshold."
        if row["kind"] == "mcp" and advertised_sessions >= 5 and sessions_used == 0:
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
            "name": row["name"], "display": row["display"],
            "namespace": row["namespace"], "kind": row["kind"],
            "calls": row["calls"], "output_tokens": row["output_tokens"],
            "flagged_tokens": row["flagged_tokens"], "errors": row["errors"],
            "oversized_calls": row["oversized_calls"], "repeat_calls": row["repeat_calls"],
            "sessions_used": sessions_used, "advertised_sessions": advertised_sessions,
            "eager_sessions": len(row["eager_sessions"]), "deferred_sessions": len(row["deferred_sessions"]),
            "projects": sorted(row["projects"]), "top_project": top_project,
            "project_share": project_share, "providers": sorted(row["providers"]),
            "last_ts": row["last_ts"],
            "last_used": time.strftime("%Y-%m-%d", time.localtime(row["last_ts"])) if row["last_ts"] else "Never",
            "recommendation": recommendation, "reason": reason,
            "mcp_server": row["namespace"] if row["kind"] == "mcp" else "",
        })

    tool_rows.sort(key=lambda r: (-r["output_tokens"], -r["calls"], r["name"]))
    namespace_rows = []
    for row in by_namespace.values():
        namespace_rows.append({
            "namespace": row["namespace"], "kind": row["kind"],
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
    if tool_rows and tool_rows[0]["output_tokens"]:
        top = tool_rows[0]
        insights.append(insight(
            f"global-tool:{top['name']}", "warn" if top["output_tokens"] >= 25000 else "neutral",
            "Tools", "Largest tool payload",
            f"{top['display']} returned ~{top['output_tokens']:,} tokens across {top['sessions_used']} sessions.",
            detail=f"{top['calls']} calls from {top['namespace']} produced the largest trace-observed payload.",
            action="Narrow the command or query output." if top["output_tokens"] >= 25000 else "",
            priority=12,
        ))
    if totals["flagged_tokens"]:
        share = totals["flagged_tokens"] / max(1, totals["total_output_tokens"])
        insights.append(insight(
            "global-flagged", "warn" if share >= 0.25 else "neutral", "Tools", "Flagged result volume",
            f"~{totals['flagged_tokens']:,} returned tokens matched an oversized, repeat, or error signal.",
            detail=f"That is {share * 100:.0f}% of {totals['total_output_tokens']:,} trace-observed tool-result tokens; categories can overlap.",
            action="Inspect the ranked tools and repeated calls first." if share >= 0.25 else "",
            priority=14,
        ))
    if totals["repeat_calls"]:
        insights.append(insight(
            "global-repeats", "warn" if totals["repeat_calls"] >= 5 else "neutral", "Flow", "Repeated calls",
            f"{totals['repeat_calls']} calls immediately repeated the same tool arguments.",
            detail=f"The exact consecutive repeats occurred within five minutes and returned ~{totals['repeat_tokens']:,} tokens.",
            action="Reuse earlier results or make the next query narrower." if totals["repeat_calls"] >= 5 else "",
            priority=20,
        ))
    if totals["errors"]:
        insights.append(insight(
            "global-errors", "warn", "Tools", "Tool errors",
            f"{totals['errors']} tool calls ended with a structured error signal.",
            detail=f"Failed calls returned ~{totals['error_tokens']:,} tokens before recovery or retry.",
            action="Fix authentication or disable persistently failing MCPs.", priority=10,
        ))
    disable_candidates = [row for row in tool_rows if row["recommendation"] == "disable"]
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


def ghost_mcp_catalog():
    rows = {}
    remote_root = os.path.join(GHOST_MCP_ROOT, "remote")
    for path in glob.glob(os.path.join(remote_root, "*.yaml")):
        name = os.path.basename(path).rsplit(".", 1)[0]
        try:
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            match = re.search(r'^name:\s*([A-Za-z0-9_.:-]+)\s*$', content, re.MULTILINE)
            name = match.group(1) if match else name
        except OSError:
            pass
        rows[name] = {"name": name, "transport": "remote"}
    local_root = os.path.join(GHOST_MCP_ROOT, "dist", "servers")
    aliases = {"cisco-directory": "cisco_directory", "splunk-docs": "splunk_docs"}
    for path in glob.glob(os.path.join(local_root, "*")):
        if not os.path.isdir(path):
            continue
        raw = os.path.basename(path)
        name = aliases.get(raw, raw)
        rows[name] = {"name": name, "transport": "local"}
    rows.update(_ghost_catalog_cache.get("rows") or {})
    return rows


def refresh_ghost_mcp_catalog(runner=None):
    ghost_path = ghost_executable()
    if not ghost_path:
        return {}
    runner = runner or subprocess.run
    try:
        completed = runner([ghost_path, "mcp", "codex", "list"], capture_output=True,
                           text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if completed.returncode != 0:
        return {}
    rows = {}
    for line in (completed.stdout or "").splitlines():
        match = re.match(r'^\s{2}([A-Za-z0-9_.:-]+)\s+(remote|local)\s+', line)
        if match:
            rows[match.group(1)] = {"name": match.group(1), "transport": match.group(2)}
    if rows:
        _ghost_catalog_cache["rows"], _ghost_catalog_cache["at"] = rows, time.time()
        _xsess["data"], _xsess["at"] = None, 0.0
        if STATE:
            updated = dict(STATE)
            updated["xsession"] = cross_session()
            publish(updated)
    return rows


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


def discovered_skills(skill_usage=None):
    usage = {str(row.get("name") or "").lower(): row for row in (skill_usage or [])}
    rows = []

    def add(path, runtime, source, enabled=True, plugin_id="", mutable=False, control_scope=""):
        name = os.path.basename(os.path.dirname(path))
        used = usage.get(name.lower()) or {}
        rows.append({
            "id": f"{runtime}:{plugin_id or source}:{name}",
            "type": "skill", "name": name, "runtime": runtime, "source": source,
            "path": home_shorten(path), "enabled": bool(enabled), "plugin_id": plugin_id,
            "mutable": bool(mutable), "control_scope": control_scope,
            "used": bool(used), "activations": int(used.get("activations") or 0),
            "sessions_used": int(used.get("sessions_used") or 0), "last_used": used.get("last_used") or "Never",
        })

    for path in glob.glob(os.path.expanduser("~/.codex/skills/**/SKILL.md"), recursive=True):
        if "/plugins/" not in path:
            add(path, "Codex", "local skill", True)

    codex_plugins = codex_plugin_states()
    codex_cache = os.path.expanduser("~/.codex/plugins/cache")
    for path in glob.glob(os.path.join(codex_cache, "*", "*", "*", "skills", "*", "SKILL.md")):
        rel = os.path.relpath(path, codex_cache).split(os.sep)
        if len(rel) < 6:
            continue
        market, plugin = rel[0], rel[1]
        plugin_id = f"{plugin}@{market}"
        configured = plugin_id in codex_plugins
        add(path, "Codex", plugin_id, codex_plugins.get(plugin_id, True), plugin_id, configured, "plugin pack" if configured else "")

    claude_settings = load_json(CLAUDE_SETTINGS, {})
    claude_enabled = claude_settings.get("enabledPlugins") if isinstance(claude_settings, dict) else {}
    for plugin_id, install in claude_plugin_installations().items():
        root = install.get("installPath") or ""
        for path in glob.glob(os.path.join(root, "skills", "*", "SKILL.md")):
            add(path, "Claude", plugin_id, bool((claude_enabled or {}).get(plugin_id)), plugin_id, True, "plugin pack")

    for data_root in CLAUDE_DESKTOP_DATA_ROOTS:
        desktop_root = os.path.join(data_root, "local-agent-mode-sessions", "skills-plugin")
        for path in glob.glob(os.path.join(desktop_root, "**", "skills", "*", "SKILL.md"), recursive=True):
            add(path, "Claude Desktop", "Cowork built-in", True)

    deduped = {}
    for row in rows:
        deduped[row["id"]] = row
    return sorted(deduped.values(), key=lambda row: (row["runtime"], row["name"], row["source"]))


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
            "id": f"tool:{row.get('name')}", "type": "tool", "name": row.get("display") or row.get("name"),
            "identity": row.get("name"), "runtime": ", ".join(row.get("providers") or []) or "trace",
            "source": row.get("namespace") or "unknown", "state": state,
            "enabled": True, "mutable": False, "used": bool(row.get("calls")),
            "calls": int(row.get("calls") or 0), "returned_tokens": int(row.get("output_tokens") or 0),
            "advertised_sessions": advertised, "eager_sessions": eager, "deferred_sessions": deferred,
            "last_used": row.get("last_used") or "Never", "recommendation": row.get("recommendation") or "keep",
        })

    mcp_catalog = ghost_mcp_catalog()
    codex_states, claude_states = codex_mcp_states(), claude_mcp_states()
    mcp_usage = defaultdict(lambda: {"calls": 0, "tokens": 0, "last_used": "Never", "used": False})
    for row in tool_evidence:
        if row.get("kind") != "mcp":
            continue
        name = row.get("mcp_server") or row.get("namespace") or "mcp"
        u = mcp_usage[name]
        u["calls"] += int(row.get("calls") or 0)
        u["tokens"] += int(row.get("output_tokens") or 0)
        u["used"] = u["used"] or bool(row.get("calls"))
        if row.get("last_ts") and row.get("last_used"):
            u["last_used"] = row["last_used"]
    all_mcp_names = set(mcp_catalog) | set(codex_states) | set(claude_states) | set(mcp_usage)
    mcp_items = []
    for name in sorted(all_mcp_names):
        codex_on = bool(codex_states.get(name))
        claude_on = bool(claude_states.get(name))
        usage_row = mcp_usage[name]
        enabled = codex_on or claude_on
        mcp_items.append({
            "id": f"mcp:{name}", "type": "mcp", "name": name, "runtime": "Codex + Claude",
            "source": (mcp_catalog.get(name) or {}).get("transport") or "trace/config",
            "state": "Enabled" if enabled else "Disabled", "enabled": enabled,
            "mutable": bool(ghost_executable() and name in mcp_catalog),
            "codex_enabled": codex_on, "claude_enabled": claude_on, "used": usage_row["used"],
            "calls": usage_row["calls"], "returned_tokens": usage_row["tokens"], "last_used": usage_row["last_used"],
        })

    skill_items = discovered_skills(waste.get("skills") or [])
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
        "definitions": {key: int(waste.get(key) or 0) for key in (
            "definition_tokens", "eager_definition_tokens", "deferred_definition_tokens", "unused_eager_definition_tokens"
        )},
    }
    return {
        "summary": summary, "items": tool_items + mcp_items + skill_items,
        "actions": {**mcp_action_capability(), "skill_pack_toggle": True},
        "claude_desktop": {
            "local_agent_sessions": len(local_agents),
            "traceable_agent_sessions": len(traceable_agents),
            "latest_local_agent": local_dt(latest_desktop) if latest_desktop else "Never",
            "cloud_trace_available": False,
            "roots": [home_shorten(root) for root in CLAUDE_DESKTOP_DATA_ROOTS if os.path.isdir(root)],
            "note": "Scanning standard and enterprise Claude Desktop Agent/Cowork traces.",
        },
        "generated_at": int(time.time()),
    }


def mcp_action_capability():
    ghost_path = ghost_executable()
    return {
        "available": bool(ghost_path),
        "token": _ACTION_TOKEN,
        "scope": "Codex and Claude",
        "command_template": "ghost mcp all remove <server>",
        "enable_command_template": "ghost mcp all add <server>",
    }


def set_mcp_server_enabled(server, enabled, ghost_path=None, runner=None):
    server = str(server or "").strip()
    if not MCP_SERVER_RE.fullmatch(server):
        return {"ok": False, "error": "Invalid MCP server name."}
    ghost_path = ghost_path or ghost_executable()
    if not ghost_path:
        return {"ok": False, "error": "Ghost CLI is not available on PATH."}
    runner = runner or subprocess.run
    operation = "add" if enabled else "remove"
    command = [ghost_path, "mcp", "all", operation, server]
    try:
        completed = runner(command, capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Ghost timed out while trying to {operation} the MCP server.", "command": command[1:]}
    except OSError as exc:
        return {"ok": False, "error": compact_text(str(exc), 240), "command": command[1:]}

    output = compact_text((completed.stdout or completed.stderr or "").strip(), 600)
    result = {
        "ok": completed.returncode == 0,
        "server": server,
        "enabled": bool(enabled),
        "command": ["ghost", "mcp", "all", operation, server],
        "message": output or (f"MCP server {'enabled' if enabled else 'disabled'}." if completed.returncode == 0 else f"Ghost could not {operation} the MCP server."),
        "restart_required": completed.returncode == 0,
    }
    if completed.returncode != 0:
        result["error"] = result["message"]
    _mcp_action_log.insert(0, {
        "ts": int(time.time()), "server": server, "ok": result["ok"], "message": result["message"],
    })
    del _mcp_action_log[20:]
    if result["ok"]:
        _xsess["data"], _xsess["at"] = None, 0.0
    return result


def disable_mcp_server(server, ghost_path=None, runner=None):
    return set_mcp_server_enabled(server, False, ghost_path=ghost_path, runner=runner)


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
    _xsess["data"], _xsess["at"] = None, 0.0
    return {"ok": True, "plugin_id": plugin_id, "runtime": "Codex", "enabled": bool(enabled), "restart_required": True}


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
    _xsess["data"], _xsess["at"] = None, 0.0
    return {"ok": True, "plugin_id": plugin_id, "runtime": "Claude", "enabled": bool(enabled), "restart_required": True}


def set_skill_pack_enabled(runtime, plugin_id, enabled):
    runtime = str(runtime or "").strip().lower()
    if runtime == "codex":
        result = set_codex_plugin_enabled(plugin_id, enabled)
    elif runtime == "claude":
        result = set_claude_plugin_enabled(plugin_id, enabled)
    else:
        return {"ok": False, "error": "Only Codex and Claude plugin packs can be changed."}
    return result


def cross_session():
    now = time.time()
    if _xsess["data"] and (now - _xsess["at"] < _XSESS_TTL):
        return _xsess["data"]

    sessions = []
    internal_rows = []
    model_cost, model_tok = defaultdict(float), defaultdict(int)
    day_cost = defaultdict(float)
    provider_cost, provider_sessions = defaultdict(float), defaultdict(int)

    for source in all_session_sources():
        row = session_summary(source)
        if row["turns"] == 0:
            continue
        internal_rows.append(row)
        sessions.append({key: value for key, value in row.items() if not key.startswith("_")})
        provider_cost[row["provider"]] += row["cost"]
        provider_sessions[row["provider"]] += 1
        for model, val in row.get("_model_cost", {}).items():
            model_cost[model] += val
        for model, val in row.get("_model_tok", {}).items():
            model_tok[model] += val
        for day, val in row.get("_day_cost", {}).items():
            day_cost[day] += val

    sessions.sort(key=lambda s: -s["mtime"])
    mm = sorted([{"model": k, "tokens": model_tok[k], "cost": model_cost[k]} for k in model_cost],
                key=lambda x: -x["cost"])
    days = sorted(day_cost)
    trend = [{"day": day, "cost": day_cost[day]} for day in days][-14:]
    costs = [t["cost"] for t in trend]
    med = sorted(costs)[len(costs) // 2] if costs else 0
    for item in trend:
        item["anomaly"] = bool(med and item["cost"] > 2.5 * med)

    total = sum(model_cost.values())
    premium = (model_cost.get("claude-opus-4-8", 0) + model_cost.get("claude-fable-5", 0)
               + model_cost.get("gpt-5.5", 0))
    tool_waste = global_tool_waste(internal_rows)
    data = {
        "sessions": sessions[:60],
        "model_mix": mm,
        "trend": trend,
        "total_cost": total,
        "total_sessions": len(sessions),
        "opus_share": (premium / total) if total else 0.0,
        "premium_share": (premium / total) if total else 0.0,
        "providers": sorted([
            {"provider": k, "cost": provider_cost[k], "sessions": provider_sessions[k]}
            for k in provider_cost
        ], key=lambda r: -r["cost"]),
        "tool_waste": tool_waste,
        "capabilities": capability_inventory(tool_waste),
        "mcp_actions": mcp_action_capability(),
    }
    _xsess["data"], _xsess["at"] = data, now
    return data


def publish(state):
    global STATE
    STATE = state
    data = "data: " + json.dumps(state) + "\n\n"
    with subscribers_lock:
        dead = []
        for q_ in subscribers:
            try:
                q_.put_nowait(data)
            except Exception:
                dead.append(q_)
        for d in dead:
            subscribers.remove(d)


def current_state():
    if STATE:
        return STATE
    source = newest_source()
    st = recompute(source) if source else None
    if st:
        st["xsession"] = cross_session()
        return st
    return {
        "ok": False,
        "message": "No Claude Code or Codex logs found yet.",
        "source": {},
        "total_cost": 0,
        "total_tokens": 0,
        "turns": 0,
        "context": {},
        "insights": [],
    }


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
    operational_warn = next((i for i in insights if i.get("kind") == "warn" and is_operational_warning(i)), None)
    last_cost = st.get("last_turn_cost") or 0
    low_yield_actionable = low_yield_should_warn(st.get("executions") or [], pct)

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
    if last_cost >= MENUBAR_COST_SPIKE:
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
    if operational_warn and (
        "tool-bloat" in (operational_warn.get("key") or "")
        or "namespace-bloat" in (operational_warn.get("key") or "")
    ):
        return {
            "label": "Inspect tool output",
            "detail": operational_warn.get("text") or "Tool output is dominating the log.",
            "severity": "warn",
            "target": "tools",
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
    pct = context.get("latest_pct") or 0
    last_cost = st.get("last_turn_cost") or 0
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
    if last_cost >= MENUBAR_COST_SPIKE:
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

    return payload(
        "healthy",
        f"Context is {pct * 100:.0f}% and no operational warning needs intervention.",
    )


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
    st = recompute(selected_source) if selected_source else current_state()
    if selected_source and not st:
        missing = True
        selected_source = None
        st = current_state()
    source = st.get("source") or {}
    context = st.get("context") or {}
    cache = st.get("cache") or {}
    activity = menubar_activity(st)
    recommendation = menubar_recommendation(st)
    verdict = menubar_verdict(st, recommendation)
    selected_id = source.get("id")
    return {
        "ok": bool(st.get("source")),
        "provider": st.get("provider"),
        "source": {
            "label": source.get("label"),
            "id": source.get("id"),
            "project": source.get("project"),
            "pricing_note": source.get("pricing_note"),
            "approximate_cost": source.get("approximate_cost"),
        },
        "session": st.get("session"),
        "project": st.get("project") or source.get("project"),
        "total_cost": st.get("total_cost", 0),
        "cost_approx": st.get("cost_approx", False),
        "total_tokens": st.get("total_tokens", 0),
        "turns": st.get("turns", 0),
        "cache": {
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
        "ts": st.get("ts"),
    }


def watcher():
    cur, last_sig = None, None
    while True:
        nf = newest_source()
        if nf and (not cur or nf["path"] != cur["path"]):
            cur, last_sig = nf, None
        if cur:
            sig = safe_mtime(cur["path"])
            if not sig:
                cur = None
                time.sleep(0.5)
                continue
            if sig != last_sig:
                last_sig = sig
                st = recompute(cur)
                if st:
                    st["xsession"] = cross_session()
                    publish(st)
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
        except ConnectionResetError:
            pass

    def log_message(self, *args):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", status=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
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
        if req_path not in ("/mcp/disable", "/capability/toggle"):
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
        if req_path == "/mcp/disable":
            result = disable_mcp_server(payload.get("server"))
        else:
            capability_type = str(payload.get("type") or "").strip().lower()
            enabled = payload.get("enabled") is True
            if capability_type == "mcp":
                known = {
                    row["name"] for row in capability_inventory().get("items") or []
                    if row.get("type") == "mcp" and row.get("mutable")
                }
                server = str(payload.get("name") or "").strip()
                if server not in known:
                    result = {"ok": False, "error": "MCP server is not in the discovered inventory."}
                else:
                    result = set_mcp_server_enabled(server, enabled)
            elif capability_type == "skill":
                result = set_skill_pack_enabled(payload.get("runtime"), payload.get("plugin_id"), enabled)
            else:
                result = {"ok": False, "error": "Unsupported capability type."}
        status = 200 if result.get("ok") else (503 if "not available" in result.get("error", "") else 400)
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
                st["xsession"] = cross_session()
                st["ended"] = True
                if st.get("timing"):
                    st["timing"]["end_label"] = "Last activity"
            self._send(json.dumps(st or {}), "application/json")
        elif req_path == "/state":
            self._send(json.dumps(current_state()), "application/json")
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
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q_ = queue.Queue(maxsize=8)
            with subscribers_lock:
                subscribers.append(q_)
            if STATE:
                q_.put_nowait("data: " + json.dumps(STATE) + "\n\n")
            try:
                while True:
                    try:
                        chunk = q_.get(timeout=15)
                    except queue.Empty:
                        chunk = ": ping\n\n"
                    self.wfile.write(chunk.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with subscribers_lock:
                    if q_ in subscribers:
                        subscribers.remove(q_)
        else:
            self.send_error(404)


if __name__ == "__main__":
    threading.Thread(target=watcher, daemon=True).start()
    threading.Thread(target=refresh_ghost_mcp_catalog, daemon=True).start()
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Token Meter live -> http://localhost:{PORT}")
    print("Auto-following newest ~/.claude and ~/.codex log. Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
