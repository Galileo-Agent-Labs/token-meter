# Live Token Meter — Requirements & Research

A taxi-meter for Claude Code and Codex sessions. Sits in your browser, tails
your active local agent session log, and shows in real time what the session is
costing and doing — with notifications, trace events, and insights when
something matters. Local, passive, always-on.

---

## 1. The core insight

People only think about token usage in **three moments**, and they want
different things in each:

| Moment | Feeling | Want | Has a good tool today? |
|---|---|---|---|
| **Mid-session (now)** | "Is this about to cost a fortune / spiral?" | A live glanceable meter | ❌ **No** — this is the gap |
| **Right after** | "What did that just cost, and why?" | A fast post-mortem | partial |
| **Over time** | "Am I spending more?" | A trend dashboard | yes, but nobody opens it |

The first moment has the **strongest emotional pull** and **no good tool**, so
that is where this product aims. The analogy: a monthly fitness report vs. the
live heart-rate number on a watch. The watch wins — not because the data is
richer, but because it's *there in the moment you care, without being asked*.
**The meter is the watch.** Post-mortem and trend views hang off the same engine.

**Design principle that separates tools people use from tools people install
and forget:** you never have to remember to run anything. It tails the log as
it's written, so it's ambient and always-on.

### 1.1 Hero use case: runaway-session guardrail

The sharpest product wedge is not "token analytics" in the abstract. It is the
developer who starts a long Claude Code or Codex run, switches context, and then
worries: **is the agent still making progress, or is it burning context, tool
output, and money?**

The hero scenario:

1. A developer starts Token Meter once and leaves it open on localhost.
2. They ask Claude Code or Codex to handle a large repo task: migration,
   refactor, test repair, incident investigation, dependency update, or research
   spike.
3. Token Meter follows the newest session automatically and shows live cost,
   context pressure, semantic token split, input/output trajectory, tools used,
   and trace events.
4. Browser notifications interrupt only when a budget/spike/insight matters.
5. The developer uses the Summary, Tools, and Activity tabs to decide whether to
   let the agent continue, stop it, summarize context, or restart with a tighter
   prompt.

This is why people would choose it over a generic spend dashboard: it answers a
live operational question at the moment of anxiety, while the session is still
recoverable. The post-mortem and global views remain valuable, but they support
the hero job rather than replace it.

---

## 2. Research findings (verified against real `~/.claude` logs)

Before building, we verified the open questions against real session logs on
this machine. Findings:

### 2.1 Logs flush live ✅
The newest session `.jsonl` had an mtime **19 seconds old** while a session was
active — lines are appended **per turn** as Claude writes, not dumped at session
close. **The streaming meter is viable and genuinely continuous (per-turn).**

### 2.2 The usage block is a *billing* breakdown, not a *semantic* one
Real per-turn shape:

```json
{
  "input_tokens": 2,
  "cache_creation_input_tokens": 10466,
  "cache_read_input_tokens": 14236,
  "output_tokens": 870,
  "server_tool_use": { "web_search_requests": 0, "web_fetch_requests": 0 },
  "service_tier": "standard",
  "cache_creation": { "ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 10466 }
}
```

**Key consequence:** the log buckets by **billing category** (fresh input,
cache-write, cache-read, output), *not* by semantic activity (reasoning vs
retrieval vs coordination). So:

- **The cache split is the real, free story.** In a sampled turn, fresh input
  was *2 tokens* while cache-read was *14,236*. The most useful live signal is
  the **cache hit ratio** and **cache-write spikes** — that's what you're billed
  for differently, and it falls out of the schema with zero inference.
- **Semantic buckets ("a tool dumped a huge file", "subagents fired") are NOT
  in the usage block.** They require correlating each assistant turn with
  adjacent `tool_use` / `attachment` lines. Doable, but real engineering →
  deferred to a stretch goal.

### 2.3 Other verified facts
- **Model:** `claude-opus-4-8` (read from the log; drives the price table).
- **Timestamps:** every assistant line carries an ISO `timestamp` → burn rate
  (tokens/min, $/min) is computable.
- **Subagents:** marked by `isSidechain: true` → subagent turns are countable.
- **Stop reason:** `stop_reason: end_turn` marks turn completion → task proxy.
- **Tool payloads:** `tool_use` (in assistant content) and `tool_result` (in
  user content) pair via `tool_use_id` → tool output sizes are measurable.
- **Pre-flight cost ("what will this cost before I run it?") is impossible from
  the log** — it's all retrospective-by-one-turn. The meter shows the fare *as
  it accrues*, like a taxi meter; it does not quote you first.

### 2.4 Thinking content is REDACTED (key finding for analysis #2)
Across all sessions, 100 of 110 `thinking` blocks have an **empty** `thinking`
field — only a cryptographic `signature` is retained. So reasoning spend cannot
be measured token-for-token from content size. **We measure *presence* instead:**
what share of output tokens were produced on turns that engaged extended
thinking. Labeled `PRESENCE` in the UI so the proxy is honest.

### 2.5 The six deeper analyses (added 2026-06-24)
On top of the live meter, six efficiency analyses were specified and built:
1. **Reasoning share** *(presence proxy, see §2.4)* — thinking-turn output share.
2. **Model mix** *(cross-session)* — cost/tokens by model; Opus share flagged.
3. **Tool & retrieval bloat** — ranked tool-payload sizes (the context tax).
4. **Coordination tax** — `isSidechain` share of spend.
5. **Cost per task** — total ÷ `end_turn` count (proxy for accepted tasks).
6. **Spend trend & anomalies** *(cross-session)* — 14-day burn, >2.5× median flagged.

Analyses 2, 4, 5 derive from the current session (live). Analyses 3 is live too.
Analyses 2(model)/3(mix)/6(trend) marked cross-session use a cached 20s scan of
all logs. The visual design was also rebuilt to a professional dashboard
(panels, sparkline, mini-bars, pulse "live" indicator) per user feedback. Named explicitly
  so v1 doesn't over-promise.

---

## 3. Product: a local web page, three layers of one instrument

A small local process tails the active session log; on each new line it parses
the usage block, updates running totals, and pushes the change to a localhost
page over a live connection (SSE). Start once, leave the tab pinned, it moves
while you work. Fully local — nothing leaves the machine.

### Zone 1 — The Meter (gets you to look)
One big number: **dollars this session**. Beside it, token total and live burn
rate ($/min). Readable from across the room. Calm when normal.

### Zone 2 — The Breakdown (answers "why is it high")
A live stacked bar across the billing buckets — **cache-read / cache-write /
fresh input / output** — that fills and re-shapes as the session runs. Plus a
**cache-hit-ratio** readout. You literally watch the shape change.

### Zone 3 — The Alert + Notifications (so you don't have to stare)
Invisible until something's wrong, then the page goes red and a banner names
*what* happened ("last turn spiked to $0.44") or *what was crossed* ("session
passed your $5 cap"). Backed by **browser notifications** so it reaches you even
when the tab isn't focused. Quiet until it matters. Set budget + spike
sensitivity once.

**Why the three belong together:** meter alone is a number with no explanation;
breakdown alone is noise you'd ignore; alert alone says something's wrong but
not what. Together they're a single instrument. Each covers the others' weakness.

### Post-mortem falls out for free
When a session ends, the page freezes into a static summary of that run — same
numbers, most expensive moment highlighted. The "what did that just cost me"
moment is already on screen the instant you stop. No second tool.

---

## 4. Functional requirements

- **FR1 Auto-follow newest session** — always track the most recently written
  `.jsonl` across all projects; switch automatically as you move between projects.
- **FR2 Live updates** — push to the browser within ~1s of a new turn landing;
  no polling, no refresh, no manual command.
- **FR3 Live cost** — total USD, split into the four billing buckets, from a
  model-aware price table.
- **FR4 Burn rate** — tokens/min and $/min over session elapsed time.
- **FR5 Cache hit ratio** — `cache_read / (cache_read + cache_write)`.
- **FR6 Turn + subagent counts** — total turns and `isSidechain` turns.
- **FR7 Budget alert** — page + notification when session cost crosses a cap.
- **FR8 Spike alert** — page + notification when a single turn exceeds a threshold.
- **FR9 Insights** — plain-language readouts derived from the numbers (e.g.
  "output is 44% of spend", "cache saved you ~$X", "most expensive turn = $Y").
- **FR10 Browser notifications** — OS-level notifications for alerts/insights so
  it reaches you when the tab is backgrounded.
- **FR11 Post-mortem freeze** — when the session goes idle, present a static
  summary with the most expensive moment highlighted.

## 5. Non-functional requirements

- **NFR1 Local-only** — no network egress; nothing leaves the machine.
- **NFR2 Zero-install** — Python stdlib only; one file; `python3 meter.py`.
- **NFR3 Passive** — no command to run per session; it finds you.
- **NFR4 Calm** — silent/neutral when normal; loud only on real events.
- **NFR5 Cheap to run** — negligible CPU; tails by mtime + reparse.

---

## 6. Architecture

```
~/.claude/projects/*/*.jsonl   (Claude writes per-turn)
        │  tail newest by mtime, reparse on change
        ▼
   watcher thread ──► recompute(state) ──► publish()
        │                                     │ SSE
        ▼                                     ▼
   price table                        localhost:8722  ──►  browser page
   (claude-opus-4-8)                                       meter · bars · alerts
                                                           + Notification API
```

- **Tailer/parser** (`recompute`): reads the file, sums usage across `assistant`
  turns, computes cost/burn/cache/biggest-turn. Stateless recompute keeps it
  simple and correct.
- **Watcher thread**: every 0.5s, find newest log; if file or mtime changed,
  recompute and publish.
- **HTTP server**: serves the page at `/` and an SSE stream at `/events`.
- **Browser page**: renders three zones; fires `Notification` on alerts/insights.

## 7. Pricing (USD per 1M tokens, `claude-opus-4-8`)

| Bucket | Rate |
|---|---|
| input | $15 |
| output | $75 |
| cache write (5m) | $18.75 |
| cache read | $1.50 |

Edit `PRICE` in `meter.py` if your contract rates differ.

---

## 8. Scope

**In (v1):** auto-follow, live meter, billing-bucket breakdown, cache ratio,
burn rate, budget + spike alerts, browser notifications, insights, post-mortem
freeze.

**Out (later):** *(NOTE — §10 below supersedes this; semantic buckets and the
cross-session trend/list were since BUILT in v2.)*
- **Pre-flight estimation** — not derivable from the log (see §2.3). Still out.
- **Semantic buckets** — ✅ built in v2 (see §10).
- **Trend dashboard** across sessions — ✅ built in v2 as the Global tab (§10).
- **"Sentry for tokens"** SDK/proxy for production apps — a *different product*
  on a different surface (team/app users, not a person mid-session). Same
  parsing engine grows into it once the meter is proven. Still out.

## 9. Run it

```bash
python3 meter.py
# → http://localhost:8722
# Toggle notifications in the top bar. Leave the tab pinned. It moves as you work.
```

---

## 10. v2 — tabs, multi-session, per-turn chart (2026-06-24)

A second iteration added a Session/Global tab split, cross-session tracking, a
per-turn I/O chart, and — most importantly — fixed a cost-doubling bug.

### 10.1 CRITICAL correctness fix: dedupe by `message.id`
One API response is split across several JSONL lines — one per content block
(thinking / text / tool_use) — and **each line repeats the same usage block**.
Summing per line **double-counts cost ~2.3×** (verified: 120 assistant lines →
57 real messages; deduped total $16.79 vs. buggy $38.53). The parser now groups
lines by `message.id`, merges their content blocks, and counts one usage block
per message. This affects every number in the app, so it is the most important
change in v2.

### 10.2 Timestamp fix: logs are UTC
Timestamps carry a trailing `Z` (UTC). Parsing them as local time (`mktime`)
offset idle/elapsed by the local UTC offset and wrongly marked live sessions as
"ended". Now parsed with `calendar.timegm`; display strings use `localtime`.

### 10.3 Two tabs
- **Session** — the active run, live. Adds, on top of v1: a **session header**
  (repo/project path + session file name), the **semantic "where the tokens go"
  bar** (reasoning / output / retrieval / coordination), and the **input/output
  per-turn chart** (log-scale Y = tokens, X = turn; dots mark tool/subagent
  turns). Freezes into a post-mortem after >90s idle.
- **Global** — all sessions sorted newest-first (scanned from every `.jsonl`),
  each row showing title (first real user message), project, time, turns, model
  badges, tokens, cost. Click a row to open that session's **frozen post-mortem**
  (served by a new `GET /session?id=<id>` endpoint). Plus total spend, model-mix
  bars, and the 14-day spend-trend sparkline with anomalies (>2.5× median).

### 10.4 Semantic buckets — definitions & honest caveat
- **reasoning** = output tokens on turns that engaged extended thinking
  (thinking *content* is redacted in logs — §2.4 — so this is turn-level)
- **output** = remaining (non-thinking) output tokens
- **retrieval** = `tool_result` payload tokens (÷4 chars/token) re-entering context
- **coordination** = output tokens on `isSidechain` (subagent) turns

Caveat: these buckets mix token *types* (reasoning/output/coordination are
output tokens; retrieval is input tokens), so the bar is a shape-watching view,
not a strict partition of one token pool. Flagged for a future "mutually
exclusive buckets" option.

### 10.5 Multi-model pricing
`PRICE` is now a per-model table (opus-4-8, fable-5, sonnet-4-6, haiku-4-5) so
the Global model-mix cost split and downgrade math are accurate.

### 10.6 UI/UX
- **Notification toggle moved to the top bar**; persisted in `localStorage`.
  Fixed a bug where insights were marked "seen" even while notifications were
  off (so enabling later fired nothing) — now only marked when actually fired,
  with an immediate confirmation notification on enable and a clear message when
  the browser has blocked notifications.
- **Budget / spike thresholds** are `type=number` inputs persisted to
  `localStorage` (fixes "I can't change the budget").
- **Chart axes bug**: CSS `var()` does not resolve in SVG presentation
  attributes (`fill="var(--x)"`), which silently dropped the chart fills, axes,
  and labels. Now uses concrete hex colors.

### 10.7 Files (v2)
The UI moved out of `meter.py` into **`page.html`** (served at `/`), so the
dashboard can be edited without touching Python. `meter.py` is now server +
parser (with dedup) + analyses + the `/events` SSE and `/session` endpoints.

---

## 11. v3 — Codex sessions, normalized schema, trace, tools, and polish (2026-06-24)

This iteration extends the product from Claude Code-only to Claude Code plus
Codex. It also turns the dashboard from a cost meter with analyses into an
execution instrument: a user can now see which execution happened, which tools
or MCP namespaces were used, what came back from those tools, and how token
flow changed across executions.

### 11.1 Codex log support

Local Codex session logs were verified under:

```text
~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
```

The Codex session index lives at:

```text
~/.codex/session_index.jsonl
```

Codex token accounting differs from Claude. Claude uses assistant
`message.usage` blocks, and one logical assistant response can be split across
multiple JSONL lines. Codex uses `event_msg` payloads where
`payload.type == "token_count"`. Each `token_count` event has
`last_token_usage` for the latest execution and `total_token_usage` for the
cumulative session. Token Meter sums `last_token_usage` slices.

Codex tool calls are `response_item` payloads with `payload.type` equal to
`function_call`, `custom_tool_call`, or `web_search_call`. Results are
`function_call_output` or `web_search_end`, joined by `call_id` when present.

### 11.2 Normalized schema

`meter.py` now normalizes both providers before the UI sees data. The frontend
renders the following provider-independent fields:

```text
provider       "claude" or "codex"
source         provider label, id, path, project, pricing note
executions     per-execution tokens, cost, tools, model, timing
trace          timestamped execution events
tools          by-name, by-namespace, and by-execution rollups
chart          chart series and default scale hint
semantic       reasoning/output/retrieval/coordination token split
analyses       reasoning, model mix, tool bloat, coordination, cost per task
```

An **execution** is one billable model step or turn-like slice:

- Claude Code: one deduped assistant message with usage.
- Codex: one `token_count` event and the nearby message, reasoning, tool-call,
  and tool-result events since the previous token count.

### 11.3 Dashboard polish

The visual design was rebuilt around a dark-only operational palette, tighter
cards, and denser dashboard surfaces. The Session view now includes:

- Execution overview cards.
- A trace timeline that grows as SSE state changes.
- An adjustable input/output chart with Linear, Sqrt, and Log Y-scale modes.
- Tools and MCP usage by namespace and by execution.
- Existing semantic split, efficiency analyses, insights, alerts, and
  post-mortem pinning.

The chart defaults to linear scale because that is the normal interpretation of
the Y axis. Sqrt and log remain available when very large input values compress
the output line.

### 11.4 Notifications fix

Notification behavior was tightened. The toggle now paints permission state,
uses a safe permission request flow, sends an immediate enable-confirmation when
allowed, and dedupes budget/spike/insight notifications by stable event keys.
Pinned post-mortems do not fire notifications.

### 11.5 Pricing note

Claude pricing uses the local `CLAUDE_PRICE` table. Codex uses the local
`OPENAI_PRICE` table and is labeled as estimated because Codex subscription
billing can differ from public API-rate accounting. The user can edit either
table in `meter.py`.

---

## 12. v4 — macOS menu bar companion (2026-06-25)

The next product move is to make Token Meter visible even when the browser
dashboard is covered. The menu bar companion is intentionally small: it does not
parse logs, scan files, price tokens, or duplicate dashboard logic. It polls a
compact localhost endpoint exposed by `meter.py` and turns the current run into
a glanceable native status item.

### 12.1 User-visible behavior

The menu bar title is a short live summary:

```text
TM $0.42 38% ctx
TM ! $1.80 71% ctx
TM !! $3.24 88% ctx
TM off
```

The dropdown answers the three operational questions that matter mid-session:

- Is the agent run okay?
- How much has it cost?
- Should I intervene now?

It shows provider/project, live or idle state, current activity from the latest
trace event, recommended action, cost, compact token count, context pressure, a
small context bar, last execution cost, a verdict (`Healthy`, `Watch closely`,
`Intervene now`, `Idle`, or `Server offline`), the top signal, and actions to
open the dashboard, jump to the current execution, or quit the companion.

### 12.2 Server interface

`GET /state` returns the full normalized dashboard state for local integrations
that need it. `GET /menubar` returns a compact subset for frequent polling:

```text
ok, provider, source.label, source.id, source.project, source.pricing_note,
session, project, total_cost, cost_approx, total_tokens, turns,
context.latest, context.window, context.latest_pct, last_turn_cost,
idle_s, ended, activity, recommendation, insights[0..3], ts
```

The compact endpoint exists because the full dashboard payload can include
trace, execution rows, chart series, tools, and global history. A native menu
bar item polling every two seconds should not fetch that heavier payload.

### 12.3 Implementation choice

The companion is a single Swift/AppKit source file:

```text
menubar/TokenMeterMenuBar.swift
```

It is optional and native to macOS. The existing zero-install browser dashboard
remains Python stdlib-only; the Swift toolchain is needed only for users who
want the menu bar companion. `scripts/run-menubar` compiles the Swift file into
`.build/token-meter-menubar` and executes it.

`scripts/start-token-meter` is the login-item entrypoint. It starts
`python3 meter.py` if `/health` is not ready, waits briefly for the local server,
and then runs the menu bar companion. `scripts/install-launch-agent` writes a
user LaunchAgent for that entrypoint, and `scripts/uninstall-launch-agent`
removes it.
