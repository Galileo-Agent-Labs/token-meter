# Documentation and Models UX Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create one canonical architecture/documentation tree, simplify the Session scale selector and Models hover card, add exact Today/Yesterday Models filters, and merge the verified result into local `main`.

**Architecture:** `README.md` becomes the only tracked root Markdown entry point and links to current documents under `specs/`. Browser-only Models projections share one exact-day predicate, while the existing Python matched-pace windows map gains the same Today/Yesterday keys. No runtime parser, pricing rule, or new endpoint is introduced.

**Tech Stack:** Python 3 standard library, embedded HTML/CSS/JavaScript in `page.html`, `unittest`, Swift/AppKit smoke validation, shell launchers, Git worktrees.

## Global Constraints

- Keep `meter.py` and `token_meter_mcp.py` on the Python standard library.
- Keep the dashboard local-only with no hosted assets, analytics, telemetry, raw traces, prompts, responses, reasoning, credentials, paths, or tool contents in public projections.
- Keep model identity scoped by runtime and keep unavailable evidence distinct from measured zero.
- Preserve existing routes, project/model preferences, 7/30/90/all ranges, Cumulative behavior, table structure, and chart metrics.
- `README.md` must be the only tracked root Markdown file; it must disclose that coding agents need to open `specs/AGENTS.md` explicitly.
- Use failing tests before production behavior changes.
- Do not push or open a pull request. Merge into local `main` only after validation.

---

### Task 1: Reorganize and Correct Documentation

**Files:**
- Create: `specs/ARCHITECTURE.md`
- Move: `AGENTS.md` to `specs/AGENTS.md`
- Move: `CLAUDE.md` to `specs/CLAUDE.md`
- Move: `CONTRIBUTING.md` to `specs/CONTRIBUTING.md`
- Move: `DESIGN.md` to `specs/DESIGN.md`
- Move: `SECURITY.md` to `specs/SECURITY.md`
- Move: `REQUIREMENTS.md` to `specs/history/REQUIREMENTS.md`
- Add when safe: ignored `PRODUCT.md`, `STATE.md`, `research.md`, and `implementation-log.md` under `specs/` or `specs/history/`
- Modify: `README.md`
- Modify: `runtime-manifest.txt`, tests, scripts, and specs only where they reference moved documents

**Interfaces:**
- Consumes: current package boundaries under `token_meter/`, `runtime-manifest.txt`, and validation commands from the former root `AGENTS.md`.
- Produces: `specs/ARCHITECTURE.md` as the current engineering authority and `specs/AGENTS.md` as the explicit coding-agent runbook.

- [x] **Step 1: Record the complete reference inventory**

Run:

    rg -n 'AGENTS\.md|CLAUDE\.md|CONTRIBUTING\.md|DESIGN\.md|PRODUCT\.md|REQUIREMENTS\.md|SECURITY\.md|STATE\.md|implementation-log\.md|research\.md|ARCHITECTURE\.md' . --glob '!*.pyc' --glob '!.git/**' --glob '!.worktrees/**'

Expected: a bounded list of root-document references to update after moves.

- [x] **Step 2: Move files with history and add the architecture authority**

Use `git mv` for tracked files. `specs/ARCHITECTURE.md` must explain this flow:

    local trace roots
      -> token_meter.runtimes registry and adapter
      -> normalized evidence/contracts
      -> token_meter.domain aggregation
      -> token_meter.app composition and caches
      -> explicit web/menubar/MCP projections
      -> browser, native companion, or local MCP caller

It must also document runtime, platform, pricing, and telemetry extension budgets; the privacy denylist; cache/revision ownership; packaging manifest; and validation layers.

- [x] **Step 3: Make README the root index and user guide**

Keep Quick Start, user workflows, requirements, privacy, update/uninstall, and troubleshooting. Remove the developer validation catalog, repository tree, and publishing checklist. Add direct links:

    - [Architecture](../ARCHITECTURE.md)
    - [Contributing](../CONTRIBUTING.md)
    - [Security](../SECURITY.md)
    - [Coding-agent instructions](../AGENTS.md)

Add one explicit sentence that root auto-discovery is intentionally unavailable and coding agents must read `specs/AGENTS.md` before editing.

- [x] **Step 4: Correct and classify the remaining documents**

Update current references in `specs/AGENTS.md`, `specs/CONTRIBUTING.md`, `specs/DESIGN.md`, `specs/PRODUCT.md`, and `specs/SECURITY.md`. Add a visible historical banner to files under `specs/history/`. Replace the volatile contents of historical `STATE.md` with a short dated index linking current architecture and Git history.

- [x] **Step 5: Verify the documentation tree**

Run a Python link/path checker over tracked Markdown. It must ignore HTTP links and anchors, resolve relative file targets, and print:

    markdown links ok
    root markdown: README.md

Run `git diff --check` and inspect `git diff --stat` before committing.

- [x] **Step 6: Commit the documentation milestone**

    git add README.md specs runtime-manifest.txt tests scripts
    git commit -m "docs: centralize architecture and repository guidance"

### Task 2: Remove Sqrt and Log Session Scale Choices

**Files:**
- Modify: `tests/test_meter.py` in `DashboardLayoutTests`
- Modify: `page.html` scale markup, `CHART_SCALES`, `scaledY`, and `ticks`

**Interfaces:**
- Consumes: `tm_chart_scale` local-storage preference.
- Produces: supported values `linear | cumulative`; any other stored value is normalized and persisted as `linear`.

- [x] **Step 1: Write the failing UI contract**

Add a test that slices the Session scale control and asserts `data-scale=linear` and `data-scale=cumulative` exist, `data-scale=sqrt` and `data-scale=log` do not, and the script contains:

    const CHART_SCALES=['linear','cumulative'];

The same test must assert the invalid-value branch writes the normalized value to `tm_chart_scale`.

- [x] **Step 2: Run the focused test and verify RED**

    python3 -m unittest tests.test_meter.DashboardLayoutTests.test_session_chart_only_offers_linear_and_cumulative_scales -v

Expected: FAIL because Sqrt/Log and the four-value allowlist still exist.

- [x] **Step 3: Implement the minimal scale change**

Remove the two buttons, narrow `CHART_SCALES`, and reduce `scaledY`/`ticks` to their linear behavior. Do not change cumulative token/cost construction or other chart modes.

- [x] **Step 4: Verify GREEN and JavaScript syntax**

    python3 -m unittest tests.test_meter.DashboardLayoutTests.test_session_chart_only_offers_linear_and_cumulative_scales -v
    node -e "const fs=require('fs');const h=fs.readFileSync('page.html','utf8');const m=h.match(/<script>([\\s\\S]*)<\\/script>/);new Function(m[1]);console.log('js ok')"

Expected: focused test passes and output ends with `js ok`.

- [x] **Step 5: Commit with the related Models behavior milestone**

    git add page.html tests/test_meter.py
    git commit -m "ui: simplify session chart scales"

### Task 3: Add Exact Models Today and Yesterday Windows

**Files:**
- Modify: `tests/test_meter.py` matched-pace tests and `DashboardLayoutTests`
- Modify: `token_meter/app.py:matched_pace_windows`
- Modify: `page.html` Models History options and date filtering helpers

**Interfaces:**
- Produces: `modelRangeWindow(range, now = new Date()) -> {daySet: Set<string> | null, days: string[] | null}` and `modelDayInRange(day, window) -> boolean` in dashboard JavaScript.
- Extends: `matched_pace.windows` with `today` and `yesterday`, preserving `7`, `30`, `90`, and `all`.

- [x] **Step 1: Write the failing Python exact-window test**

Build two model groups with 20 Today samples, 20 Yesterday samples, and one older sample each. Pass a deterministic local-noon `now_ts` to `matched_pace_windows`. Assert keys equal `today`, `yesterday`, `7`, `30`, `90`, `all`; Today and Yesterday comparisons each report 20 input samples; the 7-day comparison reports 40.

- [x] **Step 2: Write the failing dashboard exact-window contract**

Assert the History selector orders `today`, `yesterday`, `7`, `30`, `90`, `all`; invalid stored values normalize to `30`; and KPI merge, trend construction, and table aggregation call the shared `modelDayInRange` predicate.

- [x] **Step 3: Run both tests and verify RED**

    python3 -m unittest \
      tests.test_meter.ModelPerformanceTests.test_matched_pace_has_exact_today_and_yesterday_windows \
      tests.test_meter.DashboardLayoutTests.test_models_history_supports_exact_local_days -v

Expected: failures because exact windows/options/helpers do not exist.

- [x] **Step 4: Implement exact Python windows**

In `matched_pace_windows`, derive:

    today_key = today.isoformat()
    yesterday_key = (today - datetime.timedelta(days=1)).isoformat()

Filter exact windows by equality on sample `day`; retain the current lower-bound filtering for numeric windows and no filtering for all history. Reuse the existing per-pair distance cache.

- [x] **Step 5: Implement the shared browser window**

Validate `modelRange` against `['today','yesterday','7','30','90','all']`, default invalid values to `30`, and persist the fallback. Use one render-time window object in `renderModelStats` for `mergeModelDays`, `buildModelTrend`, and table daily rows. For exact windows, the trend axis contains exactly the selected day even when it has no rows.

- [x] **Step 6: Verify GREEN and regressions**

    python3 -m unittest \
      tests.test_meter.ModelPerformanceTests.test_matched_pace_has_exact_today_and_yesterday_windows \
      tests.test_meter.DashboardLayoutTests.test_models_history_supports_exact_local_days -v
    python3 -m unittest tests.test_meter.ModelPerformanceTests -q

Expected: focused tests and model-performance class pass.

- [x] **Step 7: Commit with the related dashboard behavior milestone**

    git add token_meter/app.py page.html tests/test_meter.py
    git commit -m "feat: filter models by today or yesterday"

### Task 4: Simplify the Model Trends Hover Card

**Files:**
- Modify: `tests/test_meter.py` existing model trend tooltip contract
- Modify: `page.html:drawModelTrend`

**Interfaces:**
- Consumes: the selected `MODEL_TREND_METRICS[modelTrendMetric]` entry.
- Produces: one tooltip row per active model with identity, total output, and selected metric; unavailable selected metrics render `--`.

- [x] **Step 1: Rewrite the tooltip contract and verify RED**

Slice `drawModelTrend` through `renderMatchedPace`. Require model identity, `<small>output</small>`, and a dynamic selected-metric label. Reject the fixed `input`, `executions`, `avg input`, `avg output`, `tok/s`, and `typical wait` grid inside that slice. Run:

    python3 -m unittest tests.test_meter.DashboardLayoutTests.test_model_trend_omits_legend_and_limits_hover_to_relevant_metrics -v

Expected: FAIL because the tooltip still renders seven fixed fields.

- [x] **Step 2: Implement the focused tooltip**

Build the selected metric label from `metric.note` and its value from
`metric.available(row) ? metric.format(metric.value(row)) : '--'`. Keep output
availability based on token evidence and leave all event/geometry handlers
unchanged.

- [x] **Step 3: Verify GREEN and adjacent Models contracts**

    python3 -m unittest \
      tests.test_meter.DashboardLayoutTests.test_model_trend_omits_legend_and_limits_hover_to_relevant_metrics \
      tests.test_meter.DashboardLayoutTests.test_model_stats_supports_multi_model_comparison \
      tests.test_meter.DashboardLayoutTests.test_model_stats_supports_project_scoped_average_io_trends -v

Expected: all three tests pass.

- [x] **Step 4: Commit with the related dashboard behavior milestone**

    git add page.html tests/test_meter.py
    git commit -m "ui: focus model trend tooltips"

### Task 5: Full Validation, Installation, and Local Main Merge

**Files:**
- Modify: `specs/documentation-models-ux/tasks.md`, this plan, and the ignored temporary execution plan while work remains
- Remove at completion: ignored root `plan.md`

**Interfaces:**
- Consumes: all prior task commits.
- Produces: verified local `main` containing the feature branch; no remote mutation.

- [ ] **Step 1: Run complete source validation**

    python3 -m unittest discover -s tests -v
    PYTHONPYCACHEPREFIX=/private/tmp/token-meter-pycache python3 -m py_compile meter.py token_meter_mcp.py $(find token_meter -type f -name '*.py' -print | LC_ALL=C sort)
    node -e "const fs=require('fs');const h=fs.readFileSync('page.html','utf8');const m=h.match(/<script>([\\s\\S]*)<\\/script>/);new Function(m[1]);console.log('js ok')"
    bash -n scripts/install scripts/install-linux scripts/install-launch-agent scripts/run-menubar scripts/run-token-meter-mcp scripts/start-token-meter scripts/uninstall-launch-agent scripts/install-systemd-user scripts/uninstall-systemd-user
    swiftc menubar/TokenMeterMenuBar.swift -o /private/tmp/token-meter-menubar
    TOKEN_METER_MENUBAR_SMOKE=1 /private/tmp/token-meter-menubar
    git diff --check

Expected: zero test failures, only documented host-native skips, and all syntax/compile/smoke checks exit zero.

- [ ] **Step 2: Install and validate the feature commit**

Run `./scripts/install`, then verify `/health`, `/menubar`, both LaunchAgents,
`127.0.0.1:8722` listener ownership, `INSTALLED_REVISION`, and:

    python3 -m token_meter.packaging parity "$PWD" "$HOME/Library/Application Support/Token Meter/runtime" "$PWD/runtime-manifest.txt"

Expected: healthy endpoints, no adapter failures, running services, loopback-only listener, exact revision, and silent parity success.

- [ ] **Step 3: Perform responsive browser validation**

Exercise Sessions and Models at 1280px, 1024px, and 390px. Verify Linear/Cumulative only, injected old `sqrt` storage migrates to Linear, Today and Yesterday persist across reload, exact-day empty state is honest, tooltip shows output plus the selected metric, chart/table are unchanged, there is no page-level horizontal overflow, and the console has no errors.

- [ ] **Step 4: Commit durable completion records**

Mark `specs/documentation-models-ux/tasks.md` complete, update plan outcomes with exact evidence, remove transient placeholders, run `git diff --check`, and commit:

    git add specs
    git commit -m "docs: record documentation and models UX validation"

- [ ] **Step 5: Merge into local main**

From the primary checkout, confirm `main` is clean, then:

    git merge --no-ff codex/documentation-models-ux -m "Merge documentation and models UX cleanup"

Do not push. Run the focused tests plus `git diff --check` on merged `main`.

- [ ] **Step 6: Install and verify the merge commit**

Run `./scripts/install` from local `main` and repeat revision, health, services,
listener, and manifest-parity checks. The installed revision must equal local
`main` HEAD.

- [ ] **Step 7: Clean the worktree and temporary execution state**

After confirming no uncommitted feature changes:

    git worktree remove .worktrees/documentation-models-ux

Remove ignored root `plan.md` with a recoverable patch once no work remains.
