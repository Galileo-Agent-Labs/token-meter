# README Capabilities Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the public README around Token Meter's current user-facing capabilities while clearly labeling the Windows extension as beta.

**Architecture:** Keep `README.md` as the single public entry point and preserve its operational sections. Rewrite only the opening capability summary and platform-status wording, using `specs/PRODUCT.md` and the existing runtime/platform implementations as the claim boundary.

**Tech Stack:** CommonMark Markdown, repository-relative links, shell-based documentation checks.

## Global Constraints

- Change only `README.md` during implementation.
- Keep the audience generic rather than internal, enterprise-specific, or release-specific.
- Present macOS and Linux as supported installation paths.
- Present the Windows extension, installer, tray companion, startup behavior, and related instructions as **beta**.
- Preserve estimate, unavailable-evidence, privacy, and MCP provider-processing qualifications.
- Do not claim universal runtime parity or a versioned, signed, or notarized release.
- Keep `README.md` as the only tracked Markdown file at the repository root.

---

### Task 1: Publish the outcome-oriented README capability map

**Files:**
- Modify: `README.md`
- Reference: `specs/readme-capabilities/design.md`
- Reference: `specs/PRODUCT.md`

**Interfaces:**
- Consumes: the current runtime list, platform install paths, dashboard surfaces, native companion behavior, privacy contract, and MCP contract documented in the repository.
- Produces: a generic public README whose capability summary, platform status, Quick Start, requirements, lifecycle, and troubleshooting language agree.

- [ ] **Step 1: Record the current public structure and Windows references**

Run:

```bash
rg -n '^## |^### |Windows|Claude|Codex|Cursor|OpenCode|Kiro|estimated|unavailable' README.md
```

Expected: the current capability table and every Windows reference are visible before editing.

- [ ] **Step 2: Rewrite the opening around operating decisions**

Replace the current opening with concise copy that identifies Token Meter as a
local-first observability dashboard for AI coding agents and states that its
purpose is to help users decide whether to continue, intervene, compare, or
investigate a run. Retain these facts in the opening:

```text
Claude, Codex, Cursor, OpenCode, and Kiro
local session evidence
Python standard library only
no API keys for trace analysis
no Token Meter analytics or telemetry leaves the machine
```

- [ ] **Step 3: Replace the capability inventory with outcome-oriented groups**

Use one scan-friendly capability table covering exactly these groups:

```text
Live run visibility
History and spend
Models and execution
Tools and skills
Provider limits and budgets
Read-only agent access
Native desktop experience
```

Describe the user outcome first in every row. Include current capabilities such
as context pressure, wait, output pace, range-based spend, runtime/project
filters, evidence-gated model comparison, observed tool failures and repeats,
provider-reported quota windows, monthly budget allocations, bounded MCP
queries, native companions, notifications, and updates. Preserve the rule that
estimated evidence stays labeled and unavailable evidence is not presented as
zero.

- [ ] **Step 4: Add compact runtime and platform coverage**

Immediately after the capability table, add a coverage section that names:

```text
Claude Code and Claude Desktop Agent/Cowork
Codex CLI and desktop
Cursor Agent/Composer
OpenCode
Kiro
```

Add a platform-status table with these exact status distinctions:

```text
macOS — Supported — browser dashboard and native menu-bar companion
Linux — Supported — browser dashboard and AppIndicator tray companion
Windows — Beta — browser dashboard and notification-area extension
```

State that runtime evidence varies and that not every capability is available
for every runtime.

- [ ] **Step 5: Make Windows beta status unambiguous throughout**

Update these existing locations without removing their commands:

```text
Quick Start heading
Windows installer description
Native Companions
Requirements
Automatic Startup and Uninstall
native-companion troubleshooting
```

Use `Windows (beta)` or `Windows beta` in each location. Do not describe macOS
or Linux as beta.

- [ ] **Step 6: Remove repetition while retaining operational detail**

Keep Quick Start near the top and retain the visual tour, MCP details, updates,
uninstall instructions, evidence notes, privacy, troubleshooting, project
documentation, and license. Remove only wording duplicated by the new
capability and coverage sections; do not remove commands, screenshots, security
qualifications, or maintained-document links.

- [ ] **Step 7: Verify platform labeling and capability claims**

Run:

```bash
rg -n '^## |^### |Windows|beta|Claude|Codex|Cursor|OpenCode|Kiro|estimated|unavailable' README.md
```

Expected:

```text
Every Windows product/install/lifecycle reference is explicitly beta or is inside an unambiguously beta-labeled subsection.
All five runtime families remain named.
Estimate and unavailable-evidence qualifications remain present.
```

- [ ] **Step 8: Verify links, repository paths, and Markdown whitespace**

Run:

```bash
python3 - <<'PY'
import pathlib
import re

root = pathlib.Path('.')
text = (root / 'README.md').read_text(encoding='utf-8')
missing = []
for target in re.findall(r'\[[^]]+\]\(([^)]+)\)', text):
    if '://' in target or target.startswith('#'):
        continue
    path = target.split('#', 1)[0]
    if path and not (root / path).exists():
        missing.append(target)
raise SystemExit('missing README links: ' + ', '.join(missing) if missing else 0)
PY
git diff --check
```

Expected: both commands exit successfully with no output from `git diff --check`.

- [ ] **Step 9: Review and commit the README change**

Run:

```bash
git diff -- README.md
git status --short
git add README.md
git commit -m "docs: refresh readme capabilities"
```

Expected: the diff changes only `README.md`, existing unrelated commits remain
untouched, and the commit completes successfully.
