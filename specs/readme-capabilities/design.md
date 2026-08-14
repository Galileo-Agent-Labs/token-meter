# README Capabilities Refresh

## Goal

Update `README.md` so a general technical reader can quickly understand what
Token Meter does, which decisions it supports, where it runs, and which claims
depend on incomplete or estimated evidence.

The README should describe the current product rather than an internal launch
story, enterprise pitch, or exhaustive implementation inventory.

## Audience

The primary reader is someone encountering Token Meter for the first time who
wants to decide whether it is relevant, trustworthy, and practical to install.
The copy should remain useful to contributors and internal stakeholders without
being written specifically for either group.

## Structure

1. Open with the problem and the operating decision Token Meter supports:
   whether to continue, intervene, or investigate an agent run.
2. Present capabilities in outcome-oriented groups:
   - live run visibility;
   - session history and spend;
   - model comparison;
   - tool and skill evidence;
   - provider limits and budgets;
   - bounded read-only agent access.
3. Add a compact coverage section naming supported runtimes and platforms.
4. Keep Quick Start near the top.
5. Retain the visual tour, privacy model, measured-versus-estimated semantics,
   updates, uninstall instructions, troubleshooting, and maintainer links.
6. Remove repeated capability descriptions when a later section already owns
   the operational detail.

## Platform Status

- Present macOS and Linux as supported installation paths.
- Present the Windows extension, installer, tray companion, startup behavior,
  and related instructions as **beta**.
- Do not imply that Windows has the same validation maturity as macOS or Linux.
- Keep the existing Windows commands available so beta users can still install
  and test it.

## Claim Boundaries

- Preserve explicit labels for estimated cost and token evidence.
- Preserve unavailable states instead of describing missing evidence as zero.
- Do not claim universal capability parity across runtimes.
- Do not claim a versioned, signed, or notarized release.
- Keep local-only language precise: Token Meter does not upload trace content or
  analytics, while explicitly connected agent clients may process bounded MCP
  results under their own provider terms.

## Editing Boundaries

- Change only `README.md` during implementation.
- Do not change product behavior, installation scripts, screenshots, or product
  specifications.
- Keep `README.md` as the only tracked Markdown file at the repository root.
- Preserve relative links and commands unless a capability correction requires
  a wording change.

## Verification

- Review the README against `specs/PRODUCT.md`, `specs/ARCHITECTURE.md`, and the
  registered runtime/platform implementations.
- Confirm every Windows reference is either explicitly marked beta or appears
  inside a section whose beta status is unambiguous.
- Check Markdown links and referenced repository paths.
- Run `git diff --check` and inspect the final README diff for duplicated or
  overstated claims.
