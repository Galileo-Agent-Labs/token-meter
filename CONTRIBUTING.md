# Contributing

Thanks for helping improve Token Meter.

## Local Setup

Token Meter has no Python package install step. The dashboard server uses only
the Python standard library.

The normal local setup runs both the dashboard server and the macOS menu bar
companion:

```bash
git clone https://github.com/Galileo-Agent-Labs/token-meter.git
./token-meter/scripts/install
```

Open `http://localhost:8722`.

You can also check the local health endpoint:

```bash
curl http://127.0.0.1:8722/health
```

The dashboard works best when your machine already has Claude Code, Codex, or
Cursor session logs under `~/.claude/projects`, `~/.codex/sessions`, or
`~/.cursor/projects/*/agent-transcripts`.

If no logs exist yet, the app should still start. Run a Claude Code, Codex, or
Cursor Agent/Composer session and reload the dashboard to see live data.

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
- Keep provider quota reads narrow and read-only. Use only the matching
  first-party usage endpoint or provider-owned local service, fixed HTTPS URLs,
  hard timeouts, bounded responses, background refreshes, and sanitized errors.
  Never log, persist, return, or cross-send access tokens, cookies, account IDs,
  authorization headers, or raw provider response bodies. Do not fabricate a
  session or weekly window when a provider reports a different cap.
- Keep Cursor access read-only. The shared `state.vscdb`, its WAL, and request
  trace logs are enrichment inputs only; deletion may move the exact discovered
  transcript JSONL but must never modify or remove shared Cursor state.
- Keep Cursor usage provenance explicit. Input is a one-context-snapshot proxy,
  output is trace-visible text estimated at four characters per token, and cost
  uses only a persisted supported model/variant plus its configured public rate.
  Keep every such value marked `est`; keep cache, hidden reasoning, repeated
  internal model-call input, and authoritative billing unavailable. Do not let
  Cursor proxy cost trigger budget/spike intervention alerts.
- Avoid committing local session logs, generated logs, `.DS_Store`,
  `__pycache__/`, or `.build/`.
- If you change screenshots, keep the README image paths under `images/` so
  GitHub renders them correctly.

## Validation

Run the lightweight checks before opening a pull request:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/token-meter-pycache python3 -m py_compile meter.py token_meter_mcp.py
python3 -m unittest discover -s tests -v
bash -n scripts/install scripts/install-launch-agent scripts/run-menubar scripts/run-token-meter-mcp scripts/start-token-meter scripts/uninstall-launch-agent
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
