# Changelog

## 3.11.5 - 2026-07-15

- Integrate the offline runtime and Cursor adapter with exact-recipient draft
  verification, bounded reply-state reporting, and provider-specific Sent
  mailbox resolution.

## 3.11.4 - 2026-07-11

- Add separate Cursor plugin and local MCP adapters to the offline release payload.
- Keep the Cursor launcher draft-safe and version-synchronized with the Claude and Codex adapters.

## 3.11.3 - 2026-07-11

- Add a hash-locked offline wheelhouse for the macOS arm64 CPython 3.13 plugin release channel.
- Make the plugin launcher fail closed instead of downloading runtime dependencies.

All notable changes to **apple-mail-mcp** (PyPI: `mcp-apple-mail`) are documented
here. The plugin/MCPB/marketplace versions track this file.

## Unreleased

## 3.11.3 - 2026-07-11

### Fixed

- **Compose draft smoke verification now requires the exact persisted To
  recipient set and uses an identity-guarded cleanup transaction.** A recipient
  mismatch or Exchange Drafts ID drift now retains the artifact instead of
  risking deletion of another draft.
- **Reply-state Drafts scans stay bounded at 50 without claiming false
  negatives.** When a capped scan omits older drafts, matching rows remain
  `true` and nonmatches return `null` (`unknown`). The performance check now
  scales its mailbox metadata threshold from the mailbox response envelope.
- **Identity-guarded cleanup no longer refuses to delete a verified smoke
  draft whose recipient list contains duplicates.** The delete transaction
  now proves exact recipient-set equality by mutual containment instead of a
  count comparison that mismatched against the deduplicated expected list.
- **A Drafts snapshot with an unreadable mailbox-wide total now fails open.**
  A missing `TOTAL` marker is treated as a truncated scan, so nonmatches
  report `null` (`unknown`) rather than a definitive `false`.
- **Every `draft_scan` producer now emits the same envelope.** `total` and
  `truncated` appear on `get_needs_response`, the inbox skipped/error paths,
  and the empty-scan early return, matching the annotated list/search
  responses; skill references and conventions docs describe the three-state
  `has_draft` semantics including truncation.
- **The identity-guarded delete AppleScript is covered by the osacompile
  parse gate.** The script moved into a discoverable `_script()` builder, and
  an account-resolution failure now returns the helper's structured JSON
  error shape instead of a raw string.
- **Recipient normalization is unified on one casefolding helper** shared by
  draft verification, the smoke CLI's exact-set check, and the cleanup
  identity literal, so Unicode addresses compare identically at every stage.

## 3.11.2 - 2026-07-11

### Fixed

- **Native reply verification now uses a persisted, header-linked Drafts
  identity.** `reply_to_email` never treats Mail's transient outgoing-message
  id as a Drafts id. After saving, it takes a complete bounded Drafts snapshot,
  requires exactly one new persisted message, and requires that message's RFC
  `Message-ID` and `In-Reply-To` link it to the source. Only then does it emit
  `Draft ID` plus the internal identity capsule, verify that exact artifact, or
  permit an automatic delete-and-retype. The verifier and deletion path both
  revalidate the capsule. Cap limits, indexing delay, ambiguity, or identity
  drift fail closed: fallback may report an artifact, but never authorizes
  deletion or retyping.
- **`verify_draft(expected_body_contains=...)` no longer mistakes ordinary
  authored `wrote:` prose for quoted text.** Quote scoping now recognizes an
  Apple Mail `On <date>, ... wrote:` attribution, Outlook's structured header
  block, or the Outlook original-message separator. If none is present, the
  expectation checks the whole body preview.
- **Native reply AppleScript is now explicitly compiled in the test suite.**
  This covers the helper-prefixed native builder and its focus-guarded chunked
  typer, which generic builder discovery does not select.

## 3.11.1 - 2026-07-10

AGENTIC-1214 reply drafting correctness: chunked native typing, full-body
draft verification, and honest threading contracts.

### Fixed

- **AGENTIC-1214: `reply_to_email` native reply body no longer truncates or
  types in ALL CAPS.** The native reply path (`native_format=True`) previously
  inserted the entire `reply_body` with one System Events `keystroke` call,
  which silently dropped the tail of long bodies around 320-480 characters
  and could leak leftover shift state into ALL-CAPS output on short bodies.
  It now types the body in small, focus-guarded chunks (`TYPING_CHUNK_SIZE`,
  `TYPING_INTER_CHUNK_DELAY`), releasing keyboard modifiers before and after
  every chunk and re-checking both Mail's own front window and System
  Events' process-level focus before each chunk, aborting immediately
  (never re-stealing focus) on a mismatch instead of typing into whatever
  now holds focus. A mid-typing abort discards the partially typed compose
  window and returns the new `REPLY_BODY_TYPING_INTERRUPTED` structured
  error, distinct from the pre-typing `REPLY_WINDOW_FOCUS_FAILED` /
  `REPLY_SUBJECT_GUARD_MISMATCH` abort codes. The native-path AppleScript
  timeout now scales with the projected chunk-typing time (floored at
  120s); a body long enough to exceed the documented typing budget is
  refused up front with `REPLY_BODY_TYPING_BUDGET_EXCEEDED` instead of
  risking a mid-typing timeout.
- **AGENTIC-1214: post-save reply verification now checks the full body, not
  just its first line.** The saved-draft verifier previously matched only
  the first non-empty line of `reply_body`, so a truncated or miscased tail
  could still pass. It now compares the FULL body against the saved draft
  above the quoted original: whitespace-flattened, smart-punctuation-folded,
  sentence-start case neutralized (so Mail's own autocapitalization cannot
  cause a false mismatch), located first under `considering case` so a body
  that itself contains "wrote:" cannot false-fail into "after quote", then
  compared case-sensitively so an ALL-CAPS draft still fails. On a
  `body_missing` mismatch with a concrete artifact id, `reply_to_email`
  automatically deletes the artifact and retypes the identical body once
  before re-verifying; a mismatch that persists (or an unconfirmed delete)
  returns the new `REPLY_BODY_MISMATCH` structured error naming the suspect
  Drafts artifact id, with `retyped` and `stale_artifact_id` remediation
  fields. The success payload and text output gained `body_verified`,
  `retyped`, and `stale_artifact_id`. This verification (and its automatic
  retype) only runs for `mode="draft"` / `mode="open"`; a `mode="send"`
  native reply still gets the chunked-typing fix above but has no saved
  Drafts artifact left to verify afterward, so draft-then-verify-then-send
  stays the safe sequence when typed-body correctness matters.
- **AGENTIC-1214: `manage_drafts(action="create")` no longer silently drops
  `in_reply_to`.** Passing `in_reply_to` to `action="create"` now returns a
  structured `CREATE_CANNOT_THREAD` error before any AppleScript runs and
  before the standalone reply-like guard, since the Mail scripting
  dictionary exposes no header property on a new outgoing message and
  `create` can never set In-Reply-To/References. `in_reply_to` remains
  honored only by `action="find"`. The remediation points at
  `reply_to_email(message_id=...)` to thread a reply, or
  `manage_drafts(action="find", in_reply_to=...)` to locate an
  already-saved reply draft.
- **`manage_drafts(action="create")`'s standalone reply-like guard now names
  the tool that was actually called.** `_standalone_compose_thread_warning`
  previously always said "compose_email" in its error message even when
  `manage_drafts(action="create")` triggered it; it now names the calling
  tool.
- **Draft-id instability on Exchange is now documented.** `manage_drafts`'s
  `action` and `draft_id` docstrings now note that server-account Drafts
  numeric ids are reassigned on sync (observed drifting between two
  `action="list"` calls with zero writes in between) and are not a stable
  handle across turns; `action="find"` with `in_reply_to` is the durable
  handle for a reply draft.
- **Native typing timeout projection now models per-chunk overhead.** The
  scaled AppleScript timeout accounts for the per-chunk focus re-check and
  keystroke cost (`TYPING_PER_CHUNK_OVERHEAD_SECONDS`), not just the
  inter-chunk delay, so long reply bodies no longer risk `AppleScriptTimeout`
  killing osascript mid-typing and stranding a partially typed compose window.
- **The automatic retype never deletes a draft that is not provably ours.**
  The delete-and-retype retry now requires the verifier's mismatch artifact id
  to equal the draft id Mail itself returned for this compose call; under
  Exchange eventual-consistency lag the subject-scan fallback could otherwise
  name a pre-existing same-subject draft the user wrote, and the retry would
  have deleted it. When ids differ, the tool returns `REPLY_BODY_MISMATCH`
  naming the suspect id and deletes nothing.
- **Tab characters in `reply_body` are converted to spaces on the native typed
  path.** A typed tab is a field-navigation key that can move focus out of the
  compose body mid-draft; the conversion happens before the body temp file is
  written and is compare-neutral in verification (which flattens all
  whitespace on both sides).
- **The reply-draft verifier's sentence-start case fold now scales with
  sentence count instead of body length.** The previous per-character
  AppleScript walk was quadratic over the full draft content, so replies on
  long quoted threads could exhaust the verification timeout and mask real
  body mismatches as `verification_timeout`.
- **AGENTIC-1192 item 2: `verify_draft` / `verify_drafts` no longer false-pass
  `expected_body_contains` on quoted text.** The needle is now scoped to the
  reply body above the first quote boundary; when it appears only inside the
  quoted original the payload carries `body_needle_only_in_quote: true` and an
  `expected_body_only_in_quote` warning instead of a pass.

### Known limitations (found in the 2026-07-10 live verification, fail closed)

- **Accented and composed characters can corrupt during native typing.**
  Observed live: "Renée" saved as "Renae" (System Events keystroke layer or
  Mail autocorrect; smart quotes, em dashes, and ellipsis typed correctly).
  The full-body verifier catches this and returns `REPLY_BODY_MISMATCH`
  naming the artifact instead of silently saving a corrupted draft. Until the
  typing-fidelity follow-up ships, prefer ASCII spellings in `reply_body` on
  the native path.
- **The automatic retype engages only when Mail exposes the compose draft id
  and the verifier resolves the same id.** On Exchange, post-save id capture
  can fail or drift, in which case the tool skips the delete-and-retype (it
  never deletes a draft it cannot prove it created) and returns
  `REPLY_BODY_MISMATCH` with the suspect id for manual cleanup.
- **A focus steal landing mid-keystroke can corrupt the typed body without
  tripping `REPLY_BODY_TYPING_INTERRUPTED`** (the per-chunk guard checks
  before each chunk, not during one). Verification still fails closed with
  `REPLY_BODY_MISMATCH`; the caller deletes the named artifact and retries.
- **`verify_draft`'s `body_preview` is capped at 5000 characters** (a
  pre-existing cap, unchanged here), so `expected_body_contains` needles for
  replies longer than that must target the body prefix, not the tail.
  `reply_to_email`'s internal full-body verifier has no such cap.

## 3.11.0 - 2026-07-10

Automatic reply-state annotation: every primary read and triage tool now reports
whether an email was already answered or already has a reply draft, with no
opt-in flag required, so agents never double-draft a reply that exists.

### Added

- **`was_replied_to` on every discovery row** (always present, no parameter
  gates it): `list_inbox_emails`, `search_emails`, `get_email_by_id`,
  `get_email_by_ids`, `get_email_thread`, `get_needs_response`,
  `inbox_dashboard`, and `get_inbox_overview` recent rows now read Mail's
  native `was replied to` flag inside the existing per-message AppleScript
  property pass (measured ~15ms/message marginal, no extra round trip).
- **`has_draft` on the same rows** (`true` / `false` / `null`; `null` means
  the draft scan was skipped or errored, never silently false): one bounded
  Drafts-mailbox snapshot per account per call (`DRAFT_LOOKUP=75` cap,
  ~2s flat) correlates drafts to candidates by In-Reply-To/References header
  match (headers read for the newest `DRAFT_SNAPSHOT_HEADER_CAP=10` drafts)
  or by normalized-subject equality plus draft-recipient equals sender plus
  draft date not before the email. Governed by a per-tool
  `include_draft_state: bool = True` escape hatch; annotation is automatic
  by default. JSON responses carry a top-level `draft_scan` status object;
  text modes append `[REPLIED]` / `[HAS DRAFT]` markers.
- **`core/reply_state.py`**: shared snapshot builder, correlation rule, and a
  localized Drafts-mailbox name resolver ("Drafts", "Brouillons",
  "Entwürfe", "Borradores"), the fallback treatment Inbox and Sent
  mailboxes already had.
- **`exclude_drafted: bool = False`** on `list_inbox_emails` and
  `search_emails` alongside the existing `exclude_replied`.
- **`include_drafted: bool = False`** on `get_needs_response`.

### Changed

- **`get_needs_response` now excludes already-handled mail by default**: rows
  with `was_replied_to=true` or `has_draft=true` are skipped and reported via
  visible `skipped_replied_count` / `skipped_drafted_count` fields.
  `include_already_replied=True` / `include_drafted=True` restore them,
  annotated. On a draft-scan error nothing is excluded for draft state
  (fail-open) and `draft_scan.status` reports `"error"`. The legacy
  `check_already_replied` Sent-header scan remains as an opt-in extra
  verification layer.
- **`exclude_replied` on `list_inbox_emails` / `search_emails`** now filters
  on the native flag instead of a Sent-mailbox header scan (faster, no scan
  cap interaction). `flag_replied` is deprecated but still accepted; the
  canonical field is `was_replied_to`.
- **Skills and references** (`pre-draft-verification`, `recent-first-triage`,
  triage/drafting/management skills) now teach the row-level
  `was_replied_to` / `has_draft` check as the primary pre-draft duplicate
  guard, with the thread check as fallback.
- `get_awaiting_reply` is intentionally unchanged: it tracks the opposite
  direction (did they reply to me), for which no native Mail property exists.

## 3.10.1 - 2026-07-10

### Changed

- **`email-archive-cleanup` skill: Human-Sender Screen.** The archiving skill now
  applies a conservative human-sender filter at the dry-run/preview stage before
  any message becomes an archive candidate. It never archives mail from a real
  person the user corresponds with unless it is confidently spam; archiving is
  reserved for promotional and marketing mail, newsletters, automated updates and
  notifications, receipts, order/shipping/calendar/system notices, and obvious
  spam. When the sender's nature is uncertain, the safe default is to leave the
  message visible in the inbox rather than archive it.

## 3.10.0 - 2026-07-10

Apple Calendar tool surface: 10 new MCP tools (41 total), 2 new workflow skills
(11 total), and a hybrid calendar engine, all behind the same safety doctrine as
the mail surface.

### Added

- **10 Apple Calendar tools**: `list_calendars`, `list_events`, `get_events_by_id`,
  `check_availability`, `create_event`, `batch_create_events`, `update_event`,
  `delete_events`, `manage_calendars`, and the `respond_to_invitation`
  documented-refusal shim (no public macOS API can RSVP).
- **Hybrid calendar engine**: Calendar.app AppleScript via the shared
  `run_applescript` lock is the guaranteed engine on every install surface; an
  optional EventKit read fast path (`pip install 'mcp-apple-mail[eventkit]'`)
  activates only when Calendars full access is already granted and never
  triggers the consent prompt from a tool call.
- **Bounded-read contract for calendars**: every event read requires a capped
  window (370-day width cap, 200-event return cap with paging, 750-occurrence
  recurring expansion ceiling, 20-calendar fan-out cap, and an aggregate
  240-second per-call budget with partial results). Central caps live in
  `constants.CALENDAR_BOUNDS`.
- **New mode gating for calendars** (stricter than the mail tools by design):
  `--read-only` removes every calendar write tool; `--draft-safe` additionally
  blocks calendar deletes (`CALENDAR_DELETE_BLOCKED`, env unlock
  `CALENDAR_ALLOW_DESTRUCTIVE=1`) and attendee invitation sends
  (`INVITE_SEND_BLOCKED`). Mail tool gating is unchanged; the server
  instructions now document the domain split.
- **Safety doctrine**: ID-first mutations with no fuzzy destructive selectors,
  dry-run-default deletes that abort on any unresolved id, a triple-gated
  calendar delete (preview, confirm, force), recurring mutations requiring
  `span='all_occurrences'`, allowlisted RRULE grammar, and attendee writes
  gated behind explicit `send_invitations=True` with
  `invitation_delivery: "platform_dependent"` disclosure.
- **Timezone correctness**: IANA `timezone` parameters everywhere, dual
  zone-local plus UTC output, and integer-component AppleScript date
  interpolation (no locale string coercion).
- **2 new workflow skills**: `calendar-operator` (bounded reads, ID-first
  mutations, TCC troubleshooting) and `meeting-scheduler` (find-slot workflow,
  cross-timezone discipline, the .ics-via-Mail invitation alternative), plus
  the shared `calendar-safety-limits.md` reference.
- **CLI**: `apple-mail calendars`, `apple-mail calendar-events`, and
  `apple-mail calendar-grant` (the only code path allowed to request EventKit
  access; human-run, terminal only, permission-specific exit codes).

### Fixed

- **Cross-engine event ids now round-trip** (default `auto` mode): the EventKit
  read engine reports `calendarItemIdentifier` (the value Calendar.app AppleScript
  exposes as `uid` on every account type, verified live against Google-CalDAV,
  iCloud, and local stores) as `event_id`, not `calendarItemExternalIdentifier`
  (a `...@google.com` / hex id that never resolved through the AppleScript writer).
  A create id now round-trips to `get_events_by_id` / `update_event` /
  `delete_events` under the shipped default; the external id is preserved as a
  secondary `external_id` payload field.
- **Recurring delete never reports an unverified whole-series success**:
  Calendar.app scripting cannot delete a whole recurring series (its `delete`
  removes only the targeted occurrence and rule-clearing is silently ignored,
  proven live). `delete_events` now re-queries the series after deleting and returns
  the structured `RECURRING_DELETE_INCOMPLETE` (with the surviving occurrence dates
  and a Calendar.app remediation) when occurrences survive, instead of the previous
  false `recurring_deleted_whole_series: true`. `update_event` still mutates the
  whole series (that path works and is verified live).
- **`manage_calendars(action="delete")` works**: the delete script now uses the
  inline `delete (first calendar whose name is ...)` specifier, which deletes
  cleanly (including non-empty calendars whose events cascade away); the previous
  variable-bound `delete targetCal` form failed live with `AppleEvent handler
  failed`. Generic Calendar.app write failures now surface the structured
  `CALENDAR_WRITE_FAILED` error instead of raw `Error:` text.
- **All-day create echo instant**: `create_event` / `batch_create_events` all-day
  responses now echo the host-local calendar-date midnight actually stored (so the
  echo matches a later `get_events_by_id`), instead of the requested-zone midnight
  instant, which for a far-east or far-west zone described a moment hours away from
  the stored event.
- **All-day timezone date shift**: all-day events now land on the requested
  calendar date in the requested zone instead of the host-local conversion of
  midnight-in-zone. Previously an all-day request in a zone far east or west of
  the Mac could roll the date back or forward one day (`create_event`,
  `batch_create_events`, and `update_event` all-day paths).
- **Delete access-denied is no longer soft**: an Automation-denied
  (`-1743`/not authorized) `delete_events` now raises the structured
  `CALENDAR_ACCESS_DENIED` remediation like create/update, instead of reporting
  a "successful" empty delete.
- **Recurring write lookup**: `update_event` and `delete_events` widen the
  write-side uid lookup for recurring targets back by the 400-day recurring
  lookback horizon (still date-bounded), so a standing series whose master
  started before the read window no longer spuriously returns `EVENT_NOT_FOUND`.
- **Attendee-removal honesty**: `update_event` no longer reports
  `attendees_changed`/`invitation_delivery` for a removal-only or empty
  attendee diff (Calendar.app scripting cannot remove attendees); it returns an
  explicit "attendee removal is unsupported" note instead.

### Changed

- **Recurring coverage disclosure**: `list_events` and `check_availability`
  now surface `recurring_lookback_days` and a `recurring_coverage_note` when the
  AppleScript recurring-master pass runs, so callers know standing series older
  than the 400-day horizon may be missing (the EventKit engine expands
  natively and carries no such note).
- **Honest `output_format="text"`**: `get_events_by_id`, `update_event`,
  `delete_events`, `batch_create_events`, and `manage_calendars` now emit
  compact text summaries in text mode instead of pretty-printed JSON, matching
  `list_events`/`list_calendars`/`check_availability`.
- **Docstrings**: `list_events` states the 280-char `notes_preview` query match
  limit and the recurring lookback horizon; `update_event`/`delete_events`
  document the recurring-target lookup requirement and all-day moves.

### Notes

- Invitation delivery and RSVP are platform gaps, not omissions: no public
  macOS API guarantees invitation transmission, and EventKit participant
  status is read-only. Both are documented in the tools and skills.
- `DEFAULT_CALENDAR` (env) sets the create target; unscoped reads fan out
  across calendars (capped), which deliberately differs from mail's
  account-scoping default and is documented in each fan-out tool.
## 3.9.4 - 2026-07-10

Bundled-skill guidance accuracy pass. A parallel review of all nine workflow
skills against the live tool signatures found copy-paste examples that would
fail or churn on first use; this release corrects them. No tool-surface or
behavior change (still 31 tools).

### Fixed

- **`get_top_senders(limit=...)` examples corrected to `top_n=...`.** The tool
  has no `limit` parameter, so the documented call raised a `TypeError`. Fixed
  in the mail-rules-advisor, email-management, and mailbox-taxonomy skills.
- **`get_email_thread` / `get_email_by_id` / `get_email_by_ids` examples now
  pass the required `account`.** Unlike `search_emails` / `list_inbox_emails`,
  these three tools have no `DEFAULT_MAIL_ACCOUNT` fallback, so examples that
  omitted `account` failed with a missing-argument error. Fixed across the
  drafting, style-profile, management, triage skills, the shared
  `exchange-account-patterns` reference, and the CLI `show` example.
- **`list_inbox_emails` result now read via the `emails` key, not `items`.** A
  cleanup template read `result["items"]` off a `list_inbox_emails` result
  (its shape is `{"emails": [...]}`), which raised a `KeyError`.
- **`get_inbox_overview` example scoped with an explicit `account`.** That tool
  does not honor `DEFAULT_MAIL_ACCOUNT` and otherwise fans out across every
  configured account, so the triage skill now passes `account` and notes the
  behavior.
- **Dropped the stale "list rows may lack `message_id`" caveat.** List output
  always includes `message_id`, so the guidance to re-resolve via an extra
  `search_emails` round-trip was removing a bounded fast path.
- **`get_statistics` docstring corrected** to the current per-mailbox cap of 50
  messages (was "10 mailboxes by 75; longer windows 20 by 250").
- Minor skill fixes: `skills/CLAUDE.md` reference-sync table completed, an
  inconsistent `recent_days` ladder example, an inflated `max_moves` example,
  and a mis-attributed `max_moves` cap on `manage_trash`.

## 3.9.3 - 2026-07-09

Safe-by-design bounded mail access. Every scan, search, and export is now hard
capped so a single tool call can never trigger a Mail.app cold-cache read storm
or 98% CPU spin on a large Exchange or Gmail inbox (tool count unchanged at 31).

### Changed

- **Hard scan ceilings on `search_emails` and `list_inbox_emails`.** A single
  call now scans at most 50 messages regardless of `limit`, `recent_days`, or
  window size. The underlying `SCAN_BOUNDS` were lowered across the board
  (base, window, per-day scaling, and body-search auto caps) so large inboxes
  no longer force thousands of uncached message reads per call.
- **`get_statistics` per-mailbox reads are hard capped** at the same 50 message
  ceiling for both short and long windows. Longer windows fan across more
  mailboxes instead of reading deeper into any one of them.
- **`export_emails` is bounded and cannot exceed 50 emails per call.** Requests
  above the cap (via `max_emails` or an over-long `message_ids` list) are
  rejected before Mail.app runs. `entire_mailbox` exports a paged slice
  (default 25) rather than listing the whole mailbox.
- **`full_inbox_export` is disabled.** It now returns a structured
  `UNBOUNDED_EXPORT_DISABLED` error that redirects to the bounded
  `export_emails`, `list_inbox_emails`, and `search_emails` tools. The tool
  stays registered so existing configs keep loading.
- **AppleScript calls are serialized through one process-wide lock.** Parallel
  (concurrent) Mail tool calls now queue instead of contending for Mail.app,
  and internal fan-out that previously issued parallel Mail queries runs
  sequentially. Server instructions and the bundled large-inbox skill rules
  advise agents to call one Mail tool at a time.
- **Missed-replies workflow on `verify_draft` and `verify_drafts`.** A new
  opt-in `resolve_source` (with `resolve_recent_days`) maps a reply draft back
  to its source inbox message through a bounded `internet_message_id` lookup and
  returns a `source` block. Defaults preserve the prior output shape. New
  bounded "missed-replies queue" guidance in the email-drafting skill.

### Fixed

- **Thread and correspondent exports no longer open the virtual "All Mail"
  container** that Gmail accounts expose (it cannot be opened and caused export
  failures). They now scan real mailboxes (`INBOX` plus Sent variants).
- **`single_email` export creates its destination directory** before writing.
- **Draft age now reads `date received`** (always populated) instead of
  `date sent` (unset for never-sent drafts, which previously showed as unset).
- **`get_statistics` window cap inversion** where a longer window could apply a
  smaller per-mailbox cap than a shorter one.

## 3.9.2 - 2026-07-09

### Changed

- **`export_emails` bounded scopes** now support sender/date filtered export,
  correspondent history export that includes Sent by default, thread export by
  `message_id`, and paged `entire_mailbox` slices. Unsupported formats such as
  `pdf` now fail before Mail.app runs instead of reporting a zero-file success.
- **Claude Code and Codex marketplace registry** — Marketplace key renamed from
  `apple-mail-mcp` to `Agentic-Assets`; plugin selector is now
  `apple-mail@Agentic-Assets`. User installs register the GitHub-backed
  marketplace (`Agentic-Assets/apple-mail-mcp` or the `.git` URL for Codex)
  instead of a local checkout path. Validators, refresh scripts, and install
  docs updated; legacy uninstall commands remain in README for migration.

## 3.9.1 - 2026-06-30

Internal module-line-budget cleanup. No behavior change, no tool-surface change
(still 31 tools); all checks and live behavior preserved.

### Changed

- **Oversized modules split into packages to satisfy the 600 LOC budget.** The
  plugin runtime modules (`cli.py`, `core.py`, and the `tools/` handlers
  `analytics`, `compose`, `inbox`, `manage`, `search`, `smart_inbox`) and the
  dev-infra validator `tools/validate_manifests.py` are now packages of
  cohesive submodules. `validate_manifests.py` stays the entry point invoked by
  `tools/validate_manifests.sh`; its checks moved to `tools/manifest_checks/`
  and are re-exported so the test suite and CI call sites are unchanged. The
  module-line-budget baseline (`tests/fixtures/module_line_budget/baseline.json`)
  is now empty.
- **Test suite reorganized into per-area subfolders** (`tests/<area>/`) with the
  collected-test count tracked in `tools/expected_test_count.txt`.

## 3.9.0 - 2026-06-30

Native-only reply drafting enforced. The windowless `native_format=False` path
is now gated so agents can no longer drift into the plain-text fallback that
drops Mail's colored quote bar and logo signature.

### Added

- **`allow_windowless_fallback` parameter on `reply_to_email`** (default
  `False`). Passing `native_format=False` without
  `allow_windowless_fallback=True` now returns the structured error
  `WINDOWLESS_FALLBACK_DISABLED` before any AppleScript runs. The windowless
  object-model path remains available for deliberate headless/bulk/CI runs
  where no GUI focus or Accessibility permission is available; agents must
  never set `allow_windowless_fallback=True` on their own.

### Changed

- **`REPLY_WINDOW_FOCUS_FAILED` remediation no longer offers the fallback.**
  The `alternative` field now tells callers to retry with
  `native_format=True` (the default) once Mail can take focus, or to stop and
  report the blocker. It no longer mentions `native_format=False`, so the tool
  itself no longer steers agents toward the plain-text path.
- **Skill and docs guidance rewritten to native-only.** `email-drafting`,
  `apple-mail-operator`, `inbox-triage`, `email-management` templates, the
  shared `pre-draft-verification` and `agent-id-first-workflow` references,
  `README.md`, `tools/CLAUDE.md`, `skills/CLAUDE.md`, and
  `docs/CLAUDE-conventions.md` now state that native drafting is the only
  supported reply method and that the windowless path is gated. The
  `email-drafting` skill leads with a binding "Native drafting only" rule.

## 3.8.0 - 2026-06-30

Native-format reply drafts. `reply_to_email` now defaults to Mail's native reply
window so saved drafts keep the colored quote bar and the account's default logo
signature, with a windowless fallback preserved for headless and bulk use.

### Added

- **`native_format` parameter on `reply_to_email`** (default `True`). The native
  path opens Mail's `reply ... with opening window`, which renders Mail's own rich
  quoted thread and default reply signature, then types `reply_body` above the quote
  with a System Events keystroke (never the clipboard). Set `native_format=False`
  for the windowless object-model path (plain-text quote, no signature logo, no
  Accessibility permission required) for headless, bulk, or CI use.
- **`REPLY_WINDOW_FOCUS_FAILED` structured error.** When the native path cannot
  bring the reply window into focus, it aborts without saving and returns a
  structured error that points callers at `native_format=False`.
- **Module line budget gate.** `tools/check_module_line_budget.py` and
  `tests/test_module_line_budget.py` warn on modules over **600 LOC** in
  `plugin/apple_mail_mcp/` and `tools/`, and fail CI on baseline regression
  (`tests/fixtures/module_line_budget/baseline.json`). Runs in `dev-check.sh`,
  `validate_manifests.py`, pre-commit, and GitHub CI. Documented in
  `docs/CLAUDE-conventions.md` § Module line budget.

### Changed

- **Reply verification is line-break-insensitive.** The saved-draft verifier now
  strips CR/LF before matching, so a soft-wrapped first line no longer trips a false
  `BODY_MISSING`. The native default also skips signature substring matching (Mail's
  own logo signature cannot be reliably substring-matched) and never pins the
  account alias on the native window (pinning had dropped the embedded logo).
- **Attachment verification matches names as a multiset.** Reply-draft verification
  and `verify_draft` / `verify_drafts` now require each expected attachment name to be
  present with its full multiplicity (duplicate filenames are consumed one for one)
  and compare raw Mail attachment names, so a draft missing one of two identically
  named files is reported as `missing` rather than passing.
- **Agent guidance ID-first alignment.** Skills, `common-workflows.md`, README tool
  table, `apple-mail-mcpb/manifest.json`, and compose/manage/analytics docstrings now
  tell the same story: `message_id` / `message_ids` required on action tools;
  `subject_keyword`, `sender`, and `draft_subject` are schema-compat only
  (`TARGET_SELECTOR_DEPRECATED`). New canonical references:
  `plugin/skills/references/agent-id-first-workflow.md` and
  `pre-draft-verification.md` (per-skill copies via `tools/sync_skill_references.py`,
  enforced by `tests/test_packaged_skill_paths.py`). Extended
  `tests/test_id_first_guidance.py` for README, manifest, and template traps. Stale
  banners on historical task docs (`scalability-24k-hardening`, `id-first-refactor-spec`,
  `LIVE_FIELD_REPORT`).

### Notes

- The native path needs the host process to hold macOS Accessibility permission
  (System Events keystroke); `native_format=False` avoids it.
- 981 collected tests; tool count unchanged (31).

## 3.6.1 — 2026-06-07

Codex plugin install-smoke regression recovery and test-count verification.

### Fixed

- **Codex plugin install surface** — recovered `plugin/.codex-plugin/plugin.json` versioning and marketplace routing after Codex setup work (2026-06-07).
- **Test count verification** — confirmed 798 tests + 30 subtests via `pytest --collect-only -q` in CI; updated root guidance.

### Changed

- **Documentation alignment** — manifest validator, release gate, and CI now all source from canonical `pyproject.toml` version (3.6.1).
## 3.7.1 — 2026-06-09

Tighter centralized ``SCAN_BOUNDS`` for large-mailbox performance. Tool count
unchanged (28).

### Changed (scan caps)

Search window ceiling **250** (was 500), search base **100** (was 200), inbox
unread scan max **500** (was 1000), ``mailbox="All"`` fan-out unchanged at
**10** folders, explicit multi-mailbox search cap **20** (was 50).
``compute_scan_upper_bound()`` reads defaults from ``SCAN_BOUNDS`` (scale **25**
days/message, was 50). ``get_statistics`` short windows: 10 mailboxes × **75**
messages; longer windows: 20 × **250**.

## 3.7.0 — 2026-06-09

ID-first mutation hardening for large mailboxes (24k+). Tool count unchanged (28).

### Changed (performance / agent safety)

- **`move_email`**, **`update_email_status`**, **`manage_trash`**: filter-based scans
  (subject/sender/date) now require **`allow_filter_scan=True`**. Default path is
  **`message_ids=[...]`** from a prior `list_inbox_emails` or `search_emails` call.
  Filter escape hatch responses include an explicit slow-scan warning.
- **`search_emails`**: **`body_text`** requires **`allow_body_scan=True`** or returns
  structured **`BODY_SCAN_DISABLED`**.
- **`mailbox="All"`** searches cap at **10** folders (was 50); JSON sets
  `mailboxes_truncated` when capped.
- Sender-only searches emit pairing hints (co-filter with subject or tight
  `recent_days`).

### Added

- Structured error **`FILTER_SCAN_DISABLED`** with remediation pointing to ID-first
  workflow and `allow_filter_scan` escape hatch.
- **`get_email_thread(message_id=...)`** for thread drill-down without subject
  re-search.
- **`list_email_attachments(message_ids=[...])`** and **`export_emails`**
  `single_email` **`message_id`** param.

### Fixed

- Mutation filter paths now pass **`recent_days`** into the search helper so scan
  caps use `compute_scan_upper_bound()` instead of bare `limit+1`.

## 3.6.0 — 2026-06-05

Compose-path race elimination + reliable draft lookup, from a second live draft-QA
session on the 24K Exchange account. The 3.5.0 `saving no` change was insufficient:
the reply/forward path was driving Mail's GUI, which is inherently racy. Tool count
unchanged (28); one additive optional param (`manage_drafts(subject_contains=...)`).

### Fixed (compose GUI races — data-loss/corruption class)

- **Reply/forward no longer leak a draft's body into the wrong thread, duplicate,
  or save empty.** `reply_to_email` and `forward_email` previously opened a Mail
  compose window (which auto-saves an empty draft *shell* → the duplicate), pasted
  the body from the **system clipboard** via `keystroke "v"` into whatever window
  had focus (→ body landing in an unrelated thread; or pasting nothing → empty
  draft), and closed with the **positional** `close window 1` (→ wrong window).
  Both tools now build the draft entirely through Mail's **object model**
  (`make new outgoing message` + `make new to recipient`), exactly like
  `compose_email`: **no window, no clipboard, no System Events, one `save`.** This
  removes the entire race class.
- **`reply_to_all=True` now includes every original party.** Instead of trusting
  Mail's reply-to-all (which silently dropped recipients), the reply now sets
  recipients **deterministically**: the original sender as To, and every other
  To/Cc party as Cc, excluding the sender and the account's own addresses.
- **Newly-created drafts are found reliably.** `manage_drafts(action="list")` and
  the draft lookup behind `send`/`open`/`delete` now read the **newest** drafts
  (a `messages startIdx thru totalDrafts` tail, newest-first) instead of the 100
  *oldest* (`messages 1 thru 100`), so a just-created draft is never missed when a
  mailbox holds >100 drafts. No date filter is used — fresh `outgoing message`
  drafts have a null `date received`, which previously made date-filtered draft
  searches silently drop them.
- **`compose_email` HTML path hardened** — the rich-HTML compose (which still needs
  the clipboard) now targets `window of newMsg` (and brings it to front before the
  paste) instead of the positional `window 1`.

### Added

- **`manage_drafts(subject_contains=...)`** — optional case-insensitive, in-loop
  subject filter for `action="list"`, giving a fast, bounded "find the draft I just
  created" lookup over the small Drafts mailbox. Prefer this (or `get_email_by_id`)
  over `search_emails` for draft verification — `search_emails` runs a date-filtered
  scan that is slow on large accounts and drops null-date drafts.

### Changed (behavior trade-off)

- **Replies/forwards are now reliable plain-text "Re:"/"Fwd:" drafts.** Because the
  clipboard was the only thing inserting rich HTML, eliminating it means reply and
  forward bodies are plain text with a `> `-quoted copy of the original (bounded to
  4000 chars) and a `Re:`/`Fwd:` subject. The draft is **correctly addressed and
  always contains your text** — the priority after the cross-thread corruption.
  `body_html` is still accepted on `reply_to_email` for backward compatibility but
  is ignored. Trade-off: replies no longer carry native `In-Reply-To`/`References`
  headers (they thread visually via subject + quote). `create_rich_email_draft` and
  `compose_email` still produce rich HTML for genuinely standalone messages.

## 3.5.0 — 2026-06-05

Live field-report hardening (draft QA workflow on a 24K Exchange account) plus
the previously-unreleased mcporter wrapper + large-mailbox work. Tool count is
unchanged (28); changes are additive params/actions/fields, all backward
compatible.

### Fixed (draft QA field report)

- **Reply / forward / rich drafts no longer create duplicate drafts.** The
  draft paths persisted twice — an explicit `save <message>` *then*
  `close window 1 saving yes` — which committed a second, byte-identical copy
  to Drafts (observed as same-second duplicate pairs). Every draft path now
  persists exactly once (`save` then `close window 1 saving no`; the rich-draft
  helper keeps its single `Cmd+S` and closes with `saving no`). Verified live:
  one reply call yields exactly one threaded draft.
- **`search_emails` no longer hangs on Exchange when a per-mailbox scan is
  slow.** A per-mailbox `with timeout` wrapper (added during the unreleased
  work) fired on the 24K-message Exchange INBOX, and the inner candidate-fetch
  `try` swallowed the timeout into a silent **0-row** result. The wrapper is
  removed; per-folder failures are still isolated by the existing
  `on error → ERROR_MAILBOX` handler, and the whole call is bounded by the
  single outer timeout budget.
- **`get_email_by_id` header parsing tolerates value-less headers.** A bare
  `In-Reply-To:` / `References:` line (no value) would make the `text N thru -1`
  slice raise and the surrounding `on error` wipe *both* fields — discarding a
  sibling header that had already parsed cleanly. Length guards now skip empty
  header values so threading metadata survives.

### Added (draft QA field report)

- **`get_email_by_id` now returns threading + recipient metadata** so an agent
  can confirm a draft is a correctly-addressed reply without opening Mail:
  `to`, `cc`, `bcc`, `in_reply_to`, `references` (parsed from `all headers`),
  and a computed `has_quoted_original` flag. Single-message, bounded, fast.
- **`search_emails(mailboxes=[...])`** — new optional parameter to search an
  explicit list of folders (e.g. `["Archive", "Sent"]`) instead of one mailbox
  or paying for `mailbox="All"`. Missing folders degrade to a structured
  per-mailbox error rather than failing the call. Recommended over `"All"` on
  large Exchange/Gmail accounts.
- **`manage_drafts(action="list")` is now triageable** — each draft reports its
  `Id`, `To` recipients, and a short body snippet; new `hide_empty=True` skips
  orphaned blank drafts.
- **`manage_drafts(action="cleanup_empty")`** — removes orphaned blank drafts
  (blank subject **and** empty body). Preview-only by default (`dry_run=True`)
  with a `max_deletes` safety cap, matching the repo's destructive-op
  conventions.
- **CLI parity for the new draft/search surfaces.** `apple-mail search` gains
  `--mailboxes a,b,c` (comma-separated targeted-folder search); `apple-mail
  drafts list` gains `--hide-empty`; and a new `apple-mail drafts cleanup-empty`
  subcommand previews orphaned blanks by default and only deletes with
  `--execute` (`--limit` caps the batch). The repo CLI is the live-test harness,
  so these mirror the MCP params 1:1.

### Changed (draft QA field report)

- **Bulk `search_emails` no longer resolves per-message recipients.** Resolving
  `to recipients`/`address of` inside the bulk scan can *hang* (uncatchable by
  `on error`) on large remote mailboxes. Recipients are now fetched per message
  via `get_email_by_id` (and shown in `manage_drafts` list over the small local
  Drafts mailbox). The record layout reserves the fields, so they still surface
  wherever a tool populates them.

### Fixed (Gmail crash)

- **`list_inbox_emails(include_read=False)` no longer crashes on Gmail / Google
  Workspace accounts.** The historical AppleScript `(candidateMessages whose
  read status is false)` evaluated `whose` against a list of message
  references; on Gmail those refs point at `[Gmail]/All Mail`, which
  Mail.app rejects with `Can't get {message id N of mailbox
  "[Gmail]/All Mail" ...} whose read status = false`. Replaced with an
  in-loop `if read status of aMessage is false` filter (the same pattern
  `search_emails` already uses safely). Works on every account type
  including 24K+ Exchange inboxes.
- **`reply_to_email` / `forward_email` subject lookup hardened the same
  way.** The historical `whose subject contains "X" and date received >=
  cutoff` over a bound slice carried the same Gmail risk; the predicate is
  now evaluated in an AppleScript `repeat` loop with an early-exit on the
  date cutoff (slices are newest-first).
- **`bounded_scan.build_bounded_message_scan(..., whose_condition=...)`
  now raises `UNSAFE_WHOSE_ON_LIST`.** The footgun is gone — any future
  caller that needs to filter a bounded slice must use the new
  `build_bounded_filtered_scan(...)` helper, which emits the safe in-loop
  pattern by construction.

### Added

- **`list_inbox_emails(read_status=...)`** — new public parameter with the
  same vocabulary as `search_emails`: `"all"` (default), `"unread"`,
  `"read"`. The legacy `include_read: bool` / `unread_only: bool` kwargs
  continue to work but emit a `DeprecationWarning`.
- **`bounded_scan.build_bounded_filtered_scan(mailbox_var, scan_cap,
  target_max, condition_expr, ...)`** — new helper that emits the safe
  bounded-slice + in-loop filter pattern. The only sanctioned way to
  filter a bound slice by message property.

### Distribution

- **New `apple-mail.plugin` build artifact**: `tools/build-artifacts.sh` now
  emits `apple-mail.plugin` (byte-identical to `apple-mail-plugin.zip`)
  alongside the existing `.zip` and `.mcpb`. The `.plugin` extension is the
  canonical upload format for Claude Desktop's **Customize → Add plugin →
  Upload plugin** flow (Cowork), which was previously documented only as a
  generic `.zip` upload. Stale `.mcpb` files from 3.2.1 / 3.3.0 / 3.3.1
  cleaned from repo root; `.gitignore` covers the new `.plugin` artifact.

### Documentation

- **`search_emails` subject-only fast path**: narrow subject lookups (no sender,
  body, attachment, or read-status filters) now scan only the requested page
  size and skip per-message date/sender/read-status reads. No-hit lookups on
  large Exchange mailboxes that previously took 48–115s now complete inside
  the wrapper request ceiling. `recent_days` still controls the bounded slice
  for searches that include other filters.
- **`search_emails` recent-window early break**: bounded scans with a
  `date_from` lower bound now read `date received` first and `exit repeat`
  once messages cross the cutoff, avoiding subject/sender/read-status reads
  on messages outside the window.
- **`full_inbox_export` AppleScript syntax fix**: per-field `(try … end try)`
  expressions were invalid AppleScript inside a concatenation and aborted the
  tool with `-2741`. Replaced with per-field variable assignments inside a
  `try` block, then concatenated. Repro: `max_emails=1` through `--raw`.
- **`full_inbox_export` named-flag input**: `fields` now accepts a
  comma-separated string in addition to a list, so generated mcporter wrappers
  that flatten the list parameter still work without `--raw`.
- **`tools/patch_mcporter_wrapper.py`**: post-generation patch renames the
  mcporter global `--timeout <ms>` (which collides with per-tool `timeout`
  seconds) to `--request-timeout-ms`, and optionally repoints embedded
  `start_mcp.sh` paths for relocated plugin roots.
- **`check_wrapper_surface.py`** now flags the global `--timeout <ms>` flag
  in generated wrappers and reminds operators to run `patch_mcporter_wrapper.py`.
- **`validate_manifests._tracked_plugin_files`** is more defensive when
  `git ls-files` returns nothing while `plugin/` exists on disk.

## 3.4.0 — 2026-05-26

Hardening release: 15 real bugs fixed (1 HIGH security, 8 type-safety / None-handling,
3 silent-error / resource, 3 AppleScript-injection / shell-quoting) plus a new lint +
static-analysis + property-test baseline. No breaking changes to MCP tool signatures
or return shapes.

### Security

- **HIGH — `create_rich_email_draft` path traversal**: `output_path` accepted from
  the caller was written directly to disk without `validate_save_path` / sensitive-dir
  guard. An attacker could pass `output_path="~/.ssh/authorized_keys"` (or `~/.aws/credentials`,
  `~/.claude/settings.json`, `~/Library/Keychains/*`) and silently corrupt the file with
  a draft `.eml` body. Now resolved with `os.path.realpath(os.path.expanduser(...))`
  and rejected against the shared `SENSITIVE_DIRS` list before any write.
- **`search_emails` forgotten-wiring**: `escaped_sender = escape_applescript(sender)`
  was computed but never used; the raw `sender` string flowed into the AppleScript
  filter fragment. Now wired correctly so quote / backslash / newline injection
  characters are escaped before they reach `osascript`.
- **`compose.py` shell-quote consistency**: 6 `do shell script "cat '{path}'"` /
  `"rm -f '{path}'"` call sites in `_send_html_email` / `reply_to_email` /
  `forward_email` rewritten to `"cat " & quoted form of "{path}"`, matching the
  safe pattern already used for `body_temp_path`. Single-quoted bare paths are
  brittle if `tempfile.gettempdir()` ever returns a path containing a quote.

### Reliability

- **`validate_save_path` NUL-byte contract change** (minor API): paths containing
  `\x00`–`\x1F` or `\x7F` previously raised `ValueError` from `os.path.realpath`,
  bubbling an uncaught exception out of the MCP tool boundary. Now returns the
  standard structured-error string, matching every other validator in `core.py`.
  Surfaced by a new Hypothesis property test.
- **`analytics.py` entire-mailbox export file-handle leak**: the batch-export
  `on error -- Continue` handler skipped `close access fileRef`, leaking a kernel
  fd per failed message. Now closes inside a guarded `try / close access / end try`
  block, mirroring the single-email export path.
- **`core.fetch_replied_ids_impl` silent except**: caught `Exception` and returned
  empty `set()` for ALL non-timeout errors (`OSError`, `PermissionError`, broken
  Mail connection). Triage tools (`get_awaiting_reply`, `get_needs_response`)
  then falsely reported every sent message as awaiting reply. Now logs at
  `WARNING` with exception class + message before returning, while still
  returning empty so callers keep working.
- **`update_email_status` bulk-action silent fallback**: bulk
  `set read status of every message …` failures fell through to the per-message
  loop without surfacing the bulk error. Now captures `errMsg`/`errNum` in the
  `on error` block and emits a `BULKERR|errNum=… errMsg=…` row so callers see
  the real failure.
- **`subprocess.run(["open", "-a", "Mail", ...])` in `create_rich_email_draft`**:
  raised `CalledProcessError` / `FileNotFoundError` uncaught when Mail.app
  wasn't available or the `.eml` was malformed. Now wrapped in try/except
  returning a structured error.

### Type-safety (mypy: 27 errors → 0 across 16 source files)

- **`compose.py` `Optional[str]` flowing into non-None operations** (5 sites):
  `account.strip()` on `str | None` → `AttributeError`; `"Account: " + account`
  string concatenation with `None` → `TypeError`; `escape_applescript(account)`
  silently stringifying `None` to the literal `"None"` reaching synthesised
  AppleScript. Each fixed with an `assert account is not None` immediately
  after the `_resolve_account` error guard, documenting the invariant that
  a non-`None` account and a `None` error are mutually exclusive.
- **`_build_found_message_lookup` return type tightened** from
  `Tuple[str, Optional[object]]` to `tuple[str, ToolError | None]` —
  reflects the actual runtime invariant and stops mypy noise at every
  call site.
- **`inbox.py` `**dict[str, int | str | None]` typed-kwargs unpacking** (4 sites):
  a heterogeneous-value dict was spread into functions with per-param types,
  hiding potential `TypeError`s at runtime. Replaced with explicit kwargs at
  every call site. Same file: `body` variable shadowing (`Dict[str, Any]`
  then re-assigned `str`) fixed by renaming to `text_body`; `item` dict in
  `list_mailboxes` annotated as `Dict[str, Any]`.
- **`core.parse_email_list` missing annotations** on `emails` and `current_email`
  (residual pre-existing mypy warning) — annotated explicitly.

### Testing & static analysis

- **+279 tests** (suite 367 → 646+), all green:
  - +90 AppleScript script-idiom regression tests (`test_applescript_script_idioms.py`)
  - +12 `osacompile` parse-checks per builder (skips on Linux, runs on macOS CI)
  - +25 Hypothesis property tests on `escape_applescript`, `validate_account_name`,
    `validate_save_path` — found the NUL-byte bug
  - +33 `jsonschema` contract tests for `get_inbox_overview`, `list_inbox_emails`,
    `get_awaiting_reply`, `search_emails`, `get_email_thread`
  - +70 bug-fix regression tests (`test_compose_none_handling.py`,
    `test_compose_security.py`, `test_core_validators.py`, `test_search_escaping.py`,
    `test_inbox_typed_kwargs.py`, `test_analytics_resource_safety.py`,
    `test_core_fetch_replied_ids.py`, `test_manage_bulk_action_errors.py`)
- **New dev dependencies** under `[project.optional-dependencies] dev`:
  `ruff`, `mypy`, `pytest-cov`, `hypothesis`, `jsonschema`. Install with
  `pip install -e ".[dev]"`.
- **`tools/dev-check.sh lint` tier**: runs `ruff check`, `ruff format --check`,
  and `mypy` on the plugin source. Wired into the `release` tier.
- **`tools/pre-commit-validate.sh`**: now runs `ruff check` on staged Python files.
- **CI**: `.github/workflows/ci.yml` installs dev deps and runs `ruff check`
  on `plugin/ tools/ tests/`.
- **`pyproject.toml`**: `[tool.ruff]`, `[tool.ruff.lint]` (rules E, F, I, B,
  UP, SIM, RET, PTH), `[tool.mypy]` (permissive baseline, no `disallow_untyped_defs`),
  `[tool.pytest.ini_options]`.
- **Coverage baseline**: 78% measured (lowest: `__main__.py` 48%, `manage.py` 62%).

## 3.3.1 — 2026-05-26

Hotfix for a 3.3.0 regression in `get_awaiting_reply`: the Phase 2 inbox
header-extraction AppleScript used `header value of header named "X" of
msg`, which is not valid Mail.app dictionary syntax and failed to parse
with osascript `-2740` ("A application constant or consideration can't
go after this identifier"). Replaced with the standard `headers of
aMessage` iteration that filters by `name of aHeader` and reads
`content of aHeader`. The INBOXHDR row protocol consumed by the Python
parser is unchanged; tests cover the parser behavior, not the broken
AppleScript form, so no test churn was required.

Reproduced on live TU Exchange inbox (24K messages): pre-fix returned
`AppleScript error: ... syntax error ... (-2740)`; post-fix returns 4
sent emails awaiting reply over a 7-day window.

## 3.3.0 — 2026-05-26

Phase 2 + Phase 3 hardening: faster analysis paths, structured JSON across
the smart-inbox surface, and one targeted breaking change to
`list_inbox_emails` JSON mode.

### Breaking

- **`list_inbox_emails` JSON mode now returns a Python `dict`, not a JSON
  string.** Stable shape: `{"emails": [...], "errors": [...]}` for every
  `output_format="json"` success and per-account-timeout path.
  - `errors` is always present (empty list when nothing timed out).
  - Account-not-found in JSON mode also returns a dict (`{"error":
    "account_not_found", "account": ..., "available_accounts": [...],
    "emails": []}`).
  - Account-listing timeouts surface as
    `{"emails": [], "errors": ["__account_listing__"]}`.
  - When deprecated aliases (`limit`, `unread_only`) are used, a `warnings`
    list is attached to the same dict.
  - **`UNBOUNDED_SCAN_REQUIRED` refusal errors remain a JSON-encoded string**
    so text-mode and JSON-mode callers see the same payload for that hard
    refusal path.
  - Migration: callers that did `json.loads(result)` on the
    `list_inbox_emails` JSON output should drop the `json.loads` call. The
    repo CLI (`apple-mail list-inbox --json`) handles dicts and strings
    transparently through `_print_result`.

  See `plugin/apple_mail_mcp/tools/inbox.py` and
  `tasks/reference/robustness-backlog-2026-05-22.md` (Phase 3) for context.

### Performance

- **`get_statistics` (`account_overview` scope) uses Mail.app's cheap
  mailbox-count APIs** instead of per-message unread scans. AppleScript now
  emits a `MBOX|||name|||total|||unread` header row per sampled mailbox
  (via `count of messages of aMailbox` + `unread count of aMailbox`); the
  per-message `read status` fetch is gone. `total_emails` and `unread` now
  reflect true mailbox-wide totals across the sampled mailboxes;
  sample-bounded stats (`flagged`, `with_attachments`, `top_senders`,
  `mailbox_distribution` ROW-derived stats) still respect `days_back`.
- **`get_needs_response` reply matching moved to Python.** The inbox
  AppleScript emits a flat `MSG|||message_id|||...` row per candidate;
  replied detection runs as an O(1) set lookup in Python via
  `fetch_replied_ids` and `_normalize_message_id_token` (was O(N×M)
  AppleScript `repeat with repliedRef`). Header-based detection only
  (`In-Reply-To`, `References`) — no subject substring matching.

### Reliability

- **Silent per-message `on error` skips replaced with `errors[]`.** Inner
  per-message failures in `account_overview` are now counted per mailbox
  and surfaced as a single
  `__APPLE_MAIL_MCP_ERROR__|||mailbox|||N message(s) skipped due to read
  errors` line, parsed into the JSON `errors[]`.

### JSON / schema consistency

- **Smart-inbox tools accept `output_format="json"` and return dicts with
  stable keys + `errors[]`:**
  - `get_needs_response` → `{account, mailbox, days_back, max_results,
    high_priority, normal_priority, skipped_replied_count, errors}`
  - `get_awaiting_reply` → `{account, days_back, max_results, awaiting,
    errors}`
  - `get_top_senders` → `{account, mailbox, days_back, top_n,
    group_by_domain, senders, total_analysed, mailbox_count,
    unique_senders, scan_cap, errors}`
  - Error and timeout paths return dicts in JSON mode.
- `inbox_dashboard` JSON path returns a Python dict (already true in code;
  verified and documented).

### Docs

- `docs/AGENT_LIVE_TESTING.md` gains a "`--raw` examples for advanced
  wrapper options" subsection covering `get-inbox-overview`,
  `get-statistics` (three scopes), smart-inbox triage, `inbox-dashboard`
  JSON mode, and `full-inbox-export`.

See `tasks/reference/robustness-backlog-2026-05-22.md` Phase 2 + Phase 3 for the
backlog this batch closes.
