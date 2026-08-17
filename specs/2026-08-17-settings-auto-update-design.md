# Settings Cleanup and Automatic Updates Design

## Status

Approved for implementation on 2026-08-17.

## Goal

Make software updates reliable for people who use Token Meter primarily from
the native companion, while tightening the Settings page around the actionable
controls and inconsistencies reported in GitHub issue #23.

Token Meter will continue to treat the managed checkout's `main` branch as the
release channel. It will check every ten minutes, optionally install a clean
fast-forward update without dashboard interaction, and surface an available or
failed update in the native menu. The Settings cleanup will normalize section
hierarchy, remove repetitive guidance, clarify model-pricing provenance and
scope, and eliminate the reported narrow-width pricing overflow.

## User Experience

### Software updates

Settings exposes two separate default-on toggles:

- **Check for updates every 10 minutes** controls the existing background Git
  metadata check.
- **Automatically install available updates** controls whether a safely
  installable result starts the updater.

Turning off automatic installation leaves automatic checks enabled. Turning
off automatic checks also turns off automatic installation because an update
cannot be installed automatically without discovery. Re-enabling checks does
not silently re-enable installation; the user makes that second choice.

`Check now` remains available as an explicit action. The existing dashboard
update notice remains a manual installation path.

When an update is available and automatic installation is off, the native menu
shows a first-level **New update available** item. Selecting it starts the
existing guarded update flow directly, without opening the dashboard. During
an update the item reads **Updating Token Meter...** and is disabled. A blocked
or failed update reads **Update needs attention** and opens the Software updates
section in Settings. Successful automatic updates do not send a notification;
the normal menu returns after the services restart.

The native companion retains its last good measurements while an expected
update restart is in progress. It does not replace the menu with a transient
disconnected state.

### Settings hierarchy

The Settings map and visible section titles use identical names:

1. Monthly budget
2. Agent connection
3. Model pricing
4. Language signals
5. Software updates

Every main section title sits inside its owning card. Monthly budget uses the
first budget card as the section entry rather than placing a separate heading
above the card group. Settings cards use the shared Token Meter card surface
without section-specific gradient top borders. Existing shared card atmosphere
elsewhere in the dashboard remains unchanged.

### Agent connection

The section shows one connection state per supported client and one Connect or
Disconnect action. It removes repeated `MCP` pills, duplicate readiness text,
the separate privacy promise banner, the raw tool-name strip, and the expanded
access explanation from Settings.

One concise sentence remains: Token Meter exposes local, read-only usage
metrics and never returns prompts or responses. A **What agents can access**
link opens the relevant explanation in Learn, where conceptual guidance lives.

### Model pricing

The section identifies bundled prices as API-equivalent estimates and exposes
the official Anthropic, OpenAI, and Cursor pricing sources plus a fixed
**Reviewed Aug 17, 2026** date. These are repository metadata, not live network
lookups. The implementation audits every bundled row against primary sources
and changes a price only when exact provider evidence supports it.

The effective-price scope becomes one explicit choice:

- From now
- From date
- All history

The date field is enabled only for **From date**. The selected scope applies to
the next save and the confirmation text uses the same vocabulary.

Edits remain local drafts until one section-level **Save changes** action is
used. Repeated Save buttons are removed from built-in rows. **Restore default**
appears only for a model with a custom override and uses the selected effective
scope. Adding a new custom model remains a separate explicit action.

At narrow widths, each model becomes a stacked pricing card with its model and
provider first, a two-column field grid, and any contextual action below. The
page must not overflow horizontally, and every action must remain reachable by
keyboard and touch.

### Language signals

The section starts with one sentence explaining that the optional phrases label
positive reactions and friction in aggregate local analytics. It removes the
header phrase-count badge, `machine-wide` status copy, and static capacity
guidance. Each textarea shows its own current count and remaining capacity so
the hard limit becomes useful only where the user edits it.

## Update Architecture

The existing update checker, status file, action-token-protected endpoints, and
detached platform update plan remain authoritative.

`updates` settings gain a validated `auto_install` Boolean beside the existing
`enabled` Boolean. Missing values migrate to `true`, so existing default-on
installations receive the requested normal behavior. Writes remain idempotent
and atomic through the existing settings path.

The watcher performs this sequence:

1. If checks are enabled, fetch and compare the managed checkout with its
   tracked `main` upstream.
2. Persist the bounded check result.
3. If the checkout is behind, clean, non-diverged, on `main`, and automatic
   installation is enabled, launch the existing detached updater.
4. Coalesce multiple upstream commits into one fast-forward to the newest
   revision observed in that check.
5. Preserve a failed target revision so the same revision is not launched in a
   tight retry loop. If installation failed after the source fast-forwarded,
   an explicit Retry action reinstalls that same source revision even though
   Git is no longer behind. A newer upstream revision may also resume the
   automatic flow.

Feature branches, dirty checkouts, diverged histories, missing source markers,
and unusable updaters are never modified automatically. The current status API
continues to explain these attention states without returning local paths or
raw command output.

`/menubar` gains one bounded `software_update` object containing display state,
the two preferences, safe revision identifiers, availability, installability,
and existing local action capability. No repository path, remote URL, command
output, or error text enters the native payload.

The dashboard adds a `#settings-updates` route so native attention states can
open the exact section. Existing routes and the `#settings-budgets`
compatibility behavior remain unchanged.

## Model-Pricing Architecture

The pricing resolver and effective-dated history semantics remain unchanged.
Primary-source metadata lives beside the built-in catalog and is projected as
a bounded source label, HTTPS URL, and review date.

The settings endpoint accepts a bounded batch of dirty model changes and
validates the complete batch before one atomic settings write. The existing
single-model payload remains supported for compatibility. A failed row rejects
the batch without partially persisting other rows. Recalculation and cache
invalidation happen once after a successful batch.

## Error Handling

- Disabling checks while installation is enabled persists both settings as off
  and updates both controls in one response.
- A settings write failure restores the prior toggle or pricing state and shows
  one concise inline error.
- An update launch failure remains visible in the dashboard and native menu;
  it does not generate repeated native notifications.
- A failed target remains the visible status while periodic checks find no
  newer revision. The manual Retry path is available when the managed source
  still matches that failed target.
- The updater preserves the existing clean, fast-forward, platform readiness,
  runtime-staging, and rollback contracts.
- If the server briefly disappears during an expected update, the native menu
  retains the last snapshot and update state. An ordinary connection failure
  still uses the existing disconnected behavior.
- Model-pricing validation names the invalid model and field without exposing
  settings paths or unrelated values.

## Accessibility and Responsive Behavior

Both update preferences use native checkboxes with visible labels and concise
supporting text. Dependency changes are announced through the existing status
region. Available, updating, and attention states use text in addition to icon
or color.

Settings headings retain a logical hierarchy. Links and buttons have visible
focus states. Language-signal counts are associated with their textareas.
Responsive checks cover wide desktop, laptop, and narrow mobile widths; the
Settings page and pricing controls must have no page-level horizontal overflow.

## Explicit Non-Goals

- No informational card at the bottom of Settings.
- No daily or user-configurable update cadence; the interval remains ten
  minutes.
- No tagged-release channel, update scheduling window, idle detector, or new
  updater framework.
- No hosted assets, telemetry, or remote pricing fetch.
- No redesign of cost calculation or effective-dated pricing semantics.
- No new top-level dashboard route.

## Test and Verification Strategy

Implementation starts with failing regression tests for:

- independent check and auto-install settings, migration, dependency behavior,
  idempotent writes, and action-token protection;
- automatic watcher launch only for a clean `main` fast-forward result;
- suppression of repeated automatic launch for the same failed revision;
- bounded `/menubar` update state and native available/updating/attention rows;
- direct native installation and the `#settings-updates` deep link;
- stable native measurements through an expected updater restart;
- matching Settings map and section titles, simplified Agent connection copy,
  contextual language-signal counts, and absence of the rejected bottom card;
- batched pricing validation and atomic persistence;
- official pricing-source metadata and every evidence-backed catalog change;
- model-pricing scope vocabulary and narrow-width layout contracts.

The completed change runs the full Python suite, embedded JavaScript parse,
Python compilation, shell checks, Swift compilation and deterministic native
smoke, and `git diff --check`. Browser verification covers wide, laptop, and
narrow widths with no console or overflow errors. The final source is installed
with `./scripts/install`, then `/health`, `/menubar`, both LaunchAgents,
automatic startup, live menu behavior, and source/runtime parity are verified.

## References

- [Settings page UX review, issue #23](https://github.com/splunk/token-meter/issues/23)
- [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [OpenAI model pricing](https://developers.openai.com/api/docs/models/compare)
- [Cursor Composer 2.5 pricing](https://cursor.com/changelog/composer-2-5)
