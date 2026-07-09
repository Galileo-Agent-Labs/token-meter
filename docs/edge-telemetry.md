# Edge Telemetry — What token-meter Collects

All data is read from local JSONL logs on the developer's machine. No API keys.
No data leaves the machine.

## Log Sources

| Provider | Path | Client |
|---|---|---|
| Claude Code CLI | `~/.claude/projects/*/*.jsonl` | `claude_code` |
| Claude Desktop (project) | `~/Library/Application Support/Claude/claude-code-sessions/` | `claude_desktop` |
| Claude Desktop (agent) | `~/Library/Application Support/Claude/local-agent-mode-sessions/` | `claude_desktop` |

---

## Fields Captured

### Tokens (`meter.py:684–706`)

All four token types are read from the `usage` block of each assistant message:

```python
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

def usage_io_tokens(u):
    return (
        int(u.get("input_tokens", 0) or 0)
        + int(u.get("cache_creation_input_tokens", 0) or 0)
        + int(u.get("cache_read_input_tokens", 0) or 0),
        int(u.get("output_tokens", 0) or 0),
    )
```

| Field | Meaning |
|---|---|
| `input_tokens` | Fresh prompt tokens (not cached) |
| `output_tokens` | Generated tokens (includes thinking tokens — not broken out) |
| `cache_creation_input_tokens` | Tokens written to the prompt cache |
| `cache_read_input_tokens` | Tokens served from cache (cheaper rate) |

Cost is computed locally by multiplying each token type by the hardcoded rate in `CLAUDE_PRICE` 

---

### Model and Provider (`meter.py:526–570`)

Set at session discovery time from the file path and optional Desktop sidecar metadata:

```python
# Claude sessions
source = {
    "provider": "claude",
    "client": desktop.get("client") or "claude_code",  # or "claude_desktop"
    "model": desktop.get("model"),
    ...
}
```

---

### Session Timing and Output Throughput (`meter.py:154–212`)

Claude reads `system` records with `subtype: "turn_duration"`:

```python
if obj.get("type") != "system" or obj.get("subtype") != "turn_duration":
    ...
duration_ms = obj.get("durationMs")
if ts and duration_ms > 0:
    intervals.append((ts - duration_ms / 1000.0, ts))
```

Output tokens ÷ generation seconds = observed tokens/sec, shown in the Stats tab.

---

### Tool Calls and Tool Result Volume (`meter.py:1331–1350`)

Tool call count is inferred from `tool_use` blocks in assistant messages. Tool result
volume is measured in characters from `tool_result` blocks returned by the user turn:

```python
def claude_tool_results(objs):
    chars_by_id = defaultdict(int)
    for obj in objs:
        if obj.get("type") != "user":
            continue
        for block in content:
            if block.get("type") == "tool_result":
                tid = block.get("tool_use_id")
                chars_by_id[tid] += observable_output_chars(block.get("content", ""))
```

Errors per tool call are also tracked via `is_error` and heuristic content inspection.

---

### Context Window Pressure (`meter.py:1617, 1659, 1783, 1796`)

`model_context_window` is read from the log and used to compute fill percentage per execution:

```python
context_window = None

# Both providers:
context_pct = (in_tok / context_window) if context_window else None
```

Warning thresholds: 65% (soft), 70% (watch), 85% (intervene) — defined as
`MENUBAR_CONTEXT_SOFT_PCT`, `MENUBAR_CONTEXT_WATCH_PCT`, `MENUBAR_CONTEXT_INTERVENE_PCT`.

---

### Working Directory / Project (`meter.py:535–536, 563`)

```python
# Claude: read from JSONL trace records
trace_cwd = claude_trace_cwd(path)
project = desktop.get("project") or home_shorten(trace_cwd) or decode_claude_project(project_raw)
```

---

## ROI Signals (Currently Computed)

| Signal | Formula | meter.py location |
|---|---|---|
| Cache efficiency | `cache_read_tokens / total_input_tokens` | `meter.py:1538` |
| Low-yield detection | `output_tokens / input_tokens < 0.005` | `meter.py:1021` |
| Low-yield warning | fires if context >25%, input >60K tokens, or 2+ consecutive low-yield turns | `meter.py:1031` |
| Cost spike | single execution ≥ $0.50 or ≥ 55% of session spend | `meter.py:2223` |
| Output throughput | `output_tokens / generation_seconds` | `meter.py:898` |
| Context pressure | `input_tokens / model_context_window` — warns at 65% / 70% / 85% | `meter.py:1796` |
| Tool result volume | characters returned per tool call; oversized flagged at >8K tokens | `meter.py:1345` |

---

## Claude Gaps — Fields in Raw JSONL Not Yet Extracted

Implemented in `meter.py` as `state.edge_telemetry` (Claude sessions only).

### Session-level fields on every `user` record

```json
{
  "type": "user",
  "gitBranch": "main",
  "entrypoint": "cli",
  "version": "2.1.196"
}
```

| Field | Notes |
|---|---|
| `gitBranch` | Git branch active when the turn was sent |
| `entrypoint` | `cli` or `vscode` — how Claude Code was launched |
| `version` | Claude Code CLI version (e.g. `2.1.196`) |

### Extra fields in the `usage` block

```json
{
  "service_tier": "standard",
  "speed": "standard",
  "cache_creation": {
    "ephemeral_5m_input_tokens": 15107,
    "ephemeral_1h_input_tokens": 0
  },
  "server_tool_use": {
    "web_search_requests": 0,
    "web_fetch_requests": 0
  }
}
```

| Field | Notes |
|---|---|
| `service_tier` | `"standard"` — billing tier for the request |
| `speed` | `"standard"` or `"fast"` — model speed setting |
| `cache_creation.ephemeral_5m_input_tokens` | Short-lived cache tier (5 min) |
| `cache_creation.ephemeral_1h_input_tokens` | Longer-lived cache tier (1 hr) |
| `server_tool_use.web_search_requests` | Web search tool calls this request |
| `server_tool_use.web_fetch_requests` | Web fetch tool calls this request |

### `stop_reason` on assistant messages

Present as `message.stop_reason` but not aggregated into a completion rate.

| Value | Meaning |
|---|---|
| `end_turn` | Model finished naturally — task complete signal |
| `tool_use` | Turn ended to invoke a tool — session still in progress |
| `max_tokens` | Hit token limit — potential truncation |

---

## Claude Gaps — Derived Metrics Not Yet Computed

Implemented under `state.edge_telemetry.derived` and `state.edge_telemetry.stop_reasons`.

| Metric | How to derive | Where to look in JSONL |
|---|---|---|
| Task completion rate | `stop_reason == "end_turn"` ÷ total sessions | `message.stop_reason` on assistant records |
| Tokens per tool call | total session tokens ÷ tool call count | `usage` blocks + `tool_use` block count |
| Tokens per turn | total session tokens ÷ user message count | `usage` blocks + `type == "user"` count |
| Files edited per session | unique `path` args in `Edit`/`Write` tool calls | `tool_use` blocks where `name` is `Edit` or `Write` |
| Repeat queries | hash normalized user message text, compare against `~/.claude/history.jsonl` | `type == "user"` message content |

---

## What Is Not Available at the Edge

| Data point | Notes |
|---|---|
| Lines of code / commits / PRs | Only in Anthropic's cloud Analytics API |
| Accepted vs rejected edits | Not in JSONL — exists only in Cursor/Copilot UI layer |
| Cross-user / org-wide view | Each machine only sees its own logs |
| Claude.ai web usage | No local logs written |
| Claude iOS / mobile usage | No local logs written |
| Thinking tokens (separate count) | Folded into `output_tokens`; not broken out |
