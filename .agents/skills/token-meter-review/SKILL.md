---
name: token-meter-review
description: Use when a Token Meter change needs independent requirements, correctness, regression, privacy, or cross-surface review.
---

# Token Meter Review

## Purpose

Review the current base-to-head change independently and return findings first. Do not
modify tracked files or silently repair an issue while acting as reviewer.

## Inputs

Require the task envelope, acceptance criteria, base and head commits, diff, developer
result, tester evidence when available, assigned lens, and
`.agents/workflow/review-policy.yaml`. If the head or requirements are ambiguous,
report the review blocked rather than reviewing a guessed target.

## Review

Trace the changed execution paths and inspect:

- Requirement and acceptance-criteria coverage.
- Correctness, errors, empty and stale data, concurrency, deletion, and upgrades.
- Callers and consumers of changed or removed helpers.
- Backend, dashboard, native, installer, runtime-manifest, and documentation effects.
- Runtime/provider identity and measured-versus-estimated semantics.
- Local-only privacy boundaries and sanitized projections.
- Compatibility routes, settings, migrations, and platform-specific behavior.
- Whether tests can pass on fixtures while real upstream or installed behavior fails.

Standard changes require one project reviewer. High-risk changes require at least two
project-reviewer results with distinct assigned lenses. Hosted reviews are advisory
and do not replace this gate.

For external feedback, **REQUIRED SUB-SKILL:** Use
`superpowers:receiving-code-review`. Verify the claim against the repository and
requirements before accepting a fix.

## Findings ledger

Each finding has severity, a concise causal explanation, tight file and line evidence,
affected behavior, and an owner. Use `blocking`, `important`, or `minor`. Do not inflate
style preferences into correctness findings. If no actionable problem remains, return
an explicit empty findings ledger instead of inventing issues.

Allowed dispositions are fixed, disproved with evidence, accepted by the owner, or
externally blocked. A reviewer cannot disposition its own finding by editing code.

## Reviewer result

Return assigned lens, reviewed base and head, findings, acceptance-criteria gaps,
test-evidence gaps, unverified behavior, and recommended next state. Any later tracked
code or test change invalidates this result.
