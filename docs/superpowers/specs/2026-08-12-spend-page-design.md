# Spend Page Design

Date: 2026-08-12

Status: Approved visual direction; awaiting written-spec review

Primary surface: `page.html`

Relationship to prior work: this document supersedes the **Daily Spend and Wait** section and the no-route/no-payload constraint of `2026-08-12-professional-chart-system-design.md` only for this surface. The shared chart primitives and every other surface in that design remain unchanged.

## Objective

Replace the one-day **Daily** dashboard surface with a range-based **Spend** surface that answers three questions directly:

1. How much did the locally observed agent activity cost in the selected period?
2. How did that spend change across calendar days?
3. Which runtime contributed each day's spend?

The first release supports Today, Last 7 days, Last 30 days, and a custom inclusive date range. Subscription renewal dates, billing-cycle budgets, and spend forecasting remain explicitly deferred.

## Audience and operating mode

The audience is an individual developer reviewing local Claude, Codex, Cursor, OpenCode, and Kiro evidence. The surface remains an **Operate** dashboard: it should make period selection, comparison, and runtime attribution fast without presenting Token Meter estimates as provider invoices.

Success means a user can select a period and immediately read the period total, daily pace, highest-spend day, top runtime, and the runtime composition of every daily bar.

## Chosen direction

The approved direction keeps Token Meter's existing Spectrum Instrument Field for the application and applies the attached Splunk Jupiter dark chart scale only to categorical spend data.

- The blue/cyan spectrum remains the interface language for navigation, active range controls, focus, and the selected day.
- Splunk chart colors are confined to stacked bar segments, legend samples, inspection rows, and runtime totals.
- Neutral dark cards and plot fields separate the two systems and keep the page from becoming multicolored.
- Semantic success, warning, and danger colors remain reserved for actual states; they are not runtime identifiers.

This role separation was selected over recoloring the whole page with the Jupiter palette or forcing all runtime series into shades of blue. The first would create a page-specific rebrand; the second would make stacked segments harder to distinguish.

## Stable runtime color mapping

Use the attached Splunk Jupiter dark categorical chart tokens with a fixed mapping across the Spend page:

| Runtime | Token | Color |
| --- | --- | --- |
| Claude | `--jds-color-chart-4` | `#f26722` |
| Codex | `--jds-color-chart-3` | `#04a4b0` |
| Cursor | `--jds-color-chart-2` | `#a974f7` |
| OpenCode | `--jds-color-chart-5` | `#fa5762` |
| Kiro | `--jds-color-chart-1` | `#868ec2` |
| Unknown or future runtime | severity-unknown fallback | `#889099` |

The legend follows this order rather than changing with daily spend rank. Every stacked bar uses the same bottom-to-top order. An absent runtime contributes no segment; it does not change another runtime's color or position.

Estimated runtime spend retains its runtime color and adds the shared estimate hatch from the professional chart system. Text and inspection details include `est`, `partial`, or `unavailable` as applicable. Color alone never communicates evidence quality.

## Page structure

### 1. Shared header and range control

Rename the visible top-level destination and page title from **Daily** to **Spend**. Keep the existing shared spectrum header geometry and page atmosphere.

The trailing control contains:

- `Today`
- `7 days`
- `30 days`
- `Custom range`

Today, 7 days, and 30 days are local-calendar windows ending today and include days with zero recorded spend. Custom range opens compact `From` and `To` date inputs, inclusive at both ends. Inputs are bounded by the earliest available local spend day and today; `From` cannot follow `To`.

Persist the last valid range choice in browser-local storage. An invalid or stale saved custom range falls back to Last 7 days without modifying server settings.

### 2. Period decision readouts

The first content row contains four readouts calculated from the selected range:

- **Selected-period spend:** sum of cost-covered daily spend, with API-equivalent estimate and partial-coverage wording.
- **Daily average:** selected-period spend divided by the number of calendar days in the range, including zero-spend days. Its note compares with the immediately preceding equal-length calendar period when evidence exists.
- **Top runtime:** the runtime with the greatest cost-covered spend in the range, plus value and share.
- **Highest day:** the calendar day with the greatest cost-covered spend, plus its value.

Do not display a measured zero when matching activity exists but pricing evidence is unavailable. In that case, show unavailable or a lower-bound/partial label using the existing evidence rules.

### 3. Stacked daily spend chart

The primary chart is a stacked daily column chart:

- Each column represents one local calendar day in the selected range.
- Full column height represents total cost-covered daily spend.
- Runtime-colored segments represent each runtime's contribution to that total.
- A zero-spend calendar day retains its x-axis position and baseline but has no fabricated colored segment.
- Ordinary bars have no glow. The focused or selected day receives the existing blue/cyan focus band or outline without changing geometry.
- The plot uses the shared professional chart surface, grid, axis, selection, tooltip, empty-state, and estimate-pattern primitives.

The legend sits above the plot and uses the fixed runtime order. It includes only runtimes present anywhere in the selected range, plus an estimate-pattern key when at least one visible segment is estimated. Legend items are labels, not filters, in this release.

Pointer hover, keyboard focus, or touch selection opens the shared inspection card with:

- full local date;
- daily total;
- one row per active runtime with cost and share;
- explicit estimate, partial, and unavailable labels.

The accessible name for each daily column repeats the total and runtime breakdown so the visual does not rely on color. Live polling updates the inspected values in place when the day still exists and does not replace or move the hovered/focused target.

### 4. Period runtime split

Below the chart, retain a labeled **Platform split** evidence panel. It aggregates the selected range and shows one row or compact tile per present runtime with the same categorical swatch, total spend, share of cost-covered spend, and estimate label.

This panel is the exact textual counterpart to the stacked chart and remains readable on narrow screens. It replaces the current one-day runtime split. The current one-day highest-cost-log and wait-analysis panels are removed from this surface; session investigation remains available in Sessions and Logs.

## Navigation and compatibility

- Make `#spend` the canonical dashboard route.
- Preserve `#daily` as a legacy alias that replaces itself with `#spend`, so bookmarks and local agent links continue to work.
- Rename the navigation item, command-palette entry, Learn destination, README surface label, and relevant accessible labels to Spend.
- Preserve the existing backend `daily` payload for current-session summaries, MCP aggregate tools, and compatibility consumers.
- Agent-generated dashboard links should move to the canonical Spend route while the legacy alias remains supported.

Internal `d-*` element IDs and helper names may remain during this focused change when renaming them adds no user value. User-visible strings, route state, and accessibility labels must consistently say Spend.

## Data architecture

Use the current trace-derived day-cost and provider attribution as the single source of truth. Do not introduce a second pricing or cost calculation.

1. Extend `daily_summaries` so `limit=None` returns all locally available daily rows in reverse chronological order.
2. In cross-session aggregation, calculate the authoritative daily rows once.
3. Continue publishing the existing bounded `daily` array for compatibility.
4. Add a compact `spend.days` projection for all available days. Each projected day contains only the fields needed by Spend: date, cost, runtime costs, availability, coverage, provenance, and estimate basis.
5. The browser builds exact calendar windows, inserts zero-activity dates for chart continuity, and sums the compact daily rows for the selected and preceding periods.

This keeps detailed per-day session lists out of the long-range payload and prevents payload growth from scaling with up to eight session summaries per historical day.

The frontend may fall back to the existing `daily` array if `spend.days` is absent, allowing a dashboard file to remain usable briefly during source/runtime transition. That fallback exposes only the history present in the legacy payload and must not claim a wider custom range.

## Evidence and calculation rules

- All windows use local calendar dates, matching existing daily aggregation.
- Last 7 and Last 30 include today and the preceding 6 or 29 calendar days.
- The comparison period is the immediately preceding equal number of calendar days.
- Missing calendar days with no observed activity count as zero spend.
- Observed activity without pricing evidence is unavailable, not zero.
- A range is partial when any observed activity in it lacks cost coverage.
- Runtime shares use cost-covered spend as the denominator and carry partial wording when coverage is incomplete.
- Cursor and any other locally estimated runtime retain existing estimate provenance.
- Ties for top runtime or highest day resolve deterministically by stable runtime order and then ascending date.
- Spend remains an API-equivalent estimate and is never labeled as a subscription charge or invoice.

## Responsive behavior

- Wide desktop: header controls stay at the trailing edge, four decision readouts share one row, and the chart fills its card.
- Tablet: header controls wrap, readouts become a two-column grid, and the legend wraps above the plot.
- Phone: the header stacks, readouts become one column, the Platform split becomes a vertical list, and the chart alone scrolls horizontally.
- Daily bars retain a minimum useful hit width. Long custom ranges widen the inner plot instead of compressing marks and labels below legibility.
- X-axis labels thin predictably while preserving the first and last visible dates. Every day remains inspectable even when its label is omitted.
- No page-level horizontal overflow is allowed.

## Empty, unavailable, and invalid states

- No local history: show a normal in-frame empty state explaining that Spend will appear after a supported runtime records activity.
- Valid range with no activity: show `$0.00`, zero-spend readouts, and empty calendar baselines rather than an error.
- Activity with no cost evidence: show unavailable or partial values and do not draw cost segments as zero measurements.
- Partially covered range: render covered segments, add the established partial/lower-bound wording, and expose coverage in inspection details.
- Invalid custom dates: keep the last valid range rendered, mark the relevant fields invalid, and announce one concise correction.
- Saved range outside current available history: clamp the custom input display to valid bounds or fall back to Last 7 days; never silently invent history.
- New or unknown runtime: use the neutral fallback swatch, display its factual runtime name, and keep it after the known runtimes in the stack and legend.

## Accessibility and interaction

- Range controls use buttons with `aria-pressed`; custom date inputs have visible labels.
- Each bar is keyboard inspectable through a roving focus model rather than placing an unbounded number of bars in the normal tab order.
- The chart has a dynamic accessible label describing the selected range, spend total, and partial/estimate state.
- The inspection card content is identical for pointer, keyboard, and touch.
- Focus outlines use the Token Meter blue/cyan interface treatment, never a runtime color.
- Runtime name, value, and evidence wording accompany every swatch.
- Motion respects `prefers-reduced-motion`; selection and refresh never animate position.

## Testing and verification

### Domain and payload tests

- `daily_summaries(limit=None)` returns complete ordered history while the legacy `daily` field remains bounded and unchanged.
- `spend.days` is compact, chronological-data-safe, free of titles/paths/session IDs, and preserves provider costs plus coverage/provenance.
- Daily and Spend totals reconcile for overlapping days.
- Unknown runtimes retain their name and neutral fallback behavior.

### Frontend contract tests

- Navigation and visible headings say Spend; `#daily` redirects to `#spend`.
- Today, 7-day, 30-day, and custom inclusive windows use local calendar days.
- Comparison periods are equal length and immediately preceding.
- Range totals, averages, top runtime, highest day, and platform split reconcile with the stacked bars.
- Runtime color mapping and legend order are fixed.
- Estimated segments use the shared hatch and textual `est` label.
- Partial and unavailable activity never renders as a measured zero.
- Invalid custom ranges retain the last valid render and expose an accessible error.
- Legacy payload fallback cannot advertise dates it does not contain.

### Visual and interaction verification

- Exercise full, sparse, zero-spend, estimated, partial, unavailable, unknown-runtime, and long custom-range states.
- Verify bar inspection with pointer, keyboard, and touch.
- Verify stationary selection and inspection through repeated live polling.
- Check 1440x900, 1024x820, and 390x844 for collision, clipping, legend wrapping, chart-only scrolling, and page-level overflow.
- Confirm blue/cyan remains visually dominant outside the data plot and categorical colors remain confined to runtime evidence.

### Repository and installed runtime

- Run the full Python test suite, embedded JavaScript parse checks, shell checks, native companion checks, and `git diff --check` used by the repository.
- Install the latest local source with `./scripts/install`.
- Verify `/health`, dashboard readiness, listener and service ownership, changed-file parity between source and staged runtime, and the canonical/legacy routes in the installed dashboard.
- Perform one batched desktop/mobile visual QA pass, fix all discovered defects together, and perform at most one confirmation pass.

## Explicit non-goals

- No subscription-price modeling or provider invoice reconciliation.
- No renewal-date or billing-cycle configuration.
- No spend forecast or budget projection on this page.
- No project, model, or session filter in the first Spend release.
- No interactive legend filtering.
- No new chart library or hosted dependency.
- No change to pricing tables, monthly-budget calculations, provider limits, session budgets, or notification policy.
- No page-wide rebrand or replacement of Token Meter's blue/cyan interaction language.

## Delivery boundary

The implementation is complete only when the Daily destination has become Spend in the source and installed runtime, all four range modes work, stacked runtime attribution reconciles with textual totals, legacy Daily links remain safe, evidence caveats remain accurate, and responsive live-use checks pass.
