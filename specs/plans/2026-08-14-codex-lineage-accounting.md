# Codex Lineage-Aware Accounting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct Codex token and API-equivalent cost inflation by removing exact inherited rollout prefixes from child traces while preserving legitimate work, current routes, and live/completed timing semantics.

**Architecture:** The Codex runtime adapter owns physical/logical lineage identity, resolves only unique direct parents, and derives one conservative corrected tuple of JSONL rows. Native loading, legacy detail, historical summaries, daily/model aggregation, budgets, tools, and throughput all consume that tuple. A bounded LRU stores only compact token fingerprints; it never stores raw trace content or changes runtime-neutral aggregation.

**Tech Stack:** Python 3 standard library, JSONL fixtures, `unittest`, existing Token Meter runtime contracts and packaging checks, shell/Node/Swift validation.

## Global Constraints

- Read and follow `specs/AGENTS.md` before every implementation session.
- Create or replace ignored `specs/plans/active.md` before production edits and keep its decisions, progress, validation, and remaining work current.
- Keep `meter.py`, `token_meter_mcp.py`, and the runtime adapter dependency-free.
- Never emit, persist, snapshot, or publish raw trace rows, prompts, responses, reasoning, tool arguments, tool outputs, credentials, account data, or local trace paths.
- Preserve public logical session IDs, `codex://threads/...` behavior, existing routes, model pricing, estimate labels, and unavailable-versus-zero semantics.
- Treat ambiguous lineage conservatively: an unresolved, duplicate, self-referential, cyclic, or non-prefix parent relationship keeps all child evidence.
- Preserve the recent bounded metadata-prefix cache and append-only fast path.
- Use a failing focused test before every behavior change, then run the nearest regression class before committing.
- Do not push, open a pull request, or publish changes.

---

## File Map

- Modify `token_meter/runtimes/codex.py`: metadata identity, lineage index/revision, token fingerprints, bounded LRU, corrected row stream, and all Codex parsing entry points.
- Modify `token_meter/app.py`: include private lineage revision in the legacy summary/detail cache signature.
- Modify `tests/runtimes/test_codex_adapter.py`: sanitized direct/nested lineage, conservative matching, native-load, metadata-cache, and fingerprint-cache tests.
- Modify `tests/test_meter.py`: legacy detail/summary, throughput, aggregate, daily/model, and cache-revision reconciliation tests.
- Modify `specs/ARCHITECTURE.md`: document adapter-owned lineage correction and revision ownership.
- Create and maintain ignored `specs/plans/active.md` while implementing.

---

### Task 1: Preserve Physical and Logical Codex Identities

**Files:**
- Modify: `tests/runtimes/test_codex_adapter.py`
- Modify: `token_meter/runtimes/codex.py`
- Create locally: `specs/plans/active.md` (ignored; never stage)

**Interfaces:**
- `metadata(path)` adds private `physical_trace_id`, `logical_session_id`, `forked_from_id`, `parent_thread_id`, and `lineage_parent_id` values.
- Public native `SessionSource.session_id` and legacy source `id` remain the logical session ID.
- Legacy source dictionaries add the five private identity fields for adapter use; public projections continue to allowlist their existing fields.

- [ ] **Step 1: Initialize the live execution record**

Create `specs/plans/active.md` with the approved goal, these task names, the selected execution mode, current commit, validation log, and remaining work. Confirm it is ignored:

    git check-ignore specs/plans/active.md

Expected: the path is printed and `git status --short` does not list it.

- [ ] **Step 2: Add reusable sanitized trace helpers**

In `CodexRuntimeAdapterTests`, replace the one-path-only writer with helpers that can create multiple dated traces without exposing real trace content:

```python
def write_trace(self, name, rows, mtime=2):
    path = self.sessions / "2026" / "08" / "11" / f"rollout-{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    os.utime(path, (mtime, mtime))
    return path

def session_meta(self, physical_id, logical_id=None, **extra):
    payload = {"id": physical_id, "cwd": "/work/project", **extra}
    if logical_id is not None:
        payload["session_id"] = logical_id
    return {"timestamp": "2026-08-11T00:00:00Z",
            "type": "session_meta", "payload": payload}
```

Keep all fixture messages and tool outputs synthetic and content-free.

- [ ] **Step 3: Write the failing first-metadata identity test**

Create a child trace whose first `session_meta` has physical ID `child-1`, logical ID `task-1`, and `forked_from_id="root-1"`. Append a copied parent `session_meta` with ID `root-1`. Assert:

```python
legacy = {row["physical_trace_id"]: row
          for row in self.adapter.discover_legacy(self.context)}
child = legacy["child-1"]
self.assertEqual(child["id"], "task-1")
self.assertEqual(child["logical_session_id"], "task-1")
self.assertEqual(child["lineage_parent_id"], "root-1")
self.assertEqual(Path(child["path"]), child_path)
self.assertEqual(
    {source.locator.value: source.session_id
     for source in self.adapter.discover(self.context)}[str(child_path)],
    "task-1",
)
```

Run:

    python3 -m unittest tests.runtimes.test_codex_adapter.CodexRuntimeAdapterTests.test_first_session_meta_owns_physical_logical_and_parent_identity -v

Expected: FAIL because later copied metadata overwrites `session_id` and private identities do not exist.

- [ ] **Step 4: Implement first-valid-metadata identity extraction**

Add a filename fallback that does not consult logical metadata, plus a bounded nested parent extractor:

```python
@staticmethod
def _physical_id_from_path(path):
    base = os.path.basename(path).rsplit(".", 1)[0]
    match = UUID_RE.search(base)
    return match.group(1) if match else base

@staticmethod
def _parent_thread_id(payload):
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    subagent = source.get("subagent") if isinstance(source.get("subagent"), dict) else {}
    spawn = subagent.get("thread_spawn") if isinstance(subagent.get("thread_spawn"), dict) else {}
    return payload.get("parent_thread_id") or spawn.get("parent_thread_id")
```

In `metadata`, initialize the five fields and an internal `identity_seen` flag. Treat a `session_meta` as identity-bearing when it has `payload.id` or `payload.session_id`; on the first such row only, set:

```python
physical = str(payload.get("id") or self._physical_id_from_path(path))
logical = str(payload.get("session_id") or physical)
forked = str(payload.get("forked_from_id") or "") or None
parent = str(self._parent_thread_id(payload) or "") or None
metadata.update({
    "physical_trace_id": physical,
    "logical_session_id": logical,
    "session_id": logical,
    "forked_from_id": forked,
    "parent_thread_id": parent,
    "lineage_parent_id": forked or parent,
})
```

Later metadata may update cwd, provider, catalog, and model, but not these identities. If no valid `session_meta` exists, use the physical filename fallback for both physical and logical IDs.

- [ ] **Step 5: Make discovery a two-pass private lineage index**

Build records first, then index unique physical IDs. Resolve a parent only when exactly one record has the requested physical ID. Store `self._records_by_path` and `self._record_by_physical_id` for later loads. Include private identity fields in legacy records but keep normalized and legacy public IDs logical.

Use index titles in this order:

```python
title = ((index.get(logical_id) or {}).get("thread_name")
         or (index.get(physical_id) or {}).get("thread_name"))
```

Do not add private fields to `SessionSource` or any projection allowlist.

- [ ] **Step 6: Verify identity behavior and the existing metadata fast path**

Run:

    python3 -m unittest \
      tests.runtimes.test_codex_adapter.CodexRuntimeAdapterTests.test_first_session_meta_owns_physical_logical_and_parent_identity \
      tests.runtimes.test_codex_adapter.CodexRuntimeAdapterTests.test_discovers_identity_model_provider_and_external_title \
      tests.test_meter.SourceDiscoveryCacheTests.test_codex_metadata_cache_reuses_completed_prefix_when_trace_only_appends -v

Expected: all pass. The append-only test must still avoid reopening a completed metadata prefix.

- [ ] **Step 7: Commit the identity milestone**

    git add token_meter/runtimes/codex.py tests/runtimes/test_codex_adapter.py
    git commit -m "fix: preserve Codex trace lineage identity"

---

### Task 2: Derive and Cache Exact Token Fingerprints

**Files:**
- Modify: `tests/runtimes/test_codex_adapter.py`
- Modify: `token_meter/runtimes/codex.py`

**Interfaces:**
- `numeric_usage_signature(value) -> tuple[tuple[str, int | float], ...]` accepts only finite, non-boolean numeric values.
- `_token_events(rows) -> tuple[(row_index, model, last_signature, total_signature), ...]` contains no timestamps or content.
- The adapter owns an `OrderedDict` LRU capped by `TOKEN_EVENT_CACHE_LIMIT = 2048`; tests may pass a smaller `token_event_cache_limit` constructor value.

- [ ] **Step 1: Write failing fingerprint semantics tests**

Add focused tests proving:

1. re-stamped events with the same model and numeric usage produce equal fingerprints;
2. different model or any different numeric usage field does not match;
3. booleans, strings, nested objects, timestamps, and unrelated `info` fields do not enter the signature;
4. one non-empty cumulative total and one empty total do not match;
5. two empty cumulative totals may match on exact model and `last_token_usage`.

Use assertions against module-private helpers because these are deterministic parser contracts. Run the new test method and verify RED before adding helpers.

- [ ] **Step 2: Implement numeric and event fingerprint helpers**

Import `OrderedDict` and add:

```python
TOKEN_EVENT_CACHE_LIMIT = 2_048

def _numeric_usage_signature(value):
    if not isinstance(value, dict):
        return ()
    fields = []
    for key, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        if isinstance(raw, float) and not math.isfinite(raw):
            continue
        fields.append((str(key), raw))
    return tuple(sorted(fields))

def _token_events(rows, default_model=DEFAULT_MODEL):
    model = default_model
    events = []
    for row_index, row in enumerate(rows):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if row.get("type") == "turn_context":
            model = str(payload.get("model") or model)
        if payload.get("type") != "token_count":
            continue
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        last = _numeric_usage_signature(info.get("last_token_usage"))
        if last:
            events.append((row_index, model, last,
                           _numeric_usage_signature(info.get("total_token_usage"))))
    return tuple(events)
```

Add `_token_events_match(left, right)` so model and last signature must be exact. Cumulative signatures must be equal when both are non-empty, fail when only one is non-empty, and are accepted when both are empty.

- [ ] **Step 3: Write the failing bounded-cache test**

Construct an adapter with `token_event_cache_limit=2`, load fingerprints for three trace paths, touch the first before inserting the third, and assert only the least-recently-used second entry is evicted. Delete one trace, run discovery, and assert its metadata and token-cache entries are pruned.

Run the test and verify RED because no fingerprint cache exists.

- [ ] **Step 4: Implement the content-free LRU**

Extend `CodexRuntimeAdapter.__init__` with:

```python
token_event_cache_limit=TOKEN_EVENT_CACHE_LIMIT
```

Store `self._token_event_cache = OrderedDict()` and clamp the limit to at least one. Key entries by absolute path and `_file_signature(path)`, and store only the signature plus `_token_events(...)`. Move cache hits to the end; evict with `popitem(last=False)`. When discovery prunes disappeared paths from `_metadata_cache`, prune the same paths from `_token_event_cache` and private record maps.

If a cache lookup or parent scan fails, call `load_rows` once and return an empty parent event tuple on unreadable input. Do not synthesize usage or mark a readable child unavailable.

- [ ] **Step 5: Verify helper and cache behavior**

Run:

    python3 -m unittest tests.runtimes.test_codex_adapter.CodexRuntimeAdapterTests -v

Expected: the full adapter test class passes and no cached value contains raw rows.

- [ ] **Step 6: Commit the fingerprint milestone**

    git add token_meter/runtimes/codex.py tests/runtimes/test_codex_adapter.py
    git commit -m "fix: fingerprint Codex token lineage safely"

---

### Task 3: Build the Conservative Corrected Row Stream

**Files:**
- Modify: `tests/runtimes/test_codex_adapter.py`
- Modify: `token_meter/runtimes/codex.py`

**Interfaces:**
- `_inherited_token_prefix(child_events, parent_events) -> int` returns the longest exact leading match.
- `_corrected_rows(rows, inherited_count, default_model) -> tuple[dict, ...]` removes inherited execution chunks and exact cumulative adjacent snapshots.
- `_accounting_rows(source, rows) -> tuple[dict, ...]` resolves lineage and is idempotent for an already corrected tuple.

- [ ] **Step 1: Write the failing direct-child native-load test**

Create root and child traces with these billed calls:

```text
root:  input 100, output 10
child copied root (new timestamp): input 100, output 10
child own: input 50, output 5
```

Give both token events exact cumulative signatures and put a synthetic copied tool call before the child's inherited token event. Discover and native-load both. Assert the root reports 110 total measured tokens, the child reports 55, and the child's normalized tools omit the inherited tool.

Run the focused test and verify RED: the child currently reports 165 and exposes the inherited tool.

- [ ] **Step 2: Implement exact prefix length and cycle-safe parent resolution**

Compare child and direct-parent raw token event tuples from index zero until the first mismatch. Before comparison, walk `lineage_parent_id` through the unique physical-record index. Return the original rows when:

- the parent is absent or non-unique;
- parent and child resolve to the same path;
- any visited physical ID repeats;
- either side has no valid token events; or
- the first token event does not match.

Nested children compare against their direct parent's raw event sequence, not the parent's already corrected sequence.

- [ ] **Step 3: Remove inherited execution chunks, preserving the physical anchor**

For the first `inherited_count` token events, drop every row from the previous chunk boundary through that token row. Exempt only the first physical `session_meta` row from the drop set:

```python
drop = set()
chunk_start = 0
for row_index, _model, _last, _total in events[:inherited_count]:
    drop.update(range(chunk_start, row_index + 1))
    chunk_start = row_index + 1
if first_session_meta_index is not None:
    drop.discard(first_session_meta_index)
corrected = tuple(row for index, row in enumerate(rows) if index not in drop)
```

This must remove copied tool calls, messages, timing rows, and copied metadata in inherited chunks while retaining the child's first metadata identity.

- [ ] **Step 4: Add the failing nested and conservative-lineage tests**

Add separate tests for:

- root → child → grandchild, where totals across native loads equal root work + child new work + grandchild new work exactly once;
- missing parent, which retains every child event;
- duplicate physical parent IDs, which retain every child event;
- self-reference and a two-trace cycle, which retain every event;
- same position but different model or one different numeric usage value, which yields no inherited prefix.

Run each new test before its minimal production adjustment, then rerun the whole class.

- [ ] **Step 5: Add and implement adjacent snapshot rules**

Write two failing tests after lineage filtering:

- consecutive token events with exact model, exact last usage, and the same non-empty `total_token_usage` count once;
- the same last usage with empty totals counts twice.

Implement the second pass by tracking the previous retained token fingerprint. Drop only the repeated token row, never the rows around it. Do not compare timestamps.

- [ ] **Step 6: Route native load through the corrected tuple**

Immediately after successful `load_rows` in `load`, add:

```python
rows = self._accounting_rows(source, rows)
```

Keep corrupt-row warnings and source availability based on the physical file read. Verify usage, turns, tools, active duration, TTFT, start/end timestamps, and detail bounds are derived from corrected rows.

- [ ] **Step 7: Verify native behavior and commit**

Run:

    python3 -m unittest tests.runtimes.test_codex_adapter.CodexRuntimeAdapterTests -v
    python3 -m unittest tests.contracts.test_contracts tests.contracts.test_runtime_registry -v

Expected: direct, nested, ambiguous, duplicate-snapshot, content-safety, and runtime-contract tests pass.

Commit:

    git add token_meter/runtimes/codex.py tests/runtimes/test_codex_adapter.py
    git commit -m "fix: filter inherited Codex execution chunks"

---

### Task 4: Reconcile Legacy Detail, Summary, and Aggregates

**Files:**
- Modify: `tests/test_meter.py`
- Modify: `token_meter/runtimes/codex.py`
- Modify: `token_meter/app.py`

**Interfaces:**
- `recompute_legacy`, `summarize_legacy`, and normalized `load` consume the same corrected tuple.
- `source_revision_signature(source)` includes `lineage_revision` so legacy summary and detail caches invalidate when parent resolution changes.
- Existing live and completed throughput helpers receive corrected rows but retain their separate timing semantics.

- [ ] **Step 1: Write the failing legacy reconciliation fixture**

In a new `CodexLineageAccountingTests` class in `tests/test_meter.py`, create a temporary Codex adapter with `compatibility=meter._codex_compatibility()`, write sanitized root/child/nested traces, and use its `discover_legacy` records. Patch `meter._codex_native_adapter` to return that adapter where the compatibility facade is exercised.

Assert for every source:

```python
detail = adapter.recompute_legacy(source)
summary = adapter.summarize_legacy(source)
self.assertEqual(detail["total_tokens"], summary["tokens"])
self.assertAlmostEqual(detail["total_cost"], summary["cost"])
self.assertEqual(len(detail["executions"]), summary["turns"])
```

Also assert the child detail trace/tool evidence excludes its inherited tool call.

Run the focused test and verify RED because both legacy paths still consume raw rows.

- [ ] **Step 2: Correct both legacy entry points exactly once**

After `load_rows` in `recompute_legacy`, call `_accounting_rows(source, objs)` before any model, timing, tool, language, or execution processing.

At the top of `summarize_legacy`, whether rows were supplied or loaded internally, normalize to a tuple and call the same method before the parsing loop and before helper calls:

```python
objs = self._accounting_rows(source, tuple(objs or ()))
```

Do not add a second deduplication in `cross_session`, domain aggregation, budgets, Daily, Models, Tools, the MCP projection, or the menu bar.

- [ ] **Step 3: Write the failing live/completed throughput test**

Use copied inherited work containing completed timing plus one live child token event. Assert:

- completed throughput counts only corrected completed child executions;
- live throughput can still report the active child event before `task_complete`;
- neither helper includes inherited output tokens, durations, or wait samples.

Run the focused test and verify RED before relying on the corrected tuple.

- [ ] **Step 4: Write the failing cross-session reconciliation test**

Clear `_summary_cache` and `_xsess`, patch the Codex adapter, and call `meter.cross_session(sources=legacy_sources)`. Assert:

- `total_tokens` equals the sum of unique root, child-new, and grandchild-new usage;
- `total_cost` equals the sum of the corrected session rows;
- model execution totals equal corrected token-event counts;
- Daily cost totals equal `total_cost`;
- Spend and monthly totals reconcile with the same daily rows;
- the child session row equals its direct `summarize_legacy` result.

This is the regression proof for Sessions, Daily, Models, Spend, and budgets.

- [ ] **Step 5: Implement stable lineage revisions**

For every record, set:

```python
lineage_revision = ("unresolved", str(lineage_parent_id or ""))
```

When the direct parent resolves uniquely and passes self/cycle checks, replace it with:

```python
lineage_revision = (
    "resolved",
    str(lineage_parent_id),
    str(parent_stat.st_dev),
    str(parent_stat.st_ino),
)
```

Do not include parent mtime or size: append-only parent growth must not invalidate an unchanged child. Parent appearance changes unresolved → resolved, and atomic replacement changes device/inode identity.

Expand this tuple into native `SourceRevision.parts`, include it in legacy source dictionaries, and include it in `token_meter.app.source_revision_signature`:

```python
tuple(source.get("lineage_revision") or ())
```

`current_revision` must refresh the bounded discovery record index before reading the source record's lineage revision. This makes parent appearance or replacement visible without incorporating parent append mtime or size.

- [ ] **Step 6: Test revision invalidation boundaries**

Add tests proving:

1. appending to a resolved parent leaves the child's lineage component unchanged;
2. discovering a previously absent parent changes the child revision;
3. atomically replacing the parent file changes the child revision;
4. changing the child file or external title still changes the existing revision fields;
5. `session_state_signature` changes when only `lineage_revision` changes.

Use `os.replace` with a sibling temporary file to force a new inode for case 3.

- [ ] **Step 7: Verify integration behavior and commit**

Run:

    python3 -m unittest \
      tests.test_meter.SessionSummaryStatsTests \
      tests.test_meter.SourceDiscoveryCacheTests \
      tests.test_meter.CodexLineageAccountingTests \
      tests.runtimes.test_codex_adapter.CodexRuntimeAdapterTests -v

Commit:

    git add token_meter/runtimes/codex.py token_meter/app.py tests/runtimes/test_codex_adapter.py tests/test_meter.py
    git commit -m "fix: reconcile Codex lineage across aggregates"

---

### Task 5: Document the Runtime Boundary and Audit the Patch

**Files:**
- Modify: `specs/ARCHITECTURE.md`
- Modify: `specs/plans/active.md` (ignored)

- [ ] **Step 1: Add the architecture invariant**

Under Runtime and Platform Boundaries, state that a runtime adapter may normalize runtime-specific copied evidence before returning usage. Under Application State and Caching, document that Codex lineage revisions depend on direct-parent resolution and stable filesystem identity, not parent append mtimes. State that runtime-neutral aggregation never reopens or deduplicates raw traces.

- [ ] **Step 2: Run privacy and diff audits**

Run:

    rg -n "physical_trace_id|logical_session_id|forked_from_id|parent_thread_id|lineage_parent_id|lineage_revision" token_meter tests specs/ARCHITECTURE.md
    rg -n "prompt|response|reasoning|arguments|output" token_meter/runtimes/codex.py
    git diff --check
    git status --short

Inspect every match. Private lineage fields must remain inside adapter-owned source records/cache signatures and must not be added to public projections. Fingerprint caches must contain only scalar signatures.

- [ ] **Step 3: Run the local sanitized accounting audit**

Use only counts and aggregate numeric totals from the existing local trace root. Report:

- resolvable child count;
- exact inherited-prefix child count;
- token events removed by the correction;
- aggregate token/cost difference before versus corrected accounting.

Do not print trace paths, raw rows, IDs, timestamps, prompts, responses, tool contents, or per-session values. Treat this as local evidence, not a committed fixture.

- [ ] **Step 4: Commit documentation**

    git add specs/ARCHITECTURE.md
    git commit -m "docs: define Codex lineage accounting boundary"

---

### Task 6: Full Verification, Installation, and Handoff

**Files:**
- Modify locally: `specs/plans/active.md` validation log (ignored)
- No production changes unless a failing verification exposes a scoped defect

- [ ] **Step 1: Run the full unit and contract suite**

    python3 -m unittest discover -s tests -v

Expected: all tests pass with zero failures and zero errors. Record the exact test count in the active plan only, not maintained documentation.

- [ ] **Step 2: Run source syntax and build validation**

    PYTHONPYCACHEPREFIX=/private/tmp/token-meter-pycache python3 -m py_compile meter.py token_meter_mcp.py $(find token_meter -type f -name '*.py' -print | LC_ALL=C sort)
    node -e "const fs=require('fs');const h=fs.readFileSync('page.html','utf8');const m=h.match(/<script>([\\s\\S]*)<\\/script>/);new Function(m[1]);console.log('js ok')"
    bash -n scripts/install scripts/install-linux scripts/install-launch-agent scripts/install-systemd-user scripts/run-menubar scripts/run-token-meter-mcp scripts/start-token-meter scripts/uninstall-launch-agent scripts/uninstall-systemd-user scripts/update
    swiftc menubar/TokenMeterMenuBar.swift -o /private/tmp/token-meter-menubar
    TOKEN_METER_MENUBAR_SMOKE=1 /private/tmp/token-meter-menubar
    git diff --check

Expected: Python, JavaScript, shell, and Swift checks succeed; smoke output is valid and contains no local trace content.

- [ ] **Step 3: Install the exact verified checkout**

    ./scripts/install

Capture the installer-reported runtime path and uninstall command. Do not use `sudo` or change macOS security settings.

- [ ] **Step 4: Verify live runtime, autostart, and source parity**

    curl -fsS http://127.0.0.1:8722/health
    curl -fsS http://127.0.0.1:8722/menubar
    launchctl print gui/$UID/com.token-meter.server
    launchctl print gui/$UID/com.token-meter.menubar
    PYTHONPATH=. python3 -m token_meter.packaging parity . "$HOME/Library/Application Support/Token Meter/runtime" runtime-manifest.txt

Expected: both endpoints return valid JSON, both LaunchAgents are loaded, automatic start is configured, and parity reports no mismatches.

- [ ] **Step 5: Verify the installed process is serving the corrected behavior**

Use the installed runtime's local APIs and the sanitized regression fixture or bounded aggregate checks to confirm its totals match the verified source behavior. Do not expose trace paths or content in output. Confirm the dashboard remains at:

    http://127.0.0.1:8722

- [ ] **Step 6: Review the final branch**

    git status --short
    git log --oneline --decorate -8
    git diff 27e4f76..HEAD --stat
    git diff 27e4f76..HEAD --check

Confirm only the planned adapter, application cache signature, tests, architecture document, design, and plan are present. Leave `specs/plans/active.md` ignored and do not stage local audit artifacts.

- [ ] **Step 7: Handoff with exact evidence**

Report:

- root cause and correction semantics;
- files and commits changed;
- focused and full test results;
- local aggregate before/after counts without trace content;
- installed commit and source/runtime parity;
- `/health` and `/menubar` results;
- both LaunchAgent states and automatic-start status;
- dashboard URL and exact uninstall command;
- any host-specific validation not run.
