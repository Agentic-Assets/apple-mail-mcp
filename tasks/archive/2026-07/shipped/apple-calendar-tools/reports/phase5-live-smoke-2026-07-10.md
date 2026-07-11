# Phase 5: Apple Calendar tools, live smoke test on the production Mac

Branch `feat/apple-calendar-tools`. Executed against real Calendar.app data (15
pre-existing calendars across iCloud, Google, Exchange/other accounts) with a
Python harness importing the tool functions directly, per
`docs/AGENT_LIVE_TESTING.md` and `docs/CLAUDE.md`'s agent-orchestration
pattern ("a small `.venv` python harness importing the tool functions"). No
CLI calendar write subcommands exist yet (the repo CLI only ships `calendars`,
`calendar-events`, `calendar-grant` per the final plan section 10 work
breakdown item 6), so every probe below calls
`apple_mail_mcp.tools.calendar.<tool>` directly, exactly as the MCP transport
would.

## Headline result: writes are blocked by a pending macOS Automation consent dialog

Every calendar **write** (`create_event`, `update_event`, `batch_create_events`,
`delete_events`, `manage_calendars`) always routes through
`get_write_engine()` -> `AppleScriptCalendarEngine` -> `run_applescript()`
(final plan section 2: "writes are always AppleScript in 3.10.0"). The first
AppleScript call to Calendar.app on this host triggers a macOS Automation
consent modal ("Terminal" wants to control "Calendar") that nobody was present
to answer, so the call hangs until the tool's own internal 120s timeout fires
and returns a structured (well, plain-text per the mail convention for
AppleScript timeouts) error naming the Automation pane. This is the exact
"Open risk 1" the final plan predicted (section 9, item 1) and the exact
"silent-hang first-use signature" the calendar-operator skill is written to
teach agents to recognize. It was confirmed two ways:

1. **Live tool call.** `manage_calendars(action="create", name="MCP Smoke Test
   undefined", dry_run=False)` ran for **120.09s** and returned:
   > `Error: manage_calendars timed out after 120s talking to Calendar.app. If
   > this is the first calendar call from this app, macOS may be waiting on
   > the Automation consent prompt (System Settings > Privacy & Security >
   > Automation); answer it once and retry.`
2. **Raw osascript probe** (`osascript -e 'tell application "Calendar" to
   name of every calendar'`, 8s hard timeout via `subprocess.run(...,
   timeout=8)`): timed out, confirming the process is genuinely blocked on a
   pending consent decision, not a fast `-1743` denial. A screenshot
   (peekaboo, read-only, no clicks) captured the actual system dialog:
   **"Terminal" wants to control "Calendar". Allowing control will provide
   access to documents and data in "Calendar", and to perform actions within
   that app.** with **Don't Allow** / **Allow** buttons, still pending.

Per the task's hard safety rule ("If TCC consent blocks a step, record it
precisely... and continue with what works"), the dialog was **not** clicked
through (accepting or declining a system security prompt on the user's behalf
is outside a smoke-test agent's remit; declining it would also convert a
recoverable "pending" state into a persistent `-1743` denial that needs
`tccutil reset Calendar` to undo). Live write execution stops here. **Remediation
for the next human-present session:** click **Allow** on the pending dialog
(or grant Terminal → Calendar under System Settings > Privacy & Security >
Automation), then re-run this harness's write stage.

Reads were unaffected: `get_engine()` picked the **EventKit** fast path
(Calendars full access is already granted to this process ancestry, confirmed
by `eventkit_status()` -> `(True, "full_access")`), which never touches
Calendar.app's Apple Events surface, so every read below completed in
well under 200ms with no permission prompt at all.

## Capability x result table

| # | Capability | Tool call | Result | Wall time |
|---|---|---|---|---|
| 1 | List calendars | `list_calendars()` | 15 real calendars returned (Work, Untitled Calendar, Calendar, Transferred from cayman@caiyman.ai, Holidays in the United States, cayman@caiyman.ai, US Holidays, Birthdays, Holidays in United States, ai.openclaw@agenticassets.ai, stace@agenticassets.ai, cayman@agenticassets.ai, REIT Factors, Agentic Assets Meeting Room, and a second "Holidays in United States"); `engine: "eventkit"`, `eventkit_available: {"available": true, "reason": "full_access"}`; `default_calendar: "ai.openclaw@agenticassets.ai"` (env `DEFAULT_CALENDAR` unset, so this is the EventKit engine's own `defaultCalendarForNewEvents`, correctly falling through the documented chain) | 0.08s |
| 2 | Fuzzy calendar-name resolution (`resolve_calendar_name`, pure function, F6 algorithm) | exact `"REIT Factors"` -> `"REIT Factors"`; case-insensitive `"reit factors"` -> `"REIT Factors"`; unique substring `"cayman@agentic"` -> `"cayman@agenticassets.ai"`; ambiguous `"Holidays"` -> `AMBIGUOUS_CALENDAR_SELECTOR` (3 real candidates: "Holidays in the United States", "US Holidays", "Holidays in United States"); not found `"Nonexistent Calendar XYZ"` -> `CALENDAR_NOT_FOUND` with close-match candidates | All correct per spec | instant |
| 3 | Bounded event list, +/-7 days | `list_events(days_back=7, days_ahead=7)` | 21 real events returned, correctly bounded and paged (`limit`/`offset`/`total_matched` present) | 0.03s |
| 4 | Get event by id | `get_events_by_id([id], calendar=...)` (id sourced read-only from the +/-7d list above: "Independence Day (substitute)" on "Holidays in the United States") | Exact event returned, no mutation | 0.03s |
| 5 | Search events (query match) | `list_events(days_back=7, days_ahead=30, query="Meeting")` | Matched a real event ("Meet with the Agentic Assets Team...") | 0.03s |
| 6 | Search events (no-hit) | `list_events(..., query="ZZZ_NO_SUCH_EVENT_MCP_SMOKE_20991231")` | `events: [], total_matched: 0` | 0.03s |
| 7 | Free-busy / find-slots, tomorrow 9-5 | `check_availability(start=tomorrow 09:00, end=tomorrow 17:00)` | `busy: [], free_slots: []` -- correct, tomorrow (2026-07-11) is a **Saturday** and `weekdays_only=True` is the default, so zero slots is right, not a bug. Re-verified with `weekdays_only=False` on the same Saturday (16 half-hour free slots returned) and again on Monday 2026-07-13 (16 free slots) to confirm slot-folding actually works | 0.02-0.11s |
| 8 | BOUNDS: default window, no args at all | `list_events()` (no start/end/days_back/days_ahead) | Uses the documented bounded default (`days_back=0, days_ahead=7`); returns immediately with real upcoming events -- **never hangs**, never scans unbounded | 0.11s |
| 9 | BOUNDS: oversized window | `list_events(days_ahead=5000)` | `CALENDAR_WINDOW_TOO_WIDE`: "Window spans 5000.0 days; the cap for this call is 370 days." | <0.01s |
| 10 | BOUNDS: zero-width window | `list_events(days_back=0, days_ahead=0)` | `UNBOUNDED_CALENDAR_SCAN`: "Calendar reads require a bounded window..." | <0.01s |
| 11 | BOUNDS: availability oversized window | `check_availability(start=..., end=+123 days)` | `CALENDAR_WINDOW_TOO_WIDE`: "Window spans 123.3 days; the cap for this call is 62 days." | <0.01s |
| 12 | BOUNDS: availability invalid slot params | `check_availability(..., slot_minutes=99999)` | `INVALID_SLOT_PARAMS`: "slot_minutes must be an integer between 5 and 480; got 99999." | <0.01s |
| 13 | BOUNDS: too many event ids | `get_events_by_id([30 fake ids])` | `INVALID_EVENT_ID`: "Received 30 event ids; the cap for this call is 25." | <0.01s |
| 14 | WRITE: create throwaway calendar | `manage_calendars(action="create", name="MCP Smoke Test undefined", dry_run=False)` | **BLOCKED by pending Calendar.app Automation TCC consent dialog** (see above); timed out after 120.09s with the documented plain-text timeout message naming the Automation pane. No calendar was created (verified: `list_calendars()` afterward does not include it) | 120.09s (flagged SLOW) |
| 15 | WRITE: create event w/ alarm + explicit tz | not exercised live (blocked by #14) | N/A -- see "Not executed" below for the code-verified contract | -- |
| 16 | WRITE: verify via get-by-id (alarm/tz round trip) | not exercised live (blocked by #14) | N/A | -- |
| 17 | WRITE: conflict detection (warn / block) | not exercised live (blocked by #14) | N/A | -- |
| 18 | WRITE: update event | not exercised live (blocked by #14) | N/A | -- |
| 19 | WRITE: batch create 3 events | not exercised live (blocked by #14) | N/A | -- |
| 20 | WRITE: bulk delete (dry_run then real) | not exercised live (blocked by #14) | N/A | -- |
| 21 | WRITE: delete last event | not exercised live (blocked by #14) | N/A | -- |
| 22 | WRITE: delete throwaway calendar | not exercised live (blocked by #14); nothing to delete since nothing was created | N/A | -- |
| 23 | WRITE: attendee param shape, invite-blocked mode, default mode, no confirm | `create_event(title=..., attendees=["mcp-smoke-test-fake-address@example.invalid"], send_invitations=False, calendar="MCP Smoke Test undefined")` | **This one DID reach a live code path** because the attendee gate (`attendee_gate()`) runs entirely in Python before any engine call -- but `calendar_write_blocked("create_event")` is checked even earlier in `create_event`'s body, and in default mode (`READ_ONLY=False`) that check is a no-op, so the call proceeded to `attendee_gate`, which correctly refused with `INVITE_SEND_REQUIRES_CONFIRM` before ever reaching `resolve_create_target`/the engine. No event created, no invitation sent, no real address touched | <0.01s (asserted in-script) |
| 24 | GATING read-only: `create_event` (no attendees) | `server.READ_ONLY=True; server.DRAFT_SAFE=True` (mirrors `__main__.py`'s `DRAFT_SAFE = args.draft_safe or args.read_only`) then call | `CALENDAR_WRITE_BLOCKED` | <0.01s |
| 25 | GATING read-only: `create_event` (with fake attendee) | same | `CALENDAR_WRITE_BLOCKED` (write-block fires before the attendee gate) | <0.01s |
| 26 | GATING read-only: `update_event` | same | `CALENDAR_WRITE_BLOCKED` | <0.01s |
| 27 | GATING read-only: `batch_create_events` | same | `CALENDAR_WRITE_BLOCKED` | <0.01s |
| 28 | GATING read-only: `manage_calendars(create)` | same | `CALENDAR_WRITE_BLOCKED` | <0.01s |
| 29 | GATING read-only: `manage_calendars(delete, confirm+force)` | same | `CALENDAR_WRITE_BLOCKED` (the destructive-path backstop chains through `calendar_write_blocked` first) | <0.01s |
| 30 | GATING read-only: `delete_events` | same | `CALENDAR_WRITE_BLOCKED` | <0.01s |
| 31 | GATING read-only: `respond_to_invitation` | same | `CALENDAR_RSVP_UNSUPPORTED` (registered and answers the same in every mode, as designed -- no engine call ever) | <0.01s |
| 32 | GATING draft-safe: `delete_events` | `server.READ_ONLY=False; server.DRAFT_SAFE=True` (fresh process) | `CALENDAR_DELETE_BLOCKED`: "delete_events deletes are disabled in draft-safe mode." with the `CALENDAR_ALLOW_DESTRUCTIVE=1` operator-unlock remediation | <0.01s |
| 33 | GATING draft-safe: `manage_calendars(delete, confirm+force)` | same | `CALENDAR_DELETE_BLOCKED` | <0.01s |
| 34 | GATING draft-safe: `create_event` w/ attendee, `send_invitations=False` | same | `INVITE_SEND_BLOCKED`: "create_event cannot attach attendees in draft-safe or read-only mode." | <0.01s |
| 35 | GATING draft-safe: `create_event` w/ attendee, `send_invitations=True` (tries to confirm past the gate) | same | **Still `INVITE_SEND_BLOCKED`** -- draft-safe blocks attendee attachment outright regardless of the confirm flag; this is the strongest of the safety guarantees and it held | <0.01s |
| 36 | GATING draft-safe: `respond_to_invitation` | same | `CALENDAR_RSVP_UNSUPPORTED` | <0.01s |
| 37 | GATING draft-safe: `create_event`/`update_event`/`batch_create_events` **without** attendees (the documented ALLOW path) | not exercised live | See "Not executed" below | -- |
| 38 | CLEANUP: throwaway calendar gone | `list_calendars()` again | `"MCP Smoke Test undefined" in names` -> `False`. Trivially true since it was never created | 0.13s |
| 39 | CLEANUP: no leaked test artifacts | `list_events(days_back=30, days_ahead=340, query="MCP Smoke")` (all-calendar fan-out) | `total_matched: 0` | 0.09s |

## What was NOT executed live, and why

Everything gated behind an actual AppleScript engine call was blocked by the
pending Automation consent dialog (rows 15-22, 37). This is a host-permission
gap, not a code defect: `calendar_write_blocked()` correctly evaluates to
"not blocked" in default and draft-safe modes (confirmed live for the
attendee-gated create in row 23, which reaches exactly that line before
falling through to the engine), and the only reason rows 15-22 didn't finish
is that the next line down the call stack (`resolve_create_target` ->
`get_write_engine().create_event(...)` -> `run_applescript(...)`) hit the
unanswered system dialog. Row 37 (draft-safe ALLOW path) is confirmed by
static code trace instead of a live call: `calendar_write_blocked()` in
`plugin/apple_mail_mcp/tools/calendar/helpers.py` checks only
`_server.READ_ONLY`, never `_server.DRAFT_SAFE`, so it necessarily returns
`None` (not blocked) whenever `READ_ONLY` is `False` -- independent of
`DRAFT_SAFE`. That is the exact mechanism the final plan's mode-gating table
(section 5) specifies for "create/update event no attendees: ALLOW under
draft-safe."

No destructive test ever ran against a real calendar or a real event. The
"MCP Smoke Test undefined" calendar was never created (the create call is
exactly what hung), so there was nothing to clean up and nothing to
accidentally destroy.

## Engine and permission state observed on this host

- `eventkit_status()` -> `(True, "full_access")`: EventKit Calendars full
  access is granted to this process ancestry, matching the final plan's
  "Platform claim 11" correction.
- Calendar.app Automation (the Apple Events grant AppleScript writes need) is
  **pending, not denied**: a live system consent dialog is showing (screenshot
  captured, not interacted with). This is a different, independent grant from
  the EventKit one and blocks every write tool regardless of mode.
- `get_engine()` under the default `APPLE_MAIL_CALENDAR_ENGINE=auto` correctly
  picked EventKit for every read in this session (`engine: "eventkit"` echoed
  on every read payload); `get_write_engine()` always returns the AppleScript
  engine, as designed for 3.10.0.

## Recommended next step

A human-present session should click **Allow** on the pending "Terminal wants
to control Calendar" dialog (or configure it ahead of time via System
Settings > Privacy & Security > Automation), then re-run `stage3_write.py`
from this harness (`tasks/active/apple-calendar-tools/reports/` sibling
scratchpad is not committed; the harness lives at
`/private/tmp/claude-501/.../scratchpad/calendar-smoke/` for this session and
would need to be recreated or the write battery re-run by hand) to complete
rows 15-22 and 37: alarm/timezone round trip, conflict warn/block, update,
batch create, bulk delete dry-run-then-real, single delete, calendar delete,
and the draft-safe ALLOW-path proof.

## Harness

Ad hoc Python scripts under the session scratchpad (not part of the repo),
importing `apple_mail_mcp.tools.calendar.*` directly and, for the two gating
stages, setting `apple_mail_mcp.server.READ_ONLY` / `DRAFT_SAFE` in a fresh
process right after import -- the same module-level flags `__main__.py` sets
from `--read-only`/`--draft-safe` argparse, so the guard checks exercised are
identical to what a real CLI relaunch would hit. Every call was wrapped with
`time.perf_counter()` timing and a JSON-lines log; nothing here touched or
required the mocked pytest suite (`.venv/bin/pytest tests/` was not run as
part of this smoke test -- it is covered by the phase-4/gates reports in this
same directory).
