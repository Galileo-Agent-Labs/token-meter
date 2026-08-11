# DEC-004 — Keep OpenTelemetry as a pure mapping boundary

Date: 2026-08-11  
Status: Accepted for this refactor

## Context

Token Meter is a dependency-free, local-only product that reads private local agent evidence. OpenTelemetry can give its aggregate measurements interoperable names, but an exporter would add a new data-flow, dependency, configuration, retry, queue, endpoint-validation, and privacy surface. The exporter task explicitly requires separate approval.

The implemented seam has two stages:

1. `token_meter/telemetry/privacy.py` constructs a deny-by-default immutable aggregate from explicit safe fields.
2. `token_meter/telemetry/otel_mapping.py` converts only that aggregate to an OpenTelemetry-shaped dictionary.

The mapper pins the GenAI semantic-convention subset to version `1.42.0`. Standard input/output token signals use `gen_ai.client.token.usage`; cache tokens, whole-session active duration, and category-only tool counts use `token_meter.*` names because their meanings do not exactly match a single GenAI client operation.

## Options considered

### In-process optional exporter

This is convenient for a self-contained installation, but it would introduce an optional SDK dependency and make endpoint, queue, retry, timeout, shutdown, and failure isolation part of the application process. It also creates the highest risk that private local evidence is transmitted because of a configuration mistake.

### Localhost collector handoff

This keeps exporter protocols and credentials outside Token Meter and fits OpenTelemetry's collector model. It still requires an approved output action, a validated loopback endpoint, bounded submission behavior, settings, and explicit user-visible state.

### Pure mapping only

This standardizes the safe interchange shape and makes privacy behavior executable without adding any output channel. It preserves the standard-library installation and lets a future exporter choose either transport without changing the internal domain.

## Decision

Choose pure mapping only. Do not add an SDK, exporter, telemetry setting, socket, file sink, or subprocess. The absence of an exporter is intentional, not an incomplete fallback.

An exporter may be reconsidered only with separate approval and must then satisfy all of the following:

- explicit opt-in protected settings and a clear disabled state;
- loopback-only by default, with strict endpoint validation;
- bounded queues, payloads, timeouts, and failure messages;
- no application availability impact when imports or delivery fail;
- submission accepts only the privacy-projected aggregate;
- no prompts, responses, reasoning, tool payloads/names, credentials, accounts, paths, project names, session labels, raw traces, or raw exceptions;
- fake-receiver tests proving disabled-mode and delivery isolation;
- optional packaging that leaves the default server and MCP path dependency-free.

## Consequences

OpenTelemetry simplifies naming and future interoperability, but it does not replace runtime parsing, the Token Meter domain, pricing, quota APIs, budgets, or lifecycle services. Token Meter currently sends no telemetry.
