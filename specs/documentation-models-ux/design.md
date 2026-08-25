# Documentation and Dashboard Cleanup Design

Status: implemented and merged into local `main` on 2026-08-11.

Related requirements: [requirements.md](requirements.md).

## Overview

This change makes `specs/ARCHITECTURE.md` the stable engineering map, narrows
`README.md` to the user journey, and moves maintenance material into
`specs/AGENTS.md`. It also removes the two unwanted nonlinear Session scale choices,
adds exact-day Models filters, and makes the Model trends hover card reflect
only what the chart currently encodes.

No runtime parser, pricing rule, or public endpoint shape needs to change.
`matched_pace_windows()` adds exact-day entries to its existing windows map so
the Matched pace KPI follows the same History selection as the rest of Models.
Documentation changes describe the architecture already introduced under
`token_meter/`; the remaining UI changes stay inside `page.html` and its
existing contracts in `tests/test_meter.py`.

## Current State and Pressure Points

`meter.py` is now a compatibility facade, while current ownership lives under
`token_meter/`. The README still contains a monolithic repository tree and
developer validation/publishing sections. `STATE.md` is a July snapshot that
names removed routes, obsolete package artifacts, old test counts, and the old
chart-scale set as current. `REQUIREMENTS.md`, `research.md`, and
`implementation-log.md` contain useful evolution history but do not clearly
separate that history from present authority. `DESIGN.md` is a visual design
system, not an engineering architecture.

In `page.html`, the Session chart stores one of `linear`, `sqrt`, `log`, or
`cumulative` in `tm_chart_scale`. Models stores `7`, `30`, `90`, or `all` in
`tm_model_range`, derives a lower-bound cutoff, and uses that cutoff for the
trend and table. Its chart hover card currently expands every active model into
seven metrics even though the chart encodes output bars plus one selected line
metric.

## Documentation Architecture

### Canonical documents

- Root `README.md` owns user value, supported runtimes/platforms, installation,
  workflows, privacy, limitations, updates, uninstall, and troubleshooting.
- `specs/ARCHITECTURE.md` owns current engineering boundaries, flow, invariants,
  extension rules, and validation layers.
- `specs/AGENTS.md` owns coding-agent operating instructions: key paths, guarded
  contracts, change workflow, commands, installed-runtime proof, and release
  hygiene.
- `specs/CONTRIBUTING.md` owns human contribution flow and links to the two technical
  authorities above.
- `specs/DESIGN.md` remains the visual design system and links to
  `specs/ARCHITECTURE.md`
  for engineering structure.
- `specs/PRODUCT.md` remains the product/UX principles document and is updated to name
  all supported runtimes and platforms.
- `specs/SECURITY.md` remains the security and disclosure policy and points to the
  architecture privacy boundary where useful.

### Historical documents

Root `REQUIREMENTS.md`, `STATE.md`, `research.md`, and `implementation-log.md`
move under `specs/history/` and are retained as history. Each receives a
prominent note explaining its date/scope and links to current authorities.
`STATE.md` is reduced to a short historical index rather than trying to
maintain a volatile inventory and validation ledger. This removes false current
claims without destroying context.

`CLAUDE.md` moves to `specs/CLAUDE.md` and remains a small reference to
`AGENTS.md` in the same directory. Because common coding-agent clients discover
these files only at the repository root, README explicitly tells agents to open
`specs/AGENTS.md` before editing. This is a deliberate consequence of the
approved root-cleanliness rule, not an assumed preservation of auto-discovery.

At completion, `README.md` is the only tracked Markdown file at root. The
temporary ignored root `plan.md` may exist while current instructions require
it as execution state, but it is removed when the work is complete.

### README movement

The README loses its validation command catalog, repository layout tree, and
pre-publication file checklist. The architecture link replaces the tree; the
AGENTS/CONTRIBUTING links replace maintenance instructions. Deep discovery and
packaging descriptions are shortened where they repeat architecture internals,
but user-visible paths, privacy behavior, install/uninstall, and troubleshooting
remain because users need them.

## UI Design

### Session chart scale migration

The markup keeps two buttons: `Linear` and `Cumulative`. The JavaScript allowlist
becomes `['linear', 'cumulative']`. Startup already normalizes values not in the
allowlist, so existing `sqrt` and `log` preferences automatically become
`linear` and are written back to local storage.

The chart's cumulative calculations remain limited to Tokens and estimated
Cost. Linear remains the effective scale for Context, Tools, Steps, and Wait.
Dead nonlinear tick/coordinate branches may be removed only after focused tests
prove the remaining behaviors.

### Exact Models date windows

History options become:

1. Today (`today`)
2. Yesterday (`yesterday`)
3. Last 7 days (`7`)
4. Last 30 days (`30`, default)
5. Last 90 days (`90`)
6. All history (`all`)

A small Models-only browser window helper derives an inclusive set or start/end
pair of local `YYYY-MM-DD` keys. Browser consumers use one predicate instead of
independently interpreting `modelRange`:

- `mergeModelDays` input for KPIs;
- `buildModelTrend` points and exact-day axis;
- table row daily aggregation.

The existing Python `matched_pace_windows(sample_groups, now_ts=None)` helper
adds `today` and `yesterday` entries. Those entries filter each eligible sample
by its normalized local `day` key before running the existing pair matcher.
Window membership is precomputed once per runtime, avoiding repeated date scans
for every runtime pair as the number of models grows.
Numeric and all-history windows retain their current timestamp behavior. The
existing `matched_pace.windows` object therefore gains two keys without a new
route or top-level payload.

For Today and Yesterday the chart always has the selected single date on its
axis, allowing the existing no-data message to remain honest. Numeric ranges
retain their current filled daily axis; all history retains its observed-day
axis. Invalid stored values normalize to the existing 30-day default.

### Model trends hover card

The chart and table remain unchanged. The hover card continues to list only
models with usage on the hovered date. Each row shows:

- model/runtime name and its existing color swatch;
- total output tokens, matching the bars;
- the selected metric label and value, matching the line.

The formatter comes from the existing `MODEL_TREND_METRICS` entry so output
pace, wait, average input, and average output retain their current units and
availability semantics. If the selected metric is unavailable, the tooltip
shows `--`; it does not infer zero. Existing pointer timing, scroll containment,
sticky date heading, keyboard focus, and viewport flipping stay intact.

## Data Flow and Invariants

Daily rows already carry local date keys, tokens, executions, and timing
aggregates. KPI, trend, and table filtering remains a pure browser projection.
Matched pace continues to be derived server-side and widens only its existing
windows map with `today` and `yesterday`; `/state` and `/model-stats` gain no new
top-level fields.

The following invariants remain mandatory:

- identical model names from different runtimes remain separate;
- missing evidence remains unavailable, never measured zero;
- cost and local token proxies retain estimate labels;
- no raw trace content or local path reaches a public projection;
- project/model/history choices remain browser-local preferences;
- live polling must not replace an interacting control or tooltip target.

## Test Strategy

Focused source-contract tests in `DashboardLayoutTests` will first fail for:

- the presence of Sqrt/Log and the old scale allowlist;
- the absence of Today/Yesterday options and exact-day range helpers;
- the seven-metric hover card instead of output plus selected metric;
- stale documentation ownership and missing architecture references.

Where practical, embedded dashboard JavaScript will be executed with controlled
daily rows so Today and Yesterday behavior is asserted as an observable result,
not merely as source text. Existing backend aggregation tests protect payload
semantics.

After focused green tests, run the complete repository suite and static checks.
Install the exact checkout, verify `/health`, `/menubar`, both LaunchAgents,
loopback listener ownership, installed revision, and manifest parity. Exercise
Sessions and Models in a browser at wide-desktop and 1024-pixel-laptop widths,
including Today, Yesterday, old stored scale migration, tooltip hover, empty
exact-day state, and console output.

## Decisions

### DEC-001: Add `specs/ARCHITECTURE.md` instead of expanding README or AGENTS

Context: Architecture needs a durable home, while README and AGENTS serve
different readers.

Options:

1. Put architecture in README. This is visible but keeps the README long and
   mixes user and maintainer jobs.
2. Put architecture in AGENTS. This helps agents but creates another large
   instruction file and hides the design from human contributors.
3. Add `specs/ARCHITECTURE.md` and link to it. This gives one shared authority without
   duplicating it.

Decision: Option 3.

Rationale: It best separates user, contributor, and implementation concerns.

Consequences: Maintained docs must link instead of copying component maps.

### DEC-005: Keep only README Markdown at the repository root

Context: The user explicitly requested one root Markdown entry point after
approving the original documentation design.

Decision: Move every other tracked project Markdown file under `specs/`, move
useful ignored historical Markdown there when it is safe to publish, and make
README the index.

Rationale: This follows the requested repository organization exactly and makes
the documentation tree visible from one root entry point.

Consequences: Root auto-discovery of `AGENTS.md` and `CLAUDE.md` is no longer
available. README must disclose this and directly link the agent instructions.

### DEC-002: Label historical documents instead of continuously rewriting them

Context: Several files are useful records but structurally guaranteed to drift.

Decision: Keep historical content with an explicit status banner; collapse the
particularly stale `STATE.md` into a historical index.

Rationale: This preserves provenance while removing false authority.

Consequences: Current validation evidence belongs in commits, CI, and AGENTS
commands rather than a manually maintained snapshot ledger.

### DEC-003: Filter exact dates in the browser

Context: The model payload already contains daily rows, and all Models surfaces
are rendered from it.

Decision: Add a single browser-side date-window predicate and extend the
existing server-side matched-pace windows map with the same two exact dates.

Rationale: It avoids a new route or payload shape and guarantees that KPIs,
chart, table, and matched pace use the same exact-day semantics.

Consequences: Tests must cover every Models consumer, matched-pace window keys,
and local-date boundaries.

### DEC-004: Tooltip mirrors chart encodings

Context: The hover card is tall because it displays seven metrics while the
chart displays output plus one selected metric.

Decision: Show those two values only, preserving model identity and existing
interaction behavior.

Rationale: The tooltip becomes faster to scan without simplifying or removing
the chart or table.

Consequences: Deeper metrics remain available in the unchanged comparison
table and KPI cards.

## Risks and Mitigations

- **Date drift near midnight:** derive all keys from one render-time local date
  and use the same helper for filter and axis generation.
- **Stored preference regression:** validate allowed values and persist a safe
  fallback for both chart scale and Models history.
- **Documentation duplication:** link to architecture sections; do not paste a
  second component inventory into README or CONTRIBUTING.
- **Tooltip regression:** retain the existing event handlers and geometry code;
  change only row content and focused CSS if needed.
- **Installed/source mismatch:** reinstall only after source tests pass and use
  the manifest parity command before local-main integration.

## Requirement Traceability

| Requirement | Design area | Planned validation |
|---|---|---|
| REQ-DOC-001 | Documentation architecture | link/path audit and human review |
| REQ-DOC-002 | Canonical documents, README movement | Markdown ownership audit |
| REQ-DOC-003 | Historical documents | stale-claim audit |
| REQ-SESSION-001 | Session scale migration | focused UI contract and browser storage check |
| REQ-MODELS-001 | Exact Models date windows | executable range tests and browser checks |
| REQ-MODELS-002 | Model trends hover card | focused tooltip contract and hover checks |
| NFR-001 | Data flow and invariants | existing privacy/contracts suite |
| NFR-002 | Data flow and invariants | full regression suite |
| NFR-003 | UI design | wide-desktop/1024-pixel-laptop browser validation |
| NFR-004 | Test strategy | repository validation and installed parity |
