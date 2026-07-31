---
name: Token Meter
description: A local-first measurement dashboard with one shared Spectrum Instrument Field across every top-level route.
colors:
  ink: "#07090c"
  cool-panel: "#111820"
  cool-panel-raised: "#17212b"
  paper: "#f6f8fb"
  muted: "#a8b3c1"
  faint: "#748195"
  electric-cyan: "#00bceb"
  blue-signal: "#1ba0e1"
  sky-signal: "#7fdbf2"
  positive: "#66d990"
  warning: "#ffb457"
  danger: "#ff6f6f"
  violet-signal: "#c7a7ff"
typography:
  spectrum-display:
    fontFamily: "Tektur Local, Avenir Next Condensed, -apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "clamp(36px, 4.2vw, 54px)"
    fontWeight: 610
    lineHeight: 0.98
    letterSpacing: "-0.035em"
  sessions-title:
    fontFamily: "Tektur Local, Avenir Next, -apple-system, BlinkMacSystemFont, sans-serif"
    fontSize: "22px"
    fontWeight: 610
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  data:
    fontFamily: "ui-monospace, SF Mono, SFMono-Regular, Menlo, monospace"
    fontSize: "18px"
    fontWeight: 720
    lineHeight: 1.15
    letterSpacing: "-0.015em"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 780
    lineHeight: 1.5
    letterSpacing: "0.04em"
rounded:
  compact: "6px"
  standard: "8px"
  pill: "999px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "16px"
  lg: "24px"
components:
  action-button:
    backgroundColor: "{colors.electric-cyan}"
    textColor: "{colors.paper}"
    typography: "{typography.label}"
    rounded: "{rounded.standard}"
    padding: "7px 10px"
  text-field:
    backgroundColor: "{colors.cool-panel}"
    textColor: "{colors.paper}"
    typography: "{typography.body}"
    rounded: "{rounded.standard}"
    padding: "9px 11px"
    height: "38px"
  dashboard-card:
    backgroundColor: "{colors.cool-panel}"
    textColor: "{colors.paper}"
    rounded: "{rounded.standard}"
    padding: "16px"
  segmented-active:
    backgroundColor: "{colors.electric-cyan}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.compact}"
    padding: "9px 13px"
  session-instrument-card:
    backgroundColor: "{colors.cool-panel}"
    textColor: "{colors.paper}"
    rounded: "{rounded.standard}"
    padding: "19px 20px 16px"
  session-status-instrument:
    backgroundColor: "{colors.cool-panel-raised}"
    textColor: "{colors.paper}"
    rounded: "{rounded.standard}"
    padding: "18px"
---

# Design System: Token Meter

## Overview

**Creative North Star: "Spectrum Instrument Field"**

Token Meter is a dense, local operator dashboard. Its incumbent world is cool and technical: near-black layers, off-white type, cyan measurement signals, compact controls, and restrained depth. The system favors legible evidence, clear state, and reusable dashboard primitives over decorative storytelling.

That system now spans the whole dashboard. Every top-level route uses the same ink surface, layered spectrum header, cyan active controls, cool-gradient cards, and compact fields. Per-route atmospheric blends vary within the original Token Meter cyan, blue, sky, violet, and orange palette, while Sessions keeps the unique data-bearing context horizon and live-run instrument cards.

**Key Characteristics:**

- Dense, factual dark-mode operation with tabular numeric readouts.
- Cyan, blue, and sky are shared Token Meter signals, expressed through reusable page, header, card, control, and active-state gradients.
- Every route shares one header geometry and component language; only atmospheric emphasis and task-specific data composition vary.
- Sessions uses a data-bearing context horizon rather than ornamental dials.
- Provider colors remain small identity signals inside the shared system.
- Motion is brief, stateful, and removed when reduced motion is requested.

## Colors

The palette is one cool Token Meter system. Cyan-to-sky is the common action and measurement range; violet and orange add restrained atmosphere without changing semantic meaning.

### Primary

- **Electric Cyan:** The primary action, focus, chart, active-state, and Sessions context signal throughout Token Meter.

### Secondary

- **Blue Signal:** The middle of active-control and data gradients, bridging electric cyan to sky light without introducing a second palette.
- **Sky Signal:** The luminous readout and gradient endpoint used in shared headers, active controls, and the Sessions context horizon.
- **Violet Signal:** A restrained atmospheric accent used inside spectrum gradients, never as a dominant action color.

### Tertiary

- **Positive, Warning, and Danger:** Semantic state colors retain their meaning across routes. Warning orange can join a spectrum gradient only where it does not masquerade as a warning state.

### Neutral

- **Ink, Cool Panel, and Cool Panel Raised:** The global page and surface stack.
- **Paper, Muted, and Faint:** The global text hierarchy from primary reading to metadata.

### Named Rules

**The Shared Spectrum Rule.** Page atmosphere, headers, cards, controls, active states, and focus treatments come from the shared spectrum tokens. Routes may tune only the atmospheric blend; they must not fork these primitives.

**The Evidence Color Rule.** Provider and semantic colors identify source or state in small, explicit signals. Atmospheric violet or orange never overrides warning, danger, or provider identity.

## Typography

**Display Font:** Tektur Local (with Avenir Next Condensed and system sans fallbacks)

**Body Font:** System sans (with Segoe UI, Inter, and system-ui fallbacks)

**Label/Mono Font:** System sans for labels; UI monospace (with SF Mono and Menlo fallbacks) for measured values

**Character:** Body copy stays compact and native to macOS, while numbers use stable tabular forms. Tektur gives every top-level route a precise retro-future display voice without displacing the system type inside working content.

### Hierarchy

- **Spectrum Display** (610, fluid 36–54px, 0.98): The route title or selected Sessions task name in the shared spectrum field header; it falls to 30px on narrow screens.
- **Sessions Title** (610, 22px, 1.2): Task names on session instruments; narrow cards use 19px and clamp to two lines.
- **Body** (400, 14px, 1.5): Global interface copy, explanations, and supporting evidence.
- **Data** (720, 18px, 1.15): Cost, context, speed, and related numeric readouts; use tabular numerals.
- **Label** (780, 11px, 0.04em): Compact measurement labels and status metadata, usually uppercase only when the label is functional.

### Named Rules

**The Task Leads Rule.** Session detail replaces a generic page title with the actual task name; provider, project, model, and controls remain subordinate.

**The Measured Numbers Rule.** Cost, context, speed, times, and identifiers use the mono stack and tabular numerals so values align while updating.

## Layout

The global dashboard sits in a centered 1320px container with 24px horizontal padding and a sticky top bar. Its default rhythm is built from 8px, 12px, 16px, and 24px intervals. Each top-level route opens with the same responsive spectrum field header; route-specific controls can occupy its trailing edge.

Sessions opens with one layered spectrum field header, then a two-column instrument grid with a 12px gap. At 700px and below the grid becomes one column. On detail, the working chart and decision instrument form an asymmetric two-column layout; it narrows at 1120px and becomes a single column at 900px. Supporting KPIs form an attached ledger: three columns below 1120px and two columns below 520px.

At 520px, shared header padding tightens, the spectrum geometry scales down, and trailing controls stack below the title. In Sessions, task titles clamp to two lines, the metric ledger wraps its third readout across the full width, and tabs scroll horizontally.

### Named Rules

**The Orientation Rule.** Overview and detail share the same layered header, task naming, cyan context signal, and instrument grammar so opening a run feels like focusing the same instrument rather than entering a different product.

## Elevation & Depth

The dashboard uses tonal layering plus two soft shadow levels for cards, menus, and floating affordances. Shared cards gain atmosphere through one radial-and-linear gradient recipe. Every top-level header adds a conic spectrum field, while the Sessions context gauge alone carries a cool ambient glow because its horizon encodes live pressure.

### Shadow Vocabulary

- **Dashboard Surface:** A fine top highlight over a broad 18px by 48px shadow for substantial incumbent cards.
- **Dashboard Soft:** A fine top highlight over a 10px by 28px shadow for compact incumbent surfaces.
- **Session Hover:** A 0 18px 38px black shadow used only while a session instrument is hovered.
- **Spectrum Gauge Glow:** A 0 24px 58px cool shadow tied to the context-pressure horizon.

### Named Rules

**The Operational Depth Rule.** Shared depth comes from soft shadows and restrained gradients. Do not add ornamental layers that look measurable but carry no evidence.

## Shapes

The Token Meter system uses compact 8px corners for cards, controls, fields, and tooltips, with 6px inner controls and pills reserved for statuses and filters. Sessions keeps these incumbent radii. Its distinctive shape is the large circular context horizon inside the detail status instrument, not a repeated card dial or calibration motif.

**The Data-Bearing Geometry Rule.** Circular geometry is reserved for the live context horizon or other evidence-backed visualizations; session cards remain rounded operational surfaces without a decorative dial.

## Components

### Buttons

- **Shape:** Actions use compact 8px containers with 6px inner controls.
- **Primary:** Actions use a subtle cyan-tinted dark fill with 7px by 10px padding. Selected top-level and segmented controls share one cyan-to-blue-to-sky gradient with dark ink text.
- **Hover / Focus:** Cyan controls strengthen their cyan border or fill. Keyboard focus remains explicit and at least 2px on session instruments.
- **Danger:** Preserve the semantic danger treatment rather than converting destructive actions to cyan.

### Chips

- **Style:** Global provider badges use compact 6px corners and provider-specific text, border, and low-opacity fill. Pills are reserved for compact status and filtering affordances.
- **State:** Inside Sessions, provider colors are identifiers only; there is no provider-colored left rail.

### Cards / Containers

- **Corner Style:** Global cards and Sessions instrument plates use compact 8px corners.
- **Background:** Shared cards use cool near-black gradients with restrained cyan and violet radial light. Sessions cards tune the same recipe for live-run emphasis.
- **Shadow Strategy:** Both global surfaces and Sessions cards use the dashboard shadow vocabulary; Sessions hover strengthens the lift.
- **Border:** One-pixel low-contrast borders globally; Sessions uses a low-opacity cyan border without a provider rail.
- **Internal Padding:** Global panels commonly use 16px. Session cards use 19px 20px 16px at wide widths and 16px 15px 13px at narrow widths.

### Inputs / Fields

- **Style:** Compact 38px fields use a translucent cool-panel fill, 8px corners, and a one-pixel neutral border.
- **Focus:** Shift the border to cyan and add a restrained three-pixel cyan focus halo.
- **Disabled:** Reduce opacity while retaining the text and border structure; unavailable evidence must not resemble a measured zero.

### Navigation

Global top-level navigation remains compact and preserves the product order. Top-level and route-local segmented controls share the same active gradient, hover, and focus treatments. At narrow widths the Sessions panel row scrolls horizontally rather than wrapping or hiding destinations.

### Session Instrument Card

Each card is one selectable, reorderable instrument plate. Provider/runtime and activity status sit at the top without a colored left rail; the task name is the primary identity; cost, context, and speed share an aligned three-readout ledger. Context history is rendered as a small cyan bar horizon. Hover lifts the plate by 3px and focus uses a visible cyan outline. Cards use layered operational gradients but no decorative dial.

### Spectrum Status Instrument

The detail-side instrument pairs live state and estimated cost with context pressure. A large circular field fills to the current context percentage, and the exact percentage is repeated in a standard progress bar. The horizon line is data-bearing; there are no calibration ticks or ornamental labels that imply unsupported precision.

## Do's and Don'ts

### Do:

- **Do** change shared spectrum tokens and primitives when a treatment should move across every route.
- **Do** lead Sessions with the task name and the three decision readouts: cost, context, and speed.
- **Do** use layered radial, linear, and conic gradients with restrained violet and orange accents across the dashboard.
- **Do** preserve semantic and provider colors as small, factual signals.
- **Do** collapse grids and remove nonessential decoration at the implemented 900px, 700px, and 520px breakpoints.
- **Do** disable decorative motion under `prefers-reduced-motion` and retain visible keyboard focus.

### Don't:

- **Don't** fork page headers, cards, controls, or active states into route-specific copies.
- **Don't** turn Sessions into interchangeable dark cards with generic headings.
- **Don't** add a provider-colored left rail, Japanese notation, calibration ticks, a card dial, or a route-specific dot field to Sessions.
- **Don't** let atmospheric violet or orange replace semantic danger, warning, live-state, or provider identity colors.
- **Don't** add hosted imagery, decorative assets, or typography that weakens Token Meter's local-only, evidence-first operation.
