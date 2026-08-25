---
name: token-meter-github-ops
description: Use when Token Meter GitHub issues, pull requests, review requests, checks, comments, merges, or closure need to be managed.
---

# Token Meter GitHub Operations

## Purpose

Maintain issue and pull-request state without confusing repository readiness with
permission to act. Default to read-only inspection.

## Read path

- Read the complete issue or pull request, comments, reviews, checks, base, current
  head, and merge state.
- Compare the pull-request head with the handoff's tested and reviewed head.
- Confirm required tester and project-reviewer results satisfy
  `.agents/workflow/review-policy.yaml`.
- Carry verified hosted review findings into the shared findings ledger. A hosted
  review is advisory and cannot satisfy the project gate.

## Write gate

Immediately before a comment, edit, label change, review request, push, merge, close,
or other external mutation:

1. Identify the exact operation and target.
2. Recheck the current head, checks, findings, evidence freshness, and merge state.
3. Confirm the task envelope contains explicit approval for that specific operation.
4. Preview user-visible text when the operation publishes text.
5. Perform only the approved operation, then read back its resulting state.

Approval for one comment does not cover another. Approval to implement, commit, push,
request review, or merge does not imply any other action. If the head changes, return
the work to verification and review.

## Review requests

Request Codex or Claude hosted review only when separately approved. Record the
requested service and head. Verify every returned claim before routing a fix; do not
apply suggestions merely because an automated reviewer sounds confident.

## Operator result

Return the repository and pull request, inspected head, gate evidence, exact approved
action, resulting state, links, remaining findings, and recommended next state. Omit
credentials, private paths, traces, prompts, responses, reasoning, and tool contents
from public text.
