# Token Meter

Claude, Codex, Cursor, OpenCode, and Kiro make it easy to start an agent
session. They make it harder to understand what happened, what it cost, and
whether usage is changing over time. Token Meter reads their local evidence and
turns it into one live dashboard.

Local-first. Python standard library only. No API keys for trace analysis. No
Token Meter analytics or telemetry leaves your machine.

## What You Can Do

| Area | What you get |
| --- | --- |
| **Follow a live run** | Watch estimated cost, tokens, context pressure, wait, output pace, tool calls, and session-budget alerts. |
| **Review session history** | Search supported runtimes together and filter by project, application, or activity window. |
| **Understand daily usage** | Explain spend and wait by day, project, runtime, and session. |
| **Compare model runtimes** | Review input, output, pace, wait, workload shape, and honestly withheld comparisons when evidence is insufficient. |
| **Review tools and skills** | Find high-output, failing, repeated, unused, or deferred capabilities from bounded trace evidence. |
| **Check provider limits** | View available Claude, Codex, and Cursor quota windows, resets, freshness, and unavailable states. |
| **Manage a monthly budget** | Configure one machine-wide budget with per-runtime allocations and threshold notifications. |
| **Ask an agent** | Let Codex or Claude query bounded, read-only current-run or aggregate evidence through the local MCP. |

Supported evidence sources include Claude Code, Claude Desktop Agent/Cowork,
Codex CLI and desktop, Cursor Agent/Composer, OpenCode, and Kiro. Evidence
varies by runtime. Token Meter keeps unavailable values unavailable instead of
displaying a misleading zero.

## Quick Start

### macOS or Linux

```bash
git clone https://github.com/splunk/token-meter.git
./token-meter/scripts/install
```

The installer selects the current operating system, stages a stable per-user
runtime outside the clone, starts the local server and native companion, waits
for readiness, and configures automatic startup.

### Windows

```powershell
git clone https://github.com/splunk/token-meter.git
Set-Location .\token-meter
powershell.exe -NoLogo -NoProfile -File .\scripts\install-windows.ps1
```

The Windows installer stages the runtime under
`%LOCALAPPDATA%\Token Meter\runtime`, starts the hidden server and WinForms tray,
and creates only the current user's login startup entry. Administrator access
is not required.

Open [http://127.0.0.1:8722](http://127.0.0.1:8722), start a normal agent run,
and choose it from **Sessions**.

### Run Without Installing

For temporary development or troubleshooting:

```bash
cd token-meter
./scripts/start-token-meter
```

To run only the web server:

```bash
python3 meter.py
```

## A Good First Five Minutes

1. Open **Sessions → Current sessions** and select an active run.
2. Check cost confidence, context pressure, wait, input, output, and output pace
   under **Run**.
3. Set a browser-local session budget if the run needs a hard attention limit.
4. Open **Sessions → All sessions** to search history and find expensive or
   slow runs.
5. Use **Spend**, **Models**, and **Tools** after enough evidence accumulates.
6. Configure the machine-wide monthly budget and optional agent connections in
   **Settings**.

## Visual Tour

### Sessions

Current sessions shows recently active runs. All sessions provides complete
search, project/application/time filters, sorting, summaries, and recoverable
session deletion where the runtime and platform support it. Selecting a card or
row opens a durable local URL such as `/sessions/<id>#summary`.

<p align="center">
  <img src="images/dashboard.png" alt="Token Meter session detail with live cost, token, context, and execution metrics" width="900">
</p>

Inside a session, **Run** holds usage and timing, **Activity** holds normalized
events, **Tools** holds capability evidence, **Insights** holds derived signals,
and **Alerts** holds budget and notification state.

### Spend

Spend compares Today, 7-day, 30-day, This month, or custom calendar ranges. Its
stacked daily bars and platform split show how much each runtime contributed
while keeping partial and locally estimated cost evidence explicit.

<p align="center">
  <img src="images/spend.png" alt="Token Meter Spend page with selected-period totals, stacked daily runtime costs, highest-cost logs, and platform split" width="900">
</p>

### Models

Models compares model-runtime histories without combining identical model names
from different applications. Filter by project, one or more model runtimes, or
history. Workload-matched pace is withheld when sample count, coverage, token
authority, or workload similarity is insufficient.

### Tools

Tools shows observed calls, returned text estimates, failures, repeats, catalog
exposure, skill-pack activation, and review candidates. Default tools, built-in
packs, read-only runtimes, and incomplete evidence are never treated as safe
disable recommendations.

<p align="center">
  <img src="images/tool-analytics.png" alt="Token Meter capability evidence and skill-pack review" width="900">
</p>

### Settings

Settings contains monthly budget allocations, thresholds, model-price periods,
software updates, menu-bar preferences, experimental language-signal phrases,
and local agent connections.

<p align="center">
  <img src="images/mcp.png" alt="Token Meter Settings view for local read-only agent connections" width="900">
</p>

## Native Companions

The macOS menu bar, Linux AppIndicator tray, and Windows notification-area
companion read the compact local `/menubar` payload. They do not parse traces or
read provider credentials directly.

The macOS companion provides **Run**, **Claude**, **Codex**, and **Cursor**
scopes. Run shows the current or pinned session; provider scopes show only the
quota windows that provider reports. Missing means unreported or unavailable,
never 0%. Menu-bar title fields and quota notifications are configurable in
Settings.

<p align="center">
  <img src="images/menu-bar-widget.png" alt="Token Meter macOS menu bar companion" width="420">
</p>

## Requirements

- Python 3.8 or newer.
- At least one supported runtime if you want trace data.
- macOS: Swift toolchain, normally from Xcode Command Line Tools.
- Linux: `systemd --user`, GTK 3, PyGObject, and Ayatana AppIndicator. GNOME
  generally needs an AppIndicator/KStatusNotifierItem extension.
- Windows: Windows PowerShell, Git, and native Windows Python 3.8 or newer.
- `curl` for macOS and Linux lifecycle helpers.

The browser dashboard has no third-party Python dependency. A machine with no
supported evidence should still start normally and show an empty state.

## Ask From Codex or Claude

Open **Settings → Agent connections** to connect the read-only local MCP entry
named `tokenmeter`. Start a new agent session after connecting.

The compact tools are:

- `mcp__tokenmeter__check` for the caller-matched current run and optional
  execution drill-down;
- `mcp__tokenmeter__usage` for bounded aggregate spend, model, tool, or change
  review;
- `mcp__tokenmeter__capabilities` for named user-installed skill-pack evidence.

Current detail is runtime/project matched. Historical output is aggregate-only.
Prompts, responses, reasoning, tool arguments/results, credentials, environment
values, settings, and trace paths are excluded. When called, the bounded result
enters the connected agent's context and may be processed by that client's
model provider under its own terms.

## Software Updates

Update checks are enabled by default and run every 10 minutes while the server
is active. A check only fetches revision metadata; it does not merge, reinstall,
or modify the development checkout.

When the managed update checkout is clean, non-diverged, and behind upstream,
the dashboard offers **New update available**. Clicking it performs a
fast-forward update, reruns the installer, and reloads after the local server
returns. Dirty or diverged checkouts are left untouched with an explanation in
Settings.

## Automatic Startup and Uninstall

macOS installs separate per-user LaunchAgents for server and menu bar:

```bash
"$HOME/Library/Application Support/Token Meter/runtime/scripts/uninstall-launch-agent"
```

Linux installs separate server and tray `systemd --user` services:

```bash
"${XDG_DATA_HOME:-$HOME/.local/share}/token-meter/runtime/scripts/uninstall-systemd-user"
```

Windows installs one owned current-user startup entry:

```powershell
& "$env:LOCALAPPDATA\Token Meter\runtime\scripts\uninstall-windows.ps1"
```

These uninstall helpers stop and remove Token Meter lifecycle entries. They do
not require `sudo` or security-control changes.

## How Data Is Found

Token Meter reads only local runtime stores: JSONL traces for Claude, Codex,
Cursor, and Kiro; read-only SQLite enrichment for Cursor and OpenCode; and
runtime-owned metadata needed to join a user-visible session to its trace.

Discovery and parsing are runtime adapters. Operating-system path rules are
platform services. Optional enrichment degrades to the authoritative base trace
instead of hiding a session. Opening a historical session without new trace
activity does not make it current.

For the exact path precedence, read-only rules, normalized data flow, cache
invalidation, and extension contracts, see
[Architecture](specs/ARCHITECTURE.md).

## Cost and Evidence Notes

Token Meter uses effective-dated provider/model price periods. Reinstalling
after a public price update does not rewrite estimates for events before that
price's effective time. Manual overrides can start a new period or explicitly
apply to all history.

Codex costs use public API-equivalent rates and remain estimates because
subscription billing can differ. Cursor input, output, pace, and cost are local
proxies derived from evidence Cursor persists and are labelled `est`; cache and
hidden model work remain unavailable. Pre-flight estimation is out of scope:
Token Meter reports evidence after usage is recorded.

## Privacy

Token Meter binds to `127.0.0.1`. It does not upload traces, prompts, responses,
project paths, token counts, costs, or derived analytics.

Claude, Codex, and Cursor quota views make bounded read-only requests using the
matching provider's existing sign-in. Credentials and raw responses are not
persisted, returned through localhost, included in logs/errors, or sent to
another provider. Third-party Claude authentication such as Bedrock fails
closed as unavailable.

The dashboard can display local project paths, runtime/model names, capability
names, and derived metrics. Do not expose the localhost page publicly. See the
[Security policy](specs/SECURITY.md) and
[architecture privacy invariants](specs/ARCHITECTURE.md#privacy-and-security-invariants).

## Troubleshooting

### Port 8722 is already in use

```bash
lsof -nP -iTCP:8722 -sTCP:LISTEN
```

Stop only the process you recognize, then rerun the installer or foreground
server. Do not start multiple Token Meter servers on the same port.

### No logs were found

Start a normal supported agent session and reload. Confirm the runtime is using
its standard local store and that Token Meter can read it. A regular Claude
Desktop cloud chat is not measurable unless Agent/Cowork produces the joined
local metadata and trace.

### Claude Desktop appears under another Claude label

Desktop attribution depends on current metadata containing a joinable session
identifier. If metadata is missing or from an unknown schema, Token Meter keeps
the authoritative trace and uses the narrower label rather than guessing.

### `page.html` is missing or source changes do not appear

Run `./scripts/install` from the intended checkout. Do not patch only the staged
Application Support or XDG runtime. The installer validates and stages the
manifest-owned source together.

### Notifications do not appear

Check browser/system notification permission and the relevant Settings toggle.
Unavailable or denied browser delivery still leaves in-app alert history.

### The native companion does not start

Check server readiness first:

```bash
curl -fsS http://127.0.0.1:8722/health
curl -fsS http://127.0.0.1:8722/menubar
```

Then rerun the platform installer. Linux users should also inspect the user
services and AppIndicator dependencies; macOS users need the Swift toolchain;
Windows users need native Windows Python available outside WSL.

## Project Documentation

README is intentionally the only tracked Markdown file at the repository root.
All maintained project documents live under `specs/`:

- [Architecture](specs/ARCHITECTURE.md)
- [Contributing](specs/CONTRIBUTING.md)
- [Security](specs/SECURITY.md)
- [Visual design system](specs/DESIGN.md)
- [Product principles](specs/PRODUCT.md)
- [Feature specifications and implementation plans](specs/)

Coding-agent instruction files are not at the root and may not be discovered
automatically. Before editing, coding agents must explicitly open and follow
[specs/AGENTS.md](specs/AGENTS.md). `specs/CLAUDE.md` points Claude-oriented
clients to the same instructions.

## License

MIT. See [LICENSE](LICENSE).
