# Contributing

Thanks for helping improve Token Meter.

## Before you start

- **New feature:** [Open a GitHub issue](https://github.com/Galileo-Agent-Labs/token-meter/issues/new)
  before writing code or creating a pull request. Describe the problem,
  proposed behavior, expected UI impact, and any privacy or local-only
  considerations. Wait until the scope is agreed before implementing it.
- **Bug fix or documentation improvement:** You may open a pull request
  directly. A separate issue is optional.
- **Security issue:** Do not open a public issue or pull request containing
  exploit details or sensitive data. Follow [SECURITY.md](SECURITY.md).

## The easiest contribution path

1. Fork the repository, or clone it directly if you have write access.
2. Create a focused branch for one change.
3. Reproduce the problem before editing when fixing a bug.
4. Make the smallest change that solves the problem and add or update tests.
5. Run the relevant checks below, then open a pull request.

```bash
git clone <your-fork-url>
cd token-meter
git switch -c fix/short-description
```

## Use a coding agent

Start the agent in the repository and give it a narrow bug report or an approved
feature issue. Coding-agent instructions live in [AGENTS.md](AGENTS.md);
Claude Code imports the same file through `CLAUDE.md`. Review the resulting
diff and validation evidence before asking the agent to push or open a pull
request.

## Local setup

Token Meter has no Python package installation step. Its Python server uses only
the standard library.

For the full macOS experience, run the installer from your checkout:

```bash
./scripts/install
```

This starts the local server and menu bar companion. Open
[http://localhost:8722](http://localhost:8722) and check its health with:

```bash
curl http://127.0.0.1:8722/health
```

Rerun `./scripts/install` after source changes when you want to test the staged
runtime. For dashboard-only development, when port 8722 is free, run:

```bash
python3 meter.py
```

The menu bar companion requires the Swift toolchain. Token Meter works best
when the machine already has Claude, Codex, or Cursor session logs. If it does
not, the app should still start; create a normal agent session and reload the
dashboard to see live data.

## Where to make changes

| Path | Purpose |
| --- | --- |
| `meter.py` | Local server, trace parsing, usage calculations, and HTTP API |
| `page.html` | Browser dashboard |
| `menubar/TokenMeterMenuBar.swift` | Native macOS menu bar companion |
| `token_meter_mcp.py` | Read-only local MCP integration |
| `tests/` | Python tests |
| `scripts/` | Installation and runtime helpers |

## Project guardrails

- Keep `meter.py` and `token_meter_mcp.py` dependency-free and on the Python
  standard library.
- Keep the dashboard local-only. Do not add external telemetry or hosted assets.
- Keep provider usage requests narrow, bounded, sanitized, and read-only. Never
  log, persist, expose, or send credentials or raw provider responses elsewhere.
- Keep access to Cursor transcripts, shared state, and request traces read-only.
  Preserve the existing `est` labels and unavailable states for values that are
  not authoritative.
- Do not commit local traces, prompts, responses, customer information,
  credentials, generated logs, `.DS_Store`, `__pycache__/`, or `.build/`.
- Preserve existing behavior outside the approved issue or reported bug.

See [SECURITY.md](SECURITY.md) for the full security and privacy model.

## Validation

Run these checks from the repository root before opening a pull request:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/token-meter-pycache python3 -m py_compile meter.py token_meter_mcp.py
python3 -m unittest discover -s tests -v
bash -n scripts/install scripts/install-launch-agent scripts/run-menubar scripts/run-token-meter-mcp scripts/start-token-meter scripts/uninstall-launch-agent scripts/update
node -e "const fs=require('fs'); const html=fs.readFileSync('page.html','utf8'); const m=html.match(/<script>([\\s\\S]*)<\\/script>/); new Function(m[1]); console.log('js ok')"
git diff --check
```

For menu bar changes, also run:

```bash
swiftc menubar/TokenMeterMenuBar.swift -o /private/tmp/token-meter-menubar
TOKEN_METER_MENUBAR_SMOKE=1 /private/tmp/token-meter-menubar
```

For visible dashboard or menu bar changes, test the behavior in the running app
and include a screenshot. If you cannot run a check—for example, because the
Swift toolchain is unavailable—say so in the pull request.

## Pull request checklist

Keep the pull request focused and include:

- The problem and why the change is needed.
- What changed.
- The feature-issue link, when applicable.
- The validation commands run and their results.
- Screenshots for visible dashboard or menu bar changes.
- Any limitations, skipped checks, or follow-up work.

Before submitting, review the diff for sensitive local data and unrelated
changes.
