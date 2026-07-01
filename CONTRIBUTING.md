# Contributing

Thanks for helping improve Token Meter.

## Local Setup

Token Meter has no Python package install step. The dashboard server uses only
the Python standard library.

The normal local setup runs both the dashboard server and the macOS menu bar
companion:

```bash
git clone https://github.com/Galileo-Agent-Labs/token-meter.git
cd token-meter
./scripts/start-token-meter
```

Open `http://localhost:8722`.

You can also check the local health endpoint:

```bash
curl http://127.0.0.1:8722/health
```

The dashboard works best when your machine already has Claude Code or Codex
session logs under `~/.claude/projects` or `~/.codex/sessions`.

If no logs exist yet, the app should still start. Run a Claude Code or Codex
session and reload the dashboard to see live data.

The menu bar companion requires the Swift toolchain. For dashboard-only
development or non-macOS testing, run:

```bash
python3 meter.py
```

## Development Notes

- Keep the server dependency-free. `meter.py` should continue to use the Python
  standard library only. `token_meter_mcp.py` follows the same rule.
- Keep the dashboard local-only. Do not add external telemetry, hosted assets,
  or network calls from `page.html`.
- Avoid committing local session logs, generated logs, `.DS_Store`,
  `__pycache__/`, or `.build/`.
- If you change screenshots, keep the README image paths under `images/` so
  GitHub renders them correctly.

## Validation

Run the lightweight checks before opening a pull request:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/token-meter-pycache python3 -m py_compile meter.py token_meter_mcp.py
python3 -m unittest discover -s tests -v
bash -n scripts/run-token-meter-mcp packaging/payload/bin/token-meter-mcp
swiftc menubar/TokenMeterMenuBar.swift -o /private/tmp/token-meter-menubar
TOKEN_METER_MENUBAR_SMOKE=1 /private/tmp/token-meter-menubar
node -e "const fs=require('fs'); const html=fs.readFileSync('page.html','utf8'); const m=html.match(/<script>([\\s\\S]*)<\\/script>/); new Function(m[1]); console.log('js ok')"
```

If you do not have the Swift toolchain installed, mention that in the pull
request and run the Python and JavaScript checks.

## Pull Requests

For new features, please open a feature request first before sending a pull
request. Include the problem, proposed behavior, expected UI impact, and any
privacy or local-only considerations. This keeps the project from accumulating
half-finished dashboard surfaces or overlapping feature ideas.

Small bug fixes, documentation fixes, validation updates, and screenshot
refreshes can go straight to a pull request.

Please include:

- What changed.
- Why it changed.
- How you validated it.
- Screenshots for visible dashboard or menu bar changes.
