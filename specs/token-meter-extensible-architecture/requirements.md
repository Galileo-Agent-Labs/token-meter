# Extensible Runtime, Model, Platform, and Telemetry Architecture

Status: Draft for review  
Date: 2026-08-10  
Scope: Internal architecture and compatibility-preserving migration

## Problem statement

Token Meter has grown by adding each runtime directly to a single implementation. `meter.py` currently owns trace discovery, runtime-specific parsing, pricing, quotas, aggregation, settings, updates, deletion, and HTTP serving. The dashboard and native clients also encode runtime-specific lists and presentation rules.

This shape makes every new runtime or operating system a cross-cutting change. The pending Kiro and Windows pull requests expose the problem: adding a runtime touches parsing, pricing identity, aggregation, UI, native menus, and tests, while adding an operating system duplicates lifecycle and tray behavior. The architecture also conflates four different concepts:

- the runtime that produced evidence, such as Claude, Codex, Cursor, OpenCode, or Kiro;
- the model provider used by that runtime;
- the provider account or quota system;
- the host operating system and lifecycle implementation.

The refactor must separate those axes without changing today's user-visible behavior or weakening Token Meter's privacy guarantees.

## Goals

1. Make a runtime integration an explicit adapter with a small, testable contract.
2. Make model identity, pricing, quotas, and runtime identity independent concepts.
3. Isolate operating-system paths, process control, installation, and native-tray behavior.
4. Move reusable usage, timing, tool, insight, and aggregation logic out of `meter.py`.
5. Preserve the current HTTP, MCP, settings, route, installation, and local-only behavior during migration.
6. Let browser and native clients render new runtimes from server-supplied metadata with safe generic fallbacks.
7. Define a privacy-safe OpenTelemetry boundary without making OpenTelemetry the internal domain model or a required dependency.
8. Keep `main` runnable and testable after every migration milestone.

## Non-goals

- Rewriting the product in another language or web framework.
- Redesigning the dashboard or navigation.
- Changing pricing values, estimate semantics, budgets, or quota calculations except where required to preserve existing behavior after extraction.
- Making OpenTelemetry collection or export mandatory.
- Exporting prompts, responses, reasoning, tool arguments/results, local paths, credentials, account data, or raw traces.
- Replacing proprietary runtime discovery and parsing with the OpenTelemetry file-log receiver.
- Creating an unbounded third-party plugin execution system.
- Merging the Kiro or Windows pull requests unchanged.
- Removing legacy APIs, hash routes, settings keys, or installation entrypoints in this effort.

## Constraints

- The Python server and MCP core remain Python-standard-library-only.
- The service remains local-only by default and must work with no collector and no network access.
- Existing user settings continue through the current atomic JSON-write and action-token-protected mutation paths.
- Existing trace stores and databases remain read-only inputs.
- Unavailable evidence must remain unavailable; it must not be converted into a measured zero.
- Estimates continue to be labeled as estimates.
- Model aggregation remains scoped by runtime unless an explicitly named cross-runtime projection says otherwise.
- macOS, Linux, and Windows lifecycle behavior requires validation on the corresponding operating system; source inspection on macOS is not sufficient native validation.
- The migration must preserve unrelated work and avoid a flag-day rewrite.

## Definitions

- **Runtime**: The client or agent environment that emits evidence, such as `claude`, `codex`, `cursor`, `opencode`, or `kiro`.
- **Model provider**: The company or catalog namespace that defines a model and its price, independently of the runtime using it.
- **Account provider**: The service whose account or quota endpoint is queried.
- **Platform**: The operating system and lifecycle environment: macOS, Linux, or Windows.
- **Session source**: A bounded locator and revision describing evidence that a runtime adapter can load.
- **Normalized session**: The runtime-neutral internal representation consumed by domain services and projections.
- **Evidence basis**: Whether a value is measured, inferred, estimated, or unavailable.
- **Adapter**: A typed implementation behind a stable contract and registered under a stable identifier.
- **Client catalog**: Bounded server metadata used by the dashboard, MCP surface, and native trays to label runtimes and select safe presentation defaults.

## User stories

- As a maintainer, I can add a runtime without editing unrelated runtime parsers, platform lifecycle code, or core aggregation logic.
- As a maintainer, I can add or update a model price without treating the model provider as the runtime.
- As a maintainer, I can add an operating-system integration without changing runtime parsers.
- As a user, I can upgrade through the refactor without losing settings, routes, session history, estimates, or menu-bar behavior.
- As a privacy-conscious user, I can leave telemetry disabled and be certain no content or trace data is transmitted.
- As an operator who explicitly enables telemetry, I can export bounded aggregate signals through an optional OpenTelemetry integration.
- As a contributor, I can validate a runtime using contract fixtures and validate platform behavior on its native OS.

## Functional requirements

### REQ-001 — Separate identity axes

The internal domain model SHALL represent `runtime_id`, `model_provider_id`, `model_id`, and `account_provider_id` as separate fields. A compatibility projection MAY continue exposing the existing external `provider` field as the runtime identifier during migration.

Acceptance criteria:

- WHEN a runtime uses a model from a different provider, THEN pricing SHALL resolve from the model provider and model identifier rather than the runtime identifier.
- WHEN quota data is requested, THEN the account provider SHALL be selected independently of model pricing.
- WHEN existing API consumers read `provider`, THEN they SHALL receive the same runtime-oriented value they receive before the refactor.

### REQ-002 — Runtime adapter contract

Each runtime SHALL implement one explicit adapter contract for source discovery, revision detection, source loading, runtime metadata, and permitted deletion targets.

Acceptance criteria:

- WHEN a registered runtime is discovered, THEN the registry SHALL invoke that runtime's adapter without a provider-specific branch in the registry or HTTP layer.
- WHEN an adapter returns a session, THEN the session SHALL satisfy the normalized-session contract before entering shared domain services.
- IF one adapter cannot parse a source, THEN the failure SHALL be isolated to that source and SHALL NOT prevent other runtimes from loading.
- WHEN deletion is unsupported or unsafe for a runtime, THEN its adapter SHALL return a deny policy rather than a guessed path.

### REQ-003 — Normalized evidence contract

Runtime adapters SHALL emit a normalized representation for identity, timestamps, token usage, cost basis, tools, timing, session linkage, and evidence basis.

Acceptance criteria:

- WHEN a field cannot be derived from source evidence, THEN it SHALL be marked unavailable rather than populated with zero.
- WHEN a value is inferred or estimated, THEN its evidence basis SHALL remain available to downstream projections.
- WHEN the same model name appears in two runtimes, THEN the default aggregate key SHALL preserve both runtime identities.
- WHEN a normalized object is serialized for a public endpoint, THEN raw locators and private source content SHALL be excluded.
- WHEN a runtime rewrites or touches an evidence container without adding a model/user event, THEN normalized activity time SHALL remain unchanged while source revision SHALL still invalidate.

### REQ-004 — Shared domain services

Usage, timing, tool, insight, and aggregate calculations that do not depend on source format SHALL live in runtime-neutral domain services.

Acceptance criteria:

- WHEN equivalent normalized evidence is provided by two adapters, THEN shared calculations SHALL apply the same rules.
- WHEN existing calculation behavior is extracted, THEN characterization fixtures SHALL demonstrate output parity before the old implementation is removed.
- IF runtime-specific evidence requires a special interpretation, THEN the adapter SHALL encode the difference explicitly rather than add a runtime branch to a shared service.

### REQ-005 — Model catalog and pricing boundary

Model aliases, model-family normalization, price histories, and price lookup SHALL live behind a model catalog and pricing boundary independent of runtime adapters.

Acceptance criteria:

- WHEN an exact model and date are known, THEN price resolution SHALL retain the existing historical behavior.
- WHEN the model provider is unknown, THEN the system SHALL return an explicit unknown-price result and SHALL NOT silently use another runtime's pricing table.
- WHEN a new model with an existing trace encoding is added, THEN the change SHALL be limited to catalog/pricing data and tests.

### REQ-006 — Quota adapter boundary

Provider-account quota requests SHALL use separate, bounded, read-only quota adapters.

Acceptance criteria:

- WHEN a runtime has no quota adapter, THEN usage parsing and local estimates SHALL continue to work.
- WHEN a quota request fails, THEN public errors SHALL remain bounded and SHALL NOT expose credentials, local paths, response bodies, or raw exception text.
- WHEN quota data is unavailable, THEN the UI and MCP output SHALL report unavailable rather than measured zero.

### REQ-007 — Platform boundary

Host paths, process discovery, detached process options, trash behavior, service lifecycle, installation, and native tray selection SHALL be isolated behind platform-specific modules or scripts.

Acceptance criteria:

- WHEN the same runtime adapter runs on two operating systems, THEN its parsing logic SHALL not contain lifecycle or tray branches for those systems.
- WHEN an unsupported platform operation is requested, THEN the platform layer SHALL return a clear unsupported result.
- WHEN a native implementation changes, THEN it SHALL be validated on its native operating system before release.

### REQ-008 — Metadata-driven clients

The server SHALL expose a bounded client catalog containing runtime identifiers, labels, capabilities, and presentation defaults needed by browser, MCP, and native clients.

Acceptance criteria:

- WHEN a new runtime is registered, THEN generic dashboard and recent-session surfaces SHALL display it without adding another provider switch.
- WHEN a client does not recognize a runtime-specific icon or color, THEN it SHALL use an accessible generic fallback.
- WHEN runtime-specific controls are unsupported, THEN clients SHALL hide or disable them based on declared capabilities.

### REQ-009 — Compatibility facade

The root `meter.py`, current scripts, HTTP routes, MCP tool contracts, settings keys, hash routes, and installed-runtime entrypoints SHALL remain compatible throughout the migration.

Acceptance criteria:

- WHEN an existing script imports or invokes `meter.py`, THEN it SHALL continue to work through a thin compatibility/composition facade.
- WHEN existing saved settings are loaded, THEN they SHALL retain their meaning or pass through an explicit idempotent migration.
- WHEN existing endpoint contract tests run after each milestone, THEN they SHALL pass without consumers opting into the new internals.
- WHEN multiple discovered trace segments share one logical session ID, THEN the session route SHALL resolve the same active or most-recent segment represented by current-session aggregation rather than depend on filesystem discovery order.

### REQ-010 — Packaging manifest

Source and installed-runtime file selection SHALL use one explicit manifest or shared staging rule that includes the new package and platform assets.

Acceptance criteria:

- WHEN a new internal module is required at runtime, THEN installation validation SHALL fail if it is absent from the staged runtime.
- WHEN installation succeeds, THEN staged files SHALL match their source counterparts.
- WHEN an uninstall runs, THEN it SHALL remove only owned runtime and service artifacts.

### REQ-011 — OpenTelemetry mapping boundary

The system SHALL define a one-way mapping from the normalized domain model to a pinned, versioned subset of OpenTelemetry GenAI attributes and metrics. This mapping SHALL be optional and SHALL not define the core domain model.

Acceptance criteria:

- WHEN telemetry is disabled, THEN no OpenTelemetry SDK/exporter SHALL be required and no telemetry SHALL leave the process.
- WHEN telemetry is enabled, THEN only explicitly allowlisted aggregate attributes SHALL be exported.
- WHEN a semantic-convention field is experimental, renamed, or absent, THEN the mapping layer SHALL absorb the change without changing runtime adapters or stored settings.
- WHEN a normalized value has measured, inferred, estimated, or unavailable status, THEN the mapping SHALL preserve that distinction or omit the value; it SHALL NOT mislabel it as measured.

### REQ-012 — OpenTelemetry privacy controls

Telemetry export SHALL be explicitly opt-in, local-first, disabled by default, and governed by a deny-by-default attribute allowlist.

Acceptance criteria:

- WHEN export is enabled, THEN prompts, responses, reasoning, system instructions, tool definitions, tool arguments/results, credentials, account data, raw traces, database contents, and local paths SHALL never be exported.
- WHEN an exporter endpoint is configured, THEN configuration SHALL use existing protected settings mutation and validation patterns.
- IF exporter initialization or delivery fails, THEN Token Meter's local parsing, dashboard, MCP, and native surfaces SHALL continue to operate.

### REQ-013 — Optional OTLP input

The architecture SHALL permit a future OTLP input adapter for runtimes that already emit compatible telemetry, without requiring existing runtimes to convert their proprietary evidence to OTLP.

Acceptance criteria:

- WHEN an OTLP input is added, THEN it SHALL enter through the runtime adapter boundary and satisfy the same normalized evidence contract.
- WHEN proprietary evidence is richer than the selected semantic conventions, THEN the native adapter SHALL retain that evidence rather than discard it to fit OTLP.
- UNTIL a concrete runtime and stable schema justify the feature, THEN OTLP input SHALL remain out of the initial implementation milestones.

### REQ-014 — Migration order for pending runtime and platform work

The extensibility foundation SHALL land before Kiro runtime support and before Windows lifecycle/tray support are ported.

Acceptance criteria:

- WHEN Kiro is ported, THEN it SHALL be implemented as a runtime adapter with independent model-provider and quota-provider identities.
- WHEN Windows support is ported, THEN it SHALL use the platform boundary and SHALL not fork or duplicate runtime parsing.
- WHEN either feature is reviewed, THEN its diff SHALL be evaluated against the extension budget in NFR-005.

## Non-functional requirements

### NFR-001 — Privacy and bounded output

No new module, endpoint, log, test artifact, or telemetry path SHALL expose private trace content, credentials, account data, local paths, or raw exceptions. Provider-account access SHALL remain narrow, bounded, sanitized, and read-only.

### NFR-002 — Dependency discipline

The default server and MCP path SHALL remain standard-library-only. Optional OpenTelemetry support SHALL be isolated behind an extra installation target or external collector integration and SHALL fail closed when absent.

### NFR-003 — Cross-platform verification

Shared contracts SHALL run in the normal unit suite. Platform lifecycle, packaging, and tray behavior SHALL also have native CI or recorded native validation on macOS, Linux, and Windows as applicable.

### NFR-004 — Performance and bounded work

The refactor SHALL preserve current revision-based caching, bounded reads, pagination, and response limits. Adapter isolation SHALL not require reparsing every runtime when one source changes.

Acceptance criteria:

- WHEN the unchanged 300-plus-session local corpus is discovered repeatedly, THEN warm median discovery time SHALL improve by at least 50% from the recorded 65.703 ms pre-registry baseline on the same machine.
- WHEN a known metadata or request-log file changes, THEN its new revision SHALL be observed on the next discovery call even while recursive path enumeration is cached.
- WHEN a new file appears under a recursively searched root, THEN it SHALL become discoverable within the bounded path-cache interval plus one watcher iteration.
- THE recursive path cache SHALL have fixed time and entry bounds and SHALL never cache parsed prompts, responses, reasoning, or tool payload content.
- WHEN an OpenCode session contains more than 500 user/assistant messages, THEN cross-session summarization SHALL process only the newest 500 in chronological order while retaining authoritative session totals.
- WHEN model aggregation receives more than 2,000 workload observations or 500 matched-pace observations for one model scope, THEN derived workload and pace samples SHALL remain within those bounds while exact token, cost, execution, and coverage totals remain uncapped.
- WHEN a session-detail request can attach an existing cross-session snapshot, THEN it SHALL reuse that snapshot instead of synchronously rebuilding all cross-session aggregates.

### NFR-005 — Extension budget

A conforming extension SHALL meet these limits:

- a new runtime requires one adapter, one registry entry, fixtures/contract tests, and optional catalog presentation metadata;
- a new model with an existing encoding requires catalog/pricing data and tests only;
- a new operating system requires platform lifecycle, packaging, and tray work but no runtime-parser changes;
- shared domain, HTTP, MCP, and unrelated adapter modules require no edits unless the new extension introduces a genuinely new normalized capability.

Any exception SHALL be documented as a design change rather than hidden in provider conditionals.

### NFR-006 — Incremental recoverability

Every migration task SHALL leave a runnable system, retain a rollback path to the previous facade, and remove old code only after parity tests pass.

### NFR-007 — Maintainability

Public contracts SHALL use typed immutable data objects where practical and explicit registries. Import-time filesystem scans, magic plugin discovery, and circular imports SHALL not be introduced.

### NFR-008 — Convention evolution

OpenTelemetry semantic-convention dependencies SHALL be pinned in the mapping module and covered by golden mapping tests because the GenAI conventions are still evolving.

## Requirements gate

Requirements are ready for design review when:

- runtime, model-provider, account-provider, and platform identities are unambiguous;
- compatibility and privacy constraints are explicit;
- normal, failure, migration, platform, and recovery behavior are covered;
- OpenTelemetry is defined as an optional boundary rather than a replacement for proprietary parsing;
- the extension budget is measurable.
