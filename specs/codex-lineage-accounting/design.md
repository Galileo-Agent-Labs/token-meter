# Codex Lineage-Aware Accounting

## Problem

Codex subagent rollout files can contain an exact copy of their parent's token
history followed by the child's new work. Copied events retain the same model
and usage values but receive new timestamps. Token Meter currently discovers
each physical file, assigns multiple files the same logical session ID, and
prices every `token_count.last_token_usage` event in every file. This inflates
session, daily, model, budget, and API-equivalent cost totals.

The parser also treats adjacent repeated token snapshots as separate usage.
When inherited events remain in a child trace, copied tool and timing records
can contaminate the child's detailed execution view even if only the duplicate
token rows are removed.

## Goals

- Count root, direct-child, and nested-child model usage exactly once.
- Preserve legitimate child work and retain all events when lineage cannot be
  resolved conservatively.
- Apply one corrected evidence stream to native loading, detailed sessions,
  historical summaries, throughput, daily/model aggregation, and budgets.
- Preserve existing logical session routes and Codex desktop deep links.
- Keep discovery lightweight and avoid caching full trace contents.
- Keep all processing local, read-only, dependency-free, and content-free in
  public projections.

## Non-Goals

- Redesign the Sessions interface around a visible subagent hierarchy.
- Merge all physical traces into a new composite trace format.
- Change provider pricing or reinterpret cumulative usage as billable deltas.
- Persist lineage indexes or trace-derived fingerprints to disk.
- Deduplicate similar but non-identical model calls.

## Considered Approaches

### Post-aggregation subtraction

Subtract repeated usage after session summaries have already been calculated.
This is a small patch, but per-session detail, throughput, tools, daily totals,
and aggregate totals can disagree. It also makes the runtime-neutral domain
layer understand Codex-specific lineage. This approach is rejected.

### Composite logical sessions

Discover one source per logical task and merge the root and all descendants
into it. This gives clean task-level semantics but changes source locators,
watcher activity, deletion, routing, live-session selection, and revision
invalidation. It is disproportionate to the accounting bug and is rejected.

### Adapter-owned lineage accounting stream

Keep physical files as adapter inputs and logical IDs as the existing public
identity. The Codex adapter records private physical and lineage identities,
constructs a conservative corrected row stream, and passes that same stream to
every parser. This keeps runtime-specific logic inside the runtime adapter and
is the selected approach.

## Identity Model

The first valid `session_meta` row is authoritative for lineage identity:

- `physical_trace_id`: the row's unique `payload.id`, falling back to the UUID
  in the filename;
- `logical_session_id`: `payload.session_id`, falling back to the physical ID;
- `forked_from_id`: the direct fork identifier when present;
- `parent_thread_id`: the explicit field or the equivalent nested
  `source.subagent.thread_spawn.parent_thread_id`;
- `lineage_parent_id`: `forked_from_id`, then `parent_thread_id`.

Later copied `session_meta` rows may still contribute non-identity metadata but
must not overwrite these identities. Existing public `id` and normalized
`SessionSource.session_id` remain the logical session ID. Legacy source records
carry the private physical and lineage fields for adapter use; public
projections continue to omit them.

The external title lookup uses the logical session ID first and the physical ID
as a fallback. This preserves current task names and `codex://threads/...`
links.

## Token Event Fingerprints

For each trace, the adapter derives a bounded tuple for every valid token event:

- source row index;
- effective model from the most recent `turn_context`;
- all flat numeric fields in `last_token_usage`, sorted by name;
- all flat numeric fields in `total_token_usage`, sorted by name.

Booleans and non-numeric provider-controlled values are excluded. Timestamps
are deliberately excluded because copied history is re-stamped.

Fingerprints are cached lazily per physical path and file signature. The cache
stores only compact scalar tuples, never raw rows, prompts, responses, tool
contents, or trace paths in a public projection. It uses least-recently-used
eviction with a 2,048-trace limit, and entries for disappeared paths are pruned
during discovery.

## Lineage Resolution

For a child trace with a resolvable direct parent, compare token events from the
start of both traces. An inherited event matches only when:

- the effective model is identical;
- every numeric `last_token_usage` field is identical; and
- when both events contain cumulative numeric usage, those signatures are
  identical;
- when only one event contains cumulative numeric usage, the events do not
  match; and
- when neither event contains cumulative numeric usage, exact model and last
  usage remain sufficient.

The inherited prefix is the longest consecutive exact match. Nested children
match their direct parent's raw token sequence, so the prefix naturally
includes root and intermediate history. Each trace is corrected independently:
the root keeps its work, a direct child keeps only its new work, and a nested
child keeps only work added after its direct parent.

If the parent is missing, resolves to the same path, contains no valid token
evidence, or has no exact prefix, the child retains all events. This favors a
visible overestimate over silently deleting legitimate usage.

## Corrected Row Stream

Rows are grouped into execution chunks ending at valid token events. Chunks
whose terminal token event belongs to the inherited prefix are removed. The
first physical `session_meta` row is retained as adapter metadata even when its
following copied execution chunk is removed. Dropping inherited chunks, rather
than only token rows, prevents copied tool calls, user/agent messages, task
timing, and completion markers from being attributed to the child's first new
execution.

After inherited chunks are removed, adjacent token snapshots are considered
duplicates only when model, numeric last usage, and a non-empty numeric
cumulative usage signature are all identical. For this second rule only the
repeated token row is removed; intervening task or timing rows are retained.
Requiring cumulative evidence prevents two independently identical calls from
being collapsed when the trace format does not provide a reliable total.

The corrected tuple of rows is used by:

- normalized `load` usage and turn evidence;
- legacy detailed recomputation;
- legacy historical summarization;
- performance, live-throughput, wait-time, tool, and language-signal helpers
  invoked by those paths.

No domain aggregate performs a second Codex-specific deduplication.

## Caching and Invalidation

Discovery continues reading only the bounded metadata prefix. Full token
fingerprints are built lazily when a source is actually loaded or summarized.
The adapter maintains a physical-ID-to-path index from the latest discovery.

A child's lineage revision distinguishes an unresolved parent from a resolved
parent and incorporates the parent's stable filesystem identity. Parent
append-only growth does not invalidate an unchanged child summary because the
child's copied prefix is immutable. Parent appearance or file replacement does
invalidate it. The existing per-path summary and detailed-state caches remain
authoritative above the adapter.

## Failure Handling

- Malformed rows remain counted as corrupt by the existing loader and are
  ignored for fingerprints.
- Missing or unreadable parent files leave the child's rows unchanged.
- Unknown usage keys are accepted when numeric and compared by exact name and
  value.
- Lineage cycles or self-references disable prefix removal for that trace.
- Cache failures degrade to a fresh local fingerprint scan, not unavailable
  usage or a synthetic zero.

## Tests

Regression tests use sanitized temporary traces and real adapter APIs:

1. discovery preserves distinct physical and logical identities;
2. a direct child removes re-stamped inherited history and keeps its new work;
3. a nested child retains only work added after its direct parent;
4. an unresolved parent retains every event;
5. a different model or usage value prevents prefix removal;
6. an exact adjacent snapshot with cumulative totals is counted once;
7. identical last usage without matching cumulative evidence is retained;
8. inherited tool calls do not appear in the child's corrected execution;
9. native load, detailed recomputation, summary rows, daily/model totals, and
   aggregate totals reconcile;
10. metadata prefix and fingerprint caches remain bounded and invalidate on
    the intended file changes.

The full unit and contract suite, Python compilation, shell syntax, embedded
JavaScript parsing, Swift compilation and smoke output, installation, loopback
health/menubar endpoints, LaunchAgent state, and source-to-runtime parity are
required before handoff.

## Acceptance Criteria

- Root, direct-child, and nested-child fixtures count each model call once.
- Children without a resolvable exact prefix retain all evidence.
- Adjacent snapshots are removed only with exact model, last usage, and
  non-empty cumulative usage agreement.
- Session, Daily, Models, Spend, and budget totals derive from the same
  corrected evidence.
- Live and completed throughput continue to use their established separate
  semantics while consuming corrected rows.
- Codex cost remains labeled as a public API-equivalent estimate.
- The installed runtime is byte-identical to the verified repository sources.
