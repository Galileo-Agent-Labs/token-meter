# Explicit Model Pricing Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the model-pricing save scope visibly apply only to explicitly selected model rows while removing the table's Actions column.

**Architecture:** Keep the existing atomic `/settings/model-pricing` batch contract and effective-dated pricing engine unchanged. Replace the browser's implicit dirty-row set with an explicit selected-row set, render one checkbox per model, move rare lifecycle controls into the Model ID cell, and construct `changes` only from selected keys. Preserve drafts on failures and live polling; clear selection after success.

**Tech Stack:** Python `unittest` source contracts, HTML, CSS, vanilla JavaScript, local HTTP endpoints, macOS Swift/AppKit smoke checks, Bash installer and runtime-manifest parity.

## Global Constraints

- The scope label is **Apply to selected models** with **From now**, **From date**, and **All history** choices.
- Editing a price automatically selects and highlights its model row.
- Unchecking a row excludes it and restores its currently saved values.
- No unchecked model is sent in the `changes` array.
- The disabled primary action reads **Save models**; selected states read **Save 1 model** or **Save N models**.
- Remove the Actions column at every width.
- Keep **Restore built-in price** and **Remove custom model** only beneath relevant model IDs.
- Preserve current effective-scope confirmations, primary-source provenance, add-model flow, validation, action-token protection, and atomic batch persistence.
- At 390 pixels, the checkbox and Model ID lead each card and the page has zero horizontal overflow.
- Do not add a backend endpoint, new dependency, modal, select-all control, or per-row Save button.

---

### Task 1: Explicit selection state and pricing-table hierarchy

**Files:**
- Modify: `tests/test_meter.py:3102-3150`
- Modify: `page.html:167-171,1008-1026,1930-2090`

**Interfaces:**
- Consumes: `model_pricing.models`, `modelPriceValues(root)`, `requestModelPrice({changes})`, `confirmModelPriceHistory(action, model)`, and the existing `data-model-price-key` row identity.
- Produces: `modelPricingSelected: Set<string>`, `setModelPriceSelected(root, selected, restore)`, `updateModelPriceSelectionState()`, `data-model-price-select`, `data-model-price-lifecycle`, and selected-only `changes` payload construction.

- [ ] **Step 1: Write failing dashboard selection contracts**

Extend `DashboardLayoutTests.test_model_pricing_is_editable_and_supports_new_models_in_settings` and add a focused test that requires the new visible and behavioral contract:

```python
def test_model_pricing_uses_explicit_row_selection_without_an_actions_column(self):
    pricing = self.page.split('id=model-pricing-settings', 1)[1].split(
        'id=frustration-settings', 1,
    )[0]
    for marker in (
        'Apply to selected models',
        'id=model-price-selection-count',
        'No models selected',
        'data-model-price-select',
        'data-model-price-lifecycle',
        'function setModelPriceSelected',
        'function updateModelPriceSelectionState',
        'modelPricingSelected',
        'Save 1 model',
        'Restore built-in price',
        'Remove custom model',
        'Select ${row.model} for price update',
    ):
        self.assertIn(marker, pricing)
    self.assertNotIn('<th class=num>Actions</th>', pricing)
    self.assertNotIn('data-label="Action"', pricing)
    self.assertNotIn('class=modelPriceActions', pricing)
```

Also require source markers showing that input events call
`setModelPriceSelected(root,true,false)`, checkbox removal restores values, and
`saveModelPriceChanges()` reads keys from `modelPricingSelected`.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_meter.DashboardLayoutTests.test_model_pricing_uses_explicit_row_selection_without_an_actions_column -v
```

Expected: FAIL because row checkboxes, explicit selection state, contextual lifecycle markers, and the new copy are absent while the Actions column remains.

- [ ] **Step 3: Replace the table and scope markup**

Change the scope legend and selection summary to:

```html
<fieldset class=modelPriceScope id=model-price-scope>
 <legend>Apply to selected models</legend>
 <!-- existing scope radios and effective-date field -->
 <span id=model-price-selection-count>No models selected</span>
 <span id=model-price-scope-note>Changes start when you save; older session estimates keep their prior prices.</span>
</fieldset>
```

Change the table header to:

```html
<tr><th>Select</th><th>Provider</th><th>Model id</th><th class=num>Input</th><th class=num>Output</th><th class=num>Cache write</th><th class=num>Cached input</th></tr>
```

Keep the footer button initially disabled with `Save models`.

- [ ] **Step 4: Implement explicit row selection and contextual actions**

Replace `modelPricingDirty` with:

```javascript
let modelPricingState=null,modelPricingSelected=new Set();
```

Render each row with a leading checkbox:

```javascript
<td class=modelPriceSelectCell data-label="Select"><input data-model-price-select type=checkbox aria-label="${esc(`Select ${row.model} for price update`)}"></td>
```

Place the lifecycle control inside `.modelPriceModel` only when applicable:

```javascript
const lifecycle=row.overridden
 ? '<button class=modelPriceLifecycle data-model-price-lifecycle type=button>Restore built-in price</button>'
 : (row.custom
   ? '<button class=modelPriceLifecycle data-model-price-lifecycle type=button>Remove custom model</button>'
   : '');
```

Add selection helpers with these semantics:

```javascript
function setModelPriceSelected(root,selected,restore=true){
 const key=root?.dataset.modelPriceKey;
 if(!key)return;
 if(selected)modelPricingSelected.add(key);else{
  modelPricingSelected.delete(key);
  if(restore)restoreModelPriceRow(root);
 }
 root.classList.toggle('selected',selected);
 const checkbox=root.querySelector('[data-model-price-select]');
 if(checkbox)checkbox.checked=selected;
 updateModelPriceSelectionState();
}
function updateModelPriceSelectionState(){
 const count=modelPricingSelected.size,button=$('model-price-save-changes');
 $('model-price-selection-count').textContent=count?`${f(count)} ${countWord(count,'model')} selected`:'No models selected';
 button.disabled=count===0;
 button.textContent=count?`Save ${f(count)} ${countWord(count,'model')}`:'Save models';
}
```

Input events select the owning row. Checkbox change events call
`setModelPriceSelected(root, checkbox.checked, true)`. Rename the lifecycle
handler to `applyModelPriceLifecycle(button)` and bind only
`[data-model-price-lifecycle]` buttons.

- [ ] **Step 5: Save only explicitly selected rows**

Change `saveModelPriceChanges()` to start from:

```javascript
const button=$('model-price-save-changes'),keys=[...modelPricingSelected];
```

Keep validation before the request. On success, delete each saved key from
`modelPricingSelected`, force-render the returned pricing state, and refresh
the selection summary. On error, do not mutate the set or re-render the rows.

- [ ] **Step 6: Implement selected-row and responsive styling**

Make the scope legend visible, add a compact checkbox cell, and show selection
without relying only on color:

```css
.modelPriceScope legend{position:static;width:auto;height:auto;margin:0;clip:auto;overflow:visible;color:var(--fg);font-size:11px;font-weight:800}
.modelPriceTable tr.selected{outline:1px solid rgba(0,188,235,.58);background:rgba(0,188,235,.055)}
.modelPriceSelectCell{width:54px;text-align:center}.modelPriceSelectCell input{width:16px;height:16px;accent-color:var(--accent)}
.modelPriceLifecycle{border:0;background:transparent;color:var(--accent);padding:0;text-align:left;font:inherit;font-size:10px;font-weight:700;line-height:1.3;cursor:pointer}
```

At `max-width:700px`, place Select at grid row 1/column 1, Model ID at grid row
1/column 2, Provider across row 2, and let the four price cells fill the
remaining two-column card. Retain `overflow:visible`, `min-width:0`, and compact
provider badges.

- [ ] **Step 7: Run focused and dashboard tests and verify GREEN**

Run:

```bash
python3 -m unittest \
  tests.test_meter.DashboardLayoutTests.test_model_pricing_uses_explicit_row_selection_without_an_actions_column \
  tests.test_meter.DashboardLayoutTests.test_model_pricing_is_editable_and_supports_new_models_in_settings -v
python3 -m unittest tests.test_meter.DashboardLayoutTests tests.test_meter.PricingTests
node -e "const fs=require('fs');const h=fs.readFileSync('page.html','utf8');const m=h.match(/<script>([\\s\\S]*)<\\/script>/);new Function(m[1]);console.log('js ok')"
```

Expected: the two focused tests pass, all dashboard/pricing tests pass, and Node prints `js ok`.

- [ ] **Step 8: Commit the selection UI**

```bash
git add page.html tests/test_meter.py
git commit -m "refactor: make model price selection explicit"
```

---

### Task 2: Documentation, browser QA, and installed runtime

**Files:**
- Modify: `specs/USER_GUIDE.md:117-125`
- Modify: `specs/plans/active.md` (ignored; do not stage)
- Test: full repository and installed runtime

**Interfaces:**
- Consumes: the explicit selection state and existing installer/runtime manifest.
- Produces: current user instructions, responsive visual evidence, and exact installed-source parity.

- [ ] **Step 1: Update the user guide**

Replace the model-pricing Settings sentence with concise behavior:

```markdown
Model pricing shows the review date and provider sources for bundled rates.
Select the models to change, edit their prices, choose **From now**, **From
date**, or **All history**, and save them together. Unselected models are not
changed.
```

- [ ] **Step 2: Run the complete source verification**

```bash
python3 -m unittest discover -s tests
PYTHONPYCACHEPREFIX=/private/tmp/token-meter-pycache python3 -m py_compile \
  meter.py token_meter_mcp.py $(find token_meter -type f -name '*.py' -print)
node -e "const fs=require('fs');const h=fs.readFileSync('page.html','utf8');const m=h.match(/<script>([\\s\\S]*)<\\/script>/);new Function(m[1]);console.log('js ok')"
bash -n scripts/install scripts/install-linux scripts/install-launch-agent \
  scripts/install-systemd-user scripts/run-menubar scripts/run-token-meter-mcp \
  scripts/start-token-meter scripts/uninstall-launch-agent \
  scripts/uninstall-systemd-user scripts/update
xcrun swiftc -typecheck menubar/TokenMeterMenuBar.swift
swiftc menubar/TokenMeterMenuBar.swift -o /private/tmp/token-meter-menubar
TOKEN_METER_MENUBAR_MENU_SMOKE=1 /private/tmp/token-meter-menubar
git diff --check
```

Expected: 518 or more tests pass with only the three documented platform skips; every static command exits zero; native smoke includes `native-update=available updating attention`.

- [ ] **Step 3: Run one bounded browser QA pass**

Serve the source on a non-installed port and inspect Settings once at 1440×1000,
1024×820, and 390×844. Verify:

- initially no row is selected and **Save models** is disabled;
- editing one price checks and highlights only that row;
- the scope reads **Apply to selected models** and **1 model selected**;
- selecting a second row changes the action to **Save 2 models**;
- unchecking one row restores its saved value and excludes it;
- From date enables the date input;
- lifecycle controls appear beneath only overridden/custom model IDs;
- the Actions column is absent;
- keyboard focus reaches checkboxes, inputs, scope controls, and Save;
- there are no console errors or page-level horizontal overflow.

Fix all observed defects in one batch and confirm once at desktop and mobile.

- [ ] **Step 4: Commit documentation and browser fixes**

```bash
git add specs/USER_GUIDE.md page.html tests/test_meter.py
git commit -m "docs: explain selected model price updates"
```

- [ ] **Step 5: Install and verify the exact committed runtime**

```bash
./scripts/install
curl -fsS http://127.0.0.1:8722/health
curl -fsS http://127.0.0.1:8722/menubar
curl -fsS http://127.0.0.1:8722/updates/status
launchctl print "gui/$UID/com.token-meter.server"
launchctl print "gui/$UID/com.token-meter.menubar"
lsof -nP -iTCP:8722 -sTCP:LISTEN
PYTHONPATH=. python3 -m token_meter.packaging parity \
  "$PWD" "$HOME/Library/Application Support/Token Meter/runtime" \
  "$PWD/runtime-manifest.txt"
```

Expected: health is ready, both LaunchAgents run, Python owns the loopback
listener, the update preferences remain enabled with a 600-second interval,
and runtime parity exits zero at the current committed revision. The update
state may remain `attention` while the development branch is not tracked
`main`; that is the intended safety guard.

- [ ] **Step 6: Run the final clean-tree check**

```bash
git status --short
git log -4 --oneline
```

Expected: no tracked changes remain and the latest commits contain the
selection UI plus its user documentation.
