# Research: Codex Desktop Model-Suggester Hook

> **Historical research.** This project-scoped hook investigation is retained
> for provenance; it does not describe Token Meter's current architecture or a
> product capability. See [../ARCHITECTURE.md](../ARCHITECTURE.md) for current
> ownership and privacy boundaries.

## Goal

Add a project-scoped, local-only Codex Desktop preflight hook that recommends
Luna, Terra, or Sol before the first model request in a task. It must silently
allow suitable selections, block only confident mismatches, support explicit
override and recheck controls, and never persist prompt content.

## Existing Repository Architecture

### Project hooks

`.codex/hooks.json:1-28` already defines project-scoped Codex hooks for
`PostToolUse` and `Stop`. Both handlers are command hooks and resolve scripts
from the active repository. This is the correct integration surface for a
first test because Codex Desktop discovers project `.codex` configuration for
trusted repositories.

The current file has no `UserPromptSubmit` hook. Adding one alongside the
existing groups preserves the design-review hooks rather than replacing them.

### Runtime installation

`scripts/install:103-111` stages the full `scripts/` directory into the user
runtime. A new script under `scripts/` therefore reaches the installed Token
Meter runtime automatically without changing the copy loop.

The project-scoped hook should execute the source-checkout script through the
Git root, not the staged runtime. Codex runs the hook for the repository opened
in Desktop, and the source checkout is the auditable definition the user is
testing. The staged copy is still useful for future global installation, but
global hook registration is outside this first experiment because it would
mutate `~/.codex/hooks.json` and affect unrelated repositories.

### Validation conventions

`tests/test_meter.py:3801-3899` validates installer and shell contracts through
source assertions. The new hook has enough behavior to merit a focused
`tests/test_model_suggester_hook.py` suite that invokes the script as a
subprocess with real hook JSON. This avoids importing executable-only code and
tests the exact stdin/stdout boundary used by Codex.

The contribution guide at the time defined standard-library compile, unittest,
shell, embedded-JavaScript, and whitespace checks. Current commands now live in
[../AGENTS.md](../AGENTS.md). The hook research assumed that validation model
rather than another toolchain.

### Privacy contract

The README privacy contract stated that Token Meter was local-only and did not
send prompts, responses, project paths, token counts, or costs externally. The
contribution guardrails forbade logging or persisting raw sensitive provider
data and prompts. The current forms are
[../../README.md](../../README.md#privacy), [../SECURITY.md](../SECURITY.md),
and [../AGENTS.md](../AGENTS.md).

`UserPromptSubmit` necessarily exposes the prompt to the hook process. The
implementation must therefore:

- process prompt text only in memory;
- never write the prompt, response, working directory, transcript path, or
  session identifier to disk;
- persist only a SHA-256 session digest, route tier, status, and timestamp;
- keep state bounded, permission-restricted, and in the operating-system temp
  directory by default;
- never make HTTP, MCP, analytics, or other network calls;
- fail open on malformed input, unknown models, state errors, or unexpected
  exceptions.

## Current Codex Hook Contract

The current Codex manual documents `UserPromptSubmit` as running before the
prompt is sent to the model. Command hooks receive JSON on stdin. Shared fields
include `session_id`, `cwd`, `transcript_path`, `hook_event_name`, and active
`model`; the event adds `prompt` and `turn_id`.

Important constraints:

- `matcher` is ignored for `UserPromptSubmit`, so the command runs for every
  submitted prompt when configured.
- Exit code `0` with no stdout silently allows the prompt.
- JSON containing `decision: "block"` and a `reason` prevents the model request.
- A hook can recommend a reasoning level but current hook input does not expose
  the active reasoning level.
- A hook cannot rewrite the active Desktop model.
- Non-managed project hooks require trust after their definition changes.

These constraints require an allow/block state machine rather than an always-
visible advisory message.

## Proposed State Machine

```text
                         +-----------------------+
                         | no state for session  |
                         +-----------+-----------+
                                     |
                              classify prompt
                                     |
               +---------------------+--------------------+
               |                                          |
       route matches / low confidence             confident mismatch
               |                                          |
       save status=resolved                       save status=pending
               |                                          |
          silent allow                            block with suggestion
                                                          |
                           +------------------------------+------------------+
                           |                                                 |
                    retry matches route                              [route:keep]
                           |                                                 |
                    status=resolved                                  status=resolved
                           |                                                 |
                      silent allow                                      silent allow
```

Once resolved, ordinary follow-ups silently pass. `[route:check]` deliberately
reruns classification for a changed task. State older than seven days is
pruned, and the file is capped to a small number of records.

## Fundamental Classifier

The first version should be deterministic and explainable. It derives four
scores from bounded phrase groups:

1. **Scope**: one explicit change versus repository-wide or cross-system work.
2. **Ambiguity**: direct transformation versus investigation or root-cause
   discovery.
3. **Consequence**: reversible local work versus security, production, data,
   or migration risk.
4. **Verifiability**: exact search/test/lint checks versus a weak correctness
   oracle.

Routing policy:

```python
if consequence is high, or consequence combines with scope/ambiguity:
    route = Sol + High
elif scope or ambiguity is moderate:
    route = Terra + Medium
elif work is mechanical and strongly verifiable:
    route = Luna + Low
else:
    route = Terra + Medium with low confidence and do not block
```

Budget pressure is intentionally absent from this first classifier. A task's
minimum safe capability should be established before budget becomes a
tie-breaker. Connecting sanitized Token Meter budget state can be evaluated
after the task-only router is measured.

## Model Normalization

The active model is compared by tier:

- values containing `luna` -> Luna;
- values containing `terra` -> Terra;
- `gpt-5.6`, values containing `sol`, and GPT-5.5 variants -> Sol-class;
- unknown models -> fail open.

Comparison is by tier because the user-facing recommendation is a Codex model
family, not a provider-wide model catalog rewrite.

## Test Scenarios

1. Sol plus bounded README replacement blocks and recommends Luna + Low.
2. Luna plus intermittent CI root-cause work blocks and recommends Terra +
   Medium.
3. Terra plus zero-downtime authentication migration blocks and recommends Sol
   + High.
4. A matching route silently allows and resolves the session.
5. A matching retry after a block silently allows future follow-ups.
6. `[route:keep]` resolves a pending mismatch without changing the model.
7. `[route:check]` reclassifies a resolved session.
8. Low-confidence and unknown-model inputs fail open.
9. Malformed JSON and unwritable/corrupt state fail open.
10. Persisted state contains no prompt, path, transcript, or raw session ID.

## Decisions and Boundaries

- First release is project-scoped to Token Meter for safe Desktop testing.
- No dashboard UI or persistent Token Meter setting is added.
- No model is switched automatically.
- No local LLM, API call, MCP call, or prompt transmission is used.
- No global `~/.codex/hooks.json` mutation is performed.
- No Ultra recommendation is produced; parallelism requires a separate task-
  decomposition decision.
- Source installation remains unchanged because `scripts/` is already staged.

## Open Concern

Desktop must reload the project hook definition and the user must trust the
changed command before it can run. The current task loaded hooks before this
change, so end-to-end verification requires a fresh Codex Desktop task after
implementation. Source-level subprocess tests can fully verify the classifier,
privacy behavior, and hook JSON contract in the current development session.
