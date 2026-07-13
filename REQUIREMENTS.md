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
- **FR7 Budget alert** — page + notification when session cost crosses a cap;
  every newly observed session starts with its own $10 cap.
- **FR8 Spike alert** — page + notification when a single turn exceeds a threshold.
- **FR9 Insights** — plain-language readouts derived from the numbers (e.g.
  "output is 44% of spend", "cache saved you ~$X", "most expensive turn = $Y").
- **FR10 Browser notifications** — OS-level notifications for alerts/insights so
  it reaches you when the tab is backgrounded.
- **FR11 Post-mortem freeze** — when the session goes idle, present a static
  summary with the most expensive moment highlighted.
- **FR12 Global trace-waste evidence** — aggregate tool calls, returned text
  tokens, oversized results, exact immediate repeats, structured errors,
  project concentration, and last use across local sessions.
- **FR13 MCP review** — show traced MCP server usage, returned-token volume,
  failures, and last use as read-only evidence without assuming a universal
  configuration mechanism.
- **FR14 Claude Desktop attribution** — join Claude Desktop `local_*.json`
  metadata to the authoritative Claude project trace through `cliSessionId`,
  then show Desktop title, project, model, and source label without duplicating
  the underlying session.
- **FR15 Capability inventory** — keep a searchable Tools, MCPs, and Skills view
  with runtime, source, state, observed use, returned tokens, and last use.
- **FR16 Actionable capability utilization** — calculate optimization only over
  enabled user-installed configured skill packs. Keep MCP servers and
  built-in/runtime packs out of review candidates. Aggregate child skills under
  their real disable control, exclude default and read-only tools, assign
  runtime-and-origin-qualified identifiers, and keep session activity separate
  from cross-session optimization evidence.
- **FR17 Capability controls** — enable or disable configured skill packs
  through their native Codex/Claude enabled state; MCP servers, runtime-owned
  tools, and standalone skills remain read-only. Confirm pack changes and read
  the persisted setting back before reporting success.
- **FR18 Bulk unused disable** — confirm and disable an exact set of current
  unused review-candidate controls, reject stale, used, built-in, and runtime
  pack identifiers before mutation, and report partial failures explicitly.
- **FR19 Global guidance views** — open Global on an operational overview, add a
  date-selectable Daily summary from trace-backed day attribution, and provide a
  Learn view with a practical workflow and searchable glossary.
- **FR20 Read-only agent access** — expose a local stdio MCP server named
  `tokenmeter` with exactly three tools: `check` for matched-current-run
  decisions, `usage` for anonymous aggregate history, and `capabilities` for
  named optional setup evidence. Results must be verdict-first, bounded to
  three evidence points and one action, and link back to dashboard evidence.
- **FR21 User-scoped connection management** — let users connect Codex and
  Claude Code independently from a dedicated Settings tab, show the exact native CLI
  command before confirmation, manage only an exact Token Meter entry, refuse
  collisions, and show restart guidance. A dismissible Current callout may
  provide one-time discovery and routes directly to Settings. Tools
  remains focused on capability evidence, optimization, and controls.
- **FR22 Observed model throughput** — calculate weighted output tokens per
  measured second for completed trace windows. Prefer tool-free Claude turns
  and Codex tasks; for Codex tool-free work, subtract reported time to first
  token when it is valid. If only tool-bearing timing exists, label the result
  end-to-end because it includes tool time. Missing timing must render as
  unavailable, not zero. The numerator includes trace-reported reasoning and
  thinking output but excludes input, cache, and external tool-result tokens;
  the UI must disclose this in an accessible tooltip.
- **FR23 Models history** — provide a first-class top-level view with
  per-model input, output, cost, executions, output per execution, timing basis,
  timing coverage, weighted output throughput, daily speed/volume history, and
  equal-window speed comparison. Model and 7/30/90-day/all-history filters must
  persist locally and recompute every displayed aggregate from the same daily
  rows.
- **FR24 Frustration signals** — count a human user turn once when it contains
  one or more configured case-insensitive whole terms, report both matched
  utterances and raw term hits, and divide utterances by human user turns for
  the session/model/window rate. Provide per-chat, per-model, daily, and weekly
  views with 7/30/90-day/all-history filters. Store one machine-wide editable
  term list and recalculate all discovered sessions after an explicit save.
- **FR25 Navigation and command palette** — order the primary navigation by
  review workflow: Current, Daily, Logs, Global, Models, Frustration, Tools,
  Learn, and Settings. Provide a searchable command palette
  that opens with Command/Ctrl+K, supports arrow-key selection, Enter, Escape,
  and direct Option/Alt+1–9 navigation without firing inside editable fields.
  Current must retain its existing return-to-live behavior.
- **FR26 Wait time** — measure one completed user request from prompt/task start
  through the completed response as end-to-end wall-clock time. Reasoning and
  tool use count because the user is still waiting; gaps between requests do
  not. Preserve reported provider duration when available and label timestamp
  fallback as observed. Show cumulative, average, longest, and sample-count
  evidence where appropriate across Current, Logs, Global, Models, and Daily.
  Provide request-level, model/day, and daily trend charts without conflating
  wait time with output-token throughput.

## 5. Non-functional requirements

- **NFR1 Local-only** — no network egress; nothing leaves the machine.
- **NFR2 Zero-install** — Python stdlib only; one file; `python3 meter.py`.
- **NFR3 Passive by default** — no command to run per session; it finds you.
  Configuration changes occur only after an explicit dashboard confirmation.
- **NFR4 Calm** — silent/neutral when normal; loud only on real events.
- **NFR5 Cheap to run** — negligible CPU; tails by mtime + reparse.
- **NFR6 Bounded disclosure** — current detail is limited to the caller's
  matched runtime/project; historical results omit run and project identity;
  MCP and skill names appear only for explicit capability review. No MCP result
  may contain prompts, messages, reasoning text, tool arguments/results,
  credentials, environment variables, config values, or local paths. Setup must
  disclose that returned metrics enter the connected agent context and may be
  processed by that client's model provider.
- **NFR7 Read-only and on demand** — agent tools cannot mutate configuration,
  stop a run, or change budgets, and they do not imply continuous monitoring.
- **NFR8 Honest performance semantics** — call the metric observed output
  throughput, not raw provider decode speed. Disclose sparse sample coverage,
  distinguish tool-free from end-to-end timing, and aggregate tokens divided by
  seconds rather than averaging per-execution rates.
- **NFR9 Honest wait semantics** — define wait as prompt-to-completed-response
  wall time, disclose that cross-log totals are cumulative agent wait rather
  than de-duplicated human time, and leave missing completed timing unavailable
  instead of rendering it as zero.

---

## 6. Architecture

```
~/.claude/projects/*/*.jsonl   (Claude writes per-turn)
~/Library/Application Support/Claude/claude-code-sessions/**/local_*.json
                              (Claude Desktop attribution metadata)
~/Library/Application Support/Claude-3p/{claude-code-sessions,local-agent-mode-sessions}/**
                              (third-party-provider Desktop/Cowork metadata and traces)
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
- Cross-session tool, MCP, and skill utilization cards in Current Summary,
  rendered from the same capability summary as Tools.
- A trace timeline that grows as SSE state changes.
- An adjustable input/output chart with Linear, Sqrt, and Log Y-scale modes.
- Tools and MCP usage by namespace and by execution.
- Existing semantic split, insights, alerts, and post-mortem pinning. The
  standalone Efficiency panel was retired; its normalized analysis data remains
  available to headline metrics and insight generation.

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

The dropdown answers the three factual questions that matter mid-session:

- Which run and model is selected?
- How much has it cost and how much context is in use?
- What observed output speed does the trace support?

It shows provider/project, live or idle state, active model, cost, compact token
count, context pressure and a small context bar, observed output speed with
timing provenance, cache reuse, and last execution cost. It does not add Now,
Action, or Status rows. Navigation actions open the dashboard, daily brief,
current trace, Tools, or quit the companion. These actions stay
together at the top of the menu. Their URLs are deterministic: Open Dashboard targets
`#summary`, Open Daily Brief targets `#daily`, Open Trace targets `#activity`,
and Tools targets `#capabilities`. Dashboard tab changes preserve the
selected panel in the URL for direct links and reloads.
The dropdown also lists up to five recently active sessions with a Claude or
Codex icon, provider label, and title/project identifier. Selecting a row pins
the native companion to that session across polls and restarts; `Follow Latest`
clears the pin. A pinned session remains visible alongside four newer sessions
if it falls outside the newest five, and Dashboard/Trace actions preserve the
pinned session path.
Cache remains a single compact summary row; the detailed read/write/saved/latest
breakdown is intentionally omitted from the menu bar.

### 12.2 Server interface

`GET /state` returns the full normalized dashboard state for local integrations
that need it. `GET /menubar` returns a compact subset for frequent polling:

```text
ok, provider, source.label, source.id, source.project, source.pricing_note,
session, project, model, total_cost, cost_approx, total_tokens, turns,
throughput.available, throughput.output_tps, throughput.basis,
throughput.sample_count, throughput.timing_coverage,
context.latest, context.window, context.latest_pct, last_turn_cost,
idle_s, ended, activity, recommendation, insights[0..3], selection,
recent_sessions[0..4], ts
```

`GET /menubar?session=<id>` returns the same compact payload recomputed for one
specific session. The server does not persist a pin; the native client stores
the selected ID locally and includes it on subsequent polls.

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

---

## 13. v5 — global trace-waste insights and MCP actions (2026-06-30)

The Global view now answers a narrower, evidence-backed question: which tools
and MCP namespaces create the most observable result volume across sessions,
and which calls show straightforward waste signals in the logs? The feature
does not attempt to reconstruct the complete system prompt or attribute exact
billing tokens to schemas.

### 13.1 Observable evidence

Claude and Codex traces provide tool identity, call order, arguments, result
payloads, timestamps, projects, and token-count events. Token Meter derives:

- Approximate returned text tokens using four characters per token.
- Oversized calls with at least 8,000 returned text tokens.
- Exact immediate repeats when consecutive calls to the same tool have the same
  hashed arguments within five minutes. Raw arguments are not stored in the
  Global aggregate.
- Structured errors from Claude `is_error` and conservative Codex status,
  success, exit-code, and error fields.
- Sessions used, projects used, last use, and runtime-reported catalog exposure.

Embedded image data and long base64 fields are removed before text-token
estimation. A result can be oversized, repeated, and failed simultaneously;
the headline `flagged_tokens` total includes that result once, while the reason
counts remain separate.

### 13.2 Global and Logs interfaces

The Global page keeps its spend summary visible and uses `Overview`,
`Global insights`, and `Capability evidence` subtabs. The searchable and
sortable log inventory lives in a dedicated top-level `Logs` route. Changes to
any discovered log invalidate the cross-session snapshot and are published
within two seconds, independently of which runtime owns the newest log. The
SSE delivery coalesces a full per-client queue to the newest snapshot; queue
pressure must not remove a still-connected browser subscriber. The insight
panel adds:

- Returned tokens, uniquely flagged tokens, oversized calls, errors, and exact
  repeats.
- A ranked horizontal capability payload chart.
- A 14-day total-versus-flagged tool-result chart.
- Plain-language Global insights using the same insight schema as Session.
- A bounded capability table with usage, returned tokens, errors, last use,
  project concentration, and recommendation.

Recommendations are limited to what the trace supports: narrow large results,
reduce exact repeats, fix repeated failures, scope project-concentrated tools,
or review an MCP function that a runtime catalog reported across at least five
sessions without any observed call.

### 13.3 MCP configuration boundary

For `mcp__server__tool` evidence, Token Meter reports usage, result volume,
failures, and last use. MCP configuration remains read-only because Codex,
Claude, desktop clients, plugins, and managed installations do not share one
safe configuration mechanism. Token Meter does not present a mutation action or
write MCP server configuration.

---

## 14. v6 — Claude Desktop project attribution (2026-06-30)

Claude Desktop project sessions have two local records. Standard project usage
and tool traces remain normal Claude JSONL under `~/.claude/projects` with
metadata beneath `~/Library/Application Support/Claude/claude-code-sessions`.
Agent/Cowork session metadata is stored beneath the standard Claude data root
under `local-agent-mode-sessions`, and the authoritative JSONL is nested inside
that session's `.claude/projects` directory. Metadata holds the Desktop session
id, title, cwd, model, timestamps, and a `cliSessionId` which identifies the
JSONL.

Token Meter indexes the targeted metadata locations, joins by `cliSessionId`,
and parses only the authoritative JSONL. This keeps one logical session and one
cost total while allowing Current and Global views to show `Claude Desktop`,
the Desktop title, and the actual project directory. For Agent sessions,
`userSelectedFolders` supplies the project when the runtime cwd is only the
managed `outputs` folder; only sessions without a selected folder are labeled
`No project`. A missing
metadata file remains a normal `Claude Code` source, and malformed Desktop
metadata is skipped without affecting other logs.

Regular Claude Desktop cloud conversations are outside this join: Claude does
not write their billable usage and tool trace to the local agent JSONL store.
The dashboard therefore reports this source boundary and the latest local
Agent/Cowork metadata instead of inventing token totals for a cloud chat.

---

## 15. v7 — capability inventory, utilization, and controls (2026-06-30)

The Tools tab combines trace evidence with local runtime configuration.
It intentionally keeps three evidence levels separate:

- Tools are runtime-reported when present in Codex `dynamic_tools`; built-in
  tools absent from that partial catalog can still be listed as observed-only.
- MCP server availability comes from Codex/Claude configuration and traced MCP
  calls; historical use comes from traced MCP calls.
- Skills come from installed Codex skill descriptors and Codex/Claude plugin
  caches. Activation is inferred only when a tool-call argument references a
  concrete `SKILL.md` path, so the UI labels skill utilization as inferred.

Current Summary and Tools answer different questions. Current Summary
reports activity for the selected log: observed tool types, total calls, and the
maximum calls in one execution. Those counts may include default tools and
result-only instrumentation because they explain what happened. They are never
used as a removal denominator.

Tools reports optimization at the removable skill-pack level. One
configured user plugin pack is one group regardless of how many skills it
contains. Any inferred skill activation marks its group used. MCP servers,
default tools, standalone skills, Cowork built-ins, Codex/Claude runtime packs,
and other read-only entries remain in the inventory but cannot become review
candidates. Every skill is identified by runtime, origin or plugin pack, and
skill name so same-named built-in and user-installed skills cannot collide.
Unused deferred definitions are not treated as initial-context waste.

Runtime catalogs expose descriptions and input schemas. Token Meter estimates
their definition size with four characters per token, then aggregates total,
eager, deferred, and unused-eager definition tokens. An eager definition is
unused for a session when that runtime advertised it without deferred loading
and the trace never called the same tool name. These are prompt-overhead
estimates repeated across sessions, not provider billing-token claims.
Definition-token totals remain evidence about prompt composition; they do not
enter the removable skill-pack review calculation because traces do not provide
a reliable mapping from tool schemas to installed skill packs.

`POST /capability/toggle` requires a local-origin action token. Skill actions
update the containing configured plugin pack because neither runtime exposes a
safe universal per-skill switch.
Standalone skills, Cowork built-ins, unmanaged plugin caches, and native tools
are displayed as read-only. The server validates the exact discovered control
identifier, reads the persisted enabled value back, and only then reports
success with a refreshed capability snapshot. All successful changes require a
runtime restart.

`POST /capability/disable-unused` accepts only the exact control identifiers in
the current optional-capability review set. The server validates the full list
before changing any setting, applies accepted controls sequentially, refreshes
capability state once, and reports any partial failure. The dashboard shows the
runtime and pack/server list in a separate confirmation dialog.

---

## 16. v8 — Global overview, Daily, and Learn (2026-07-01)

Global opens on an overview. The landing view combines
total and current-day spend, log/execution/token volume, runtime and model mix,
cross-log priorities, and direct access to the highest-cost logs. Logs, global
insights, and capability evidence remain separate destinations; Logs is a
top-level tab, while the other two remain Global subtabs.

Daily uses per-day cost already attributed from trace usage records. For each
recorded day it reports estimated spend, active logs, distinct projects,
provider cost, and highest-cost logs for that day. It does not invent daily
token or execution counts when traces do not provide reliable attribution.

Learn is a static, local guide to the operating workflow: inspect Current,
locate spikes in Activity, confirm repeated patterns in Global and Daily, review
runtime-qualified capability controls, and close with a daily comparison. Its
glossary defines the cost, token, cache, context, timing, tool, and capability
terms used by the dashboard.

---

## 17. v9 — frustration signals (2026-07-07)

Frustration analytics are lexical and trace-backed, not inferred sentiment.
Token Meter reads only human user-message boundaries, excluding Claude tool
results, metadata, sidechains, and duplicated Codex response records. A turn
with at least one configured term contributes one utterance; repeated words
also contribute to the separate term-hit count. The denominator is human user
turns, never assistant executions.

The top-level Frustration view compares the same aggregate across chats,
models, local calendar days, and Monday-based weeks. Daily rows remain the
source for finite-window KPIs even when the trend display switches to weeks, so
changing chart granularity does not change the selected time window.

Settings persists the normalized term list in
`~/.token-meter/settings.json`. `POST /settings/frustration` requires the same
local action token as other dashboard mutations, clears cached session
summaries, and republishes the recalculated cross-session snapshot. Parser
events retain timestamps, model identity, and aggregate matches only; raw user
message text is not included in the frustration payload.

---

## 18. v10 — wait time (2026-07-13)

Wait time is the end-to-end wall clock from a user prompt or task start until
the completed response. It deliberately includes model reasoning, tool calls,
and tool execution. Output speed remains a separate output-token throughput
metric and can still prefer tool-free generation timing.

Claude uses `turn_duration.durationMs` when present and otherwise falls back to
the observed human-prompt-to-final-assistant span. Claude tool-result messages
do not begin new waits, and split assistant records are deduplicated by message
identity. Codex uses `task_complete.duration_ms`, with an observed task-start
fallback when the completion exists without a reported duration. Incomplete
requests do not become completed wait samples.

Current shows cumulative wait plus a request-level chart. Logs adds filtered
wait totals and Wait sorting. Global shows cumulative agent wait with an
explicit parallel-overlap caveat. Models compares average wait by model and
switches its daily line between output speed and average wait. Daily reports
total and average completed-request wait and switches its trend between Spend
and Wait.
