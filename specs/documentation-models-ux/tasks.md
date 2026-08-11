# Documentation and Dashboard Cleanup Tasks

Status: approved for inline execution.

- [x] TASK-001: Establish the canonical documentation tree.
  - Change: create `specs/ARCHITECTURE.md`; move every tracked root Markdown
    document except `README.md` under `specs/`; move safe historical local
    Markdown into `specs/history/`; update all internal links.
  - Preserve: README remains the only root entry point; external source
    attribution stays beside its asset.
  - Verify: root tracked-Markdown inventory contains only README; local Markdown
    links and referenced repository paths resolve.
  - Requirements: REQ-DOC-001, REQ-DOC-002, REQ-DOC-003, NFR-001.
  - Depends on: none.

- [x] TASK-002: Remove nonlinear Session chart scale choices.
  - Change: add a failing dashboard contract, remove Sqrt/Log buttons, narrow
    the scale allowlist, and remove unreachable nonlinear scale branches.
  - Preserve: Linear default, stored-value fallback, Cumulative Tokens/Cost,
    accessible pressed states, and all per-execution metrics.
  - Verify: the focused test fails before production edits and passes after;
    embedded JavaScript parses.
  - Requirements: REQ-SESSION-001, NFR-002, NFR-003.
  - Depends on: none.

- [x] TASK-003: Add exact Today and Yesterday Models windows.
  - Change: add failing matched-pace and dashboard contracts; add exact-day
    options, one browser date-window predicate, and exact-day matched-pace map
    entries.
  - Preserve: existing 7/30/90/all behavior, project/model selections,
    unavailable semantics, runtime-scoped model identity, and payload shape.
  - Verify: focused Python tests prove exact sample counts and UI contracts prove
    every Models consumer uses the shared predicate.
  - Requirements: REQ-MODELS-001, NFR-001, NFR-002, NFR-003.
  - Depends on: none.

- [x] TASK-004: Simplify only the Model trends hover card.
  - Change: revise the existing failing tooltip contract and render output plus
    the selected trend metric using `MODEL_TREND_METRICS`.
  - Preserve: chart/table structure, model filtering, swatches, availability,
    pointer handoff, keyboard focus, sticky date heading, and viewport flipping.
  - Verify: focused contract passes and responsive browser hover checks show the
    selected metric with no console errors.
  - Requirements: REQ-MODELS-002, NFR-002, NFR-003.
  - Depends on: TASK-003.

- [ ] TASK-005: Validate, install, and integrate locally.
  - Change: update task/spec outcomes, run the full validation matrix, install
    the exact commit, verify endpoints/services/listener/parity, merge the
    feature branch into local `main`, reinstall the merge commit, and remove the
    linked worktree plus temporary root plan.
  - Preserve: no push or pull request; Linux/Windows native gaps remain labelled
    honestly on macOS.
  - Verify: complete suite and static checks pass; browser checks cover wide,
    laptop, and narrow layouts; installed revision equals merged local main.
  - Requirements: NFR-001, NFR-002, NFR-003, NFR-004.
  - Depends on: TASK-001, TASK-002, TASK-003, TASK-004.

## Traceability

| Requirement | Design | Tasks | Validation |
|---|---|---|---|
| REQ-DOC-001 | Documentation Architecture | TASK-001 | architecture/path audit |
| REQ-DOC-002 | Canonical documents | TASK-001 | ownership and link audit |
| REQ-DOC-003 | Historical documents | TASK-001 | root inventory and status banners |
| REQ-SESSION-001 | Session chart scale migration | TASK-002 | dashboard contract and browser storage check |
| REQ-MODELS-001 | Exact Models date windows | TASK-003 | matched-pace unit and dashboard contract |
| REQ-MODELS-002 | Model trends hover card | TASK-004 | tooltip contract and browser hover check |
| NFR-001 | Data Flow and Invariants | TASK-001, TASK-003, TASK-005 | privacy/contracts suite |
| NFR-002 | UI Design | TASK-002, TASK-003, TASK-004, TASK-005 | full regression suite |
| NFR-003 | UI Design | TASK-002, TASK-003, TASK-004, TASK-005 | responsive browser checks |
| NFR-004 | Test Strategy | TASK-005 | full source/install validation |
