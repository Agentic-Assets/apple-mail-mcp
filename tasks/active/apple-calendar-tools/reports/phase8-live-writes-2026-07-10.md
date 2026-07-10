# Phase 8: Apple Calendar tools, live write verification on the production Mac

Branch `feat/apple-calendar-tools`. Executed 2026-07-10 immediately after the owner
granted the macOS Calendar Automation consent (the blocker recorded in
[`phase5-live-smoke-2026-07-10.md`](phase5-live-smoke-2026-07-10.md) rows 15-22 and 37).
This session replicates the intent of those deferred rows against real Calendar.app,
per the final plan section 8 protocol. All writes were confined to a throwaway
calendar, `MCP Test Calendar 2026-07-10`, created at the start and deleted at the end.
No pre-existing event or calendar was touched at any point. The only attendee address
used was `test-invitee@example.invalid`, on the throwaway calendar, deleted immediately.

Harness: fresh `.venv/bin/python` process per call, importing
`apple_mail_mcp.tools.calendar.*` directly (real engines, no pytest conftest, no
mocking), timed with `time.monotonic()`. Engine selection per call via
`APPLE_MAIL_CALENDAR_ENGINE` (`auto` default, `applescript` or `eventkit` where the
step targets a specific engine). Harness scripts lived in the session scratchpad
(not committed).

## Headline

**The write surface works end to end on the AppleScript engine**: calendar create,
timed create with alarm and timezone, all-day cross-timezone create (F1 fix held),
conflict warn/block, PATCH update, batch create with dry run, id-exact bulk delete
with dry run, recurring expansion on both engines, both attendee gate shapes, and the
full cleanup chain all behaved as specified, with fast wall times.

**Four real defects were found live**, all invisible to the mocked suite:

1. **Dual event-id namespace across engines** (open risk 3 is real): ids returned by
   writes do not resolve under EventKit reads and vice versa, which breaks
   create-then-update and read-then-delete flows in the default `auto` mode,
   including one silent-no-op delete after a dry run promised the target.
2. **Recurring `span="all_occurrences"` delete is a false success**: it excludes only
   the first occurrence, reports the whole series deleted, and future occurrences
   survive.
3. **`manage_calendars` delete always fails** with a raw
   `AppleEvent handler failed` string; the script's object-addressing form is the
   cause (a working form was identified live).
4. **All-day create echo misstates `start_utc`** for cross-timezone all-day events
   (stored date is correct; only the response echo is wrong).

Cleanup is complete: final audits on both engines found zero test artifacts anywhere
(details in section 5).

## 1. Step-by-step results

Engine column is the engine that actually served the call (`auto` resolved to
EventKit for reads, AppleScript for all writes). Wall times over 30s are flagged.

| # | Step | Tool call | Engine | Wall | Result | Payload excerpt |
|---|------|-----------|--------|------|--------|-----------------|
| 1a | Create throwaway calendar | `manage_calendars(action="create", name="MCP Test Calendar 2026-07-10")` | applescript (write) | 0.26s | PASS | `{"action": "create", "created": true, "calendar": "MCP Test Calendar 2026-07-10"}` |
| 1b | Verify on AppleScript engine | `list_calendars()` | applescript | 1.32s | PASS | 18 calendars, test calendar present (AppleScript also surfaces `Scheduled Reminders` and `Siri Suggestions`, which EventKit filters) |
| 1c | Verify on EventKit engine | `list_calendars()` | eventkit | 0.08s | PASS | 16 calendars, test calendar present |
| 2a | Timed event, alarm, explicit tz | `create_event(title="MCP smoke timed event", start="2026-07-11T14:00:00", end="...15:00:00", timezone="America/Chicago", alarms_minutes_before=[10])` | applescript (write) | 0.43s | PASS | `event_id: 62CB481C-...`, `start: 2026-07-11T14:00:00-05:00`, `start_utc: ...19:00:00+00:00`, `resolved_timezone: America/Chicago`, `alarms_minutes_before: [10]` |
| 2b | Round-trip via get_events_by_id (AppleScript) | `get_events_by_id([62CB481C-...])` | applescript | 1.77s | PASS | Full detail back: start/end, both UTC fields, `alarms_minutes_before: [10]` round-tripped exactly |
| 2c | Round-trip via get_events_by_id (default auto -> EventKit) | same id | eventkit | 0.10s | **FAIL (bug 1)** | `{"events": [], "missing": ["62CB481C-..."]}`; EventKit lists the same event as id `51F26FE5-...` |
| 3a | All-day event, Asia/Tokyo | `create_event(start="2026-07-15", all_day=True, timezone="Asia/Tokyo")` | applescript (write) | 0.39s | PASS (echo caveat, bug 4) | Created; stored on the requested calendar date |
| 3b | All-day date verification | `get_events_by_id`, host tz and Asia/Tokyo | applescript | 1.77-1.79s | PASS | Host tz read: `start: 2026-07-15T00:00:00-05:00`, `end: 2026-07-15T23:59:59-05:00`. The event sits on July 15, not shifted a day. F1 regression fix confirmed live |
| 4a | Conflict warn | `create_event(start=14:30, end=15:30, on_conflict="warn")` overlapping 2a | applescript (write) | 0.35s | PASS | `created: true, has_conflicts: true, conflicts: [{"title": "MCP smoke timed event", "start": "2026-07-11T14:00:00-05:00", ...}]` |
| 4b | Conflict block | `create_event(start=14:15, end=14:45, on_conflict="block")` | n/a (blocked pre-write) | 0.10s | PASS | `EVENT_CONFLICT`: "2 existing event(s) overlap the requested time; nothing was created." with both conflicts named; later listing confirmed nothing was created |
| 5a | PATCH update, default auto mode | `update_event(event_id=62CB481C-..., title=..., start=15:00, duration_minutes=60)` | eventkit lookup | 0.11s | **FAIL (bug 1)** | `EVENT_NOT_FOUND`: "Event '62CB481C-...' was not found inside the lookup window" |
| 5b | PATCH update, AppleScript engine | same call, `APPLE_MAIL_CALENDAR_ENGINE=applescript` | applescript | 7.44s | PASS | `updated: true`, diff shows exactly title/start/end; conflict warn against the 4a event correctly excludes itself |
| 5c | PATCH verification | `get_events_by_id` | applescript | 1.79s | PASS | Title and time changed; `alarms_minutes_before: [10]` and all unpassed fields untouched |
| 6a | Batch dry run | `batch_create_events([3 items], dry_run=True)` | applescript (read of target) | 0.12s | PASS | `would_create` previews all 3 with resolved times; follow-up list shows still only 3 pre-batch events on the calendar (nothing created) |
| 6b | Batch real | same, `dry_run=False` | applescript (write) | 0.81s | PASS | `created_count: 3, failed_count: 0`, per-item `event_id`s |
| 6c | Batch verification | `list_events(calendar=test cal)` | applescript | ~2s | PASS | All 3 batch events plus the 3 earlier events, 6 total |
| 7a | EventKit read of a batch event id | `list_events(query="MCP batch alpha")` | eventkit | 0.10s | PASS (read) | EventKit id `BF69E836-...` vs AppleScript create id `8D7FE106-...`: **ids differ** |
| 7b | Delete EventKit id via AppleScript engine, dry run | `delete_events([BF69E836-...])` | applescript | 1.57s | **FAIL (bug 1c)** | `EVENT_NOT_FOUND`: "1 of 1 ids did not resolve inside the lookup window; nothing was deleted." |
| 7c | Same delete under default auto | dry run then real | eventkit resolve + applescript write | 0.10s / 0.96s | **FAIL (bug 1c, worst shape)** | Dry run promises `would_delete: [MCP batch alpha]`; real run returns `deleted_count: 0, errors: []` and the event survives (silent no-op) |
| 8a | Bulk delete remaining one-offs, dry run | `delete_events([6 AppleScript uids])` | applescript | 6.89s | PASS | `would_delete` lists all 6 with titles and starts |
| 8b | Bulk delete real | same, `dry_run=False` | applescript | 9.04s | PASS | `deleted_count: 6, errors: []`; verification list: `total_matched: 0` |
| 9a | Recurring create | `create_event(recurrence="FREQ=WEEKLY;BYDAY=MO", start=2026-07-13T16:00)` | applescript (write) | 0.39s | PASS | `recurrence_rule: "FREQ=WEEKLY;BYDAY=MO"` echoed |
| 9b | 3-week expansion, AppleScript | `list_events(2026-07-12 .. 2026-08-02)` | applescript | ~2s | PASS | Exactly 3 occurrences (Jul 13, 20, 27), `expansion: "python"`, `recurring_lookback_days: 400` disclosed |
| 9c | 3-week expansion, EventKit | same window | eventkit | ~0.1s | PASS | Same 3 occurrences, `expansion: "native"` (under the EventKit id namespace) |
| 9d | Series delete, dry run | `delete_events([uid], span="all_occurrences")` | applescript | 1.71s | PASS | Preview names the series master, `recurring_note` present |
| 9e | Series delete, real | same, `dry_run=False` | applescript | 1.96s | **FAIL (bug 2)** | Tool: `deleted_count: 1, recurring_deleted_whole_series: true`. Store: only Jul 13 excluded; Jul 20 and Jul 27 still live (stable after 20s; second delete attempt repeated the false success with no store change) |
| 10a | Attendee gate, no confirm | `create_event(attendees=["test-invitee@example.invalid"])` | n/a (gate) | <0.01s | PASS | `INVITE_SEND_REQUIRES_CONFIRM`, nothing created |
| 10b | Attendee gate, confirmed | same + `send_invitations=True` | applescript (write) | 0.52s | PASS | `created: true`, `attendees: ["test-invitee@example.invalid"]`, `invitation_delivery: "platform_dependent"` with the delivery note |
| 10c | Attendee event cleanup | `delete_events` dry then real | applescript | 1.71s / 1.98s | PASS | Preview then `deleted_count: 1` |
| 11a | Calendar delete, dry run | `manage_calendars(action="delete", dry_run=True)` | applescript (count) | 0.26s | PASS | `event_count: 1` (the bug-2 residual series master; would have been 0 otherwise), correct `next_step` naming the confirm chain |
| 11b | Calendar delete, real | `dry_run=False, confirm_delete_calendar=True, force_nonempty=True` | applescript (write) | 0.37s | **FAIL (bug 3)** | `Error: Calendar write failed: Calendar got an error: AppleEvent handler failed.` (plain text, not a structured code); failed identically on a second attempt; calendar still present |
| 11c | Calendar delete fallback (manual) | raw `osascript -e 'tell application "Calendar" to delete (first calendar whose name is "MCP Test Calendar 2026-07-10")'` | raw AppleScript | ~1s | PASS | Exit 0; cascade removed the residual series master with it |
| 11d | Gone on both engines | `list_calendars()` x2 | applescript / eventkit | 1.3s / 0.08s | PASS | AppleScript count back to 17, EventKit back to the pre-test 15; test calendar absent from both |
| 12a | Cleanup audit, EventKit | `list_events(2026-07-09 .. 2026-08-10, query="MCP")`, all calendars | eventkit | 0.13s | PASS | `total_matched: 0` |
| 12b | Cleanup audit, AppleScript | same call | applescript | **84.59s (flagged, >30s)** | PASS | `total_matched: 0`, `calendar_errors: []`, `budget_exhausted: false` |

## 2. Bugs found (exact payloads)

### Bug 1 (major): the two engines use different event-id namespaces, breaking cross-engine flows in the default auto mode

Root cause, established with a direct PyObjC probe of the created event:

- AppleScript `uid` == EventKit `calendarItemIdentifier` (`62CB481C-879D-4B31-8A80-F946B112C6DB`)
- The EventKit read engine reports `calendarItemExternalIdentifier`
  (`plugin/apple_mail_mcp/calendar_core/eventkit.py` line 163), which is a different
  value (`51F26FE5-37B7-4D7B-86BF-EAE4304FFE3F`) for events on this calendar
  (source "Default", local). The two never matched for any event in this session.
- The AppleScript write path matches deletions/updates by `whose ... uid`
  (`calendar_core/scripts_write.py`), i.e. the calendarItemIdentifier namespace.

Live consequences, all in shipped-default `auto` mode:

a. `create_event` id fails `get_events_by_id` (row 2c):

```json
{"events": [], "missing": ["62CB481C-879D-4B31-8A80-F946B112C6DB"], "engine": "eventkit", ...}
```

b. `create_event` id fails `update_event` (row 5a):

```json
{"error": true, "code": "EVENT_NOT_FOUND", "message": "Event '62CB481C-879D-4B31-8A80-F946B112C6DB' was not found inside the lookup window. Widen lookup_days_back/lookup_days_ahead or pass the calendar it lives on."}
```

c. The reverse direction is worse (rows 7b/7c). An EventKit-read id under the
AppleScript engine fails loudly (`EVENT_NOT_FOUND`, acceptable). But under `auto`,
the EventKit resolver validates the dry run:

```json
{"dry_run": true, "would_delete": [{"event_id": "BF69E836-3720-4269-8E22-91A8911414A9", "title": "MCP batch alpha", ...}], "next_step": "Re-run with dry_run=False to delete these events."}
```

and the real run then silently deletes nothing, with no error entry:

```json
{"dry_run": false, "requested": 1, "deleted": [], "deleted_count": 0, "errors": [], ..., "engine": "applescript"}
```

The event survived (verified by listing). The dry-run preview contract is violated:
the preview promises a delete the write engine cannot perform. Related cosmetic
symptom: `conflicts` rows carry EventKit ids while the enclosing `create_event`
response carries the AppleScript uid, so one payload mixes both namespaces.

Fix direction (for the lane, not applied in this session): make the EventKit engine
report `calendarItemIdentifier()` instead of `calendarItemExternalIdentifier()`; the
probe confirms that value is byte-identical to the AppleScript `uid` on this host.
Re-verify against a synced (iCloud/Google) calendar before shipping, since external
identifiers exist precisely because synced stores rewrite local ids.

### Bug 2 (major): recurring series delete reports success but deletes only the first occurrence

Row 9e. `delete_events(event_ids=[master uid], span="all_occurrences", dry_run=False)`
returned:

```json
{"dry_run": false, "requested": 1, "deleted": [{"event_id": "7777C7DD-3BDC-411F-8C9A-EA0F98A55C16", "title": "MCP recurring weekly"}], "deleted_count": 1, "errors": [], "span": "all_occurrences", "recurring_deleted_whole_series": true, "engine": "applescript"}
```

Actual store state, stable at +5s and +20s and after a second identical delete call
(which returned the same success payload with zero store effect):

- EventKit: occurrences 2026-07-20 and 2026-07-27 still present (only Jul 13 gone),
  which is what Calendar.app UI shows the user.
- Raw AppleScript: the master still exists with rule
  `FREQ=WEEKLY;INTERVAL=1;BYDAY=MO` (1 event on the calendar).
- The tool's own AppleScript read then re-expands all 3 occurrences (its Python
  expansion cannot see the exclusion), so the surface even disagrees with itself
  across engines after the delete.

Effect: `AppleScript delete` on the matched recurring event behaved as a
first-occurrence exclusion on this host (macOS Darwin 25.5, Calendar.app current),
not a series delete. `recurring_deleted_whole_series: true` and the skill teaching
("Recurring ids delete the whole series") are both false in effect. A destructive
tool reporting false success is the highest-severity class in the safety model. The
series was actually removed only by the calendar cascade delete in step 11.

### Bug 3 (major): manage_calendars delete fails on this host; script addressing form is the cause

Rows 11b/11c. The tool call (twice, identical result):

```
Error: Calendar write failed: Calendar got an error: AppleEvent handler failed.
```

The tool's `delete_calendar_script` uses:

```applescript
set targetCal to calendar "MCP Test Calendar 2026-07-10"
delete targetCal
```

The immediately following raw probe using a whose-specifier succeeded (exit 0) and
cascade-deleted the calendar and its residual event:

```applescript
delete (first calendar whose name is "MCP Test Calendar 2026-07-10")
```

Two sub-issues: (a) the variable-bound `calendar "NAME"` reference form fails where
the inline `first calendar whose name is` specifier works, so the script needs the
working addressing form; (b) the failure surfaces as a plain `Error:` string rather
than a structured `ToolError` code, so agents get no machine-readable remediation.
Note `rename_calendar_script` uses the same failing addressing form and was not
exercised live; treat it as suspect until verified.

### Bug 4 (minor): all-day create echo misstates start_utc for cross-timezone creates

Row 3a. `create_event(start="2026-07-15", all_day=True, timezone="Asia/Tokyo")`
echoed `"start": "2026-07-15T00:00:00+09:00", "start_utc": "2026-07-14T15:00:00+00:00"`
(Tokyo midnight). The stored event, read back, is host-local midnight:
`"start": "2026-07-15T00:00:00-05:00", "start_utc": "2026-07-15T05:00:00+00:00"`.
The important invariant held: the event sits on the requested calendar date
(July 15) with no day shift, which is what the F1 regression fix targeted. Only the
create response's instant fields describe a moment 14 hours away from what was
stored. Read-backs are correct; only the write echo is affected.

## 3. Timing summary

| Class | Calls | Range |
|-------|-------|-------|
| EventKit reads (list/get/audit) | 8 | 0.08 - 0.13s |
| AppleScript single-calendar reads (get/list on test calendar) | 7 | 1.3 - 2.0s |
| AppleScript writes (create/update/delete/calendar ops) | 12 | 0.26 - 9.04s |
| update_event with conflict pass | 1 | 7.44s |
| Bulk delete 6 ids (dry / real) | 2 | 6.89s / 9.04s |
| **AppleScript all-calendar audit fan-out (17 calendars, 32-day window)** | 2 | **84.59s (flagged, >30s)** |

Only the AppleScript full fan-out crossed the 30s flag line: 84.6s against EventKit's
0.13s for the identical query (a ~670x gap on this real store). It stayed well inside
the 240s call budget with `budget_exhausted: false` and zero `calendar_errors`, but it
is live confirmation of the F1/F2 cost class and of the EventKit fast path as the
structural fix. Nothing hung; no timeout fired; no runaway osascript processes were
left behind.

## 4. Deviations from the scripted battery

- Step 5 ran twice: the default-mode attempt is recorded as the bug 1b evidence; the
  PATCH verification itself was completed under `APPLE_MAIL_CALENDAR_ENGINE=applescript`.
- Step 7's "then delete that exact id via the AppleScript write path" cannot succeed
  on this host because the id namespaces differ (that is the finding). Both engine
  variants were recorded; batch alpha was then deleted by its AppleScript uid inside
  step 8's bulk call.
- Step 8 deleted six events (all one-offs: 3 batch, timed, conflict-warn, all-day),
  not just the batch remainder, so step 11 could approach an empty calendar.
- Step 11's dry-run `event_count` was 1, not 0, because the bug-2 residual series
  master was still on the calendar; the documented `force_nonempty=True` chain was
  used, failed (bug 3), and cleanup completed via the raw osascript fallback in 11c.

## 5. Cleanup confirmation

- `manage_calendars` dry-run previews, `delete_events` dry-runs, and both real delete
  passes only ever targeted events created by this session on the throwaway calendar,
  addressed by exact id.
- Final `list_calendars`: test calendar absent on both engines; EventKit count back to
  the pre-test 15, AppleScript back to 17 (its native view includes `Scheduled
  Reminders` and `Siri Suggestions`).
- Final artifact audit, all calendars, 2026-07-09 through 2026-08-10, `query="MCP"`:
  `total_matched: 0` on EventKit (0.13s) and on AppleScript (84.6s), zero
  `calendar_errors`, no budget exhaustion.
- Zero test artifacts remain anywhere. No pre-existing calendar or event was modified,
  moved, or deleted at any point in the session.
