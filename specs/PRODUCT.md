# Product

Current engineering boundaries are documented in
[ARCHITECTURE.md](ARCHITECTURE.md). This file owns product and experience
principles only.

<!-- impeccable:product-schema 1 -->

## Platform

Local browser dashboard on macOS, Linux, and Windows, with platform-native
menu-bar, tray, or notification-area companions.

## Users

People running Claude, Codex, Cursor, OpenCode, or Kiro sessions who need to
understand a live run and manage machine-wide usage without exporting their
work.

## Product Purpose

Token Meter reads local agent traces and presents bounded, clearly labeled usage estimates, context pressure, execution evidence, provider-reported quota facts, and monthly budget status. Success is a quicker, more trustworthy decision about whether to continue, intervene, or investigate a run.

## Positioning

It is a local-first instrument: the dashboard and native companion derive useful evidence from local traces while keeping prompts, responses, credentials, and raw traces off the surface.

## Operating Context

The browser dashboard supports deeper review. The macOS menu bar, Linux tray,
and Windows notification-area companions provide a fast check of the current or
pinned session. Runtimes expose different evidence, so unavailable metrics stay
unavailable rather than being presented as zero.

## Capabilities and Constraints

- Dependency-free Python server, local-only dashboard, and platform-native
  compact companions.
- Costs and selected token metrics can be estimates and must retain that label.
- Provider limits are narrow, read-only, and shown only when reported; stale and unavailable states are meaningful.
- Monthly machine-wide budgets remain in Settings, with a native deep link.
- The menu bar retains compact title preferences, quota notifications, recent-session pinning, and active-session tracking.

## Brand Commitments

Token Meter should feel like an original, precise measurement tool rather than an account console or a clone of another menu-bar app. It uses an evidence-first dark instrument vocabulary and native macOS interaction.

## Evidence on Hand

Local session metadata, aggregate token/cost/context measurements, execution summaries, bounded provider quota snapshots, monthly budget state, and the existing native smoke payload. No remote analytics, user accounts, credits, or raw trace content are available for display.

## Product Principles

- Put the next operating decision before the metric inventory.
- Prefer bounded trace-backed signals over ornamental data visualization.
- Make uncertainty, estimates, and freshness visible.
- Preserve privacy by design and by payload contract.
- Keep deep configuration in the dashboard while making the menu bar immediately useful.

## Accessibility & Inclusion

Use system typography, visible keyboard focus, textual labels in addition to color, concise non-jargon copy, and reduced-motion-safe transitions.
