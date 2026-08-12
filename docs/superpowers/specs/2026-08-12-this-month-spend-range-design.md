# This Month Spend Range

## Goal

Add a first-class **This month** control to the Spend page so a user can inspect month-to-date spend without entering custom dates. The control must preserve the existing rolling ranges, dynamic chart scale, platform breakdown, and full range-log list.

## Range behavior

- Add **This month** between **30 days** and **Custom range**.
- The range begins on the first day of the current local calendar month and ends on the current local calendar day, inclusive.
- Derive both dates in the browser's local timezone, matching the existing Today, 7 days, and 30 days behavior.
- The range's `dayCount` is the number of inclusive local calendar days from the first through today.
- Persist the selection using the existing Spend range preference. Reloading the page with **This month** selected restores it.
- A saved month selection remains valid across a month boundary and resolves against the new current month rather than retaining stale dates.
- The preceding-period comparison continues to use the existing rule: compare with the immediately preceding range of the same number of calendar days.

## Interface

- Keep all five range controls visible; do not replace them with a dropdown or remove **30 days**.
- Desktop order: **Today**, **7 days**, **30 days**, **This month**, **Custom range**.
- At widths up to 520 px, retain the two-column control grid, place **This month** beside **30 days**, and let **Custom range** span both columns on the final row.
- Preserve the current active-state and `aria-pressed` behavior. The label is exactly **This month**.
- Selecting **This month** hides the custom date inputs, just like the other preset ranges.

## Data flow

The existing `spendRangeWindow` range resolver owns the new `month` choice. Its returned start and end flow unchanged through calendar-row generation, KPI calculations, dynamic y-axis scaling, platform totals, chart rendering, and `/spend/logs` range loading. No backend endpoint or stored data shape changes.

## Edge cases

- On the first day of a month, **This month** resolves to one day.
- January resolves correctly without leaking into the previous year.
- Leap years and variable month lengths are handled by constructing the first local day directly rather than subtracting a fixed number of days.
- Empty or unavailable spend evidence uses the existing empty and partial-coverage states.

## Verification

- Unit-test exact month windows for a mid-month date, the first day of a month, and January.
- Verify saved `month` is accepted by the existing range preference.
- Verify all five controls are available and **Custom range** spans both mobile columns.
- Run the full test suite, JavaScript parse check, design detector, and `git diff --check`.
- Install the local runtime and verify desktop and 390 px mobile layouts, active state, month-to-date dates, logs, y-axis, and absence of horizontal overflow or console errors.

## Out of scope

- Subscription renewal periods
- Forecasting month-end spend
- Changing the default Spend range
- Backend aggregation changes
