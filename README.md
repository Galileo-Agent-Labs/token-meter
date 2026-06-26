# Token Meter

A local, live cost and activity dashboard for Claude Code and Codex logs.

Token Meter follows the newest local agent log on your machine, parses
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

- **Live log cost**: shows the running log/thread cost as entries are written,
  with a hover breakdown for uncached input, cached input, cache writes, and
  output tokens.
- **Token split**: separates input, output, thinking, and tool-result tokens so
  you can tell whether cost is coming from model output, cached context, or
  tool payloads.
- **Auto-following**: tracks the newest Claude Code or Codex log across local
  projects without requiring a command per run.
- **Execution trace**: normalizes messages, reasoning, tool calls, tool results,
  usage events, coordination events, and completion events into one timeline.
- **Tool and MCP usage**: groups tools by namespace, call count, returned-token
  volume, and execution so large tool outputs are easy to spot.
- **Efficiency signals**: highlights reasoning share, tool/retrieval bloat,
  coordination tax, cost per task, context pressure, and spend anomalies.
- **Global view**: summarizes local spend across logs, model mix, provider mix,
  trend, anomalies, and newest logs.
- **Alerts and notifications**: can notify on budget crossings, execution cost
  spikes, and notable log insights.
- **macOS menu bar companion**: shows a compact live status item backed by the
  same local dashboard endpoint, with actions to open the full dashboard.
- **Local-only operation**: reads local JSONL logs passively and does not
  control your agent or send telemetry anywhere.

## Requirements

- Python 3.8 or newer. The macOS package first tries `/usr/bin/python3`, then
  Homebrew and user `python3` installs.
- Claude Code and/or Codex if you want live log data.
- macOS with the Swift toolchain, usually from Xcode Command Line Tools, when
  running from source. The macOS package includes a prebuilt menu bar binary.
- `curl`, used by the helper scripts.

The web dashboard has no third-party Python packages. `meter.py` uses only the
Python standard library. Dashboard-only mode is available for development,
troubleshooting, and non-macOS use, but the normal experience includes the menu
bar companion.

## Quick Start

Choose one install path.

### Option 1: Install The macOS Package

This is the easiest path for most macOS users. We are not using GitHub Releases
yet, so open the repository's [`dist/`](dist/) folder, download the latest
`TokenMeter-*.pkg` there, then open it with Finder. The installer may ask for an
administrator password because it installs under
`/Library/Application Support`.

If you have a local clone, you can open the checked-in package from the
repository root:

```bash
open dist/TokenMeter-0.1.0.pkg
```

The installer:

- installs Token Meter to `/Library/Application Support/Token Meter`
- installs a per-user LaunchAgent at
  `~/Library/LaunchAgents/com.token-meter.menubar.plist`
- starts the local Python server
- launches the prebuilt menu bar widget
- writes runtime logs to `~/Library/Logs/Token Meter`

After install, open the full dashboard at:

```text
http://localhost:8722
```

If no Claude Code or Codex logs exist yet, the dashboard will show an empty
state. Start a Claude Code or Codex run, then refresh or leave the dashboard
open; Token Meter will follow the newest local log automatically.

Check that the local server is healthy:

```bash
curl http://127.0.0.1:8722/health
```

Package users do not need the Swift toolchain. They do need Python 3.8 or newer
available as `/usr/bin/python3`, `/opt/homebrew/bin/python3`,
`/usr/local/bin/python3`, or on `PATH` as `python3`.

To update, download the newer `.pkg` from `dist/` and install it the same way.
The postinstall hook refreshes the installed files and restarts the Token Meter
LaunchAgent.

To uninstall a package install:

```bash
sudo "/Library/Application Support/Token Meter/bin/uninstall-token-meter"
```

Local unsigned packages can trigger macOS security prompts. Public distribution
should use a Developer ID signed and notarized package.

### Option 2: Traditional Source Method

Use this path for development, local changes, or non-package installs:

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

Check that the local server is healthy:

```bash
curl http://127.0.0.1:8722/health
```

Stop the foreground server with `Ctrl-C` in the terminal that started it.

For source installs that should start automatically, see [Launch At Login](#launch-at-login).

## Build The macOS Installer Package

Maintainers can build a local installer package:

```bash
./packaging/build-pkg
```

The package is written to `dist/TokenMeter-0.1.0.pkg` by default. For the
repeatable build checklist and verification commands, see
[`packaging/BUILD_RECIPE.md`](packaging/BUILD_RECIPE.md).

For a signed package, set the signing identities while building:

```bash
TOKEN_METER_CODESIGN_IDENTITY="Developer ID Application: Your Name" \
TOKEN_METER_INSTALLER_SIGN_IDENTITY="Developer ID Installer: Your Name" \
./packaging/build-pkg
```

Public distribution should also be notarized with Apple.

## Launch At Login

For source installs, install a macOS login item that starts both the local
server and the menu bar companion:

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
status for the active log. It does not parse logs directly.

## How It Finds Logs

Token Meter reads local log stores:

```text
Claude Code: ~/.claude/projects/*/*.jsonl
Codex:       ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
```

It picks the newest source by modification time and recomputes state whenever
that source changes.

The Global tab scans both stores and sorts logs newest first. Clicking a log
opens it as a frozen view; `Back to live` returns to the active newest log.

## What The Dashboard Shows

The Current tab includes:

- Summary: live cost, tokens, burn rate, cache behavior, context pressure, and
  per-execution input/output trajectory.
- Activity: normalized event trace for messages, reasoning, tool calls, tool
  results, usage, coordination, and completion.
- Tools: tool and MCP usage by namespace and execution.
- Efficiency: reasoning share, model mix, tool/retrieval bloat, coordination
  tax, cost per task, and spend anomaly signals.
- Insights: plain-language log signals.
- Alerts: budget and spike events.

The Global tab includes:

- Total spend across local Claude Code and Codex logs.
- Provider and model mix.
- 14-day spend trend with anomaly markers.
- Newest-first log list with provider, project, models, tokens, and cost.

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
does not send logs, prompts, responses, project paths, token counts, or
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

### The dashboard says no logs were found

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

The final command requires at least one local Claude Code or Codex log.

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
|-- packaging/
|   |-- build-pkg                    # builds a macOS installer package
|   |-- payload/bin/                 # scripts installed by the package
|   `-- scripts/postinstall          # package install hook
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

Commit the app source, scripts, images, public docs, and the distributable
installer package:

```text
README.md
LICENSE
meter.py
page.html
images/dashboard.png
images/menu-bar-widget.png
menubar/TokenMeterMenuBar.swift
packaging/
scripts/
REQUIREMENTS.md
CONTRIBUTING.md
SECURITY.md
dist/TokenMeter-*.pkg
.gitignore
```

Do not commit local runtime artifacts such as `.DS_Store`, `*.log`,
`__pycache__/`, or `.build/`. The included `.gitignore` covers those files.
Keep `dist/` limited to package files that users should download.

## License

MIT. See [LICENSE](LICENSE).
