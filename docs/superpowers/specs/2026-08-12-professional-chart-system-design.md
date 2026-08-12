# Professional Chart System Design

Date: 2026-08-12

Status: Approved visual direction; awaiting implementation-plan approval

Primary surface: `page.html`

## Objective

Make every Token Meter chart feel like part of one professional analytical instrument without changing the evidence, calculations, routes, filters, or backend payloads.

The result should be quieter and easier to inspect. It should retain Token Meter's dark Spectrum Instrument Field identity, cool cyan/blue spectrum, factual provider colors, semantic warning colors, dense information, local-only behavior, and explicit estimate or unavailable labels.

## Success criteria

- A user can distinguish the primary measure, comparison measures, thresholds, and event annotations at a glance.
- Axes, gridlines, labels, legends, empty states, selected states, and tooltips use a consistent visual grammar.
- Dense multi-series charts remain readable without deleting series or hover evidence.
- Pointer, keyboard, and touch inspection expose the same factual values where the surface is interactive.
- Live polling does not replace, move, or blink the chart element or tooltip being inspected.
- Desktop, tablet, and phone layouts preserve readable type and mark geometry. Narrow screens scroll the plot when necessary instead of compressing it beyond legibility.
- Existing token, cost, context, wait, model, language-signal, budget, and capability semantics remain unchanged.

## Chosen direction: Precision Instrument

The chart system uses restrained near-black plot fields, crisp tabular typography, sparse gridlines, and a disciplined hierarchy of color. Cyan identifies the active or primary measure. Supporting series use existing sky, green, amber, violet, provider, or semantic colors at lower visual weight. Glow is reserved for selected points, live context pressure, and warning thresholds.

This direction was selected over a denser Data Lab treatment and a more decorative Neon Spectrum treatment. Data Lab would amplify the dashboard's existing visual competition; Neon Spectrum would weaken the credibility of analytical evidence.

## Scope

### Analytical charts

- Session Execution Profile: Tokens, Cost, Context, Tools, Steps, and Wait, including Linear and Cumulative behavior.
- Models: Model trends for Output pace, Output wait, Avg input, and Avg output.
- Models: User language signals for Positive and Friction, grouped by days or weeks.
- Daily: Spend and Wait trends.
- Settings: Monthly spend history and budget target.

### Compact visualizations

- Session token-semantic split and cost split.
- Current-session context sparklines.
- Session and daily runtime/provider share tracks.
- Daily highest-cost-session tracks and tool-quality split.
- Model timing coverage rails.
- Monthly budget progress, threshold markers, and runtime allocation tracks.
- Tools prompt-load evidence split.
- Other existing progress or share rails that use the same visual role.

Tables, KPI cards, page headers, navigation, and backend data are outside scope except for spacing directly needed to align a chart header, legend, tooltip, or empty state.

## Shared visual system

### Plot surfaces

- Use one near-black plot surface with a subtle inset border and no prominent decorative gradient.
- Use four major horizontal divisions at most. Gridlines are low-contrast solid lines; threshold lines may be dashed.
- Give labels a minimum readable contrast above the current faint treatment. Axis values and quantitative tooltip values use tabular numerals.
- Use a consistent plot corner radius, border, internal padding, and chart-header gap.
- Avoid shadows and glows on ordinary bars, lines, and points. A selected or focused datum may use one restrained halo.

### Color hierarchy

- Primary or active measure: existing accent/cyan at full strength.
- Supporting measures: existing factual colors at reduced opacity or thinner stroke.
- Neutral volume or denominator series: desaturated blue-gray.
- Estimated values: retain the factual series color and add a consistent dash or hatch pattern; never rely on opacity alone.
- Watch, high, and over-budget states: existing amber and red semantic colors.
- Provider colors remain stable where provider identity is the encoded dimension.
- The same color must not represent different concepts within one chart.

### Marks

- Primary line: 2.25-2.5 px, rounded joins, no permanent glow.
- Supporting line: 1.5-1.75 px with lower opacity.
- Point markers: hidden at rest for dense series; shown for the selected datum, isolated points, or short series.
- Bars: flat or subtly graded fills with a small top radius. No routine outer glow.
- Selection: brighter fill/stroke, a stable crosshair or focus band, and unchanged geometry.
- Thresholds: thin dashed line with an inline label positioned inside the plot.

### Axes and labels

- Use the same font size, color, tick density, and baseline treatment across SVG charts.
- Use three to five meaningful ticks generated from the actual domain. Keep zero when it is semantically useful.
- Unit labels sit near their corresponding axis rather than floating ambiguously above the plot.
- Dual-axis charts clearly separate the left and right units through label position and restrained color association.
- X-axis labels are thinned predictably, always preserving the first useful period and latest period when space allows.
- Mobile charts keep readable labels through a minimum plot width and horizontal scrolling, not SVG squashing.

### Legends

- Place data-series items before thresholds and event annotations.
- Separate long legends into visually labeled groups when both series and events are present.
- Use line, bar, dash, hatch, or marker samples that match the actual encoding instead of using an identical square for every item.
- Keep full explanatory fieldtips and accessible labels.
- Model names remain absent from the persistent Model trends legend; names and all available metrics remain in the inspection card.

### Inspection card

Create one shared tooltip treatment for analytical charts:

- Stable dark surface, subtle accent border, compact header, and aligned label/value rows.
- Header shows the execution, request, day, week, or month being inspected.
- Units and `est`, `partial`, or `unavailable` labels remain attached to their values.
- Rows with no relevant activity may be omitted only when the underlying metrics prove zero usage; unavailable evidence is shown as unavailable rather than silently omitted.
- Position within the chart container without covering the selected point when another side is available.
- Keep the card mounted and stationary while it or the chart datum is hovered or focused. Live refresh updates values in place.
- Keyboard focus and touch selection use the same card content as pointer hover. Escape or moving focus outside the chart closes a pinned touch/keyboard card.

### Empty, unavailable, and partial states

- Render a centered title and one short explanatory line inside the normal plot frame.
- Distinguish `no activity in this window` from `evidence unavailable`.
- Preserve lower-bound, local-estimate, partial-coverage, and unavailable wording already supplied by the product.
- Do not draw a zero line as if it were measured data when the source evidence is unavailable.

## Surface-specific design

### Session Execution Profile

- Preserve all modes, current calculations, cumulative token and cost transforms, saved control state, and existing event semantics.
- Tokens: make input total the primary line. Cache read, cache write, uncached input, and output remain available as supporting lines or areas with lower weight. Cumulative areas use restrained translucent fills and visible line boundaries.
- Cost: use the cost color as the single primary series. Estimated Cursor cost retains an explicit estimate encoding.
- Context: keep the context line dominant; retain 70% watch and 85% high thresholds as labeled dashed rules.
- Tools, Steps, and Wait: use neutral bars plus one cyan line only where the line adds a second readable encoding. Avoid drawing the same measure as equally loud bars and a line.
- Move user-message, notable-tool, notable-reasoning, and coordination annotations into a quiet event lane aligned to executions. They remain inspectable and keep their existing meanings.
- Group the legend into `Series` and `Events`; preserve all fieldtips.
- Increase the plot's effective desktop height modestly. On narrow screens, keep a legible minimum plot width and allow horizontal scrolling within the chart module.

### Model trends

- Output-volume bars become a neutral blue-gray context layer, so metric lines carry the comparison.
- When one or two runtimes are selected, their lines use their stable model colors at full weight.
- When many or all runtimes are selected, reduce default line opacity and emphasize the runtime under inspection. Do not remove any selected runtime from the chart or inspection card.
- Remove permanent point markers from continuous multi-point segments. Show points for the inspected day, isolated values, and very short segments.
- Preserve dashed treatment for locally estimated series.
- Keep both output and selected-metric axes, but color their unit labels to match the corresponding encoding and reduce grid competition.
- Keep the current active-row filtering rule: a runtime is omitted from a day's inspection card only when input, output, and executions are all zero.
- Preserve output, executions, average input/output, pace, wait, timing coverage, runtime names, and estimate status in hover evidence whenever those values are supplied.

### Daily Spend and Wait

- Use clean columns with a consistent baseline and selected-day outline.
- Keep estimate hatching, using one shared hatch definition across spend charts.
- Add a subtle average reference rule using the already calculated window average. It is informational, not a new metric.
- Hover and keyboard focus reveal the value label without changing bar size or page geometry.
- Retain each day as a button and preserve current day navigation behavior.

### User language signals

- User-turn volume uses neutral columns.
- Matched utterances use a narrower semantic-color overlay within the same period.
- Match rate is the sole dominant line; Positive uses the existing good color and Friction uses the existing warning color.
- Clarify the left count axis and right percentage axis through positioned unit labels and matching legend samples.
- Retain Days/Weeks, model, history, Positive/Friction, term counts, and chat filtering behavior.
- Keep the existing phrase-match-only disclaimer visually adjacent to the chart context.

### Monthly spend and budget

- Use the same column geometry and estimate treatment as Daily Spend.
- Current month receives a strong outline and value label; historical months stay quieter.
- The configured budget is a labeled dashed target rule spanning the plot.
- The monthly budget progress rail uses thin threshold ticks with semantic color only after a threshold is crossed.
- Runtime allocation tracks share one rail geometry while retaining stable runtime colors and explicit over-budget red.
- Preserve partial-cost-coverage and local-estimate notices.

### Compact rails, splits, and sparklines

- Define shared rail height, background, border, radius, and selected/semantic treatments by size: micro, compact, and standard.
- Split bars use crisp segment boundaries and labels outside the bar when a segment is too small for readable internal text.
- Provider or category identity uses stable factual colors; progress toward a target uses cyan until a semantic threshold is crossed.
- Current-session context sparklines retain the rising cyan horizon concept. Use quiet historical bars, a brighter latest bar, and amber/red threshold states at 70%/85%.
- Compact visualizations remain secondary to their numeric labels; no animation beyond the existing short value transition.

## Implementation architecture

Keep the dependency-free, single-file frontend. Do not introduce a chart library.

1. Extend existing chart tokens in `:root` for plot surface, grid, axis, label, neutral volume, primary/supporting opacity, selection, and estimate pattern.
2. Add small SVG helpers for repeated grid/tick markup, empty states, crosshair selection, and tooltip placement. Helpers return markup or update existing DOM; they do not own data transformation.
3. Keep domain calculations inside the current renderers (`drawIO`, `drawStepsChart`, `drawWaitChart`, `drawModelTrend`, and `drawFrustrationTrend`). The shared layer receives already calculated values and formats.
4. Add shared CSS primitives for plot containers, legends, inspection cards, rails, split bars, estimate hatching, and narrow-screen scrolling. Existing surface classes compose these primitives so unrelated layout does not change.
5. Update CSS-rendered Daily and Budget charts to use the same tokens and mark states. Do not convert them to SVG solely for consistency.
6. Preserve current localStorage keys, URLs, controls, payload fields, and event handlers.

## Data flow

1. Existing endpoints and state objects supply session, model, daily, language-signal, capability, and budget data.
2. Existing render functions calculate domains, aggregates, cumulative transforms, estimates, and availability.
3. Shared presentation helpers generate ticks, frames, selection affordances, tooltip placement, and empty states.
4. Each chart renderer emits its factual marks and binds pointer, keyboard, and touch inspection.
5. Live refresh updates the same chart container and active inspection state when the selected datum still exists; otherwise it closes the inspection card cleanly.

No visualization may infer a missing denominator, price, timing sample, context window, sentiment, or task outcome.

## Accessibility and interaction

- Keep or add `role="img"` and a useful dynamic `aria-label` for analytical SVGs.
- Provide focusable period/execution hit targets or an equivalent roving focus mechanism without adding hundreds of items to the normal tab order.
- Use `aria-pressed` for chart mode and scale controls.
- Ensure focus rings remain visible against the plot surface.
- Do not encode status using color alone: labels, dash/hatch patterns, or marker shapes remain present.
- Respect reduced-motion preferences. Selection and data refresh must not animate position.
- Touch targets for chart controls remain at least 36 px in the existing dense dashboard.

## Responsive behavior

- Wide desktop: charts fill their cards and preserve the existing dashboard grid.
- Tablet: controls wrap above charts; legends wrap below; inspection cards stay within the plot card.
- Phone: plot headers stack, controls horizontally scroll when necessary, analytical plots use a minimum content width, and only the plot region scrolls horizontally.
- Compact rails and sparklines continue to fit their cards without horizontal scrolling.
- Tooltip text wraps, remains reachable, and never forces page-width overflow.

## Error handling and defensive behavior

- Non-finite values are excluded from domains and shown as unavailable in inspection content.
- Empty arrays render an in-frame empty state and hide stale inspection cards.
- Invalid saved modes/scales keep their existing fallback behavior.
- A live refresh that removes the active datum closes the inspection card; a refresh that retains it updates the card without moving focus.
- Extremely large or tiny domains use the existing compact formatters and bounded tick counts.
- Long model, project, and message text truncates in headers but wraps or exposes its full value in the inspection card where already allowed.

## Testing and verification

### Automated contracts

- Preserve existing tests for chart controls, scale persistence, cumulative tokens/cost, availability, estimate labels, model hover filtering, and semantic thresholds.
- Add focused tests for shared tokens/primitives, legend grouping, estimate patterns, empty/unavailable states, average and budget reference lines, and accessible chart labels.
- Add direct JavaScript checks for tick generation, domain safety, active-series emphasis, and retained tooltip selection across compatible refreshes.

### Visual and interaction verification

- Check Sessions in all six modes, including both Linear and Cumulative where supported.
- Check Models with all runtimes, one runtime, two runtimes, local estimates, missing timing, and no data.
- Check Positive/Friction, Days/Weeks, and sparse or empty signal histories.
- Check Daily Spend/Wait with selected, estimated, empty, and long history states.
- Check configured/unconfigured, partial-coverage, threshold-crossed, and over-budget states.
- Check compact bars with zero, tiny, dominant, and unavailable segments.
- Verify pointer, keyboard, and touch inspection at 1440x900, 1024x820, and 390x844.
- Verify no console errors, visible clipping, page-width overflow, layout shift, hover blinking, or focus loss.

### Repository and installed runtime

- Run the full Python test suite, embedded JavaScript parse check, shell checks, Swift compile/smoke checks, and `git diff --check` used by the repository.
- Install with `./scripts/install`.
- Verify `/health`, `/menubar`, LaunchAgent state, listener ownership, source/runtime parity for changed files, and live dashboard behavior from the installed runtime.
- Perform one batched visual QA round across desktop and phone, fix all observed defects together, then perform at most one confirmation round.

## Non-goals

- No new charting dependency.
- No new metrics, aggregation rules, conclusions, or backend endpoints.
- No removal of series, estimates, thresholds, event evidence, or detailed hover information.
- No page-wide rebrand, navigation change, card redesign, or unrelated refactor.
- No animated chart entrance, continuous glow, 3D treatment, or decorative particle effects.

## Delivery boundary

Implementation is complete only when all chart families use the shared system, existing behavior remains intact, automated verification passes, and the staged installed dashboard has been checked at representative desktop and phone widths.
