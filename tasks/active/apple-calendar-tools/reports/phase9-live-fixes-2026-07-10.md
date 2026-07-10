# Phase 9: Apple Calendar tools, live fixes for the four phase-8 defects

Branch `feat/apple-calendar-tools`. Executed 2026-07-10 with macOS Calendar
Automation and EventKit full access both granted on the production Mac
(`cayman-mac-mini`). This phase fixes the four defects found by the phase-8 write
battery ([`phase8-live-writes-2026-07-10.md`](phase8-live-writes-2026-07-10.md))
and re-verifies each against real Calendar.app.

All live writes were confined to throwaway calendars whose names start
`MCP Fix Test`; no pre-existing calendar or event was modified. Read-only probes
compared identifiers on real calendars (`cayman@agenticassets.ai` Google-CalDAV,
iCloud `Calendar`) without any write. Final audit on both engines found zero test
artifacts.

## Headline

| Bug | Severity | Disposition | Live proof |
|-----|----------|-------------|------------|
| 1 dual id namespace | major | FIXED | create id round-trips through EventKit get/update/delete in default `auto` mode |
| 2 recurring delete false success | major | FIXED (honest report) | returns `RECURRING_DELETE_INCOMPLETE` with surviving dates, not a false whole-series success |
| 3 calendar delete broken | major | FIXED | `manage_calendars(action="delete")` deletes cleanly; write errors now structured |
| 4 all-day echo | minor | FIXED | all-day create echo now byte-matches the stored event |

## Read-only identifier probe (bug 1 pre-work, required before shipping)

Paired the SAME event instant across engines on two account types. AppleScript
`uid` equals EventKit `calendarItemIdentifier` on every account type; the external
identifier never matches.

| Account type | Event instant | AppleScript `uid` | EK `calendarItemIdentifier` | EK `calendarItemExternalIdentifier` |
|--------------|---------------|-------------------|-----------------------------|-------------------------------------|
| Google CalDAV (`cayman@agenticassets.ai`) | 2026-06-10 09:15 | `E110E16B-…` | `E110E16B-…` (match) | `3EA268DB-…` (differs) |
| Google CalDAV | 2026-06-10 16:00 | `C873A2F8-…` | `C873A2F8-…` (match) | `7na2…@google.com` (differs) |
| iCloud (`Calendar`) | 2026-06-27 19:00 | `1A749E2B-…` | `1A749E2B-…` (match) | `eae1727504…` hex (differs) |

Conclusion: `calendarItemIdentifier` is the authoritative, AppleScript-resolvable
id on Google, iCloud, and local stores alike. The external identifier is a
sync-source id (`...@google.com` on Google, hex on iCloud, a distinct UUID on
local) and must be a secondary field only.

---

## Bug 1: dual event-id namespace across engines

- **Root cause.** The EventKit read engine reported
  `calendarItemExternalIdentifier()` as `event_id`
  (`calendar_core/eventkit.py`). The AppleScript write path matches
  update/delete targets by `uid`, which equals `calendarItemIdentifier`, so ids
  minted by writes never resolved under EventKit reads (and vice versa). In the
  shipped default `auto` mode (EventKit reads, AppleScript writes) this broke
  create-then-get, create-then-update, and read-then-delete, including a silent
  no-op delete after a dry run promised the target.
- **Fix.** `_map_event` now reports `calendarItemIdentifier()` as `event_id`
  and keeps the external identifier as a secondary `external_id` payload field
  (surfaced by `records.event_payload` only when present, so AppleScript payloads
  are unchanged). Verified account-type-safe by the read-only probe above.
- **Mocked test.** `tests/calendar_surface/test_eventkit_engine.py`: `_EventStub`
  now exposes a distinct `calendarItemIdentifier` (round-trippable) and
  `calendarItemExternalIdentifier` (`ext-…`); `test_fetch_window_maps_records`
  asserts `event_id == "EK-1"` and `external_id == "ext-EK-1"`.
- **Live proof (default `auto` mode, throwaway calendar).** create returned
  `event_id: E5BA9047-…`; the same id resolved in every downstream call:

```json
// get_events_by_id (auto -> EventKit): resolved, was {"events":[],"missing":[...]} in phase 8
{"events":[{"event_id":"E5BA9047-…","engine":"eventkit","external_id":"513432D9-…", ...}],"missing":[]}
// update_event (auto): updated, was EVENT_NOT_FOUND in phase 8
{"updated":true,"event_id":"E5BA9047-…","engine":"applescript"}
// delete_events (auto): actually deleted, was a silent no-op in phase 8
{"deleted_count":1,"deleted":[{"event_id":"E5BA9047-…","title":"B1 timed updated"}],"errors":[]}
// post-delete get on both engines: {"missing":["E5BA9047-…"]}
```

---

## Bug 2: recurring `span="all_occurrences"` delete reported false success

- **Root cause.** Calendar.app AppleScript `delete` on a recurring event removes
  only the targeted occurrence (it records an exclusion), yet the tool set
  `recurring_deleted_whole_series: true` unconditionally. Live experiments proved
  **no** AppleScript form deletes a whole series on this host (macOS Darwin 25.5,
  current Calendar.app):

  | Form tried | Result (8-occurrence weekly series) |
  |------------|-------------------------------------|
  | A `delete anEvent` (shipped) | 7 survive (only first excluded) |
  | B `set recurrence to ""` then delete | 7 survive (clear ignored) |
  | C `delete (every event whose uid is X)` (bounded) | 7 survive |
  | D `set recurrence to missing value` then delete | rule-clear ignored |
  | E repeat-until-gone loop (61 passes) | 1 removed (loop does not accumulate) |
  | G unbounded `delete (every event whose uid)` | 24 survive |
  | H COUNT=3 series, loop (11 passes) | 1 removed, 2 survive |

  Rule-clearing is silently ignored and per-occurrence deletes do not accumulate
  in a single script, so a whole-series delete is not achievable via AppleScript.
  (An EventKit `removeEvent:span:` write path is the future route, deliberately
  out of scope for the 3.10.0 single AppleScript write path.) Note the asymmetry:
  live-tested `update` on a recurring series DOES mutate the whole series (all 8
  occurrences changed), so only the delete claim was false.
- **Fix.** `delete_events` now verifies recurring targets after the write:
  `helpers.surviving_recurring_occurrences` re-queries the series over the widened
  write window and, if occurrences survive, the tool returns the structured
  `RECURRING_DELETE_INCOMPLETE` (surviving dates + the occurrence(s) removed +
  a Calendar.app remediation) instead of success. `recurring_deleted_whole_series`
  is `true` only when verification finds zero survivors. The dry-run note and the
  docstring were corrected to state the best-effort/verify reality.
- **Mocked test.** `tests/calendar_surface/test_delete_events.py::TestRecurringVerify`:
  `test_survivors_report_incomplete_not_success` (default fake keeps serving the
  series → `RECURRING_DELETE_INCOMPLETE`) and `test_verified_removal_reports_whole_series`
  (a stateful fake whose series vanishes on the verify pass → success).
- **Live proof.** Real delete of a weekly series returned a structured partial
  failure, not success:

```json
{"error":true,"code":"RECURRING_DELETE_INCOMPLETE",
 "message":"Calendar.app scripting cannot delete a whole recurring series; it removed only individual occurrence(s) and the rest survive. The delete was verified after running and is incomplete.",
 "remediation":{"surviving_occurrences":{"DCAE39B8-…":["2026-07-20","2026-07-27", ... "2026-10-05"]},
                "occurrences_removed":[{"event_id":"DCAE39B8-…","title":"B2 weekly"}],
                "manual":"Open Calendar.app, select any occurrence of the series, choose Delete, and pick 'Delete All' to remove the whole series."}}
```

  Both engines confirmed the survivors post-delete (EventKit exact, AppleScript
  over-reports by re-expanding, which errs safe). The residual series was then
  fully removed by the calendar cascade delete in bug 3 (zero survivors on both
  engines in the final audit).

---

## Bug 3: `manage_calendars(action="delete")` failed with a raw error string

- **Root cause.** `delete_calendar_script` used the variable-bound form
  (`set targetCal to calendar "NAME"` then `delete targetCal`), which fails live
  with `Calendar got an error: AppleEvent handler failed`. The failure also
  surfaced as raw `Error:` text, not a structured code.
- **Fix.** The delete script now uses the inline whose-specifier
  `delete (first calendar whose name is "NAME")`, which deletes cleanly including
  non-empty calendars (live-verified: it cascade-removed the residual series).
  `_map_write_error` now raises the structured `CALENDAR_WRITE_FAILED` (instead of
  a bare `Exception`) for generic Calendar.app write errors, so agents get
  machine-readable remediation. `rename_calendar_script` was left unchanged after
  live-verifying its addressing form works.
- **Mocked test.** `tests/calendar_surface/test_calendar_engine.py::test_generic_write_error_maps_to_structured_error`
  asserts `CALENDAR_WRITE_FAILED` with the detail in the message. The existing
  script-content and osacompile tests cover the new delete form.
- **Live proof.**

```json
// rename: was untested; verified live
{"renamed":true,"from":"MCP Fix Test Battery","calendar":"MCP Fix Test Battery Renamed"}
// delete dry-run: event_count=2 (recurring master + all-day event)
{"dry_run":true,"event_count":2,"next_step":"Re-run with dry_run=False and confirm_delete_calendar=True and force_nonempty=True ..."}
// delete real (confirm + force): was "AppleEvent handler failed" in phase 8
{"deleted":true,"calendar":"MCP Fix Test Battery Renamed","event_count":2}
```

---

## Bug 4: all-day create echo misstated the instant

- **Root cause.** For an all-day create the event is stored on the host-local
  calendar date (the requested-zone date carried through unchanged, not converted
  to a host-local instant). The echo used `isoformat_pair(start_dt, tz)`, i.e. the
  requested-zone midnight instant, which for a far zone described a moment hours
  away from what a later read returns.
- **Fix.** New `window.all_day_echo_instants` computes host-local midnight of the
  stored calendar date; `create_event` (and `batch_create_events`, same root
  cause) use it on the all-day path so the echo matches `get_events_by_id`.
- **Mocked test.** `tests/calendar_surface/test_create_event.py::test_all_day_echo_matches_host_local_midnight`
  asserts an `Asia/Tokyo` all-day create echoes host-local midnight of 2026-07-15,
  not `+09:00`.
- **Live proof (all-day `Asia/Tokyo` create, then read back).**

```
echo   start = 2026-07-15T00:00:00-05:00  start_utc = 2026-07-15T05:00:00+00:00
stored start = 2026-07-15T00:00:00-05:00  start_utc = 2026-07-15T05:00:00+00:00   (byte-identical)
```

  Phase 8 echoed `2026-07-14T15:00:00+00:00` (Tokyo midnight, 14 hours off).

---

## Verification status

| Gate | Result |
|------|--------|
| Mocked suite (`PYTEST_ADDOPTS='' pytest tests/`) | 1392 passed, 45 subtests passed |
| Collected-test count (`tools/expected_test_count.txt`) | 1389 -> 1392 (+3), gate OK |
| Lint trio (`ruff check`, `ruff format --check`, `mypy --strict` on `plugin/apple_mail_mcp/`) | clean |
| Module line budget | OK, no module over 600 LOC, no baseline regression |
| Skill reference sync (`sync_skill_references.py --check`) | OK |
| `bash tools/gates/dev-check.sh release` | green end to end (lint, artifact rebuild + validate, pytest, wrapper) |
| Live battery re-run (all four bugs, default + both engines) | all assertions passed |
| Cleanup audit (both engines) | zero `MCP Fix Test` calendars, zero stray events |

## Files touched

Source: `calendar_core/eventkit.py`, `calendar_core/records.py`,
`calendar_core/scripts_write.py`, `calendar_core/engine.py`,
`calendar_core/window.py`, `calendar_core/__init__.py`,
`tools/calendar/events_create.py`, `tools/calendar/events_batch.py`,
`tools/calendar/events_delete.py`, `tools/calendar/helpers.py`.

Tests: `test_eventkit_engine.py`, `test_calendar_engine.py`,
`test_delete_events.py`, `test_create_event.py`.

Docs: `CHANGELOG.md` (four 3.10.0 `### Fixed` bullets),
`plugin/skills/references/calendar-safety-limits.md` (+ synced per-skill copies),
`plugin/skills/calendar-operator/SKILL.md`, `tools/expected_test_count.txt`.

Artifacts rebuilt: `apple-mail-plugin.zip`, `apple-mail.plugin`,
`apple-mail-mcp-v3.10.0.mcpb`.

## Notes and residual limitation

Bug 2's "zero surviving occurrences via the tool's own delete" outcome is not
achievable on this host: Calendar.app AppleScript cannot delete a whole recurring
series (proven live with forms A-H). The fix makes the tool honest, returning
`RECURRING_DELETE_INCOMPLETE` with the surviving dates rather than a false
success, and points the user to Calendar.app (or the future EventKit
`removeEvent:span:` write path) for a reliable series delete. The zero-survivor
end state is reached via the calendar cascade delete (bug 3), which was exercised
in the battery and confirmed by the final audit.
