# MCP Trace and Statistics Query Design

## Status

Proposed for user review on 2026-08-21.

## Goal

Expand Token Meter's local, read-only MCP from three opinionated insights into a
general query surface for session discovery, detailed standardized traces,
sanitized runtime-native structure, and bounded statistics. An MCP client must
be able to select recorded sessions, extract comparable evidence across
runtimes, and calculate its own experiment results without Token Meter running
prompts, evaluating output quality, or storing experiment state.

The default contract is parsed and standardized evidence. A diagnostic native
view preserves useful runtime-specific structure through explicit per-adapter
allowlists. It is not a byte-faithful raw trace and never returns provider-owned
content.

## Product Boundary

Token Meter remains a passive evidence layer:

- every new MCP tool is read-only, idempotent, and local over stdio;
- Token Meter does not invoke Codex, Claude, Cursor, OpenCode, Kiro, models, or
  evaluators;
- Token Meter does not create experiments, attach labels, or record outcomes;
- MCP clients may compare returned evidence however they choose;
- existing `check`, `usage`, and `capabilities` tools remain compatible.

The repository's privacy boundary remains authoritative. MCP output must not
contain prompts, assistant responses, reasoning text, tool arguments or
results, credentials, account data, configuration values, trace/database
paths, raw exceptions, or byte-faithful raw trace rows. This design therefore
does not add an unredacted-content setting. Such a setting would be a separate
security-policy decision, not an incidental query option.

## Alternatives Considered

### Standardized data only

This is the smallest and safest surface and is sufficient for cross-runtime
statistics. It does not let developers diagnose information lost during
normalization or inspect runtime-specific structural signals.

### Raw trace passthrough

This preserves maximum source fidelity but violates the repository's privacy
contract, creates incompatible runtime schemas, bypasses lineage and cumulative
usage corrections, and can inject unbounded provider-controlled content into a
connected model. It is rejected.

### Standardized data plus sanitized native structure

This is the selected approach. Standardized evidence is the stable comparison
contract. A separate view retains explicitly allowlisted native event types,
subtypes, numeric usage, timestamps, statuses, and relationships for debugging
and experimental analysis without returning content payloads.

## MCP Tool Surface

The server adds four tools. Existing tools keep their current schemas and
behavior.

### `sessions`

Discover sessions that may be passed to the other query tools.

Inputs:

- `scope`: `current_project` or `all`, default `current_project`;
- optional `runtime`, `client`, `model`, `state`, `start`, and `end` filters;
- `cursor` and `limit`, with a default of 20 and maximum of 100.

Each result contains only an opaque session ID, runtime, client, model/provider,
start/end or last-activity timestamps, current/completed state, and metric
availability. Titles, project names, paths, prompt-derived labels, and trace
locators are excluded. `current_project` uses the existing caller-context match
without returning the matched project value.

### `trace`

Return paginated evidence for one explicit session ID.

Inputs:

- required `session_id`;
- `view`: `standardized` or `native_structure`, default `standardized`;
- optional `sections`: `session`, `executions`, `events`, `tools`, `context`,
  `coverage`, and `warnings`;
- optional execution number and event-type filters;
- `cursor` and `limit`, with a default of 50 and maximum of 200.

The standardized view returns the canonical envelope described below. The
native-structure view returns the same safe session header plus adapter-owned,
allowlisted structural records. Neither view accepts an `include_content` or
unredacted option.

### `stats`

Calculate bounded aggregates from standardized evidence.

Inputs:

- one to eight allowlisted `metrics`;
- zero to three allowlisted `group_by` dimensions;
- the same runtime, client, model, state, session, and time filters used by
  `sessions`;
- optional sort metric/direction;
- `limit`, default 20 and maximum 100.

Initial metrics are session count, execution count, model calls, input tokens,
output tokens, cache-read tokens, cache-write tokens, total tokens, estimated
cost, active seconds, wait seconds, TTFT, tool calls, tool-result tokens,
attempts, retries, failed attempts, latest context, and peak context. Initial
dimensions are runtime, client, model provider, model, day, session ID, tool
category, and tool name.

There is no arbitrary SQL, expression language, filesystem selector, regex, or
provider-field path. Statistics use the normalized and lineage-corrected data
path; they never aggregate native-structure records.

### `schema`

Describe queryable fields without requiring a caller to guess runtime support.

Inputs select `sessions`, `standardized_trace`, `native_structure`, or `stats`,
plus an optional runtime. Output includes schema version, field names, types,
units, metric definitions, availability semantics, supported filters and
dimensions, approximation rules, and runtime coverage. It contains no sampled
session data.

## Canonical Standardized Trace

The trace envelope is versioned independently from the MCP server:

```json
{
  "schema_version": "1.0",
  "session": {},
  "executions": [],
  "events": [],
  "tools": [],
  "context": {},
  "coverage": {},
  "warnings": [],
  "page": {"next_cursor": null, "truncated": false}
}
```

The session header contains opaque ID, runtime/client, model/provider, timing,
state, totals, and evidence availability. Each execution may contain timestamps,
model, token/cache/cost evidence, active/wait/TTFT timing, context/window use,
model and tool call counts, attempts, retries, failed attempts, and subagent or
parent relationships when safely observable.

Events use a runtime-neutral type vocabulary: `start`, `user`, `model`,
`reasoning`, `tool_call`, `tool_result`, `usage`, `context`, `compaction`,
`coordination`, `complete`, and `error`. They may include sequence, timestamp,
execution, parent event, model, tool identity/category, status, numeric tokens,
numeric cost, duration, and severity. Labels are fixed Token Meter labels or
sanitized tool/model identities; event detail and provider content are absent.

Every metric carries or references:

- availability: measured value, estimated/inferred value, or unavailable;
- unit and semantic definition;
- source runtime and relevant pricing basis;
- approximation and partial-coverage warnings.

Unavailable evidence remains unavailable rather than becoming zero.

## Sanitized Native Structure

Each runtime adapter may implement a native-structure projector. The projector
returns a shared wrapper around adapter-specific allowlisted fields:

```json
{
  "sequence": 19,
  "timestamp": 1787254000.0,
  "native_type": "response_item",
  "native_subtype": "function_call",
  "execution": 3,
  "status": "completed",
  "numeric": {"output_tokens": 420},
  "relationships": {"parent": "opaque-event-12"}
}
```

Projectors must be positive allowlists, not recursive redactors. Unknown keys
are dropped. Text fields are restricted to fixed native type/subtype/status
enums and sanitized model/tool identities. IDs are converted to response-local
opaque references unless the value is already the selected public session ID.
Paths and arbitrary strings never cross the adapter boundary.

If a runtime has no native-structure projector, the view returns a bounded
`unavailable` result with its coverage reason. Adding support belongs in that
runtime's adapter and fixtures; shared MCP or statistics code must not branch on
runtime names.

## Pagination and Freshness

Session and trace cursors are opaque, URL-safe values bound to the query shape,
session ID, trace revision, view, filters, and next position. A cursor is not a
path or provider identifier. If the underlying trace changes, the server
returns `stale_cursor` and instructs the caller to restart from the first page.

Exact numeric totals are computed across the complete available scope before
pagination. Event and group rows are bounded. A response is capped at 64 KiB;
if a requested page would exceed the cap, the server returns the largest
complete prefix plus `truncated: true` and a continuation cursor.

## Data Flow and Components

1. `token_meter_mcp.py` validates MCP inputs and delegates to the agent API.
2. `AgentAPIService` exposes session discovery, trace query, statistics, and
   schema services without embedding parsing or aggregation logic.
3. Runtime adapters remain responsible for discovery, correction, parsing,
   normalized evidence, and optional native-structure projection.
4. New content-free query contracts define canonical sessions, executions,
   events, pages, coverage, and native structural records.
5. Domain query code filters and aggregates standardized evidence.
6. MCP projections apply final allowlists, pagination, size bounds, and privacy
   checks before transport serialization.

The implementation may reuse current cached summaries and detailed recompute
state behind a strict projection boundary. It must not expose legacy state
dictionaries directly, and cross-session statistics must not reopen raw traces
when cached normalized summaries are sufficient.

## Error Contract

All new tools return structured, non-sensitive errors with stable codes:

- `invalid_argument` for unsupported filters, fields, metrics, or dimensions;
- `session_not_found` for an unknown explicit session ID;
- `stale_cursor` when source revision or query shape changed;
- `evidence_unavailable` when a runtime cannot provide the requested view;
- `response_too_large` only when no complete row fits under the response cap;
- `internal_error` with no raw exception, path, or provider content.

Corrupt or partially written traces preserve any safe evidence already parsed,
set partial coverage and warnings, and never fabricate zero values.

## Documentation Changes

README and the User Guide will explain the query tools with content-free
examples. Architecture and Security will distinguish standardized trace data
and sanitized native structure from prohibited raw trace passthrough. Tool
descriptions will warn that returned evidence may enter the connected client's
model context.

No dashboard or native-client UI is required for this feature.

## Verification

Implementation will be test-first. Contract tests must prove:

- all new tools advertise read-only, non-destructive, idempotent annotations;
- unsupported fields and unbounded query shapes fail closed;
- every page respects item and 64 KiB limits and supplies stable continuation;
- changed trace revisions invalidate cursors;
- standardized totals match existing normalized session evidence;
- statistics aggregate the complete filtered scope rather than the displayed
  page;
- lineage and cumulative-token corrections remain adapter-owned and are not
  counted twice;
- missing runtime evidence remains unavailable;
- all supported runtime fixtures produce valid standardized pages;
- native-structure projectors expose only their positive allowlists;
- sentinel prompts, responses, reasoning, tool arguments/results, credentials,
  paths, environment values, settings, and arbitrary provider strings never
  appear anywhere in serialized MCP output;
- existing `check`, `usage`, and `capabilities` transcripts remain compatible.

Focused MCP, projection, runtime-adapter, domain aggregation, architecture, and
privacy tests will run alongside the complete unittest suite, Python compile,
shell checks, and `git diff --check`. The exact source will then be installed;
`/health`, `/menubar`, both LaunchAgents, MCP stdio transcripts, and
source-to-runtime manifest parity will be verified. Because this has no visible
dashboard or native layout change, browser and Swift visual QA are unnecessary;
the native smoke and compile checks remain part of the standard installation
proof.
