# Implementation Log: Codex Model Suggester

> **Historical implementation log.** The dated results below apply only to the
> model-suggester experiment. Current Token Meter behavior and validation are
> defined by [../ARCHITECTURE.md](../ARCHITECTURE.md), current specifications,
> tests, and Git history.

## 2026-08-03

- Added the dependency-free `scripts/model-suggester-hook` command.
- Added project-scoped `UserPromptSubmit` registration while preserving the
  existing design hooks.
- Added subprocess tests for routing, pending/resolved state, overrides,
  fail-open behavior, project registration, and prompt-free state.
- Focused hook suite passed all 10 tests.
- Documented project-scoped activation, Desktop trust/reload behavior, three
  representative prompts, override controls, limitations, and privacy.
- Updated contributor validation to compile the new Python executable and
  preserve its prompt-handling boundary.
- Full validation passed: 238 unit/contract tests, Python compilation, shell
  syntax, embedded JavaScript parsing, Swift compilation, native smoke output,
  and `git diff --check`.
- Added state-cap enforcement and corrupt-state fail-open coverage. Final full
  validation passed 239 unit/contract tests plus all compile, syntax, smoke,
  and whitespace checks.
- Installed the exact source runtime as `1bae91b+local`; both LaunchAgents are
  running, `/health` is ready, `/menubar` is valid, and the source/runtime hook
  scripts are byte-identical with mode `0755`.
- The only remaining human check is a fresh Codex Desktop task, because this
  development task loaded project hooks before `UserPromptSubmit` was added.
- Aligned the lowest-effort user-facing label with Codex Desktop (`Light`) while
  retaining the machine-readable configuration value `low` in temporary state.
