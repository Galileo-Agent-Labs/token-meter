# Token Meter

A local, live cost and activity dashboard for Claude Code and Codex sessions.

Token Meter follows the newest local agent session log on your machine, parses
usage as it lands, and streams updates to a localhost dashboard. It is meant for
the moment when a long agent run is still active and you need to know whether it
is productive, expensive, stuck, or filling context.

Local-only. Python standard library only. No API keys. No telemetry leaves your
machine.

<p align="center">
  <img src="images/dashboard.png" alt="Token Meter dashboard" width="900">
</p>

<p align="center">
  <img src="images/menu-bar-widget.png" alt="Token Meter macOS menu bar widget" width="420">
</p>

## Features

- **Live session cost**: shows the running session cost as logs are written,
  with a hover breakdown for fresh input, cached input, cache writes, and
  output tokens.
- **Token split**: separates input, output, thinking, and tool-result tokens so
  you can tell whether cost is coming from model output, cached context, or
  tool payloads.
- **Auto-following**: tracks the newest Claude Code or Codex session across
  local projects without requiring a command per session.
- **Execution trace**: normalizes messages, reasoning, tool calls, tool results,
  usage events, coordination events, and completion events into one timeline.
- **Tool and MCP usage**: groups tools by namespace, call count, returned-token
  volume, and execution so large tool outputs are easy to spot.
- **Efficiency signals**: highlights reasoning share, tool/retrieval bloat,
  coordination tax, cost per task, context pressure, and spend anomalies.
- **Global view**: summarizes local spend across sessions, model mix, provider
  mix, trend, anomalies, and newest sessions.
- **Alerts and notifications**: can notify on budget crossings, execution cost
  spikes, and notable session insights.
- **macOS menu bar companion**: shows a compact live status item backed by the
  same local dashboard endpoint, with actions to open the full dashboard.
- **Local-only operation**: reads local JSONL logs passively and does not
  control your agent session or send telemetry anywhere.

## Requirements

- Python 3.9 or newer.
- Claude Code and/or Codex if you want live session data.
- macOS with the Swift toolchain, usually from Xcode Command Line Tools, for
  the menu bar companion.
- `curl`, used by the helper scripts.

The web dashboard has no third-party Python packages. `meter.py` uses only the
Python standard library. Dashboard-only mode is available for development,
troubleshooting, and non-macOS use, but the normal experience includes the menu
bar companion.

## Quick Start

The recommended setup runs both the local dashboard server and the macOS menu
bar companion:

```bash
git clone https://github.com/Galileo-Agent-Labs/token-meter.git
cd token-meter
./scripts/start-token-meter
```

The menu bar item starts automatically from that script. Open the full
dashboard at:

```text
http://localhost:8722
```

If no Claude Code or Codex logs exist yet, the dashboard will show an empty
state. Start a Claude Code or Codex session, then refresh or leave the dashboard
open; Token Meter will follow the newest local session automatically.

Check that the local server is healthy:

```bash
curl http://127.0.0.1:8722/health
```

Stop the server with `Ctrl-C` in the terminal that started it.

## Launch At Login

Install a macOS login item that starts both the local server and the menu bar
companion:

```bash
./scripts/install-launch-agent
```

Remove it:

```bash
./scripts/uninstall-launch-agent
```

## Dashboard-Only Mode

For development, troubleshooting, or non-macOS use, run only the local web
dashboard:

```bash
python3 meter.py
```

The menu bar companion polls the local `/menubar` endpoint and shows compact
status for the active session. It does not parse logs directly.

## How It Finds Sessions

Token Meter reads local session stores:

```text
Claude Code: ~/.claude/projects/*/*.jsonl
Codex:       ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
```

It picks the newest source by modification time and recomputes state whenever
that source changes.

The Global tab scans both stores and sorts sessions newest first. Clicking a
session opens it as a frozen post-mortem; `Back to live` returns to the active
newest session.

## What The Dashboard Shows

The Session tab includes:

- Summary: live cost, tokens, burn rate, cache behavior, context pressure, and
  per-execution input/output trajectory.
- Activity: normalized event trace for messages, reasoning, tool calls, tool
  results, usage, coordination, and completion.
- Tools: tool and MCP usage by namespace and execution.
- Efficiency: reasoning share, model mix, tool/retrieval bloat, coordination
  tax, cost per task, and spend anomaly signals.
- Insights: plain-language session signals.
- Alerts: budget and spike events.

The Global tab includes:

- Total spend across local Claude Code and Codex sessions.
- Provider and model mix.
- 14-day spend trend with anomaly markers.
- Newest-first session list with provider, project, models, tokens, and cost.

## Cost Notes

Claude costs are computed from the local `CLAUDE_PRICE` table in `meter.py`.
Codex costs are computed from the local `OPENAI_PRICE` table and are shown as
estimates because Codex subscription billing can differ from public API-rate
accounting.

Pre-flight estimation is out of scope. Token Meter reads logs after usage is
recorded, so it shows cost as it accrues rather than predicting the next
execution.

## Privacy

Token Meter binds to `127.0.0.1` and serves only a local browser dashboard. It
does not send session logs, prompts, responses, project paths, token counts, or
costs to any external service.

The dashboard can display local project paths, tool names, and trace metadata.
Do not expose the localhost page publicly unless you are comfortable sharing
that information.

## Troubleshooting

### Port 8722 is already in use

Find the process:

```bash
lsof -nP -iTCP:8722 -sTCP:LISTEN
```

Stop the existing Token Meter process, or edit `PORT` in `meter.py`.

### The dashboard says no sessions were found

Confirm that at least one of these directories exists and contains JSONL logs:

```bash
ls ~/.claude/projects
ls ~/.codex/sessions
```

Then run a Claude Code or Codex task and reload the dashboard.

### The dashboard says `page.html` is missing

Token Meter serves the UI from `page.html`. Use a full repository clone and run
from the clone:

```bash
git clone https://github.com/Galileo-Agent-Labs/token-meter.git
cd token-meter
./scripts/start-token-meter
```

If you copied `meter.py` somewhere else, copy `page.html` into that same folder
too, or start the server from the repository root.

If you already started Token Meter from the wrong directory, the old server may
still be running. Stop the process listening on port `8722`, then start again:

```bash
lsof -nP -iTCP:8722 -sTCP:LISTEN
kill <PID>
./scripts/start-token-meter
```

### Browser notifications do not appear

Use the notification toggle in the top-right of the dashboard. If notifications
were previously blocked for `localhost`, clear the block in browser site
settings and toggle notifications again.

### The menu bar companion will not build

Check that `swiftc` is available:

```bash
swiftc --version
```

On macOS, install the Xcode Command Line Tools if needed:

```bash
xcode-select --install
```

## Validate

Run these checks from the repository root:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/token-meter-pycache python3 -m py_compile meter.py
swiftc menubar/TokenMeterMenuBar.swift -o /private/tmp/token-meter-menubar
TOKEN_METER_MENUBAR_SMOKE=1 /private/tmp/token-meter-menubar
node -e "const fs=require('fs'); const html=fs.readFileSync('page.html','utf8'); const m=html.match(/<script>([\\s\\S]*)<\\/script>/); new Function(m[1]); console.log('js ok')"
python3 -c 'import meter; st=meter.recompute(meter.newest_source()); print(st["provider"], st["turns"], len(st["trace"]), st["tools"]["total_calls"])'
```

The final command requires at least one local Claude Code or Codex session log.

## Repository Layout

```text
.
|-- meter.py                         # local HTTP server, log parsers, pricing, SSE
|-- page.html                        # single-file dashboard UI
|-- images/
|   |-- dashboard.png                # README dashboard screenshot
|   `-- menu-bar-widget.png          # README menu bar screenshot
|-- menubar/
|   `-- TokenMeterMenuBar.swift      # native macOS menu bar companion
|-- scripts/
|   |-- run-menubar                  # build and run the menu bar companion
|   |-- start-token-meter            # start server if needed, then menu bar
|   |-- install-launch-agent         # install macOS login item
|   `-- uninstall-launch-agent       # remove macOS login item
|-- REQUIREMENTS.md                  # product rationale and historical notes
|-- CONTRIBUTING.md
|-- SECURITY.md
|-- LICENSE
`-- README.md
```

## Before Publishing On GitHub

Commit the app source, scripts, images, and public docs:

```text
README.md
LICENSE
meter.py
page.html
images/dashboard.png
images/menu-bar-widget.png
menubar/TokenMeterMenuBar.swift
scripts/
REQUIREMENTS.md
CONTRIBUTING.md
SECURITY.md
.gitignore
```

Do not commit local runtime artifacts such as `.DS_Store`, `*.log`,
`__pycache__/`, or `.build/`. The included `.gitignore` covers those files.

## License

MIT. See [LICENSE](LICENSE).
