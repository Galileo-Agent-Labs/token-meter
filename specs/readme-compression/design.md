# README Compression and User Guide

## Goal

Reduce README cognitive load by turning it into a concise product evaluation and
first-use page. Move operational reference material into one maintained
`specs/USER_GUIDE.md` and link it prominently from the README.

## Audience and Reading Path

The README serves a first-time technical evaluator. Within a few minutes, the
reader should understand what Token Meter does, what it supports, why its local
evidence can be trusted, and how to install it.

Readers who need requirements, lifecycle behavior, evidence semantics, MCP
details, or troubleshooting continue into the user guide. Maintainer and
security details remain in their existing canonical documents.

## README Scope

Target 900 to 1,100 words. Retain only:

1. One-paragraph positioning and a short local-first trust statement.
2. A capability summary compressed from seven rows to no more than five.
3. Compact runtime and platform coverage with Windows clearly marked beta.
4. Quick Start commands for macOS/Linux and Windows.
5. A three-step first-use workflow.
6. Three representative screenshots: session dashboard, spend, and native
   companion.
7. One short evidence/privacy qualification.
8. A documentation section linking the user guide, security, architecture,
   contributing guide, design system, product principles, and implementation
   plans.
9. License.

Preserve the exact `### Windows` Quick Start heading because repository tests
treat it as a documentation interface. Put the beta notice directly below the
heading and keep the one-command bootstrap block unchanged.

## User Guide Scope

Create `specs/USER_GUIDE.md` as the detailed operational reference. It owns:

1. Requirements and platform-specific prerequisites.
2. Detailed installation, local rerun, and temporary development startup.
3. Dashboard and native-companion operating guidance.
4. Codex and Claude MCP connection steps and bounded tool descriptions.
5. Software-update behavior.
6. Automatic startup and uninstall commands.
7. Local data discovery and evidence behavior.
8. Cost and estimate semantics.
9. Detailed privacy and provider-limit qualifications, with a link to
   `SECURITY.md` for the canonical security policy.
10. Existing troubleshooting procedures.

The move must preserve commands and meaningful qualifications. It should not
duplicate architecture internals already owned by `ARCHITECTURE.md` or the full
security model already owned by `SECURITY.md`.

## Capability Compression

Group the current capability inventory into no more than five outcomes:

- Understand a live run.
- Review history and spend.
- Compare models and execution.
- Investigate tools and skills.
- Manage limits, budgets, and bounded agent access.

Native companions and managed updates belong in platform/operations copy, not
as a separate capability row.

## Visual Compression

Keep only:

- `images/dashboard.png` for live session evidence;
- `images/spend.png` for historical usage;
- `images/menu-bar-widget.png` for the native companion.

Remove the detailed Settings and tool-analytics screenshots from the README.
Their capabilities remain named in the summary and can be described in the user
guide without lengthening the first-use path.

## Claim Boundaries

- Windows remains beta everywhere it is described.
- Runtime evidence varies; unavailable values never become zero.
- Costs and selected token metrics remain labeled as estimates.
- Token Meter does not upload traces or analytics.
- Provider-limit checks use the matching provider's existing sign-in.
- Explicitly connected agent clients may process bounded MCP results under
  their own provider terms.
- Do not claim versioned, signed, or notarized distribution.

## Files

- Modify `README.md`.
- Create `specs/USER_GUIDE.md`.
- Do not change product behavior, scripts, screenshots, tests, or other product
  specifications.

## Verification

- Confirm README word count is between 900 and 1,100 words.
- Confirm the user guide contains every moved command and qualification.
- Run the full test suite, including the Windows README packaging contract.
- Validate local Markdown links in both files.
- Confirm every Windows product and lifecycle reference is unambiguously beta.
- Run `git diff --check` and inspect the final diff for accidental loss,
  duplication, or overclaiming.
