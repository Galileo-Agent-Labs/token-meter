# Tasks: Extensible Runtime, Model, Platform, and Telemetry Architecture

Status: Implemented; native host matrix gaps recorded  
Date: 2026-08-10  
Requirements: [requirements.md](requirements.md)  
Design: [design.md](design.md)

## Execution rules

- Execute tasks in dependency order; do not create the full target tree as empty scaffolding.
- Keep `main` runnable after every task.
- Add a failing test or characterization assertion before changing production behavior.
- Preserve current endpoint, MCP, settings, route, pricing, estimate, cache, installer, and native behavior unless a task explicitly changes an additive internal contract.
- Remove a legacy branch only in the same task that proves replacement parity.
- Keep `meter.py` and `token_meter_mcp.py` standard-library-only.
- Do not merge the pending Kiro or Windows changes unchanged. Port their behavior only after their prerequisite tasks pass.
- Do not enable telemetry export by default, and do not implement OTLP input in this plan.
- For every multi-file implementation milestone, keep the repository's local `plan.md` current and never stage it.

## Execution status

Updated 2026-08-11 after the source migration:

- TASK-001 is complete. Sanitized normalized fixtures cover all five registered runtimes plus corrupt/partial adapter cases, unavailable versus measured zero, and historical pricing boundaries; tests are split across contract, domain, integration, runtime, and fixture directories without removing the legacy regression suite.
- TASK-002 is complete. Immutable normalized contracts enforce unavailable-versus-zero semantics, runtime-scoped model keys, locator privacy, payload-free normalized tool evidence, and explicit public projection.
- TASK-003 is complete. `all_session_sources()` and `recompute()` route through the explicit ordered registry; all five current discovery/parsers are native adapters; synthetic-runtime, duplicate-ID, deterministic-order, and bounded discovery/load failure tests pass.
- TASK-004 is complete. Explicit allowlisted projections cover session, state, model statistics, menubar, and MCP shapes; golden tests prove exact omission semantics and deny locators, revisions, project paths, raw content, and unknown fields.
- TASK-005 is complete. Canonical provider identity, bundled catalog data, built-in histories, longest-prefix matching, effective-history resolution, `PriceQuery`, and explicit `PriceQuote` results now live behind `token_meter/models`. `meter.py` retains the existing pricing/settings facade and intentional legacy fallback policy.
- TASK-008 is complete. One path-safe manifest drives macOS, Linux, and Windows staging, automatically expands Python package sources, and enforces missing-file and byte-parity gates.

Implementation evidence: 437 tests pass with two explicit host-native skips (Linux path defaults and Windows PowerShell/tray validation on macOS). The source has passed Python, embedded JavaScript, shell, Swift compilation/smoke, manifest, architecture, privacy, whitespace, installed-runtime parity, and live browser checks. Performance improvements include a 36% model-aggregation reduction, 71% Cursor discovery reduction, 39% Codex discovery reduction, 15.9% Claude discovery reduction, 53.6% Kiro metadata-discovery reduction, and 43.6% Python composition compile reduction. Native Windows and Linux lifecycle validation remains required on those hosts.

## Milestone 0 — Establish evidence and guardrails

### TASK-001 — Split and freeze characterization coverage

Change:

- Add sanitized fixtures for Claude, Codex, Cursor, and OpenCode, including corrupt, partial, duplicate, unavailable-evidence, and historical-pricing cases.
- Add golden compatibility assertions for `/state`, `/session`, `/model-stats`, `/menubar`, MCP tools, settings migration, revision caching, and deletion planning.
- Begin splitting tests into `tests/runtimes`, `tests/domain`, `tests/contracts`, and `tests/integration` without changing production imports.

Preserve:

- All current response shapes and existing test semantics.
- User changes currently present in `page.html` and `tests/test_meter.py`; move or edit overlapping tests only after reconciling ownership.

Tests that must fail first:

- At least one new characterization test for each runtime and each unavailable-vs-zero invariant must fail because the fixture/golden contract does not yet exist.

Verify:

- `python3 -m unittest discover -s tests -v`
- Existing suite count and skips are explained; no current passing test disappears without a mapped replacement.

Requirements: REQ-003, REQ-004, REQ-009; NFR-001, NFR-003, NFR-004, NFR-006  
Depends on: none

Exit condition: Current behavior has enough sanitized evidence to detect semantic drift during extraction.

### TASK-002 — Add typed contracts and invariant tests

Change:

- Create `token_meter/contracts.py` with immutable identity, evidence, source, normalized-session, warning, revision, pricing, and deletion types.
- Encode the `UNAVAILABLE => value is None` invariant.
- Add public-serialization tests proving internal locators and private fields cannot leak.

Preserve:

- Production orchestration and current external dictionaries; no parser moves yet.

Tests that must fail first:

- Contract construction rejects unavailable values carrying zero.
- Public serialization rejects or excludes `SourceLocator` and private source data.
- Two identical model names in different runtimes remain distinct keys.

Verify:

- Contract and privacy tests plus the full unit suite.

Requirements: REQ-001, REQ-003, REQ-009; NFR-001, NFR-002, NFR-007  
Depends on: TASK-001

Exit condition: The normalized boundary is concrete and usable without changing product output.

## Milestone 1 — Route through explicit seams

### TASK-003 — Introduce the runtime registry with legacy adapters

Change:

- Add `RuntimeAdapter`, `RuntimeDescriptor`, and `RuntimeRegistry`.
- Register four legacy adapters that delegate to existing discovery/recompute functions.
- Route `all_session_sources()` and `recompute()` through the registry while keeping their public signatures.
- Isolate per-adapter discovery and load failures into bounded warnings.

Preserve:

- Existing runtime discovery order, cache keys, source IDs, and response projections.
- Existing parser implementations remain in place.

Tests that must fail first:

- Duplicate runtime IDs are rejected.
- A synthetic fifth runtime is discovered and loaded without editing registry orchestration.
- One broken adapter does not block successful adapters.

Verify:

- Runtime registry tests, all characterization tests, full unit suite, Python compilation.

Requirements: REQ-002, REQ-003, REQ-009; NFR-004, NFR-005, NFR-006, NFR-007  
Depends on: TASK-002

Exit condition: Shared orchestration contains no runtime-specific dispatch branches even though legacy functions still do the parsing.

### TASK-004 — Add normalized-to-legacy compatibility projections

Change:

- Add explicit serializers that turn normalized contracts into current session, state, model-stat, menubar, and MCP dictionaries.
- Add `runtime_id`, `model_provider_id`, and `account_provider_id` internally while keeping the external `provider` value unchanged.
- Prohibit transport code from serializing internal dataclasses wholesale.

Preserve:

- Exact current public field names, omission rules, bounds, and unavailable behavior.

Tests that must fail first:

- Golden endpoint and MCP projections from a normalized fixture match current output.
- A private locator added to an internal fixture never appears in serialized JSON or MCP output.
- A Kiro-like runtime using an Anthropic-like model resolves distinct runtime/model identities.

Verify:

- Contract, integration, MCP, and full unit suites.

Requirements: REQ-001, REQ-003, REQ-008, REQ-009; NFR-001, NFR-006  
Depends on: TASK-003

Exit condition: Internal identity can evolve without changing the external compatibility contract.

## Milestone 2 — Separate model, quota, platform, and packaging concerns

### TASK-005 — Extract model catalog and pricing

Status: Complete (2026-08-10). The remaining TASK-004 serializers are independent of this boundary; its completed identity-axis seam supplied the required prerequisite.

Change:

- Move model aliases, canonicalization, price histories, and price lookup into `token_meter/models`.
- Replace runtime-named pricing lookups with `ModelRef` and `PriceQuery`.
- Return explicit unknown and evidence-basis results.
- Keep compatibility functions/re-exports in `meter.py`.

Preserve:

- Current prices, historical date boundaries, estimates, labels, settings overrides, and model aggregation scope.

Tests that must fail first:

- Cross-runtime model-provider pricing case.
- Unknown-provider case that must not fall back to another table.
- Golden parity at every current price-history boundary.

Verify:

- Pricing/model tests, characterization tests, full unit suite, Python compilation.

Requirements: REQ-001, REQ-005, REQ-009; NFR-002, NFR-005, NFR-006  
Depends on: TASK-004

Exit condition: Adding a normally encoded model touches only catalog/pricing data and tests.

### TASK-006 — Extract quota adapters

Change:

- Introduce an explicit quota registry keyed by `account_provider_id`.
- Move provider-account request construction, parsing, timeouts, and sanitization into quota adapters.
- Make quota availability a capability, not a prerequisite for runtime support.

Preserve:

- Current read-only behavior, endpoint bounds, authentication sources, timeout behavior, and UI/MCP unavailable states.

Tests that must fail first:

- Runtime with no quota adapter still returns local estimates.
- Credential, response-body, local-path, and raw-exception sentinel values never enter public errors.
- Quota failure produces unavailable rather than zero.

Verify:

- Quota privacy/error tests, endpoint/MCP characterization, full unit suite.

Requirements: REQ-001, REQ-006, REQ-009; NFR-001, NFR-002, NFR-005  
Depends on: TASK-004

Exit condition: Quota support is independently registered and cannot break local parsing.

Completed 2026-08-11. The explicit account-provider registry, three provider modules, bounded common HTTP client, compatibility facade, installer staging, and privacy/error contracts are in place. Focused verification passed 19 tests; the complete suite passed 334 tests with one expected Linux-only skip. The public loader order and legacy identifiers remain stable, missing capability is optional, oversized responses fail closed, and provider body/header/path sentinels cannot enter public errors.

### TASK-007 — Extract platform services

Change:

- Introduce platform detection plus macOS and Linux implementations for path resolution, process options, and trash decisions.
- Move host-specific branches out of runtime orchestration and deletion services.
- Define unsupported-capability results for future Windows implementation.

Preserve:

- Current macOS/Linux path precedence, environment overrides, local-only binding, process behavior, and safe deletion scope.

Tests that must fail first:

- Table-driven macOS/Linux path and process cases.
- Unsupported platform operation returns a bounded result.
- Runtime adapter tests contain no OS-specific path fixtures.

Verify:

- Platform tests on each available host, existing deletion and installer tests, full unit suite.
- Record native-host coverage gaps rather than claiming them passed.

Requirements: REQ-007, REQ-009, REQ-014; NFR-003, NFR-005, NFR-006  
Depends on: TASK-003

Exit condition: Runtime parsing no longer decides OS lifecycle behavior.

Completed 2026-08-11 on macOS. Host selection and immutable platform contracts now live in `token_meter/platforms/`; macOS/Linux path precedence, detached update behavior, trash strategy, and bounded unsupported-host results are covered by table-driven tests. `meter.py` projects compatibility constants from the selected platform and delegates deletion and detached process policy. Focused validation passed 25 tests with the Linux-native case skipped; the full suite passed 339 tests with that same single expected skip. Native Linux lifecycle behavior remains unverified on this macOS host and is not claimed as passed.

### TASK-008 — Create one runtime packaging manifest

Change:

- Define a source-controlled manifest or shared staging function covering root entrypoints, `token_meter/`, dashboard, native assets, and scripts.
- Update installers and parity tests to consume it in their native format.
- Fail installation validation when an imported module is omitted.

Preserve:

- Existing user-owned installation paths, LaunchAgent behavior, service names, automatic start, and uninstall scope.

Tests that must fail first:

- A deliberately omitted required package file makes the staged-runtime parity test fail.
- Uninstall manifest cannot target files outside owned roots.

Verify:

- Installer syntax tests on applicable hosts.
- Source/staged manifest parity in a temporary install target.
- Full unit suite.

Requirements: REQ-009, REQ-010; NFR-003, NFR-006  
Depends on: TASK-002, TASK-007

Exit condition: Adding a Python module cannot silently produce a broken installed runtime.

Completed 2026-08-11. `runtime-manifest.txt` now covers root files, the automatically expanded Python package, native companions, scripts, and assets. Both installers consume it and run the shared path-safe validator before lifecycle changes and byte parity after staging. Contracts prove traversal/absolute targets are rejected, an omitted imported module fails parity, and the real repository round-trips into a temporary runtime. The full suite passed 343 tests with one expected Linux-native skip; Python, embedded JavaScript, shell, Swift compile/smoke, and whitespace checks passed. The real macOS install passed manifest parity, indexed 347 sources with no adapter failures, ran both LaunchAgents, and listened only on `127.0.0.1:8722`.

## Milestone 3 — Extract shared domain behavior

### TASK-009 — Extract usage and cost calculations

Change:

- Move runtime-neutral token, cache, and cost calculations into `token_meter/domain/usage.py`.
- Consume normalized evidence and `PriceQuote`, not raw runtime events.
- Leave raw-event interpretation in adapters.

Preserve:

- Exact current usage totals, estimate labels, unknown-price behavior, and cache-token semantics.

Tests that must fail first:

- Runtime-neutral golden cases reproduce every usage/cost edge case from characterization fixtures.
- Equivalent normalized evidence from two runtime IDs produces equivalent calculations while retaining runtime-scoped aggregation keys.

Verify:

- Usage, pricing, characterization, and full unit suites.

Requirements: REQ-003, REQ-004, REQ-005; NFR-004, NFR-006  
Depends on: TASK-005

Exit condition: Shared usage and cost code contains no known runtime identifiers.

Completed 2026-08-11. `token_meter/domain/usage.py` now owns typed price application, normalized-count compatibility math, token/cache totals, reported-cost distribution, cache metrics and savings, availability, and usage provenance. Runtime parsing remains in adapter-era code and `meter.py` only projects legacy shapes/policy. Nine runtime-neutral goldens cover exact bucket math, cache semantics, long-context multipliers, unavailable versus measured zero, equivalent evidence across runtime-scoped keys, authoritative totals, provenance labels, and an architecture guard for known runtime identifiers. The full suite passed 352 tests with one expected Linux-native skip. During implementation an allocation-heavy first version measured 13.696 microseconds per cost call; the final shared-kernel wrapper measured 2.813 microseconds versus a 2.660-microsecond inline baseline, while the price lookup hot path improved from the prior 2.155 to 1.904 microseconds per call.

### TASK-010 — Extract timing, tools, and insights

Change:

- Move runtime-neutral timing, tool categorization, capability use, and insight rules into `token_meter/domain` modules.
- Require adapters to express source-specific facts as normalized evidence and warnings.

Preserve:

- Active-duration semantics, tool evidence rules, capability classification, and “No use observed” handling for incomplete evidence.

Tests that must fail first:

- Runtime-neutral fixtures for measured/inferred/unavailable timing.
- Native skill/tool invocation evidence remains correctly distinguished from text mentions.
- Partial evidence cannot produce a disable recommendation.

Verify:

- Domain tests, capability/MCP characterization, full unit suite.

Requirements: REQ-003, REQ-004; NFR-001, NFR-004, NFR-006  
Depends on: TASK-004

Exit condition: Shared timing/tool/insight modules contain no raw trace parsing.

Completed 2026-08-11. Runtime-neutral timing, tools, and insight implementations now live in `token_meter/domain/timing.py`, `tools.py`, and `insights.py`; raw-event extraction remains outside those modules. Contracts cover measured/inferred/unavailable timing, merged active windows, reported/observed waits, weighted tool-free throughput, explicit native skill evidence, partial-measurement review suppression, incomplete catalog suppression, insight deduplication, and a known-runtime identifier guard. Existing active-time, wait, performance, tool, capability, MCP, menubar, and recommendation tests run through compatibility wrappers over the new domain. The full suite passed 358 tests with one expected Linux-native skip.

### TASK-011 — Extract aggregation services

Change:

- Move daily, model, tool, session, and cross-session aggregation into `token_meter/domain/aggregates.py`.
- Key default model aggregation by runtime plus canonical model reference.
- Keep compatibility projection shapes at service/transport boundaries.

Preserve:

- Existing Sessions All, Daily, Models, Tools, MCP, and menubar values and ordering.

Tests that must fail first:

- Same model ID in two runtimes remains separate.
- Unknown/unavailable components do not become zero in totals.
- Golden projections match current aggregate endpoints.

Verify:

- Aggregate, endpoint, MCP, menubar, and full unit suites.

Requirements: REQ-001, REQ-003, REQ-004, REQ-009; NFR-004, NFR-006  
Depends on: TASK-009, TASK-010

Exit condition: Aggregate code consumes normalized domain data and is runtime-neutral.

Completed 2026-08-11. Daily, monthly, model, tool, current-session, language-signal, and cross-session rollups now live in `token_meter/domain/aggregates.py`; discovery, cache refresh, settings, and trace loading remain outside the domain. Compatibility wrappers preserve the established endpoint, MCP, menu-bar, and dashboard projections. Contract tests prove runtime-scoped model keys, unavailable-versus-zero coverage, deterministic ordering, bounded path-free current-session cards, and runtime-neutral source. On 329 real local sessions the new daily/monthly/model/tool results exactly matched the legacy functions. A per-model-pair distance cache also removed repeated 7/30/90/all pace calculations, reducing the model aggregate median from 1,485 ms to 951 ms. The full suite passed 364 tests with one expected Linux-native skip.

## Milestone 4 — Move each runtime behind the contract

### TASK-012 — Port OpenCode to a native runtime adapter

Change:

- Move OpenCode discovery, SQLite/file reads, revision logic, parsing, and warnings into `token_meter/runtimes/opencode.py`.
- Remove the corresponding legacy branches only after parity passes.

Preserve:

- Current OpenCode sessions, token/cost estimates, timing, tools, source precedence, cache behavior, and safe read-only database handling.

Tests that must fail first:

- OpenCode adapter contract suite against sanitized database/file fixtures.
- Corrupt, locked, changing, and partially available source cases.

Verify:

- Adapter contract, OpenCode characterization, cache, aggregate, full unit suites.

Requirements: REQ-002, REQ-003, REQ-004; NFR-001, NFR-004, NFR-005, NFR-006  
Depends on: TASK-009, TASK-010, TASK-011

Exit condition: OpenCode has no discovery or recompute implementation in `meter.py` beyond compatibility exports.

Completed 2026-08-11. `token_meter/runtimes/opencode.py` now owns OpenCode path-scoped adapter state, WAL-aware query-only SQLite access, model-catalog loading, top-level discovery, message/part revision calculation, normalized summary/full loading, payload-free tool/turn evidence, warnings, detailed-state recompute, aggregate summary projection, and read-only deletion policy. The explicit registry uses a native proxy so patched or configured database paths resolve without rebuilding the registry. `meter.py` retains thin compatibility exports and injects established presentation/state builders; the superseded discovery, recompute, and summary implementations were removed. Sanitized SQLite contracts cover top-level filtering, revision invalidation, measured zero versus unavailable, summary/full detail, content exclusion, query-only enforcement, corrupt/partial schemas, legacy projection parity, and native registry dispatch. The full suite passed 372 tests with one expected Linux-native skip.

### TASK-013 — Port Cursor to a native runtime adapter

Change:

- Move Cursor discovery, SQLite joins, revision logic, parsing, and warnings into `token_meter/runtimes/cursor.py`.
- Validate that the source contract handles databases without leaking query/storage details.

Preserve:

- Current Cursor session identity, model inference, usage, tools, ordering, cache behavior, and read-only database access.

Tests that must fail first:

- Cursor adapter contract against sanitized multi-table and partial-schema fixtures.
- Database revision change invalidates only affected sources.

Verify:

- Adapter contract, Cursor characterization, cache, aggregate, full unit suites.

Requirements: REQ-002, REQ-003, REQ-004; NFR-001, NFR-004, NFR-005, NFR-006  
Depends on: TASK-012

Exit condition: The abstraction has proven both file and database evidence sources.

Completed 2026-08-11. `token_meter/runtimes/cursor.py` now owns transcript plus Composer-database discovery, a WAL-aware query-only connection, bounded metadata/path caches, per-session database/transcript/request revisions, normalized content-free estimates, detailed legacy recompute, cross-session summaries, warnings, and transcript-only deletion planning. Shared-database changes use per-Composer header revisions so unrelated sessions remain cache-valid. The explicit registry now routes Cursor through a native path-sensitive proxy rather than `LegacyRuntimeAdapter`; `meter.py` retains compatibility wrappers and injected presentation builders while dead superseded bodies remain marked for TASK-023 removal. Sanitized multi-table, partial-schema, content-privacy, unavailable-evidence, query-only, scoped-revision, fallback, dispatch, real-inventory parity, and cache tests pass. The full suite passed 380 tests with one expected Linux-native skip, and warm real-inventory discovery improved from 0.6584 ms to 0.1889 ms median.

### TASK-014 — Port Codex to a native runtime adapter

Change:

- Move Codex discovery, event/session boundaries, revisions, parsing, and warnings into `token_meter/runtimes/codex.py`.
- Retain stable compatibility exports needed by MCP and tests.

Preserve:

- Current Codex session identity, model/effort interpretation, token accounting, tool evidence, timing, and ordering.

Tests that must fail first:

- Codex adapter contract over all current schema variants and partial traces.
- Model-provider mapping does not depend on `runtime_id == "codex"`.

Verify:

- Adapter contract, Codex characterization, MCP, aggregate, full unit suites.

Requirements: REQ-001, REQ-002, REQ-003, REQ-004; NFR-004, NFR-005, NFR-006  
Depends on: TASK-013

Exit condition: Codex parsing is isolated and current MCP behavior is unchanged.

Completed 2026-08-11. `token_meter/runtimes/codex.py` now owns date-tree discovery, index/title revisions, bounded metadata caching, explicit trace-reported model-provider identity, corrupt/partial JSONL handling, payload-free normalized measured usage/timing/tools/turns, detailed event-boundary recompute, aggregate summary projection, warnings, and trace-only deletion planning. The runtime registry routes Codex through a native path-sensitive proxy rather than `LegacyRuntimeAdapter`; `meter.py` keeps stable compatibility exports and injected presentation/domain helpers, with dead superseded bodies reserved for TASK-023 removal. Sanitized schema, partial/corrupt, privacy, unavailable-evidence, revision, provider-independence, dispatch, cache, pricing-boundary, timing, aggregate, and MCP tests pass. All 295 real source dictionaries matched exactly and the 30 newest detailed states matched on stable fields. The full suite passed 386 tests with one expected Linux-native skip; warm discovery improved from 4.3138 ms to 2.6357 ms median.

### TASK-015 — Port Claude to a native runtime adapter

Change:

- Move Claude discovery, deduplication, revisions, parsing, and warnings into `token_meter/runtimes/claude.py`.
- Remove the final runtime-specific orchestration branches from `meter.py`.

Preserve:

- Current Claude session identity, deduplication, model mapping, token/cache estimates, timing, tools, and ordering.

Tests that must fail first:

- Claude adapter contract across duplicate, rotated, partial, and schema-variant fixtures.
- Discovery failure isolation with all four adapters registered.

Verify:

- Adapter contract, Claude characterization, full integration and unit suites.

Requirements: REQ-001, REQ-002, REQ-003, REQ-004; NFR-004, NFR-005, NFR-006  
Depends on: TASK-014

Exit condition: All current runtimes are native adapters and provider dispatch is absent from shared orchestration.

Completed 2026-08-11. `token_meter/runtimes/claude.py` now owns Claude Code and Claude Desktop discovery, Desktop metadata precedence, local-agent traces, trace-plus-metadata revisions, bounded JSONL loading, split-message deduplication, content-free normalized measured evidence, detailed recompute, aggregate summaries, warnings, and trace-only deletion planning. The registry routes Claude through a native path-sensitive proxy, so all four current runtimes now use native adapters and shared orchestration has no runtime parser switch. Sanitized duplicate, partial/corrupt, privacy, revision, Desktop enrichment, dispatch, timing, aggregate, and real-inventory parity tests pass. All 43 real source dictionaries, detailed states, and summaries matched exactly on stable fields. The full suite passed 390 tests with one expected Linux-native skip; warm discovery improved from 2.6740 ms to 2.2501 ms median (15.9%).

## Milestone 5 — Move application and clients onto metadata

### TASK-016 — Extract application services and thin the facade

Change:

- Move session orchestration, settings, budgets, capabilities, updates, deletion, menubar, agent API, and HTTP transport into the designed service/web modules.
- Make `meter.py` a composition root plus explicit compatibility exports, targeting at most 150 substantive lines excluding compatibility declarations.
- Point `token_meter_mcp.py` at the application service while preserving its contract.

Preserve:

- All routes, payloads, settings and migrations, action tokens, local binding, event behavior, imports, CLI flags, and MCP tools.

Tests that must fail first:

- Composition/integration test builds an application with synthetic registries.
- Legacy `meter` imports and CLI entrypoint remain available.
- HTTP/MCP golden contracts run against the new application object.

Verify:

- Full unit/contract/integration suites, Python compilation, endpoint smoke checks.

Requirements: REQ-002, REQ-004, REQ-006, REQ-007, REQ-009; NFR-001, NFR-002, NFR-006, NFR-007  
Depends on: TASK-006, TASK-007, TASK-008, TASK-015

Exit condition: `meter.py` no longer owns domain, runtime, platform, quota, or HTTP implementations.

Completed 2026-08-11. The original implementation now lives behind `token_meter/app.py`; root `meter.py` is a 16-line executable/import compatibility facade that preserves mutable legacy patch points while delegating the CLI to the application. Explicit service modules own sessions, settings, budgets, capabilities, updates, deletion, menubar, agent API, and composition, while `token_meter/web/server.py` owns local HTTP lifecycle. `token_meter_mcp.py` resolves calls through the application service. Synthetic-registry composition, facade import/patch behavior, CLI/path compatibility, and full endpoint/MCP regressions pass.

### TASK-017 — Add the runtime catalog to server contracts

Change:

- Add bounded runtime catalog data to `/state` and the required subset to `/menubar`.
- Derive labels and capabilities from registered descriptors.
- Add a generic unknown-runtime descriptor.

Preserve:

- Existing payload fields and sizes; catalog is additive and bounded.

Tests that must fail first:

- Synthetic runtime appears in catalog and generic session payloads.
- Catalog rejects URLs, markup, executable text, and unknown capability names.
- `/menubar` remains within its response bound.

Verify:

- Endpoint, privacy, bounds, MCP, menubar, and full unit suites.

Requirements: REQ-002, REQ-008, REQ-009; NFR-001, NFR-005  
Depends on: TASK-016

Exit condition: Clients can render a new runtime without a server-side provider switch.

Completed 2026-08-11. `token_meter/services/runtime_catalog.py` derives an immutable, bounded presentation catalog from registered runtime descriptors, adds a neutral unknown-runtime entry, rejects unsafe labels/assets/capabilities, and projects only the required metadata into `/state` and `/menubar`. Synthetic runtime tests prove catalog propagation without runtime-specific server branches, invalid metadata fails closed, and the menubar response remains below its hard size bound.

### TASK-018 — Make browser and native clients catalog-aware

Change:

- Replace hard-coded runtime lists in generic dashboard surfaces with catalog-driven iteration.
- Replace native provider label/symbol switches with catalog lookup plus accessible local fallback.
- Keep special controls capability-gated.

Preserve:

- Dashboard order, legacy hash routes, stored preferences, hover detail, responsive behavior, macOS labels, and current known-runtime appearance.

Tests that must fail first:

- Unknown synthetic runtime renders in Sessions, Daily, Models, Tools, and recent-session/menu payloads.
- Generic fallback remains readable and accessible.
- Unsupported runtime actions are absent.

Verify:

- Embedded JavaScript parse.
- Browser checks at wide, laptop, and narrow widths.
- Swift compile, deterministic smoke output, and live menu-bar check on macOS.
- Relevant source-contract and full unit suites.

Requirements: REQ-008, REQ-009; NFR-003, NFR-005, NFR-006  
Depends on: TASK-017

Exit condition: Adding a runtime requires no edits to generic dashboard or native recent-session rendering.

Completed 2026-08-11. Browser session cards, badges, filters, empty states, and live updates now use runtime catalog metadata plus the neutral fallback. The Swift/AppKit and Linux tray recent-session surfaces use the same catalog for labels and local symbol tokens without provider switches. Existing preference migration remains explicit compatibility logic. Embedded JavaScript, client source contracts, Swift compilation/smoke, and installed browser checks passed at 1440x900, 1024x768, and 390x844 with no overflow or console errors.

## Milestone 6 — Add a safe OpenTelemetry seam

### TASK-019 — Implement the pure privacy projection and OTel mapping

Change:

- Add a deny-by-default privacy projection that accepts only bounded normalized aggregates.
- Add a pure mapping to a documented, pinned subset of OpenTelemetry GenAI/system attributes.
- Keep all convention names and version assumptions in `otel_mapping.py`.
- Do not import an OTel SDK or send data.

Preserve:

- Zero network behavior, dependency-free installation, and all existing local outputs.

Tests that must fail first:

- Adversarial normalized fixtures containing prompt, response, reasoning, tool payload, credential, account, path, project, and session-label sentinels cannot reach the export object.
- Unknown fields are denied.
- Evidence basis is represented safely or omitted.
- Golden mappings identify the pinned convention subset.

Verify:

- Telemetry privacy/mapping tests, dependency/import test, full unit suite.

Requirements: REQ-011, REQ-012; NFR-001, NFR-002, NFR-008  
Depends on: TASK-002, TASK-009, TASK-010

Exit condition: Token Meter can produce a safe, testable OTel-shaped object without an SDK or side effect.

Completed 2026-08-11. `token_meter/telemetry/privacy.py` creates an immutable, explicit allowlist of bounded runtime/model identity, token evidence, active duration, safe tool-category counts, OS family, and Token Meter version. `otel_mapping.py` pins the OpenTelemetry GenAI subset to `1.42.0`, uses the standard input/output token metric and custom names where Token Meter's cache/session semantics are not equivalent to one client operation. Adversarial content/private-field tests, exact golden mapping, raw-object rejection, dependency checks, and patched network/file/process no-side-effect tests pass.

### TASK-020 — Gate an optional exporter behind a separate approval

Change:

- Prepare a short decision record comparing an in-process optional exporter with a localhost collector handoff.
- If explicitly approved, add extra-only packaging, opt-in protected settings, strict endpoint validation, bounded queue/timeouts, and failure isolation.
- If not approved, stop with the pure mapping seam from TASK-019.

Preserve:

- Disabled-by-default behavior, standard-library default path, local application availability, and uninstall correctness.

Tests that must fail first if implementation is approved:

- Disabled mode performs no optional import, socket creation, file write, or subprocess start.
- Missing dependency fails closed with a bounded status.
- Exporter timeout/failure does not change `/state`, MCP, or `/menubar` results.
- Only the privacy-projected object can be submitted.

Verify:

- Telemetry tests with a local fake receiver; no external endpoint.
- Settings validation/idempotence tests and full unit suite.

Requirements: REQ-011, REQ-012; NFR-001, NFR-002, NFR-006, NFR-008  
Depends on: TASK-019 and explicit approval

Exit condition: Either the mapping-only boundary is documented as the chosen scope, or an optional exporter is proven isolated and off by default.

Completed 2026-08-11 as a mapping-only decision. `telemetry-decision.md` compares an in-process optional exporter and localhost collector handoff, records why neither output channel is authorized or justified in the default local-only product, and defines the privacy, isolation, packaging, endpoint, and test gates for any separately approved future exporter. There is no exporter, OTel SDK import, telemetry setting, socket, file sink, or subprocess.

## Milestone 7 — Port pending extensions

### TASK-021 — Port Kiro as a runtime adapter

Change:

- Reuse the pending PR's sanitized fixtures and behavior intent.
- Implement Kiro discovery/parsing as one registered adapter.
- Resolve its models through `ModelRef`; do not reuse a runtime identifier as a price-provider shortcut.
- Add presentation metadata only where the generic fallback is insufficient.

Preserve:

- All current-runtime behavior and shared domain invariants.

Tests that must fail first:

- Kiro adapter contract and fixture cases.
- Kiro runtime plus non-Kiro model provider pricing.
- A review guard showing no unrelated runtime, shared domain, HTTP, platform, or generic client file requires a Kiro branch.

Verify:

- Adapter, pricing, catalog/client, integration, and full unit suites.
- Installed-runtime parity and endpoint checks.

Requirements: REQ-001, REQ-002, REQ-005, REQ-008, REQ-010, REQ-014; NFR-005  
Depends on: TASK-018

Exit condition: Kiro satisfies the runtime extension budget.

Completed 2026-08-11. PR #11 was inspected but not merged. `token_meter/runtimes/kiro.py` now owns both `~/.kiro/sessions` JSONL and Kiro Agent extension-storage discovery, combined revisions, bounded/corrupt reads, visible-text token estimates, inferred timing, payload-free tools, safe deletion plans, detailed state, and cross-session summaries. Model identity is an independent `ModelRef` (`anthropic`, `openai`, `amazon`, or explicit unknown), never the `kiro` runtime ID; pricing resolves through the typed model-provider query. The single registry entry supplies neutral catalog metadata, generic clients render it unchanged, and the budget UI now grows from catalog entries rather than a Kiro branch. Sanitized fixtures, privacy, partial/corrupt, metadata invalidation, agent-storage, state/summary, pricing-axis, dispatch, catalog, budget, and extension-guard tests pass. The complete suite passed 413 tests with one expected Linux-native skip; all language/client/native checks passed; the installed catalog and Kiro budget row were verified live. Safe metadata projection caching reduced a 250-session synthetic discovery median from 19.601 ms to 9.095 ms (53.6%).

Follow-up verification 2026-08-11: a real Kiro CLI trace exposed an additional top-level `kind/data` message schema (`Prompt` and `AssistantMessage`) that discovery admitted but the PR-era `payload.type` parser did not count. A sanitized fixture first reproduced the zero-turn cross-session omission; the adapter now normalizes both schemas without exposing content. The complete 431-test suite passes, the exact source is reinstalled with manifest parity, `/logs` reports one five-turn Kiro session, and the live Current Sessions view renders the Kiro card without console errors.

Extension-budget note: three existing adapter proxies gained the same optional `summarize_legacy` compatibility method so `session_summary` could replace its four-runtime dispatch with one registry call. This is a one-time closure of the compatibility protocol, not a Kiro-specific capability or branch; shared domain, HTTP, MCP, platforms, and native clients required no Kiro conditional.

### TASK-022 — Port Windows through the platform boundary

Change:

- Reuse the pending PR's behavior intent for PowerShell installation, service lifecycle, and native tray.
- Implement Windows platform services and packaging without copying runtime parsing.
- Consume the same server, runtime catalog, health, and menubar contracts.

Preserve:

- macOS/Linux behavior, current-runtime parsing, local-only binding, safe install/uninstall scope, and generic client semantics.

Tests that must fail first:

- Windows path/process/trash/service decision tests.
- PowerShell syntax and manifest parity tests.
- Native tray contract tests with known and unknown runtimes.

Verify:

- Windows-native install, automatic start, service ownership, `/health`, `/menubar`, tray interaction, staged parity, and uninstall.
- macOS and Linux regression suites on their native CI jobs.

Requirements: REQ-007, REQ-008, REQ-009, REQ-010, REQ-014; NFR-003, NFR-005, NFR-006  
Depends on: TASK-008, TASK-017

Exit condition: Windows satisfies the platform extension budget and has native evidence.

Implemented 2026-08-11 with an explicit native-evidence exception. PR #12 was inspected but not merged. `token_meter/platforms/windows.py` owns AppData paths, detached/no-window process flags, private trash, update launch, and the MCP launcher choice. The shared manifest drives PowerShell installation and parity; start, tray, update, uninstall, and MCP launchers reuse the same server and catalog contracts. Quota subprocesses apply the Windows no-window flag, and SQLite inputs use portable read-only file URIs without a runtime parser fork. Portable path/process/trash/update/manifest/lifecycle/tray tests pass. A PowerShell executable and Windows service manager are unavailable on this macOS host, so native installation, automatic start, tray interaction, uninstall, and staged parity remain a Windows release gate and are not claimed as passed.

## Milestone 8 — Close migration and document extension paths

### TASK-023 — Enforce architecture and complete installed validation

Change:

- Remove dead legacy code after all parity gates pass.
- Add lightweight architecture checks forbidding runtime names in shared domain/orchestration and preventing private internal serialization.
- Document recipes for adding a runtime, model, quota adapter, platform, and optional telemetry mapping.
- Update the manifest and contributor validation matrix.

Preserve:

- Compatibility exports and public behavior unless separately deprecated.

Tests that must fail first:

- Architecture test catches a synthetic runtime branch in a shared module.
- Extension-template test proves a synthetic adapter needs only the allowed extension points.

Verify:

- Full unit/contract/integration suite.
- Python, JavaScript, shell, PowerShell, and native compilation/smoke checks as applicable.
- Native matrix on macOS, Linux, and Windows.
- Install latest source, verify `/health` and `/menubar`, services/trays, source/staged parity, automatic start, and uninstall command.
- `git diff --check`.

Requirements: REQ-001 through REQ-014; NFR-001 through NFR-008  
Depends on: TASK-020 decision, TASK-021, TASK-022

Exit condition: The old architecture is gone, compatibility is proven, and each extension path has an executable recipe and native evidence where required.

Implemented 2026-08-11 for source and available-host validation. Forty-three superseded `_legacy_*` composition bodies plus obsolete compatibility cache/helper seams were removed; `token_meter/app.py` is 7,498 lines versus the 11,381-line baseline. Architecture guards reject superseded legacy definitions, known-runtime dispatch in shared domain/service/web code, wholesale dataclass transport serialization, and missing contributor extension recipes. `token_meter/projections.py` supplies the explicit normalized compatibility boundary, while the existing synthetic runtime integration proves the extension template. The complete suite passes 430 tests with the two named non-host-native skips. Repeat-101 Python compilation improved from 41.048 ms to 23.159 ms median (43.6%). The exact tree was installed on macOS: both LaunchAgents run automatically, endpoints and loopback ownership are healthy, 348 sources load without adapter failures, staged parity passes, and responsive browser checks pass at wide/laptop/narrow sizes without overflow or console errors. Native Linux and Windows lifecycle evidence stays required in their release jobs.

### TASK-024 — Align duplicate-session detail routing with current-session selection

- [x] Change: replaced first-match logical session lookup with one runtime-neutral deterministic selector that preserves exact physical aliases and otherwise prefers non-terminal work, newest activity, and a stable locator tie-breaker.
- [x] Preserve: existing `/sessions/<id>` URLs, single-source lookup cost, runtime adapter ownership, privacy boundaries, source deletion validation, and current-session aggregation semantics.
- [x] Verify: added a failing regression with completed Codex review segments surrounding one active trace under the same logical ID; proved the detail route selects the active trace regardless of discovery order and falls back to the newest completed segment when no active segment remains. Re-ran 435 tests, installed, verified the exact live Codex URL, and confirmed source/runtime parity.
- [x] Requirements: REQ-002, REQ-009; NFR-001, NFR-004, NFR-006
- [x] Depends on: TASK-023

Exit condition: a logical session link and its Current Sessions card resolve the same trace segment without exposing physical locators.

Completed 2026-08-11. Four regression cases cover active-over-newer-terminal selection, discovery-order independence, newest-terminal fallback, unique physical-alias preservation, and the single-match fast path. The complete suite passes 435 tests with the two expected non-host-native skips. The exact source was installed with manifest parity; the reported live Codex detail route and Current Sessions card both show the active `gpt-5.6-sol` trace at about `$109`, while the old two-execution review segment is no longer selected. Browser verification found no console errors.

### TASK-025 — Keep opened-only Claude Desktop sessions out of Current Sessions

- [x] Change: derived Claude activity from semantic trace/metadata timestamps while retaining filesystem signatures exclusively for revision invalidation; used a bounded, signature-cached JSONL tail reader.
- [x] Preserve: Claude Code and Desktop discovery, title/project enrichment, active-session freshness, read-only trace handling, source revision invalidation, privacy, and the 30-minute Current Sessions contract.
- [x] Verify: first reproduced an old trace whose filesystem and metadata mtimes advance without event timestamps; proved it is excluded from Current Sessions, then proved a newly appended event is included. Ran the 437-test suite and language checks, reinstalled, verified live endpoints and parity, and inspected the installed Sessions page.
- [x] Requirements: REQ-002, REQ-003, REQ-009; NFR-001, NFR-004, NFR-006
- [x] Depends on: TASK-023

Exit condition: opening historical Claude Desktop UI state invalidates caches if needed but does not count as agent activity.

Completed 2026-08-11. The regression was observed live across six old Claude Desktop/3P sessions: their JSONL and metadata filesystem mtimes advanced to the current morning while semantic event timestamps stayed in July or the prior evening. Two new adapter tests were watched failing before implementation and passing afterward. The complete 437-test suite passes with two expected host-native skips; warm 23-source Claude Desktop discovery is 2.807 ms median. The exact source was installed with both LaunchAgents running, 352 sources and no adapter failures, manifest parity, and loopback-only listening. `/state`, `/menubar`, and the browser show only one genuinely recent Desktop/3P trace; opened-only historical sessions remain absent across polling with no console errors.

### TASK-026 — Reconcile merged upstream PR #15 and PR #16

- [x] Change: merged current `origin/main`, preserved PR #15 Linux select readability, and ported PR #16 OpenCode/message, derived-sample, and session-detail cache bounds into the extracted runtime/domain/application owners.
- [x] Preserve: the 16-line `meter.py` facade, exact cost/token/execution totals, chronological processing of retained messages, current runtime/platform/model boundaries, macOS styling, local-only privacy, and prior Kiro/Windows/Claude fixes.
- [x] Verify: watched focused bounds/cache tests fail before the port and pass afterward; ran the full portable suite, language/native checks, installation, endpoints, service/listener checks, manifest parity, and responsive browser validation.
- [x] Requirements: REQ-002, REQ-004, REQ-008, REQ-009; NFR-001, NFR-003, NFR-004, NFR-006
- [x] Depends on: TASK-025

Exit condition: the reconciliation branch contains upstream commits `52d678c` and `d14f932`, all three PR #16 performance protections live behind the refactored boundaries, PR #15 select styling remains visible, and no prior behavior or architecture guard regresses.

Completed 2026-08-11. The refactor was checkpointed as `68c1b96`, then `origin/main` was merged as `c2a6f78`; the only textual conflict was the old monolithic `meter.py`, which remained a 16-line facade while every incoming behavior was ported to its extracted owner. Three direct tests failed for the intended reasons before implementation and passed afterward, alongside PR #16's original large-volume timing test. The complete suite passes 441 tests with two expected non-host-native skips. Python, JavaScript, shell, Swift, native smoke, whitespace, installed manifest parity, both LaunchAgents, loopback-only listening, and live endpoints pass. The installed dashboard indexes 354 sources without adapter failures; wide 1280, laptop 1024, and narrow 390 visual checks show readable dark selects and no console errors. Native Linux visual confirmation remains a release-matrix check because this host is macOS.

## Delivery sequence

Recommended pull-request boundaries:

1. TASK-001–TASK-004: contracts, registry, and compatibility seam.
2. TASK-005–TASK-008: model/quota/platform/packaging boundaries.
3. TASK-009–TASK-011: shared domain extraction.
4. TASK-012, TASK-013, TASK-014, TASK-015: one runtime per pull request.
5. TASK-016–TASK-018: thin application facade and metadata-driven clients.
6. TASK-019: pure OTel privacy and mapping seam.
7. TASK-020: optional and separately approved; omit if mapping-only is sufficient.
8. TASK-021: Kiro port.
9. TASK-022: Windows port.
10. TASK-023: cleanup, documentation, and complete matrix validation.
11. TASK-024: duplicate logical-session routing regression.
12. TASK-025: Claude Desktop evidence-activity correction.
13. TASK-026: merged-main performance and Linux-select reconciliation.

Do not combine the four runtime ports into one review. The sequential order deliberately tests the abstraction against both database and file evidence before moving the oldest parsers.

## Traceability matrix

| Requirement | Implementing tasks | Primary verification |
|---|---|---|
| REQ-001 | TASK-002, 004, 005, 011, 014, 015, 021 | Identity, pricing, aggregate, adapter tests |
| REQ-002 | TASK-003, 012–017, 021 | Registry and shared adapter contract suite |
| REQ-003 | TASK-001–004, 009–015, 025 | Invariants, characterization, activity evidence, privacy tests |
| REQ-004 | TASK-001, 009–016, 026 | Runtime-neutral domain, bounds, and parity tests |
| REQ-005 | TASK-005, 009, 021 | Historical/cross-runtime pricing tests |
| REQ-006 | TASK-006, 016 | Quota isolation and sanitized-error tests |
| REQ-007 | TASK-007, 016, 022 | Platform contracts and native matrix |
| REQ-008 | TASK-004, 017, 018, 021, 022, 026 | Catalog, responsive UI, select, and native tray tests |
| REQ-009 | TASK-001, 003–008, 011, 016–018, 022, 024, 026 | Golden HTTP/MCP/settings/install, cache, and duplicate-route compatibility |
| REQ-010 | TASK-008, 021, 022, 023 | Manifest and staged/source parity |
| REQ-011 | TASK-019, 020 | Golden mapping and optional exporter isolation |
| REQ-012 | TASK-019, 020 | Adversarial privacy and disabled-mode tests |
| REQ-013 | No implementation; architecture seam in TASK-003 and explicit deferral in TASK-020 | Design review confirms no proprietary adapter depends on OTLP |
| REQ-014 | TASK-007, 021, 022 | Extension-budget review and native validation |
| NFR-001 | TASK-001–006, 009–010, 012–020 | Privacy, serialization, sanitized-error tests |
| NFR-002 | TASK-002–006, 016, 019–020 | Clean-environment import and default install |
| NFR-003 | TASK-001, 007–008, 018, 022–023, 026 | macOS/Linux/Windows native matrix |
| NFR-004 | TASK-001, 003, 009–015, 026 | Revision/cache/bounds/performance regression |
| NFR-005 | TASK-003, 005–007, 012–015, 017–018, 021–023 | Synthetic extension and diff-boundary checks |
| NFR-006 | Every implementation task | Green full suite and rollback-compatible facade per task |
| NFR-007 | TASK-002–003, 016, 023 | Type, registry, import, and architecture checks |
| NFR-008 | TASK-019–020 | Version-pinned golden OTel mappings |

## Final definition of done and host-matrix status

- Current behavior is covered by sanitized characterization fixtures.
- Claude, Codex, Cursor, OpenCode, and Kiro satisfy one adapter contract.
- Model pricing and quotas are independent of runtime identity.
- macOS, Linux, and Windows lifecycle code is separated; macOS is natively validated here, while Linux and Windows native lifecycle validation remains a release gate on those hosts.
- Generic browser and native surfaces render a synthetic runtime without a provider-specific switch.
- `meter.py` is a small composition/compatibility facade.
- Default installation remains dependency-free, local-only, and telemetry-free.
- The OTel mapping passes adversarial privacy tests; exporter work is either explicitly approved and isolated or intentionally omitted.
- Current HTTP, MCP, routes, settings, estimates, caches, install behavior, and user-visible navigation remain compatible.
- Kiro and Windows meet the extension budget instead of expanding the monolith.
