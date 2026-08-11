# Documentation and Dashboard Cleanup Requirements

Status: approved for implementation on 2026-08-11.

## Problem

Token Meter now has explicit runtime, platform, domain, service, packaging, and
privacy boundaries, but its primary documentation still describes portions of
the former monolithic implementation. The user-facing README also contains
developer validation and repository-maintenance material. At the same time,
the Session chart exposes two scale choices that are no longer wanted, and the
Models history selector cannot isolate today or yesterday.

## Goals

- Make one document the canonical current architecture reference.
- Keep the README focused on installing, using, trusting, and troubleshooting
  Token Meter.
- Put coding-agent workflow, validation, release hygiene, and implementation
  constraints in AGENTS.md, with contributor-facing links in CONTRIBUTING.md.
- Prevent historical notes from being mistaken for current behavior.
- Simplify the Session chart scale selector without breaking stored browser
  preferences.
- Let Models users inspect exactly today or exactly yesterday.
- Simplify the Model trends hover card without changing the chart, metrics, or
  comparison table.

## Non-goals

- Redesigning the Models chart or comparison table.
- Changing pricing, runtime discovery, or public endpoint shapes beyond adding
  Today and Yesterday keys to the existing matched-pace windows map.
- Rewriting historical product research as if it were a current specification.
- Adding a documentation generator or a third-party dependency.

## Definitions

- **Canonical architecture**: the current description of component ownership,
  data flow, extension points, and invariants that maintainers should trust.
- **Historical document**: a useful record of an earlier product state that is
  not an authority for current code or validation status.
- **Local calendar day**: a `YYYY-MM-DD` date derived in the browser's local
  timezone, consistent with the dashboard's existing daily views.
- **Model trend tooltip**: the hover card attached to the daily Model trends
  chart. It is not a table-header help tooltip.

## Functional Requirements

### REQ-DOC-001: Canonical architecture document

THE repository SHALL contain `ARCHITECTURE.md` describing the current runtime,
platform, model, domain, service, web, native-companion, MCP, packaging, caching,
privacy, and testing boundaries.

THE architecture document SHALL explain the normal data flow from local trace
discovery through normalization and aggregation to dashboard, native, and MCP
projections.

THE architecture document SHALL state the extension budget for adding a
runtime, operating system, or pricing rule and identify core areas that should
remain untouched.

### REQ-DOC-002: Documentation ownership

WHEN a reader opens `README.md` THEN the repository SHALL prioritize product
value, supported environments, installation, primary workflows, privacy,
limitations, updates, uninstall, and troubleshooting.

WHEN a coding agent opens `AGENTS.md` THEN the repository SHALL provide the
current component map, architecture link, implementation invariants, complete
validation commands, runtime installation checks, and release hygiene.

WHEN a human contributor opens `CONTRIBUTING.md` THEN the repository SHALL link
to both the architecture and agent runbook and SHALL retain contributor-oriented
setup and pull-request guidance.

### REQ-DOC-003: Current versus historical documents

WHEN a maintained document describes architecture or repository state THEN it
SHALL link to `ARCHITECTURE.md` instead of duplicating a conflicting component
map.

IF a document records an earlier implementation or point-in-time validation
THEN it SHALL be visibly labelled historical and SHALL direct readers to the
current authority.

THE documentation audit SHALL cover every tracked Markdown file except files
whose content is an external license or third-party source attribution.

### REQ-SESSION-001: Supported chart scales

WHEN a user views the Session execution chart THEN the scale selector SHALL
offer only `Linear` and `Cumulative`.

IF `tm_chart_scale` contains `sqrt`, `log`, or any unknown value THEN the
dashboard SHALL normalize it to `linear`, update storage, and render without an
initialization error.

WHILE `Cumulative` is selected, the existing cumulative Tokens and estimated
Cost behavior SHALL remain unchanged; other chart metrics SHALL retain their
existing per-execution behavior.

### REQ-MODELS-001: Exact-day history filters

WHEN a user opens the Models History selector THEN it SHALL offer `Today` and
`Yesterday` before the existing 7-day, 30-day, 90-day, and all-history options.

WHEN `Today` is selected THEN Model KPIs, the trend, the comparison table, and
matched-pace inputs SHALL include only rows whose day equals the current local
calendar day.

WHEN `Yesterday` is selected THEN those Models surfaces SHALL include only rows
whose day equals the previous local calendar day.

IF the selected exact day has no model activity THEN the chart and table SHALL
show their existing empty states rather than falling back to another day or
displaying measured zeroes for unavailable evidence.

WHEN the page reloads THEN a valid Today or Yesterday selection SHALL be
restored from `tm_model_range`.

### REQ-MODELS-002: Focused trend tooltip

WHEN a user hovers a populated day in Model trends THEN the tooltip SHALL show
the date and, for each active model runtime, its identity, total output tokens,
and the value of the currently selected trend metric.

THE tooltip SHALL preserve the existing active-row filtering, color swatch,
left/right viewport positioning, pointer handoff, and keyboard-focus behavior.

THE Models chart, metric choices, KPI cards, comparison table, and table-header
help SHALL remain structurally and semantically unchanged.

## Quality Requirements

### NFR-001: Privacy and locality

THE change SHALL add no hosted assets, analytics, telemetry, trace persistence,
or disclosure of prompts, responses, paths, credentials, tool contents, or raw
provider data.

### NFR-002: Compatibility

THE change SHALL preserve current hash routes, project/model selections,
metric selections, and all model aggregation/API contracts outside the explicit
history-window additions.

### NFR-003: Responsive and accessible UI

THE new filter options and simplified tooltip SHALL remain usable at wide,
laptop, and narrow dashboard widths, with no new horizontal overflow or browser
console errors.

THE scale buttons and history selector SHALL retain accessible labels and
selected-state semantics.

### NFR-004: Verification

THE implementation SHALL be developed with focused failing tests before
production changes, followed by the full unit/contract suite, Python compile,
embedded JavaScript parse, shell checks, Swift compile and smoke, browser checks,
installed-runtime health checks, and manifest parity validation.

## Acceptance Criteria

- `ARCHITECTURE.md` exists and maintained entry documents link to it.
- README no longer contains the validation command catalog, repository tree, or
  publishing checklist.
- Current developer commands and runtime checks are present in AGENTS.md.
- Historical Markdown files cannot be mistaken for current architecture or
  validation status.
- Session scale UI contains Linear and Cumulative but not Sqrt or Log.
- A stored `sqrt`, `log`, or invalid scale loads as Linear.
- Models History exposes Today and Yesterday and filters all dependent Models
  surfaces to the exact selected local date.
- The Model trends hover card contains output plus only the selected metric per
  model, while the chart and table retain their current structure.
- Source and installed runtime pass the repository validation requirements.
