# Spend Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the one-day Daily dashboard with a range-based Spend page whose stacked daily bars show stable Splunk-colored runtime contributions.

**Architecture:** Keep `daily_summaries` as the only authoritative day-cost aggregation, calculate complete history once, retain the bounded legacy `daily` payload, and publish a privacy-safe compact `spend.days` projection. Keep the dependency-free `page.html` frontend and add pure calendar/range reducers plus a DOM-stable stacked-bar renderer; make `#spend` canonical while redirecting `#daily`.

**Tech Stack:** Python 3.8+ standard library, dependency-free HTML/CSS/JavaScript, `unittest`, Node.js syntax checks, existing install and native-companion scripts.

## Global Constraints

- Support Today, Last 7 days, Last 30 days, and an inclusive custom date range.
- Defer subscription renewal dates, billing-cycle budgets, and spend forecasting.
- Keep blue/cyan for navigation, active controls, focus, and selection; use the approved Splunk chart colors only for spend evidence.
- Preserve unavailable, partial, lower-bound, and local-estimate semantics; unknown is never zero.
- Preserve the bounded `daily` payload and `#daily` links for compatibility.
- Keep the frontend dependency-free and local-only; add no chart library or hosted asset.
- Preserve hovered and focused chart DOM during one-second live polling.
- Do not modify pricing tables, monthly budgets, provider limits, session budgets, or alert policy.
- Validate source and staged installed-runtime parity before completion.

---

## File map

- Modify `token_meter/domain/aggregates.py`: allow unbounded daily aggregation and create the compact Spend projection.
- Modify `token_meter/app.py`: expose the projection through the compatibility surface, calculate daily history once, publish `spend.days`, and update the agent dashboard link.
- Modify `page.html`: rename the route and surface, add range controls/readouts/stacked chart/platform split, and implement range aggregation and stable inspection.
- Modify `tests/test_meter.py`: cover payload privacy/reconciliation, route compatibility, visible structure, fixed colors, range behavior contracts, and removal of obsolete Daily/Wait assertions.
- Modify `README.md`: describe Spend instead of Daily while retaining the existing screenshot path until a new product screenshot is intentionally captured.
- Create no production file beyond these existing ownership boundaries.

---

### Task 1: Publish complete compact Spend history without changing legacy Daily

**Files:**
- Modify: `token_meter/domain/aggregates.py:407-619`
- Modify: `token_meter/app.py:69-79,5236-5245,5687-5748`
- Test: `tests/test_meter.py:5582-5612`

**Interfaces:**
- Consumes: finalized rows from `daily_summaries(session_rows, limit=None)`.
- Produces: `spend_projection(daily_rows) -> list[dict]`, where each row contains `day`, `cost`, `providers`, `coverage`, `provenance`, `usage_basis`, and `availability` only.
- Produces: `cross_session(sources=None)["spend"]["days"]` as a list of compact day rows while `cross_session(sources=None)["daily"]` remains the newest 30 recorded rows.

- [ ] **Step 1: Write failing domain and payload tests**

Add tests with explicit 31-day input and privacy assertions:

```python
def test_unbounded_daily_history_has_compact_spend_projection(self):
    sessions = [{
        "id": "one", "title": "Private title", "project": "/private/repo",
        "provider": "codex", "label": "Codex",
        "_day_cost": {f"2026-07-{day:02d}": float(day) for day in range(1, 32)},
    }]
    legacy = meter.daily_summaries(sessions)
    complete = meter.daily_summaries(sessions, limit=None)
    spend = meter.spend_projection(complete)
    self.assertEqual(len(legacy), 30)
    self.assertEqual(len(complete), 31)
    self.assertEqual(spend[0]["day"], "2026-07-31")
    self.assertEqual(spend[0]["providers"][0]["provider"], "codex")
    self.assertEqual(spend[0]["providers"][0]["cost"], 31.0)
    self.assertEqual(
        set(spend[0]),
        {"day", "cost", "providers", "coverage", "provenance",
         "usage_basis", "availability"},
    )
    serialized = json.dumps(spend)
    self.assertNotIn("Private title", serialized)
    self.assertNotIn("/private/repo", serialized)
    self.assertNotIn("top_sessions", serialized)
```

Add a cross-session empty-state contract after clearing `_xsess`:

```python
def test_cross_session_publishes_spend_history_and_keeps_daily(self):
    meter._xsess.update({"data": None, "at": 0, "sessions": []})
    result = meter.cross_session(sources=[])
    self.assertEqual(result["spend"], {"days": []})
    self.assertEqual(result["daily"], [])
```

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run:

```bash
python3 -m unittest \
  tests.test_meter.DailySummaryTests.test_unbounded_daily_history_has_compact_spend_projection \
  tests.test_meter.DailySummaryTests.test_cross_session_publishes_spend_history_and_keeps_daily -v
```

Expected: failures because `limit=None` is unsupported, `spend_projection` is undefined, and `spend` is absent.

- [ ] **Step 3: Implement unbounded aggregation and the compact projection**

In `daily_summaries`, replace unconditional slicing with the same bounded-key pattern already used by `monthly_summaries`:

```python
keys = sorted((value for value in days if value), reverse=True)
if limit is not None and limit > 0:
    keys = keys[:limit]
```

Keep the existing result-building loop body unchanged and change its iterable from the inline sorted slice to `keys`.

Add the projection beside `daily_summaries`:

```python
def spend_projection(daily_rows):
    """Project aggregate day-cost evidence without session or project identity."""
    return [{
        "day": row.get("day") or "",
        "cost": float(row.get("cost") or 0),
        "providers": [{
            "provider": provider.get("provider") or "unknown",
            "cost": float(provider.get("cost") or 0),
            "coverage": copy.deepcopy(provider.get("coverage") or {}),
            "provenance": copy.deepcopy(provider.get("provenance") or {}),
            "usage_basis": provider.get("usage_basis") or "unavailable",
            "availability": copy.deepcopy(provider.get("availability") or {}),
        } for provider in (row.get("providers") or [])],
        "coverage": copy.deepcopy(row.get("coverage") or {}),
        "provenance": copy.deepcopy(row.get("provenance") or {}),
        "usage_basis": row.get("usage_basis") or "unavailable",
        "availability": copy.deepcopy(row.get("availability") or {}),
    } for row in (daily_rows or [])]
```

Import `copy` in the domain module if it is not already present. Import the function into `token_meter/app.py` as `_domain_spend_projection`, expose the compatibility wrapper `spend_projection`, and update `cross_session`:

```python
daily_all = daily_summaries(internal_rows, limit=None)
daily = daily_all[:30]
spend = {"days": spend_projection(daily_all)}
```

Add both fields to the existing `data` mapping:

```python
"daily": daily,
"spend": spend,
```

- [ ] **Step 4: Run focused and aggregate regression tests**

Run:

```bash
python3 -m unittest tests.test_meter.DailySummaryTests -v
python3 -m unittest tests.domain.test_aggregates -v
```

Expected: all tests pass; the legacy default remains 30 rows and compact rows contain no private identities.

- [ ] **Step 5: Commit the backend contract**

```bash
git add token_meter/domain/aggregates.py token_meter/app.py tests/test_meter.py
git commit -m "feat: publish compact spend history"
```

---

### Task 2: Convert Daily navigation and markup into the Spend surface

**Files:**
- Modify: `page.html:140-185,251,397-420,829-864,876,1435,1580-1720`
- Modify: `tests/test_meter.py:1948-3320`

**Interfaces:**
- Consumes: the existing top-level `showTab`, `openTopLevelRoute`, `applyHashRoute`, command-palette, and Learn routing mechanisms.
- Produces: canonical `spend` route state rendered inside the incumbent `view-daily` DOM owner, with `daily` as a replace-state alias.
- Produces: DOM IDs `s-range`, `s-custom`, `s-from`, `s-to`, `s-total`, `s-average`, `s-top-runtime`, `s-highest-day`, `s-chart`, `s-chart-inner`, `s-chart-tip`, `s-legend`, and `s-platforms`.

- [ ] **Step 1: Replace obsolete layout expectations with failing Spend contracts**

Update `DashboardLayoutTests` so it requires:

```python
for marker in (
    'data-label=Spend aria-label=Spend',
    '<span class=tabLabel>Spend</span>',
    '<h1>Spend</h1>',
    'id=s-range', 'data-spend-range=today', 'data-spend-range=7',
    'data-spend-range=30', 'data-spend-range=custom',
    'id=s-from', 'id=s-to', 'id=s-total', 'id=s-average',
    'id=s-top-runtime', 'id=s-highest-day', 'id=s-chart',
    'id=s-chart-tip', 'id=s-legend', 'id=s-platforms',
):
    self.assertIn(marker, self.page)
self.assertNotIn('<h1>Daily brief</h1>', self.page)
self.assertNotIn('id=d-trend-mode', self.page)
self.assertNotIn('data-daily-trend=wait', self.page)
```

Require route compatibility:

```python
self.assertIn("if(h==='daily')setHashRoute('spend',{replace:true,apply:false})", self.page)
self.assertIn("if(h==='spend'||h==='daily')", self.page)
self.assertIn("openTopLevelRoute('spend')", self.page)
self.assertIn("{id:'spend',label:'Spend'", self.page)
```

Rename the wait-time contract test to cover Sessions, Logs, and Models only; remove Daily wait markers because Spend is cost-focused.

- [ ] **Step 2: Run the focused layout tests and confirm the red state**

Run:

```bash
python3 -m unittest \
  tests.test_meter.DashboardLayoutTests.test_sessions_all_daily_learn_and_settings_are_first_class_routes \
  tests.test_meter.DashboardLayoutTests.test_wait_time_is_first_class_across_current_logs_models_and_daily \
  tests.test_meter.DashboardLayoutTests.test_redundant_hover_tips_are_removed_from_dashboard_labels -v
```

Expected: failures on Spend copy, route, controls, and markup.

- [ ] **Step 3: Implement canonical routing and visible structure**

Keep stable internal `tab-daily` and `view-daily` IDs, but change visible copy and user route:

```html
<button class=tab id=tab-daily data-label=Spend aria-label=Spend aria-keyshortcuts="Alt+2" title="Spend · Shortcut: Option+2">
  <svg class=tabIcon viewBox="0 0 24 24" aria-hidden=true><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 9h16M8 13h3M8 17h7"/></svg>
  <span class=tabLabel>Spend</span><span class=tabShortcut aria-hidden=true>2</span>
</button>
```

Change the command item to `{id:'spend',label:'Spend',description:'Review spend across days and runtimes',section:'Navigate',route:'spend',directKey:'Digit2',glyph:'2'}`. Make the click handler open `spend`. In `applyHashRoute`, canonicalize `daily` to `spend` with replace state, then render the incumbent view for `spend`.

Replace the Daily header/body with:

```html
<div class=view id=view-daily>
  <div class="dailyHead spectrumPageHead">
    <div class=spectrumPageHeadCopy><h1>Spend</h1><p>Understand where estimated agent spend is going over time.</p></div>
    <div class=spendRangeControls>
      <div class=seg id=s-range aria-label="Spend range">
        <button data-spend-range=today>Today</button>
        <button data-spend-range=7>7 days</button>
        <button data-spend-range=30>30 days</button>
        <button data-spend-range=custom>Custom range</button>
      </div>
      <div class=spendCustom id=s-custom hidden>
        <label><span>From</span><input id=s-from type=date></label>
        <label><span>To</span><input id=s-to type=date></label>
        <span id=s-range-error role=status aria-live=polite></span>
      </div>
    </div>
  </div>
  <div class=spendKpis>
    <div class="card spendKpi"><div class=label>Selected-period spend</div><div class="v mono" id=s-total>--</div><div class=subline id=s-total-note></div></div>
    <div class="card spendKpi"><div class=label>Daily average</div><div class="v mono" id=s-average>--</div><div class=subline id=s-average-note></div></div>
    <div class="card spendKpi"><div class=label>Top platform</div><div class="v mono" id=s-top-runtime>--</div><div class=subline id=s-top-runtime-note></div></div>
    <div class="card spendKpi"><div class=label>Highest day</div><div class="v mono" id=s-highest-day>--</div><div class=subline id=s-highest-day-note></div></div>
  </div>
  <section class="card spendChartCard">
    <div class=spendChartHead><div><h2>Spend trend</h2><p>Bar height is total daily spend; color is platform contribution.</p></div><div class=spendLegend id=s-legend></div></div>
    <div class=spendChart id=s-chart aria-label="Spend trend by day and platform"><div class=spendChartInner id=s-chart-inner></div></div>
    <div class=spendChartTip id=s-chart-tip tabindex=0 hidden aria-live=polite></div>
  </section>
  <section class="card spendPlatformCard"><h2>Platform split</h2><div id=s-platforms></div></section>
</div>
```

Remove the old day picker, wait/spend mode toggle, day-at-a-glance card, highest-cost-log panel, and one-day runtime panel. Rename the Learn step to **Spend review** and route it to `spend`.

- [ ] **Step 4: Add responsive structural CSS using incumbent tokens**

Add Spend primitives beside the current Daily CSS. Use four desktop KPI columns, a neutral professional plot card, chart-only horizontal scrolling, and compact mobile stacking. Define the fixed categorical CSS variables exactly:

```css
#view-daily{--spend-claude:#f26722;--spend-codex:#04a4b0;--spend-cursor:#a974f7;--spend-opencode:#fa5762;--spend-kiro:#868ec2;--spend-unknown:#889099}
```

Blue/cyan remains on `.tab.on`, `.seg button.on`, focus, and `.spendDay.selected`; runtime colors appear only on `.spendSegment`, `.spendLegendSwatch`, and `.spendPlatformSwatch`.

- [ ] **Step 5: Run focused tests and parse the embedded JavaScript**

Run:

```bash
python3 -m unittest tests.test_meter.DashboardLayoutTests -v
python3 - <<'PY'
from pathlib import Path
import re, subprocess, tempfile
page = Path('page.html').read_text()
script = re.findall(r'<script>(.*?)</script>', page, re.S)[-1]
with tempfile.NamedTemporaryFile('w', suffix='.js') as handle:
    handle.write(script)
    handle.flush()
    subprocess.run(['node', '--check', handle.name], check=True)
PY
```

Expected: layout tests pass and Node reports no syntax error.

- [ ] **Step 6: Commit the Spend shell**

```bash
git add page.html tests/test_meter.py
git commit -m "feat: replace daily shell with spend"
```

---

### Task 3: Implement exact ranges, stacked bars, evidence states, and stable inspection

**Files:**
- Modify: `page.html:4000-4110,4680-4715,4870-4890`
- Modify: `tests/test_meter.py:1948-3320`

**Interfaces:**
- Consumes: `xs.spend.days`, falling back to `xs.daily` only when the compact payload is absent.
- Produces: `spendDateKeys(start, end) -> string[]`, `spendWindow(range, now) -> {start, end}`, `summarizeSpend(days, keys) -> summary`, and `renderSpend(xs)`.
- Persists: `tm_spend_range`, `tm_spend_from`, `tm_spend_to`, and `tm_spend_day` in browser-local storage.

- [ ] **Step 1: Add failing renderer and calculation contract tests**

Require exact helper and rendering markers:

```python
for marker in (
    "const SPEND_RANGES=['today','7','30','custom'];",
    "const SPEND_RUNTIME_ORDER=['claude','codex','cursor','opencode','kiro'];",
    "claude:'#f26722'", "codex:'#04a4b0'", "cursor:'#a974f7'",
    "opencode:'#fa5762'", "kiro:'#868ec2'", "unknown:'#889099'",
    'function spendDateKeys(start,end)',
    'function spendWindow(range,now=new Date())',
    'function summarizeSpend(days,keys)',
    'function renderSpend(xs)',
    "xs?.spend?.days||xs?.daily||[]",
    "interacting=chart.querySelector('.spendDay:hover,.spendDay:focus')",
    "if(!interacting)reconcileSpendBars",
    "aria-label=\"Spend trend by day and platform\"",
    "localStorage.setItem('tm_spend_range'",
):
    self.assertIn(marker, self.page)
```

Require the five stable platform names in legend order and textual evidence fields in the inspection card. Assert that `renderDaily(` and the old `tm_daily_*` storage keys are absent.

- [ ] **Step 2: Run the focused renderer test and confirm the red state**

Run:

```bash
python3 -m unittest tests.test_meter.DashboardLayoutTests.test_spend_range_and_stacked_runtime_chart_contract -v
```

Expected: failure because the Spend renderer does not exist.

- [ ] **Step 3: Implement pure local-calendar range helpers**

Add helpers that parse `YYYY-MM-DD` at local noon, advance with `setDate`, and format with `localDayKey` to avoid UTC rollover:

```javascript
function spendDateKeys(start,end){
 const keys=[],cursor=new Date(`${start}T12:00:00`),last=new Date(`${end}T12:00:00`);
 if(!Number.isFinite(cursor.getTime())||!Number.isFinite(last.getTime())||cursor>last)return keys;
 while(cursor<=last){keys.push(localDayKey(cursor));cursor.setDate(cursor.getDate()+1);}
 return keys;
}
function spendWindow(range,now=new Date()){
 const end=localDayKey(now),days=range==='today'?1:Number(range);
 const startDate=new Date(`${end}T12:00:00`);startDate.setDate(startDate.getDate()-Math.max(0,days-1));
 return {start:localDayKey(startDate),end};
}
```

Implement `summarizeSpend` with a date map, inserted zero-activity rows, cost-covered totals, runtime totals, partial/estimated flags, daily average over calendar days, deterministic top-runtime/highest-day ties, and an immediately preceding equal-length comparison window.

- [ ] **Step 4: Render KPIs, fixed-order legend, stacked segments, and platform totals**

Use one max daily total for bar heights and each runtime's share for segment height. Keep stack and legend order fixed. Use a hatch class when provider provenance is `local_estimate` or `mixed`. Render `--` for activity with no cost availability and append `partial`/`est` to covered values as required.

Build every bar as a button with a complete accessible label:

```html
<button class="spendDay" data-spend-day="2026-08-12"
 aria-label="Wed, Aug 12, 2026: $12.44 estimated; Claude $5.10, Codex $4.34, Cursor $3.00 estimated">
 <span class="spendBarValue mono">$12.44 est</span>
 <span class=spendBarTrack><span class=spendStack style="height:72%">
  <i class="spendSegment spendClaude" style="height:41%"></i>
  <i class="spendSegment spendCodex" style="height:35%"></i>
  <i class="spendSegment spendCursor estimated" style="height:24%"></i>
 </span></span>
 <span class=spendBarLabel>Aug 12</span>
</button>
```

The Platform split repeats runtime name, swatch, total, share, and estimate text. Do not make legend entries interactive.

- [ ] **Step 5: Preserve inspected DOM during polling**

Use delegated pointer/focus/click handlers on `s-chart-inner`. Before reconciling bars during `renderSpend`, detect `.spendDay:hover,.spendDay:focus`. When interacting, update KPIs, platform totals, and the mounted inspection card but leave the chart subtree untouched. When idle, reconcile bars by `data-spend-day` rather than replacing the entire chart node.

Keep the selected day in `tm_spend_day`, apply a blue/cyan outline without changing bar geometry, and close a stale inspection card only when its date falls outside the selected range.

- [ ] **Step 6: Wire presets, custom validation, routing refresh, and live state**

Preset buttons set `aria-pressed`, persist the range, and render immediately. Custom inputs keep the last valid render when empty, reversed, future-dated, or outside available history; they set `aria-invalid` and announce a concise error. Replace calls to `renderDaily` with `renderSpend` in route entry and live refresh.

Update `agent_usage` to use the existing bounded `daily` data but link to `panel="spend"`; change the action copy from “Review the Daily view” to “Review the Spend view.”

- [ ] **Step 7: Run focused tests and full frontend source checks**

Run:

```bash
python3 -m unittest tests.test_meter.DashboardLayoutTests -v
python3 -m unittest tests.test_meter.AgentDataContractTests -v
python3 -m unittest tests.test_meter.DailySummaryTests -v
git diff --check
```

Expected: all focused tests pass and no whitespace errors remain.

- [ ] **Step 8: Commit the working Spend interaction**

```bash
git add page.html token_meter/app.py tests/test_meter.py
git commit -m "feat: analyze spend by range and runtime"
```

---

### Task 4: Update documentation and prove source, responsive UI, and installed runtime

**Files:**
- Modify: `README.md:14-34,71-114`
- Modify: `tests/test_meter.py` only if validation exposes a genuine missing regression
- Modify: implementation files from Tasks 1-3 only for defects found in the bounded QA pass

**Interfaces:**
- Consumes: completed source implementation and existing `scripts/install` staged-runtime workflow.
- Produces: verified local installation serving the same changed files and the canonical Spend route.

- [ ] **Step 1: Update user-facing documentation**

Change the capability table and visual-tour heading from Daily to Spend. Describe Today/7/30/custom ranges, stacked runtime attribution, and API-equivalent estimates. Keep `images/daily.png` as the current asset path but update its alt text; do not fabricate a new screenshot.

- [ ] **Step 2: Run the full repository verification suite**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile meter.py token_meter/app.py token_meter/domain/aggregates.py
bash -n scripts/install scripts/start-token-meter scripts/update
git diff --check
```

Run the repository's current Swift/native smoke commands discovered from the existing Makefile or test scripts; do not invent a new build path.

Expected: every test and syntax check passes.

- [ ] **Step 3: Install the latest local source**

Run:

```bash
./scripts/install
```

Expected: per-user runtime staging completes, server and native companion restart, and readiness succeeds without `sudo`.

- [ ] **Step 4: Verify runtime ownership, health, and file parity**

Run read-only checks for:

```bash
curl -fsS http://127.0.0.1:8722/health
curl -fsS http://127.0.0.1:8722/state
curl -fsS http://127.0.0.1:8722/#spend
```

Inspect the active listener and LaunchAgent/service state using the repository's existing platform commands. Compare `page.html`, `token_meter/app.py`, and `token_meter/domain/aggregates.py` against the staged runtime files with `cmp`. Confirm the installed root contains Spend copy and the legacy `#daily` alias.

- [ ] **Step 5: Perform one bounded visual/interaction QA pass**

At 1440x900, 1024x820, and 390x844 verify:

- Today, 7 days, 30 days, and valid/invalid custom ranges;
- complete, estimated, partial, zero-spend, and empty states available from local data or a controlled browser fixture;
- fixed legend/stack colors and textual Platform split reconciliation;
- pointer, keyboard, and touch-equivalent inspection;
- stable hover/focus through repeated polling;
- chart-only horizontal scrolling, no page overflow, no console errors, and no clipped controls or inspection card.

Fix every defect found in one batch, rerun affected automated tests, and perform at most one confirmation pass.

- [ ] **Step 6: Commit documentation and bounded QA fixes**

```bash
git add README.md page.html token_meter/app.py token_meter/domain/aggregates.py tests/test_meter.py
git commit -m "docs: document spend history analysis"
```

- [ ] **Step 7: Final verification snapshot**

Record the final commit, dashboard URL, service/autostart state, `/health` result, source/runtime parity result, responsive widths checked, and exact uninstall command for the installed platform. Confirm unrelated untracked benchmark and launch-blog files remain untouched.
