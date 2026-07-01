# Security Policy

## Supported Versions

Token Meter is a small local tool. Security fixes should target the current
`main` branch unless the project later publishes versioned releases.

## Reporting A Vulnerability

Please report security issues privately to the project maintainers rather than
opening a public issue with exploit details.

When reporting, include:

- The affected commit or release.
- Your operating system and Python version.
- Steps to reproduce.
- Any relevant error messages.

Do not attach Claude Code or Codex session logs unless you have reviewed and
redacted them. Local session logs may contain prompts, responses, project paths,
tool outputs, and other sensitive information.

## Security Model

Token Meter is intended to run on your own machine and bind to `127.0.0.1`.
It should not expose the dashboard to public networks. The project should not
send logs, prompts, responses, project paths, token counts, or costs to external
services.

The optional `tokenmeter` MCP server is also local and read-only. It uses stdio,
does not open another listening port, and returns bounded derived evidence. It
must never return prompts, messages, reasoning text, tool arguments, tool
results, credentials, environment variables, configuration values, or log
paths. Detailed output is limited to the caller's matched current runtime and
project; historical output is aggregate-only. Capability names are returned
only when capability review is explicitly requested.

When an MCP tool is called, the bounded derived result is handed to the
connected Codex or Claude client. That client may send the result to its model
provider under the client's own terms and configuration. “Local-only” means
Token Meter itself makes no outbound request; it does not mean data returned to
an explicitly connected AI client necessarily remains on the machine.

Dashboard connection actions are protected by the existing local-origin action
token and fixed subprocess argument vectors. They may add or remove only the
exact user-level MCP entry named `tokenmeter`, must refuse a conflicting entry,
and must verify the persisted state before reporting success. The MCP tools
themselves cannot change configuration, budgets, sessions, or Token Meter state.
