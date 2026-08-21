# MCP Trace and Statistics Query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four bounded, read-only MCP tools for session discovery, standardized and sanitized-native trace extraction, statistics queries, and schema discovery.

**Architecture:** A new `token_meter.mcp` package owns query validation, opaque cursors, privacy-safe projections, schema metadata, and aggregation. `AgentAPIService` exposes that package to `token_meter_mcp.py`; existing runtime adapters and cached detailed state remain the evidence sources, and no legacy state dictionary is serialized without a positive allowlist.

**Tech Stack:** Python 3.8+ standard library, MCP JSON-RPC over stdio, `unittest`, existing runtime registry and legacy compatibility state.

**Spec:** `specs/2026-08-21-mcp-trace-query-design.md`

## Global Constraints

- All tools remain local, read-only, idempotent, non-destructive, and dependency-free.
- Preserve existing `check`, `usage`, and `capabilities` schemas and behavior.
- Never serialize prompts, responses, reasoning text, tool arguments/results, credentials, account data, settings, environment values, paths, raw exceptions, or raw trace rows.
- Standardized evidence is the statistics source; sanitized native structure is diagnostic only.
- Missing evidence remains unavailable rather than becoming measured zero.
- Default/max limits are 20/100 sessions, 50/200 trace rows, and 20/100 statistic groups.
- A serialized query response is capped at 65,536 bytes and continues with an opaque cursor.
- A changed source revision invalidates an existing trace cursor with `stale_cursor`.
- Shared query code must not branch on runtime names.
- No dashboard or native-client visual change is in scope.

---

### Task 1: Query errors, validation, opaque cursors, and bounded pages

**Files:**
- Create: `token_meter/mcp/__init__.py`
- Create: `token_meter/mcp/contracts.py`
- Create: `tests/test_mcp_queries.py`

**Interfaces:**
- Consumes: JSON-compatible query mappings and content-free source revisions.
- Produces: `MCPQueryError`, `normalize_limit`, `normalize_string_list`, `make_cursor`, `read_cursor`, and `bounded_page`.

- [ ] **Step 1: Write failing cursor and response-bound tests**

```python
def test_cursor_is_bound_to_query_and_revision(self):
    cursor = make_cursor(7, {"runtime": "codex"}, ("rev-1",))
    self.assertEqual(read_cursor(cursor, {"runtime": "codex"}, ("rev-1",)), 7)
    with self.assertRaises(MCPQueryError) as raised:
        read_cursor(cursor, {"runtime": "codex"}, ("rev-2",))
    self.assertEqual(raised.exception.code, "stale_cursor")

def test_bounded_page_returns_complete_prefix_and_cursor(self):
    page = bounded_page(
        [{"value": "x" * 80}, {"value": "y" * 80}], offset=0, limit=2,
        query={"view": "standardized"}, revision=("rev",), max_bytes=180,
    )
    self.assertEqual(len(page["items"]), 1)
    self.assertTrue(page["truncated"])
    self.assertIsNotNone(page["next_cursor"])
```

- [ ] **Step 2: Run the focused tests and confirm missing-module failures**

Run: `python3 -m unittest tests.test_mcp_queries -v`

Expected: FAIL because `token_meter.mcp.contracts` does not exist.

- [ ] **Step 3: Implement strict validators and hashed cursors**

```python
class MCPQueryError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)

def normalize_limit(value, default, maximum):
    try:
        result = int(default if value is None else value)
    except (TypeError, ValueError):
        raise MCPQueryError("invalid_argument", "limit must be an integer")
    if result < 1 or result > maximum:
        raise MCPQueryError(
            "invalid_argument", "limit must be between 1 and {}".format(maximum),
        )
    return result
```

Encode only `{position, query_hash, revision_hash}` with compact JSON and
URL-safe base64. `read_cursor` rejects malformed cursors as `invalid_argument`,
a query mismatch as `invalid_argument`, and a revision mismatch as
`stale_cursor`. `bounded_page` JSON-encodes the complete result after each
appended item and stops before `max_bytes`; if the first individually bounded
row cannot fit, it raises `response_too_large`.

- [ ] **Step 4: Run the focused tests**

Run: `python3 -m unittest tests.test_mcp_queries -v`

Expected: PASS.

- [ ] **Step 5: Commit the query primitives**

```bash
git add token_meter/mcp/__init__.py token_meter/mcp/contracts.py tests/test_mcp_queries.py
git commit -m "feat: add bounded MCP query primitives"
```

### Task 2: Positive-allowlist session and trace projections

**Files:**
- Create: `token_meter/mcp/projections.py`
- Modify: `tests/test_mcp_queries.py`

**Interfaces:**
- Consumes: legacy discovered source mappings and detailed states returned by `cached_session_state(source)`.
- Produces: `session_projection(source, summary, now)`, `standardized_trace_projection(source, state, sections, execution, event_types)`, and `native_structure_projection(source, state, execution, event_types)`.

- [ ] **Step 1: Write failing standardized-shape and privacy tests**

```python
def test_standardized_trace_is_detailed_and_content_free(self):
    source, state = synthetic_source_and_state(secret="SENTINEL-PRIVATE")
    result = standardized_trace_projection(
        source, state, sections=("session", "executions", "events", "tools",
                                 "context", "coverage", "warnings"),
        execution=None, event_types=(),
    )
    encoded = json.dumps(result)
    self.assertEqual(result["schema_version"], "1.0")
    self.assertEqual(result["executions"][0]["tokens"]["input"], 100)
    self.assertEqual(result["events"][0]["type"], "tool_result")
    self.assertNotIn("SENTINEL-PRIVATE", encoded)
    self.assertNotIn(source["path"], encoded)

def test_native_structure_keeps_types_and_numbers_but_drops_payloads(self):
    source, state = synthetic_source_and_state(secret="SENTINEL-PRIVATE")
    rows = native_structure_projection(source, state, None, ())
    self.assertEqual(rows[0]["native_type"], "tool_result")
    self.assertEqual(rows[0]["numeric"]["tokens"], 400)
    self.assertNotIn("SENTINEL-PRIVATE", json.dumps(rows))
```

The fixture places the sentinel in source title/project/path, event detail,
user input, reasoning summary, tool arguments/result, settings-shaped metadata,
and arbitrary nested fields so the test proves omission rather than accidental
absence.

- [ ] **Step 2: Run the focused tests and confirm missing-projection failures**

Run: `python3 -m unittest tests.test_mcp_queries -v`

Expected: FAIL because `token_meter.mcp.projections` does not exist.

- [ ] **Step 3: Implement content-free projections**

```python
SAFE_EVENT_TYPES = {
    "start": "start", "user": "user", "message": "model",
    "reasoning": "reasoning", "tool_call": "tool_call",
    "tool_result": "tool_result", "usage": "usage",
    "context": "context", "goal": "coordination",
    "coordination": "coordination", "complete": "complete",
    "error": "error",
}

def safe_identity(value, maximum=120):
    value = str(value or "").strip()
    if not value or len(value) > maximum:
        return ""
    return value if re.fullmatch(r"[A-Za-z0-9_.:/@+ -]+", value) else ""
```

Project only explicitly named numeric, Boolean, enum, timestamp, model, tool,
relationship, availability, and warning fields. Omit `detail`, `user_message`,
`user_inputs`, arguments, outputs, source labels/titles/projects/paths, and every
unknown key. Use response-local `event-<sequence>` references rather than native
call, trace, turn, or parent IDs. Preserve `None` for unavailable values and
include per-field basis metadata from state availability and estimate flags.

- [ ] **Step 4: Run focused projection and privacy tests**

Run: `python3 -m unittest tests.test_mcp_queries -v`

Expected: PASS, including the serialized sentinel scan.

- [ ] **Step 5: Commit the trace projections**

```bash
git add token_meter/mcp/projections.py tests/test_mcp_queries.py
git commit -m "feat: add privacy-safe MCP trace projections"
```

### Task 3: Session discovery and paginated trace service

**Files:**
- Create: `token_meter/mcp/service.py`
- Modify: `tests/test_mcp_queries.py`

**Interfaces:**
- Consumes: injected `sources()`, `find_session(id, sources)`, `summary(source)`, `state(source)`, `revision(source)`, `project_key(value)`, `runtime_descriptors()`, and `now()` callables.
- Produces: `MCPQueryService.sessions(caller=None, **arguments)` and `MCPQueryService.trace(**arguments)`.

- [ ] **Step 1: Write failing discovery, filters, pagination, and stale-cursor tests**

```python
def test_sessions_returns_opaque_content_free_inventory(self):
    service = synthetic_query_service()
    result = service.sessions(scope="all", runtime="codex", limit=1)
    self.assertEqual(result["sessions"][0]["id"], "session-1")
    self.assertNotIn("project", json.dumps(result))
    self.assertIsNotNone(result["page"]["next_cursor"])

def test_trace_filters_execution_and_rejects_changed_revision(self):
    service = synthetic_query_service()
    first = service.trace(session_id="session-1", limit=1)
    service.revisions["session-1"] = ("changed",)
    with self.assertRaises(MCPQueryError) as raised:
        service.trace(session_id="session-1", cursor=first["page"]["next_cursor"])
    self.assertEqual(raised.exception.code, "stale_cursor")
```

Cover `current_project` matching, runtime/client/model/state/time filters,
unknown session, unsupported view/section/event type, and a session whose
detailed state is unavailable.

- [ ] **Step 2: Run focused tests and confirm missing-service failures**

Run: `python3 -m unittest tests.test_mcp_queries -v`

Expected: FAIL because `MCPQueryService` does not exist.

- [ ] **Step 3: Implement discovery and trace orchestration**

```python
class MCPQueryService:
    def __init__(self, *, sources, find_session, summary, state, revision,
                 project_key, runtime_descriptors, now):
        self._sources = sources
        self._find_session = find_session
        self._summary = summary
        self._state = state
        self._revision = revision
        self._project_key = project_key
        self._runtime_descriptors = runtime_descriptors
        self._now = now

    def trace(self, session_id, view="standardized", sections=None,
              execution=None, event_types=None, cursor=None, limit=None):
        arguments = normalize_trace_arguments(
            session_id, view, sections, execution, event_types, limit,
        )
        return self._trace_result(arguments, cursor)
```

`normalize_trace_arguments` validates the documented enums and positive
execution number. `_trace_result` resolves from one captured source inventory,
raises `session_not_found` when absent, loads cached state once, selects the
standardized or native-structure projection, binds the cursor to normalized
arguments and `revision(source)`, and returns `as_of`, `data_scope`, coverage,
page, and requested sections. It must not call the source callback more than
once per tool invocation.

- [ ] **Step 4: Run focused service tests**

Run: `python3 -m unittest tests.test_mcp_queries -v`

Expected: PASS.

- [ ] **Step 5: Commit session and trace queries**

```bash
git add token_meter/mcp/service.py tests/test_mcp_queries.py
git commit -m "feat: add MCP session and trace queries"
```

### Task 4: Statistics aggregation and query schema

**Files:**
- Create: `token_meter/mcp/schema.py`
- Modify: `token_meter/mcp/service.py`
- Modify: `tests/test_mcp_queries.py`

**Interfaces:**
- Consumes: content-free session and detailed-state projections from Tasks 2-3.
- Produces: `MCPQueryService.stats(**arguments)`, `MCPQueryService.schema(**arguments)`, `METRICS`, `DIMENSIONS`, `TRACE_FIELDS`, and `NATIVE_FIELDS`.

- [ ] **Step 1: Write failing aggregation and schema tests**

```python
def test_stats_groups_complete_scope_by_runtime_and_model(self):
    service = synthetic_query_service(two_sessions=True)
    result = service.stats(
        metrics=("session_count", "input_tokens", "cost_usd"),
        group_by=("runtime", "model"), limit=20,
    )
    self.assertEqual(result["groups"][0]["metrics"]["session_count"], 2)
    self.assertEqual(result["groups"][0]["metrics"]["input_tokens"], 300)

def test_schema_describes_units_coverage_and_compatibility(self):
    result = synthetic_query_service().schema(subject="stats", runtime="codex")
    self.assertEqual(result["schema_version"], "1.0")
    self.assertEqual(result["metrics"]["cost_usd"]["unit"], "USD")
    self.assertIn("tool_name", result["dimensions"])
```

Also test invalid metrics/dimensions, more than eight metrics or three
dimensions, tool dimensions used with non-tool metrics, full-scope totals with
group pagination, sorting, unavailable coverage, and measured zero.

- [ ] **Step 2: Run focused tests and confirm missing-method failures**

Run: `python3 -m unittest tests.test_mcp_queries -v`

Expected: FAIL because `stats` and `schema` are not implemented.

- [ ] **Step 3: Implement static schema and bounded aggregation**

```python
METRICS = {
    "session_count": {"unit": "sessions", "source": "session"},
    "execution_count": {"unit": "executions", "source": "execution"},
    "input_tokens": {"unit": "tokens", "source": "execution"},
    "output_tokens": {"unit": "tokens", "source": "execution"},
    "cache_read_tokens": {"unit": "tokens", "source": "execution"},
    "cache_write_tokens": {"unit": "tokens", "source": "execution"},
    "total_tokens": {"unit": "tokens", "source": "execution"},
    "cost_usd": {"unit": "USD", "source": "execution"},
    "active_seconds": {"unit": "seconds", "source": "execution"},
    "wait_seconds": {"unit": "seconds", "source": "execution"},
    "ttft_seconds": {"unit": "seconds", "source": "execution"},
    "model_calls": {"unit": "calls", "source": "execution"},
    "tool_calls": {"unit": "calls", "source": "tool"},
    "tool_result_tokens": {"unit": "tokens", "source": "tool"},
    "attempts": {"unit": "attempts", "source": "execution"},
    "retries": {"unit": "retries", "source": "execution"},
    "failed_attempts": {"unit": "attempts", "source": "execution"},
    "context_latest": {"unit": "tokens", "source": "session", "reduce": "latest"},
    "context_peak": {"unit": "tokens", "source": "session", "reduce": "max"},
}
```

Build session-, execution-, and tool-grain rows from standardized projections.
Reject metric combinations that cannot share one grain rather than duplicating
session totals across tool rows. Track covered and unavailable row counts for
each metric. Sort only on an included metric, paginate after computing complete
totals, and return static schema data without sampled sessions.

- [ ] **Step 4: Run focused statistics and schema tests**

Run: `python3 -m unittest tests.test_mcp_queries -v`

Expected: PASS.

- [ ] **Step 5: Commit statistics and schema**

```bash
git add token_meter/mcp/schema.py token_meter/mcp/service.py tests/test_mcp_queries.py
git commit -m "feat: add MCP statistics and schema queries"
```

### Task 5: Wire query services into the application and MCP protocol

**Files:**
- Modify: `token_meter/services/agent_api.py`
- Modify: `token_meter/app.py`
- Modify: `token_meter_mcp.py`
- Modify: `tests/test_mcp_server.py`
- Modify: `tests/integration/test_application_composition.py`

**Interfaces:**
- Consumes: `MCPQueryService` from Tasks 3-4 and existing application callbacks.
- Produces: `AgentAPIService.sessions`, `.trace`, `.stats`, `.schema`, and MCP tools named `sessions`, `trace`, `stats`, and `schema`.

- [ ] **Step 1: Write failing application and protocol tests**

```python
def test_initialize_advertises_all_read_only_query_tools(self):
    listed, _ = server.dispatch(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, initialized=True,
    )
    self.assertEqual(
        [tool["name"] for tool in listed["result"]["tools"]],
        ["check", "usage", "capabilities", "sessions", "trace", "stats", "schema"],
    )
    self.assertTrue(all(tool["annotations"]["readOnlyHint"]
                        for tool in listed["result"]["tools"]))

def test_query_error_keeps_stable_code_and_sanitized_message(self):
    result = server.call_tool("trace", {"session_id": "missing"})
    self.assertTrue(result["isError"])
    self.assertEqual(result["structuredContent"]["error_code"], "session_not_found")
```

Update existing exact tool-list assertions and add successful dispatch tests
for each new tool with patched agent API methods.

- [ ] **Step 2: Run protocol and application tests and confirm failures**

Run: `python3 -m unittest tests.test_mcp_server tests.integration.test_application_composition -v`

Expected: FAIL because the query methods and tool schemas are absent.

- [ ] **Step 3: Compose and expose the query service**

```python
query_service = MCPQueryService(
    sources=all_session_sources,
    find_session=lambda session_id, sources: find_session(session_id, sources=sources),
    summary=session_summary,
    state=cached_session_state,
    revision=source_revision_signature,
    project_key=agent_project_key,
    runtime_descriptors=lambda: runtime_registry().descriptors,
    now=time.time,
)
```

Extend `AgentAPIService` with one injected `queries` object and four forwarding
methods. Add strict MCP input schemas using only documented enums, ISO-8601
timestamp strings, bounded arrays, cursors, and limits. Update `call_tool` to
reject unknown keys before delegation and turn `MCPQueryError.code` into
`structuredContent.error_code` without returning a traceback or raw exception.
Use a query response schema separate from the current three-evidence insight
schema.

- [ ] **Step 4: Run MCP, query, and composition tests**

Run: `python3 -m unittest tests.test_mcp_queries tests.test_mcp_server tests.integration.test_application_composition -v`

Expected: PASS.

- [ ] **Step 5: Commit the MCP transport integration**

```bash
git add token_meter/services/agent_api.py token_meter/app.py token_meter_mcp.py tests/test_mcp_server.py tests/integration/test_application_composition.py
git commit -m "feat: expose trace queries through MCP"
```

### Task 6: Runtime fixture privacy coverage and public documentation

**Files:**
- Modify: `tests/runtimes/test_claude_adapter.py`
- Modify: `tests/runtimes/test_codex_adapter.py`
- Modify: `tests/runtimes/test_cursor_adapter.py`
- Modify: `tests/runtimes/test_opencode_adapter.py`
- Modify: `tests/runtimes/test_kiro_adapter.py`
- Modify: `README.md`
- Modify: `specs/USER_GUIDE.md`
- Modify: `specs/ARCHITECTURE.md`
- Modify: `specs/SECURITY.md`
- Modify: `tests/test_meter.py`

**Interfaces:**
- Consumes: public query tools and positive-allowlist projections from Tasks 2-5.
- Produces: runtime-specific regression proof and user-facing query semantics.

- [ ] **Step 1: Add failing per-runtime serialized privacy tests**

For each existing sanitized runtime fixture, load a detailed state through its
real adapter, call both trace views, and assert that the encoded result contains
runtime/model/tool structural evidence but none of the fixture's prompt,
response, reasoning, tool-result, project, or path sentinels. Add a shared helper
that recursively scans keys and string values for prohibited content.

- [ ] **Step 2: Run the five runtime modules and confirm projection gaps**

Run: `python3 -m unittest tests.runtimes.test_claude_adapter tests.runtimes.test_codex_adapter tests.runtimes.test_cursor_adapter tests.runtimes.test_opencode_adapter tests.runtimes.test_kiro_adapter -v`

Expected: FAIL only where a runtime's detailed-state shape lacks a safe mapping
required by the standardized contract.

- [ ] **Step 3: Fix adapter-owned safe metadata at the source**

Add only content-free `native_type`, `native_subtype`, numeric, enum, and
relationship metadata to existing adapter-generated trace events. Do not add a
recursive raw-record sanitizer or pass provider payloads to shared query code.
Keep each runtime's changes inside its adapter and its fixture tests.

- [ ] **Step 4: Document the query surface and privacy contract**

README and User Guide list all seven MCP tools, show a content-free
`sessions` to `trace` to `stats` example, explain pagination and estimate
provenance, and say explicitly that native structure is not raw trace content.
Architecture adds the `token_meter/mcp/` query layer to the component/data flow
map. Security retains the prohibited-content list and documents opaque session
IDs, cursors, response bounds, and sanitized native allowlists.

- [ ] **Step 5: Add documentation/source contract assertions**

In `tests/test_meter.py`, assert that README and maintained specifications name
`sessions`, `trace`, `stats`, and `schema`, retain the read-only wording, and do
not claim that raw prompts, responses, tool payloads, or trace paths are
available.

- [ ] **Step 6: Run runtime, documentation, and privacy tests**

Run: `python3 -m unittest tests.test_mcp_queries tests.test_mcp_server tests.runtimes.test_claude_adapter tests.runtimes.test_codex_adapter tests.runtimes.test_cursor_adapter tests.runtimes.test_opencode_adapter tests.runtimes.test_kiro_adapter -v`

Expected: PASS.

- [ ] **Step 7: Commit runtime coverage and documentation**

```bash
git add token_meter/runtimes tests/runtimes README.md specs/USER_GUIDE.md specs/ARCHITECTURE.md specs/SECURITY.md tests/test_meter.py
git commit -m "docs: document MCP trace query tools"
```

### Task 7: Full verification and installed-runtime proof

**Files:**
- Modify: `specs/plans/active.md` (ignored; validation log only)

**Interfaces:**
- Consumes: the complete implementation and repository validation commands.
- Produces: verified source, staged runtime, local services, and MCP transcript evidence.

- [ ] **Step 1: Run complete source validation**

Run:

```bash
python3 -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/private/tmp/token-meter-pycache python3 -m py_compile meter.py token_meter_mcp.py $(find token_meter -type f -name '*.py' -print)
bash -n scripts/install scripts/install-linux scripts/install-launch-agent scripts/install-systemd-user scripts/run-menubar scripts/run-token-meter-mcp scripts/start-token-meter scripts/uninstall-launch-agent scripts/uninstall-systemd-user scripts/update
swiftc menubar/TokenMeterMenuBar.swift -o /private/tmp/token-meter-menubar
TOKEN_METER_MENUBAR_SMOKE=1 /private/tmp/token-meter-menubar
git diff --check
```

Expected: all unit tests pass with only documented platform skips; Python,
shell, and Swift compile checks exit zero; native smoke emits valid JSON; diff
check is clean.

- [ ] **Step 2: Exercise a deterministic MCP stdio transcript**

Send `initialize`, `tools/list`, and one call each to `schema`, `sessions`,
`trace`, and `stats` through `python3 token_meter_mcp.py`. Verify one compact
JSON-RPC object per line, all seven tools, bounded responses, and no local paths
or trace content.

- [ ] **Step 3: Install the exact source and verify runtime health**

Run:

```bash
./scripts/install
curl -fsS http://127.0.0.1:8722/health
curl -fsS http://127.0.0.1:8722/menubar
launchctl print gui/$(id -u)/com.token-meter.server
launchctl print gui/$(id -u)/com.token-meter.menubar
```

Expected: installation succeeds, both endpoints return valid JSON, both
LaunchAgents are running, and the server owns `127.0.0.1:8722`.

- [ ] **Step 4: Verify source-to-runtime parity**

Byte-compare every manifest-owned file, including `token_meter_mcp.py` and the
expanded `token_meter/` tree, with the staged runtime under
`~/Library/Application Support/Token Meter/runtime`. Record checked-file and
mismatch counts in `specs/plans/active.md`; mismatch count must be zero.

- [ ] **Step 5: Record final evidence and commit validation fixes if required**

Update the ignored active plan with exact current results. If verification
required tracked fixes, stage only those explicit files and commit them with a
message describing the corrected contract. Leave the final worktree clean.
