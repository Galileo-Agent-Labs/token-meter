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
import html
import json
import os
import queue
import re
import time
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")
CODEX_SESSIONS = os.path.expanduser("~/.codex/sessions")
CODEX_INDEX = os.path.expanduser("~/.codex/session_index.jsonl")
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

subscribers, subscribers_lock = [], threading.Lock()
STATE = {}
_xsess = {"data": None, "at": 0.0}
_XSESS_TTL = 15.0


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


def safe_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def home_shorten(path):
    home = os.path.expanduser("~")
    return path.replace(home, "~", 1) if path and path.startswith(home) else path


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


def codex_meta(path):
    meta = {"session_id": None, "cwd": None, "model": None, "model_provider": None,
            "tools_loaded": 0, "tool_catalog": [], "tool_namespaces": []}
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
                        meta["tools_loaded"] = len(dynamic_tools)
                        meta["tool_catalog"] = [
                            {
                                "namespace": (t.get("namespace") if isinstance(t, dict) else "") or "unknown",
                                "name": (t.get("name") if isinstance(t, dict) else str(t)) or "?",
                                "defer_loading": bool(t.get("deferLoading")) if isinstance(t, dict) else False,
                            }
                            for t in dynamic_tools
                        ][:120]
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


def all_session_sources():
    sources = []

    for path in glob.glob(os.path.join(CLAUDE_PROJECTS, "*", "*.jsonl")):
        sid = os.path.basename(path).rsplit(".", 1)[0]
        project_raw = os.path.basename(os.path.dirname(path))
        sources.append({
            "provider": "claude",
            "label": "Claude Code",
            "id": sid,
            "session": os.path.basename(path),
            "path": path,
            "project": decode_claude_project(project_raw),
            "mtime": safe_mtime(path),
            "title": None,
        })

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
            "tool_catalog": meta.get("tool_catalog") or [],
            "tool_namespaces": meta.get("tool_namespaces") or [],
        }
    sid = os.path.basename(path).rsplit(".", 1)[0]
    return {
        "provider": "claude", "label": "Claude Code", "id": sid, "session": os.path.basename(path),
        "path": path, "project": decode_claude_project(os.path.basename(os.path.dirname(path))),
        "mtime": safe_mtime(path), "title": None,
    }


def newest_source():
    sources = all_session_sources()
    return max(sources, key=lambda s: s["mtime"]) if sources else None


def find_session(sid):
    for source in all_session_sources():
        stem = os.path.basename(source["path"]).rsplit(".", 1)[0]
        if sid in (source["id"], source["session"], stem):
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
                "executions": set(),
            })
            n["calls"] += 1
            n["output_tokens"] += tokens
            n["output_chars"] += output_chars
            n["args_chars"] += args_chars
            n["executions"].add(ex["idx"])

            ns = by_namespace.setdefault(namespace, {
                "namespace": namespace,
                "kind": kind,
                "calls": 0,
                "output_tokens": 0,
                "executions": set(),
            })
            ns["calls"] += 1
            ns["output_tokens"] += tokens
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
            })
            rt["calls"] += 1
            rt["output_tokens"] += tokens
            rt["output_chars"] += output_chars
            rt["args_chars"] += args_chars
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


def claude_tool_results(objs):
    chars_by_id = defaultdict(int)
    ts_by_id = {}
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
                chars_by_id[tid] += len(json.dumps(block.get("content", "")))
                ts_by_id[tid] = parse_iso(obj.get("timestamp", ""))
    return chars_by_id, ts_by_id


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
    result_chars, result_ts = claude_tool_results(objs)
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
                                         tool=tool["name"], tokens=tool["output_tokens"], severity="retrieval",
                                         model=model, output_chars=tool["output_chars"],
                                         retrieval_tokens=tool["output_tokens"]))

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
                       primary_model, "exact Claude API-rate estimate")


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
                tools_loaded = len(dynamic_tools)
                tool_catalog = [
                    {
                        "namespace": (t.get("namespace") if isinstance(t, dict) else "") or "unknown",
                        "name": (t.get("name") if isinstance(t, dict) else str(t)) or "?",
                        "defer_loading": bool(t.get("deferLoading")) if isinstance(t, dict) else False,
                    }
                    for t in dynamic_tools
                ][:120]
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
                        "args_chars": 0, "output_chars": 0, "output_tokens": 0}
                pending["calls"][call_id] = tool
                call_map[call_id] = tool
            output = payload.get("output") if "output" in payload else payload
            out_chars = len(str(output or ""))
            tool["output_chars"] += out_chars
            tool["output_tokens"] = tool["output_chars"] // CHARS_PER_TOKEN
            pending["trace"].append(trace_event(ts, "tool_result", tool["display"],
                                                f"~{tool['output_tokens']:,} returned tokens",
                                                tool=tool["name"], tokens=tool["output_tokens"],
                                                severity="retrieval", model=model,
                                                output_chars=tool["output_chars"],
                                                retrieval_tokens=tool["output_tokens"]))
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
    source["tool_catalog"] = tool_catalog
    source["tool_namespaces"] = tool_namespaces
    return build_state(source, tot, cost, total_tokens, total_cost, series, executions, trace, semantic,
                       analyses, insights, first_ts, last_ts, idle, biggest, len(coord_execs), True,
                       primary_model, "estimated with public OpenAI API rates")


def build_state(source, tot, cost, total_tokens, total_cost, series, executions, trace, semantic,
                analyses, insights, first_ts, last_ts, idle, biggest, side_turns, approx_cost,
                primary_model, pricing_note):
    elapsed = (last_ts - first_ts) if (first_ts and last_ts) else 0
    minutes = max(elapsed / 60.0, 1e-9)
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
    tool_data["loaded"] = tools_loaded
    tool_data["loaded_known"] = loaded_known
    tool_data["loaded_namespaces"] = list(source.get("tool_namespaces") or [])
    tool_data["catalog"] = list(source.get("tool_catalog") or [])[:80]
    insights = enrich_insights(insights, executions, tool_data, context_window, context_latest, context_peak,
                               source["provider"])
    source_obj = {
        "provider": source["provider"],
        "label": source["label"],
        "id": source["id"],
        "path": source["path"],
        "project": source.get("project") or "",
        "pricing_note": pricing_note,
        "approximate_cost": bool(approx_cost),
        "tools_loaded": tools_loaded,
        "tools_loaded_known": loaded_known,
    }
    return {
        "provider": source["provider"],
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
        "burn_tok_min": total_tokens / minutes if elapsed else 0,
        "burn_usd_min": total_cost / minutes if elapsed else 0,
        "timing": {
            "start_ts": first_ts or 0,
            "end_ts": last_ts or 0,
            "start_local": local_dt(first_ts),
            "end_local": local_dt(last_ts),
            "duration_s": int(elapsed),
            "duration": duration_label(elapsed),
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
    loaded = tool_data.get("loaded") or 0
    unique_used = tool_data.get("unique_used") or 0
    if loaded and tool_data.get("loaded_known"):
        ratio = unique_used / loaded if loaded else 0
        kind = "neutral" if ratio >= 0.25 else "warn"
        out.append(insight(
            "tools-loaded", kind, "Tools", "Tool surface",
            f"{loaded} tools loaded; {unique_used} used in this log.",
            detail="A wide tool surface can make planning noisier, even when most tools are unused.",
            action="Trim enabled tools if startup or tool selection feels noisy." if kind == "warn" else "",
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

    title = None
    for obj in objs:
        if obj.get("type") == "user":
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            txt = text_from_content(msg.get("content")).strip()
            if txt and not txt.startswith("<") and "command-" not in txt[:20]:
                title = compact_text(txt, 60)
                break

    return summary_row(source, title, cost, tokens, len(msgs), models, first_ts, last_ts, model_cost, model_tok, day_cost, approx)


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
    return summary_row(source, title, cost, tokens, turns, models, first_ts, last_ts, model_cost, model_tok, day_cost, approx)


def summary_row(source, title, cost, tokens, turns, models, first_ts, last_ts, model_cost, model_tok, day_cost, approx):
    return {
        "id": source["id"],
        "path": source["path"],
        "provider": source["provider"],
        "label": source["label"],
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
        "duration_s": int((last_ts - first_ts) if (first_ts and last_ts) else 0),
        "_model_cost": dict(model_cost),
        "_model_tok": dict(model_tok),
        "_day_cost": dict(day_cost),
    }


def session_summary(source):
    objs = load(source["path"])
    if source["provider"] == "codex":
        return codex_summary(source, objs)
    return claude_summary(source, objs)


def cross_session():
    now = time.time()
    if _xsess["data"] and (now - _xsess["at"] < _XSESS_TTL):
        return _xsess["data"]

    sessions = []
    model_cost, model_tok = defaultdict(float), defaultdict(int)
    day_cost = defaultdict(float)
    provider_cost, provider_sessions = defaultdict(float), defaultdict(int)

    for source in all_session_sources():
        row = session_summary(source)
        if row["turns"] == 0:
            continue
        sessions.append(row)
        provider_cost[row["provider"]] += row["cost"]
        provider_sessions[row["provider"]] += 1
        for model, val in row.pop("_model_cost").items():
            model_cost[model] += val
        for model, val in row.pop("_model_tok").items():
            model_tok[model] += val
        for day, val in row.pop("_day_cost").items():
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


def menubar_state():
    st = current_state()
    source = st.get("source") or {}
    context = st.get("context") or {}
    cache = st.get("cache") or {}
    activity = menubar_activity(st)
    recommendation = menubar_recommendation(st)
    verdict = menubar_verdict(st, recommendation)
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
        if req_path == "/":
            path = page_path()
            body = b"" if path else missing_page_html().encode()
            self.send_response(200 if path else 503)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(os.path.getsize(path) if path else len(body)))
            self.end_headers()
        else:
            self.send_error(404)

    def do_GET(self):
        parsed = urlparse(self.path)
        req_path = parsed.path
        if req_path == "/":
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
            self._send(json.dumps(menubar_state()), "application/json")
        elif req_path == "/health":
            path = page_path()
            self._send(json.dumps({
                "ok": bool(path),
                "state_ready": bool(STATE),
                "sources": len(all_session_sources()),
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
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Token Meter live -> http://localhost:{PORT}")
    print("Auto-following newest ~/.claude and ~/.codex log. Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
