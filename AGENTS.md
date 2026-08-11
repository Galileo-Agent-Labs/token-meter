# AGENTS.md

AI-agent context for Token Meter, a local cross-platform dashboard and native companion for Claude, Codex, Cursor, OpenCode, and Kiro usage.

## Context

Token Meter reads local agent traces, calculates clearly labeled usage estimates, and serves a browser dashboard plus a native macOS menu-bar companion. The Python server is dependency-free and local-only. Mistakes can misstate cost, expose private trace data, or leave the installed runtime out of sync with the repository.

## Key Paths

| Path | Purpose |
|---|---|
| `meter.py` | Small executable/import compatibility facade for `token_meter.app` |
| `token_meter/app.py` | Composition, compatibility exports, settings, and application lifecycle |
| `token_meter/runtimes/` | Registered runtime discovery, parsing, revisions, and safe projections |
| `token_meter/platforms/` | Host paths, process/update policy, and trash behavior |
| `token_meter/domain/` | Runtime-neutral usage, timing, tools, insights, and aggregates |
| `token_meter/projections.py` | Explicit allowlisted public compatibility projections |
| `page.html` | Entire browser dashboard: markup, styles, routing, and JavaScript |
| `menubar/TokenMeterMenuBar.swift` | Native AppKit companion, preferences, notifications |
| `token_meter_mcp.py` | Bounded read-only MCP interface |
| `tests/test_meter.py` | Server, parser, UI-contract, installer, and Swift-source tests |
| `tests/test_mcp_server.py` | MCP contract and privacy tests |
| `runtime-manifest.txt` | Shared source-to-runtime packaging contract |
| `scripts/install` | Stage user runtime and install both macOS LaunchAgents |
| `scripts/install-windows.ps1` | Stage the same manifest and install Windows lifecycle/tray launchers |
| `README.md` | User installation and behavior documentation |
| `CONTRIBUTING.md` | Contribution policy and complete validation commands |

## Commands

| Command | Purpose |
|---|---|
| `python3 -m unittest discover -s tests -v` | Run all unit and contract tests |
| `PYTHONPYCACHEPREFIX=/private/tmp/token-meter-pycache python3 -m py_compile meter.py token_meter_mcp.py $(find token_meter -type f -name '*.py' -print)` | Compile Python without polluting the repo |
| `node -e "const fs=require('fs');const h=fs.readFileSync('page.html','utf8');const m=h.match(/<script>([\\s\\S]*)<\\/script>/);new Function(m[1]);console.log('js ok')"` | Parse embedded dashboard JavaScript |
| `bash -n scripts/install scripts/install-launch-agent scripts/run-menubar scripts/run-token-meter-mcp scripts/start-token-meter scripts/uninstall-launch-agent` | Check shell syntax |
| `swiftc menubar/TokenMeterMenuBar.swift -o /private/tmp/token-meter-menubar` | Compile the native companion |
| `TOKEN_METER_MENUBAR_SMOKE=1 /private/tmp/token-meter-menubar` | Run deterministic native smoke output |
| `powershell -NoProfile -Command "[void] [scriptblock]::Create((Get-Content -Raw scripts/install-windows.ps1))"` | Parse a Windows script on a Windows host |
| `./scripts/install` | Stage and start the exact repository runtime |
| `curl -fsS http://127.0.0.1:8722/health` | Verify server/page readiness |
| `curl -fsS http://127.0.0.1:8722/menubar` | Verify compact native payload |
| `git diff --check` | Reject whitespace errors |

## Rules & Patterns

- Treat `README.md` and `CONTRIBUTING.md` as human documentation; keep this file dense and agent-specific.
- For multi-file or multi-milestone work, create or replace root `plan.md` before code edits.
- Keep `plan.md` current with goal, decisions, progress, validation, and remaining work at every stopping point.
- `plan.md` is local execution state: never stage or commit it.
- Preserve unrelated worktree changes. Do not reset, checkout, or rewrite user changes.
- Keep `meter.py` and `token_meter_mcp.py` on the Python standard library.
- Keep the dashboard local-only; do not add hosted assets, analytics, or telemetry.
- Never output, commit, persist, or transmit prompts, responses, reasoning, tool contents, credentials, account data, or raw traces.
- Provider-account requests must remain narrow, bounded, sanitized, and read-only.
- Cursor databases, transcripts, and request logs are read-only inputs.
- Label estimates as estimates. Unavailable evidence must not become a measured zero.
- Scope models by runtime when aggregating; identical model names in different clients are not one history.
- Persist machine-wide settings through the existing atomic JSON-write path and action-token-protected HTTP endpoints.
- New settings require validation, idempotent writes, migration behavior, and tests.
- Preserve legacy hash routes and stored preferences when changing navigation or native settings.
- Keep the top-level dashboard order `Sessions → Daily → Models → Tools → Learn → Settings`. Sessions contains `Current sessions` and `All sessions`; All owns cross-session review.
- Global is not a dashboard surface. Keep cross-session aggregation as shared backend data for Sessions All, Daily, Models, Tools, MCP, and the menu bar.
- Keep the complete machine-wide monthly budget dashboard and controls inside Settings. The native companion may deep-link to `#settings-budgets`; preserve `#budgets` as a compatibility redirect.
- Use macOS labels such as `⌥`, never `Alt`, in user-facing copy.
- Do not add a top-level dashboard view when an existing workflow can contain the complete capability, unless the approved design explicitly calls for one.
- Visible dashboard changes require embedded-JS validation and browser checks at wide, laptop, and narrow widths.
- Native changes require Swift compilation, smoke output, and a live menu-bar check.
- Source-only success is insufficient: run `./scripts/install`, verify `/health` and `/menubar`, and confirm staged runtime parity.
- Do not patch only `~/Library/Application Support/Token Meter/runtime`; change source, reinstall, then verify.
- Never use `sudo` or disable macOS security controls for installation.
- Do not commit local traces, settings, generated logs, caches, `.DS_Store`, `.build/`, `plan.md`, or `STATE.md`.

## Agent Workflow

- Read this file before editing, then inspect the relevant implementation and tests.
- For a bug fix, reproduce the problem when practical and distinguish the observed failure from an inferred cause.
- Keep work scoped to the requested bug or feature. Preserve unrelated changes and avoid opportunistic refactors.
- Add or update tests with the implementation and run the relevant commands in this file.
- Before handoff, report the files changed, exact validation results, installed-runtime checks when applicable, and anything not verified.
- Do not push, open a pull request, publish, or otherwise send changes externally unless the user explicitly requests it.

## Change Boundaries

- Bug fixes, documentation, and new features may proceed directly when clearly requested by the user.
- Prefer the smallest complete behavior and retain current semantics outside the requested scope.
- Update tests with implementation. UI-source string assertions are useful but do not replace behavioral tests.
- Keep API payloads bounded and free of local paths or raw exception text.
- Keep Current-session caps separate from machine-wide monthly budgets.
- Preserve the current pricing engine unless pricing behavior is explicitly in scope; model-table edits require exact source evidence and tests.

## Installed Runtime

`./scripts/install` stages files under `~/Library/Application Support/Token Meter/runtime` and manages `com.token-meter.server` plus `com.token-meter.menubar`.

For an agent-led installation, complete the clone and install workflow directly instead of asking the user to copy commands into Terminal. If the sandbox blocks the user-owned Application Support runtime, LaunchAgents directory, or `launchctl`, request only the narrow permission needed and continue after approval. Never use `sudo` or disable macOS security controls.

A healthy handoff reports the installed source commit, dashboard URL, both LaunchAgent states, valid `/health` and `/menubar` responses, source/runtime parity, automatic-start status, and the uninstall command printed by the installer.
