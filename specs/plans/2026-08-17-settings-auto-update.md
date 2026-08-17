# Settings Cleanup and Automatic Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically install safe `main` updates when enabled, surface manual updates in the native menu, and resolve the approved high-confidence Settings UX findings from issue #23.

**Architecture:** Extend the existing atomic update settings and watcher rather than adding another updater. Project one bounded update snapshot through `/menubar`, keep the native AppKit client as a thin action/status surface, and refine the existing single-file dashboard in place. Preserve the effective-dated pricing engine while adding provider-source metadata and one atomic batch-save boundary for edited rows.

**Tech Stack:** Python 3 standard library, HTML/CSS/vanilla JavaScript, Swift/AppKit, Bash, `unittest`, local HTTP endpoints, LaunchAgents.

## Global Constraints

- Keep checks fixed at `10 * 60` seconds; do not add daily or configurable cadence.
- Keep `enabled` and new `auto_install` as separate validated Booleans; both default to true.
- Disabling checks disables automatic installation; disabling installation alone leaves checks enabled.
- Auto-install only a clean, non-diverged `main` checkout tracking a remote `main` branch.
- Preserve the existing action-token protection, bounded payloads, detached updater, runtime staging, and rollback behavior.
- Do not add the rejected bottom information card, hosted assets, telemetry, or a new top-level route.
- Preserve pricing resolution and effective-dated history semantics; only exact primary-source evidence may change bundled prices.
- Keep prompts, responses, credentials, local paths, raw traces, and command output out of HTTP/native payloads.
- Visible dashboard changes require wide, laptop, and narrow browser verification; native changes require Swift compile, smoke, live check, install, and source/runtime parity.

---

### Task 1: Independent update settings and automatic watcher launch

**Files:**
- Modify: `token_meter/app.py:1178-1591`
- Modify: `scripts/update`
- Test: `tests/test_meter.py:5668-5830`

**Interfaces:**
- Consumes: existing `update_settings`, `set_update_settings`, `check_for_software_update`, `start_software_update`, `_persist_update_status`, and platform `update_plan`.
- Produces: `normalize_update_settings(values) -> {enabled, auto_install, interval_seconds}`, `software_update_watcher(checker=None, starter=None)`, persisted `failed_revision`, and explicit reinstall eligibility for a failed current target.

- [ ] **Step 1: Write failing settings tests**

Add focused tests with literal expectations:

```python
def test_update_settings_keep_checks_and_auto_install_independent(self):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "settings.json")
        self.assertEqual(
            meter.update_settings(path),
            {"enabled": True, "auto_install": True, "interval_seconds": 600},
        )
        result = meter.set_update_settings(
            {"enabled": True, "auto_install": False}, path
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            meter.update_settings(path),
            {"enabled": True, "auto_install": False, "interval_seconds": 600},
        )

def test_disabling_checks_also_disables_auto_install(self):
    # Persist enabled=false and auto_install=false in one atomic result.
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
python3 -m unittest \
  tests.test_meter.SoftwareUpdateTests.test_update_settings_keep_checks_and_auto_install_independent \
  tests.test_meter.SoftwareUpdateTests.test_disabling_checks_also_disables_auto_install -v
```

Expected: failures because `auto_install` is absent and disabling checks does not normalize the dependency.

- [ ] **Step 3: Implement validated settings and migration**

Update normalization and persistence so missing `auto_install` defaults true, invalid non-Booleans fail, and `enabled=False` forces `auto_install=False`:

```python
enabled = values.get("enabled", True)
auto_install = values.get("auto_install", True)
if not isinstance(enabled, bool) or not isinstance(auto_install, bool):
    raise ValueError("Update preferences must be on or off.")
if not enabled:
    auto_install = False
```

- [ ] **Step 4: Run the settings tests and confirm GREEN**

Run the command from Step 2 plus the existing `SoftwareUpdateTests` class.

- [ ] **Step 5: Write failing watcher behavior tests**

Cover these observable branches with injected checker/starter functions:

```python
def test_watcher_starts_safe_available_update_when_auto_install_is_on(self):
    # checker returns available=True, can_update=True; starter records one call.

def test_watcher_keeps_checking_without_install_when_auto_install_is_off(self):
    # checker runs, starter is never called.

def test_watcher_does_not_repeat_same_failed_revision(self):
    # failed_revision == latest_revision suppresses the automatic starter.
```

- [ ] **Step 6: Run watcher tests and confirm RED**

Run the three named tests; expected failures are missing injection and unconditional check-only behavior.

- [ ] **Step 7: Implement watcher launch and failed-target suppression**

Have the watcher consume the returned status and call the starter only when checks and automatic installation are enabled, the result is safely installable, and the target is not the persisted failed revision. Preserve terminal status while no newer revision exists. Add `failed_revision` to bounded persistence and `scripts/update` status writes.

Require `main` plus a tracked remote `main` before automatic install. Keep manual checks able to report attention on other branches.

- [ ] **Step 8: Add failed-current-target retry coverage and implementation**

Write a failing test where update status is failed, source HEAD equals the failed target, and the previous revision differs. Extend `start_software_update(..., allow_retry=True)` so an explicit `/updates/install` call can rerun the platform update plan for that exact source revision even though Git reports no commits behind. Automatic watcher calls do not use this retry override.

- [ ] **Step 9: Run update tests and shell syntax**

```bash
python3 -m unittest tests.test_meter.SoftwareUpdateTests -v
bash -n scripts/update
```

- [ ] **Step 10: Commit Task 1**

```bash
git add token_meter/app.py scripts/update tests/test_meter.py
git commit -m "feat: automatically install safe main updates"
```

---

### Task 2: Bounded menu-bar update projection and native actions

**Files:**
- Modify: `token_meter/app.py:7174-7245`
- Modify: `page.html:route handling around 1600-1660`
- Modify: `menubar/TokenMeterMenuBar.swift`
- Test: `tests/test_meter.py:4251-4634,4928-5076,5668-5830`

**Interfaces:**
- Consumes: `software_update_status()`, `_ACTION_TOKEN`, `/updates/install`, and the existing two-second `/menubar` poll.
- Produces: bounded `software_update` JSON, `SoftwareUpdateSnapshot.fromJSON`, direct install action, `#settings-updates`, and expected-restart snapshot retention.

- [ ] **Step 1: Write failing backend projection tests**

Assert behavior rather than source text:

```python
def test_menubar_projects_bounded_available_update(self):
    with mock.patch.object(meter, "software_update_status", return_value={
        "enabled": True, "auto_install": False, "state": "available",
        "available": True, "can_update": True,
        "latest_revision": "abcdef123456", "actions": {"token": "local"},
    }):
        update = meter.menubar_state()["software_update"]
    self.assertEqual(update["state"], "available")
    self.assertEqual(update["latest_revision"], "abcdef123456")
    self.assertNotIn("path", update)
    self.assertNotIn("message", update)
```

Also cover updating, attention, and preference values.

- [ ] **Step 2: Run the projection test and confirm RED**

Expected: missing `software_update` key.

- [ ] **Step 3: Implement a strict allowlisted projection**

Add a helper returning only `enabled`, `auto_install`, `state`, `available`, `can_update`, safe revisions, and the local action token/capability needed by the native process. Do not reuse the complete dashboard status object.

- [ ] **Step 4: Run backend projection tests and confirm GREEN**

- [ ] **Step 5: Write failing Swift source/smoke contracts**

Extend existing native tests and deterministic smoke fixtures to require:

- `New update available` as a first-level action when available and auto-install is off;
- `Updating Token Meter...` as disabled state;
- `Update needs attention` opening `#settings-updates`;
- an action-token POST to `/updates/install` with JSON content type;
- last snapshot retention only while the prior update state was updating.

- [ ] **Step 6: Run the native tests and confirm RED**

```bash
python3 -m unittest \
  tests.test_meter.MenubarSourceTests \
  tests.test_meter.MenubarSessionTests -v
```

- [ ] **Step 7: Implement native decoding, rows, and actions**

Add a bounded `SoftwareUpdateSnapshot`, parse it in `fetchState`, and insert the update row immediately after Open Dashboard. Use a local POST helper with the projected token. Do not rebuild while the menu is open. When a fetch fails during an update, retain the last snapshot and update row; otherwise use the existing disconnected state.

- [ ] **Step 8: Add and implement the update deep link**

Extend route handling with `#settings-updates`, open Settings, and scroll/focus `#update-settings`. Add a native URL/action for that exact route.

- [ ] **Step 9: Verify focused native/backend behavior**

```bash
python3 -m unittest \
  tests.test_meter.MenubarSourceTests \
  tests.test_meter.MenubarSessionTests \
  tests.test_meter.SoftwareUpdateTests -v
xcrun swiftc -typecheck menubar/TokenMeterMenuBar.swift
swiftc menubar/TokenMeterMenuBar.swift -o /private/tmp/token-meter-menubar
TOKEN_METER_MENUBAR_SMOKE=1 /private/tmp/token-meter-menubar
```

- [ ] **Step 10: Commit Task 2**

```bash
git add token_meter/app.py page.html menubar/TokenMeterMenuBar.swift tests/test_meter.py
git commit -m "feat: surface updates in the menu bar"
```

---

### Task 3: Two update controls and normalized Settings hierarchy

**Files:**
- Modify: `page.html:Settings CSS, markup, routing, and update JavaScript`
- Test: `tests/test_meter.py:2469-4250`

**Interfaces:**
- Consumes: `/settings/updates`, `/updates/check`, `/updates/install`, and `software_update` status fields from Tasks 1-2.
- Produces: `#update-enabled`, `#update-auto-install`, matching Settings navigation/headings, simplified Agent connection, and contextual language-signal counters.

- [ ] **Step 1: Write failing dashboard contract tests**

Add assertions for observable DOM/behavior:

- two independently labelled update checkboxes;
- turning off auto-install posts `{enabled:true, auto_install:false}`;
- turning off checks posts both false and disables the install checkbox;
- the five map labels exactly match the five section titles;
- removed duplicate MCP pills/readiness/privacy/tool-strip copy;
- one Agent connection privacy sentence and Learn link;
- no language-signal header count or `machine-wide` copy;
- each textarea has a live `N of 64` description;
- no bottom update information card.

- [ ] **Step 2: Run focused layout tests and confirm RED**

```bash
python3 -m unittest tests.test_meter.DashboardLayoutTests -v
```

- [ ] **Step 3: Normalize Settings markup and card hierarchy**

Make map labels and section titles identical. Move the Monthly budget title into its first owning card. Remove Settings-only accent top-border pseudo-elements while preserving shared card atmosphere.

Simplify Agent connection to one status per client, one action, one concise privacy sentence, and a Learn deep link. Remove the duplicate pills, tool-name strip, and expanded disclosure from Settings; place the access explanation in an existing Learn card/section and add a stable Learn route anchor.

- [ ] **Step 4: Implement language-signal copy and counters**

Use one sentence describing aggregate local positive/friction matching. Update counts on input and after reset/save, associate them through `aria-describedby`, and show literal current/remaining values per textarea.

- [ ] **Step 5: Implement the two update controls**

Render both values from status. The check control governs the install control's enabled state. Submit both Boolean values on either change so the backend remains authoritative. Keep Check now and the existing floating/manual install notice.

- [ ] **Step 6: Run layout tests and embedded JS parse**

```bash
python3 -m unittest tests.test_meter.DashboardLayoutTests -v
node -e "const fs=require('fs');const h=fs.readFileSync('page.html','utf8');const m=h.match(/<script>([\\s\\S]*)<\\/script>/);new Function(m[1]);console.log('js ok')"
```

- [ ] **Step 7: Commit Task 3**

```bash
git add page.html tests/test_meter.py
git commit -m "refactor: clarify settings controls and hierarchy"
```

---

### Task 4: Pricing provenance, evidence-backed catalog audit, and batch save

**Files:**
- Modify: `token_meter/models/catalog.py`
- Modify: `token_meter/app.py:688-1040,7636-7655`
- Modify: `page.html:model pricing CSS, markup, and JavaScript`
- Test: `tests/test_meter.py:1220-1554,2469-4250`

**Interfaces:**
- Consumes: existing `normalize_model_price`, effective-dated histories, atomic settings file, and pricing sources approved in the design.
- Produces: `BUILTIN_PRICE_SOURCES`, bounded `sources` projection, `set_model_prices(changes, ...)`, one section-level Save changes action, scoped restore actions, and narrow stacked cards.

- [ ] **Step 1: Write failing catalog/source tests with literal values**

Add primary-source-derived expectations for every changed row and metadata:

```python
def test_builtin_pricing_exposes_reviewed_primary_sources(self):
    state = meter.model_pricing_settings(path=self.settings_path)
    self.assertEqual(state["reviewed_on"], "2026-08-17")
    self.assertEqual(
        [source["provider"] for source in state["sources"]],
        ["anthropic", "openai", "cursor"],
    )

def test_current_anthropic_builtin_prices_match_primary_source(self):
    self.assertEqual(ANTHROPIC_PRICE["claude-fable-5"]["input"], 10.0)
    self.assertEqual(ANTHROPIC_PRICE["claude-fable-5"]["output"], 50.0)
    self.assertEqual(ANTHROPIC_PRICE["claude-opus-4-8"]["input"], 5.0)
    self.assertEqual(ANTHROPIC_PRICE["claude-opus-4-8"]["output"], 25.0)
```

Audit all remaining rows against the linked official pages before adding any further literal correction.

- [ ] **Step 2: Run pricing tests and confirm RED**

```bash
python3 -m unittest tests.test_meter.PricingTests -v
```

- [ ] **Step 3: Add bounded provenance and exact catalog corrections**

Store source label/URL plus one review date beside the built-in catalog. Expose only HTTPS provider links. Correct only rows whose current official evidence is unambiguous; update effective-dated cutovers when a time-bounded provider price requires them.

- [ ] **Step 4: Write failing atomic batch tests**

Cover:

- two valid row edits persist in one settings write and one result;
- one invalid row rejects all changes without modifying the file;
- duplicate provider/model entries reject the batch;
- existing single-row payload remains supported;
- selected `from_now`, `from_date`, or `all_history` scope applies consistently.

- [ ] **Step 5: Run batch tests and confirm RED**

- [ ] **Step 6: Implement `set_model_prices` and endpoint dispatch**

Normalize the entire bounded change list first, apply it to an in-memory copy of histories, and call `atomic_write_text` once. Return the refreshed bounded pricing projection. Route list payloads to the batch function and retain existing single-row behavior.

- [ ] **Step 7: Write failing pricing UI tests**

Require:

- provenance links and review date;
- one scope control with From now, From date, All history;
- one disabled-until-dirty Save changes button;
- no repeated built-in row Save buttons;
- Restore default only for overridden/custom rows;
- stacked narrow layout and no page-width overflow contract.

- [ ] **Step 8: Implement pricing UI and responsive behavior**

Track dirty row drafts, submit one batch, and retain drafts if the request fails. Use the same scope terms in labels, confirmation, and success status. At the narrow breakpoint render each row as a grid/card without horizontal page overflow.

- [ ] **Step 9: Run pricing/layout/JS checks**

```bash
python3 -m unittest \
  tests.test_meter.PricingTests \
  tests.test_meter.DashboardLayoutTests -v
node -e "const fs=require('fs');const h=fs.readFileSync('page.html','utf8');const m=h.match(/<script>([\\s\\S]*)<\\/script>/);new Function(m[1]);console.log('js ok')"
```

- [ ] **Step 10: Commit Task 4**

```bash
git add token_meter/models/catalog.py token_meter/app.py page.html tests/test_meter.py
git commit -m "refactor: clarify model pricing settings"
```

---

### Task 5: Documentation, complete verification, and installed runtime

**Files:**
- Modify: `README.md`
- Modify: `specs/USER_GUIDE.md`
- Modify: `specs/plans/active.md` (ignored; do not stage)
- Test: full repository and installed runtime

**Interfaces:**
- Consumes: completed behavior from Tasks 1-4.
- Produces: current user documentation, verified source, and installed runtime parity.

- [ ] **Step 1: Update user documentation**

Document two update preferences, default behavior, clean-main guard, native available/update/attention states, manual retry, fixed ten-minute cadence, and uninstall/troubleshooting continuity. Do not copy implementation internals or a test-count ledger into docs.

- [ ] **Step 2: Run complete static and unit verification**

```bash
python3 -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/private/tmp/token-meter-pycache python3 -m py_compile \
  meter.py token_meter_mcp.py $(find token_meter -type f -name '*.py' -print)
node -e "const fs=require('fs');const h=fs.readFileSync('page.html','utf8');const m=h.match(/<script>([\\s\\S]*)<\\/script>/);new Function(m[1]);console.log('js ok')"
bash -n scripts/install scripts/install-linux scripts/install-launch-agent \
  scripts/install-systemd-user scripts/run-menubar scripts/run-token-meter-mcp \
  scripts/start-token-meter scripts/uninstall-launch-agent \
  scripts/uninstall-systemd-user scripts/update
xcrun swiftc -typecheck menubar/TokenMeterMenuBar.swift
swiftc menubar/TokenMeterMenuBar.swift -o /private/tmp/token-meter-menubar
TOKEN_METER_MENUBAR_SMOKE=1 /private/tmp/token-meter-menubar
git diff --check
```

- [ ] **Step 3: Run bounded browser QA**

Inspect Settings at wide desktop, 1024px laptop, and 390px mobile widths in one pass. Verify section hierarchy, keyboard focus, update dependency behavior, model-price edits/scope, source links, language counts, no console errors, and zero page-level horizontal overflow. Fix discovered defects in one batch and confirm once.

- [ ] **Step 4: Install source and verify live services**

```bash
./scripts/install
curl -fsS http://127.0.0.1:8722/health
curl -fsS http://127.0.0.1:8722/menubar
launchctl print "gui/$UID/com.token-meter.server"
launchctl print "gui/$UID/com.token-meter.menubar"
```

Verify `/updates/status`, both preference values, current/available native smoke branches, `#settings-updates`, listener ownership on port 8722, automatic startup, and byte-for-byte parity for every changed runtime file.

- [ ] **Step 5: Run final verification after all fixes**

Repeat the complete test suite, static checks, Swift compile/smoke, browser confirmation, installed endpoints, LaunchAgents, and source/runtime parity. Read the full outputs before making completion claims.

- [ ] **Step 6: Commit documentation and any final verified fixes**

```bash
git add README.md specs/USER_GUIDE.md token_meter page.html \
  menubar/TokenMeterMenuBar.swift scripts/update tests/test_meter.py
git commit -m "docs: explain automatic update controls"
```

