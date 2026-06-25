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
