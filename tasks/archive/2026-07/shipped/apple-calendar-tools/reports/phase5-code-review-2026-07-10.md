# Phase 5: Apple Calendar tools adversarial code review (v3.10.0)

Branch `feat/apple-calendar-tools`, reviewed 2026-07-10 against the working tree
(uncommitted). Scope: the ten new calendar tools, the `calendar_core/` engine
seam, mode gating, and the `tests/calendar_surface/` suite. Style contract held
(no em dashes, no banned words) in the reviewed prose.

Method: read every new module in full, traced the read/write/gating paths,
reproduced the one confirmed correctness bug in a scratch interpreter, and ran
`pytest tests/calendar_surface` (312 passed) to confirm the baseline the review
sits on. Full suite count is 1365 per `tools/expected_test_count.txt`.

## Verdict

No blockers. Safety enforcement is solid (see the affirmative section below).
One major correctness bug (all-day events land on the wrong calendar day when
the caller passes a non-host `timezone`), one major-leaning silent-omission risk
in the AppleScript recurring path, and a handful of minors. The bug in F1 is
CONFIRMED and reproduced; it is masked by the test doubles (F6), so it would ship
green.

---

## Safety enforcement: verified (adversarial pass, no bypass found)

I tried to construct a bypass for each safety claim and could not. Recording the
positive proof because it is load-bearing for the release:

- **`--read-only` blocks every calendar write path.** Registry removal in
  `__main__.py:61-64` removes `SEND_TOOLS + CALENDAR_WRITE_TOOLS +
  CALENDAR_DESTRUCTIVE_TOOLS` (all five write/destructive tools). Every write
  tool also carries an internal backstop that fires before any engine work:
  `create_event` (`events_create.py:146`), `update_event`
  (`events_update.py:149`), `batch_create_events` (`events_batch.py:123`),
  `manage_calendars` create/rename (`calendars_manage.py:104,121`) and delete
  (`calendars_manage.py:137` via `calendar_delete_blocked` which calls
  `calendar_write_blocked` first), `delete_events` (`events_delete.py:72`). The
  CLI (`cli/commands.py`) exposes only reads plus `calendar-grant`, so the
  registry-bypass concern is covered by the backstops.
- **`--draft-safe` blocks deletes AND invitation sending.** Deletes:
  `calendar_delete_blocked` (`helpers.py:75-91`) returns `CALENDAR_DELETE_BLOCKED`
  when `DRAFT_SAFE and not CALENDAR_ALLOW_DESTRUCTIVE`; the env unlock is
  environment-only and cannot be passed as an argument, and read-only still wins
  because `calendar_write_blocked` is checked first. Invitations: `attendee_gate`
  (`helpers.py:94-138`) returns `INVITE_SEND_BLOCKED` under read-only or
  draft-safe before validating, and `INVITE_SEND_REQUIRES_CONFIRM` when
  `send_invitations` is not True. `batch_create_events` forbids the `attendees`,
  `recurrence`, and `send_invitations` keys entirely (`events_batch.py:35`, plus
  the allowlist check at `events_batch.py:49` rejects capitalized or padded key
  variants), so attendees cannot be smuggled through the batch path.
- **Every event read requires a bounded window with a hard cap.**
  `bounded_calendar_window` (`window.py:87-156`) is the only issuer of
  `CalendarWindow` tokens; a zero-width or absent window raises
  `UNBOUNDED_CALENDAR_SCAN`, width over the cap raises `CALENDAR_WINDOW_TOO_WIDE`,
  and engines refuse foreign tokens via `require_issued_window`
  (`window.py:159-166`, enforced in `engine.py:144,165,247,271`). The AppleScript
  builders are the only emitters of `every event of ... whose`, every predicate is
  date-bounded on both ends, and the calendar lint
  (`test_calendar_scripts.py:57-97`) enforces both invariants plus the
  no-recurrence-in-`whose` rule.
- **Mutations are ID-first only.** `delete_events` accepts exact ids
  (`normalize_event_ids`), resolves all of them inside the window first, and fails
  the whole call on any miss (`events_delete.py:109-120`). `update_event` takes
  `event_id` only, no fuzzy target. `manage_calendars` rename/delete accept the
  exact name or `calendar_id` only (`_resolve_exact_target`,
  `calendars_manage.py:19-51`); fuzzy resolution never reaches a destructive
  target.
- **Injection escaping is airtight.** All user text reaches AppleScript through
  `escape_applescript` (backslash first, then quote, then newlines/tabs, then
  the Unicode line/paragraph separators). Dates cross the boundary only as
  validated integer components (`applescript_date_block`), uids are
  shape-validated to reject `"`, `\`, control chars, and `|||`
  (`validation.py:41`) and escaped again as defense in depth, RRULEs are rebuilt
  from an allowlist grammar (`recurrence.py`), and read rows run
  `sanitize_pipe_delimited_field` before the `|||` join with Python-side field
  counting. I checked titles/notes/locations with quotes, backslashes, and
  unicode and found no unescaped interpolation site.

---

## Findings

### F1 (major, CONFIRMED) — all-day events land on the wrong day when `timezone` differs from the host

`plugin/apple_mail_mcp/calendar_core/scripts_read.py:41` (`applescript_date_block`
does `local = dt.astimezone()`) combined with the all-day branch of
`plugin/apple_mail_mcp/tools/calendar/events_create.py:48-55`.

An all-day create sets `start_dt` to midnight in the requested IANA zone
(`resolve_event_times`, `events_create.py:49`), then the engine converts that
instant to the host's local wall clock before emitting the integer date block.
When the requested zone is far enough east of the host that midnight-in-zone
falls on the previous calendar day locally, the day component (and therefore the
all-day event's date) rolls back one day. `create_event`, `batch_create_events`
(via `resolve_event_times`), and `update_event` all-day paths share this.

Reproduced on a CDT host:

```
create_event(all_day=True, start="2026-07-10", timezone="Asia/Tokyo")
# applescript_date_block emits: set day of d to 9  (Tokyo midnight 07-10 == CDT 07-09 10:00)
# -> the all-day event is created on 2026-07-09, not 2026-07-10.
```

Failure scenario: an agent in Tokyo asks to block "all day July 10"; the event is
created on July 9. Symmetric one-day-forward shift for zones far west of the
host. Timed (non-all-day) events are correct because the host-local conversion
preserves the absolute instant; only all-day is wrong because the date is the
whole meaning.

Fix: for all-day events, do not tz-convert. Carry the naive calendar date
(year/month/day from the requested-zone wall clock) into `applescript_date_block`
directly, or add an `all_day` flag to the date block that skips `dt.astimezone()`
and uses the requested-zone components. Add a test with `timezone` set to a zone
several hours off the host (see F6).

### F2 (major, PLAUSIBLE) — AppleScript engine silently omits recurring series older than the 400-day lookback

`plugin/apple_mail_mcp/calendar_core/engine.py:167-172`
(`fetch_recurring_masters` lookback = `[window.start - RECURRING_LOOKBACK_DAYS,
window.start]`) and `constants.py:131` (`RECURRING_LOOKBACK_DAYS = 400`).

The AppleScript recurring path only finds masters whose `start date` falls inside
the 400-day lookback window, then projects their occurrences forward. A series
whose master started more than 400 days ago (a standing weekly meeting created two
years back, indefinite recurrence) is invisible to `list_events` and
`check_availability` on the AppleScript engine, with no flag in the response
telling the caller a horizon was applied.

Failure scenario: on a host where the EventKit fast path is unavailable (Claude
Desktop / Codex Desktop declare no Calendars usage string, per `eventkit.py:11`),
`list_events(days_ahead=7)` omits a 2-year-old weekly standup entirely, and
`check_availability` reports that slot free. The EventKit engine (native
expansion) avoids this and is the default when granted, so the miss is
engine-dependent, but the AppleScript engine is the guaranteed baseline where the
gap is silent.

Fix: surface the horizon in the response when the AppleScript recurring pass runs
(for example a `recurring_lookback_days` field or a `calendar_errors` note when
the master scan cap or lookback is hit), and/or document the limitation in the
recurring-master path so callers know long-running series need the EventKit
engine. Widening the constant is not free (it is the cost cap), so disclosure is
the honest fix.

### F3 (minor, CONFIRMED) — delete access-denied / calendar-not-found is a soft error, unlike create and update

`plugin/apple_mail_mcp/calendar_core/engine.py:286-296` (delete loop only maps
`ERROR_READONLY|||`) and `scripts_write.py:177-179` (the delete script's outer
`on error` emits `ERROR_EVENT|||`, not `ERROR_CALENDAR_WRITE|||`).

`create_event`/`update_event`/calendar-CRUD map a `-1743` write result to a
structured `CALENDAR_ACCESS_DENIED` with pane-specific remediation
(`engine.py:103-114`). The delete path routes only `ERROR_READONLY` through
`_map_write_error`; an Automation-denied delete (or a mid-call calendar failure)
lands in `delete_errors` as a raw string and the tool still reports
`"deleted": []` with `dry_run: False`, so the caller sees a "successful" empty
delete rather than the actionable access-denied remediation.

Fix: in `AppleScriptCalendarEngine.delete_events`, detect `-1743`/`not
authorized` in the collected `ERROR_EVENT` messages (or map the outer script
error) and raise `CALENDAR_ACCESS_DENIED` the way the other write paths do.

### F4 (minor, CONFIRMED) — `output_format="text"` returns raw JSON for five tools

`plugin/apple_mail_mcp/tools/calendar/helpers.py:414-422` (`finish` falls back to
`json.dumps` when no renderer is passed).

`delete_events`, `get_events_by_id`, `update_event`, `batch_create_events`, and
`manage_calendars` all call `finish(payload, output_format)` with no renderer, so
`output_format="text"` returns pretty-printed JSON. Their docstrings advertise
`"text" ... a readable summary`. Only `list_events`, `list_calendars`, and
`check_availability` pass a renderer.

Fix: either add compact text renderers for the five tools or drop the "readable
summary" language from their docstrings and state that they always return JSON.

### F5 (minor, PLAUSIBLE) — cross-engine recurring update/delete can report EVENT_NOT_FOUND

`plugin/apple_mail_mcp/tools/calendar/events_update.py:310` and
`scripts_write.py:135` (the write predicate is `whose start date >= windowStart
and start date <= windowEnd and uid is ...`).

When the read engine is EventKit (native occurrence expansion) and the target is
a recurring event, EventKit resolves an in-window occurrence whose external
identifier matches the master uid, so the lookup succeeds. The AppleScript write
then locates the event by the master's own `start date` inside the same lookup
window; for a series whose master started outside `[now-30, now+90]`, that
predicate matches nothing and the write returns `ERROR_NOT_FOUND` ->
`EVENT_NOT_FOUND`, even though the read found it. `delete_events` shares the
pattern. This overlaps the phase-4 "EventKit ids vs AppleScript uids" open risk 3
but the concrete failure is the window predicate, not the id shape.

Fix: for recurring targets (which already require `span='all_occurrences'`), widen
the write-side lookup window to cover the master, or locate recurring masters by
uid without the start-date bound (the uid is exact, so the bound adds no safety
for a single-id predicate). At minimum, note in the `update_event` docstring that
recurring targets need `lookup_days_back` wide enough to include the series
start.

### F6 (minor, CONFIRMED) — test doubles hide the timezone/date-block conversion

`tests/calendar_surface/conftest.py:125-127` (`FakeWriteEngine.create_event`
records the incoming aware `start` datetime and returns a synthetic id).

No test drives `applescript_date_block`, so the entire timezone -> host-local
integer-date conversion is unverified for semantic correctness. `test_create_event.py`
asserts on the aware `start` the fake stored, which is midnight in the requested
zone, so the F1 all-day shift passes green. The osacompile tests
(`test_calendar_scripts.py:254-329`) prove the scripts compile but never assert
the date components they emit.

Fix: add a unit test on `applescript_date_block` output for an all-day and a timed
datetime with `timezone` several hours off the host, asserting the emitted `set
day of ... to N` matches the intended calendar date. This both closes the
coverage gap and pins the F1 fix.

### F7 (minor, CONFIRMED) — `list_events` query matches only the notes preview, not full notes

`plugin/apple_mail_mcp/tools/calendar/helpers.py:216-222` (`_matches_query`) plus
the `list_events` docstring at `events_list.py:56,67`.

For non-detail list rows (all `list_events` rows), notes are carried only as
`notes_preview` truncated to `NOTES_PREVIEW_CHARS` (280). `_matches_query` checks
`notes_preview`, so a `query` that appears only past the first 280 characters of a
note is missed, though the docstring promises matching over "title, location, and
notes."

Fix: either state the 280-char preview limit in the docstring, or fetch/match
against full notes when a `query` is supplied (accepting the extra cost) so the
contract matches the behavior.

### F8 (minor, CONFIRMED) — `update_event` reports an attendee change when nothing is written

`plugin/apple_mail_mcp/tools/calendar/events_update.py:211-221,335-340`.

Passing `attendees=[]` (or any subset that only removes addresses, which
Calendar.app scripting cannot do) makes `requested != stored_attendees` true, so
`attendees_changed=True`, `add_attendees=[]`, `changes["attendees_added"]=[]`, and
the response gains `invitation_delivery: "platform_dependent"` and the attendee
note, while `build_event_set_lines` emits no attendee lines. The caller is told an
attendee change happened when it did not. Not a safety hole (nothing is sent),
just a misleading payload.

Fix: set `attendees_changed` only when `add_attendees` is non-empty, or emit an
explicit "attendee removal is unsupported; no change applied" note instead of the
delivery disclosure when the diff is removal-only.

---

## Consistency, churn, and layout (checked, no action required)

- Param/output conventions (`output_format`, `dry_run`, `timeout`, `calendar` vs
  `calendars`, structured `ToolError` codes) match the mail surface; `dry_run`
  defaults are correct per the plan (delete and calendar-delete default to
  dry-run; create/update write by default).
- Churn: fan-out is capped at `MAX_CALENDARS_PER_QUERY` (20) with a monotonic
  `CALL_BUDGET_SECONDS` (240) budget checked between calendars
  (`helpers.py:252-257`), per-pass scan caps (`EVENT_SCAN_CAP` 300,
  `RECURRING_MASTER_SCAN_CAP` 200), an `OCCURRENCE_SCAN_CEILING` (750) with a hard
  `_MAX_EXPANSION_STEPS` (20000) inside the RRULE loops, and per-call osascript
  timeouts. No unbounded fan-out or unbounded expansion path found.
- Module line budget: largest new modules are `helpers.py` (443),
  `engine.py` (364), `events_update.py` (341); all under 600.
- Version surfaces are all 3.10.0, the embedded tool count is 41, `Unreleased` is
  empty above `## 3.10.0 - 2026-07-10`, and the collected-test count file reads
  1365.
- Test quality overall is strong: the suite asserts error codes, payload fields,
  write-engine call arguments, `set_lines` content, the F5 attendee set-diff
  (`test_update_event.py:101-127`), the conflict/self-exclusion logic, and the
  full mode matrix (`test_calendar_gating.py`). F6 is the one real coverage gap.

## Suggested disposition

- Fix F1 before any live write verification (silent wrong-date data), and add the
  F6 test alongside it.
- Fix or document F2 before relying on the AppleScript engine for recurring
  reads (Desktop hosts).
- F3, F4, F5, F7, F8 are polish and can ship as follow-ups if time-boxed, but F3
  and F4 are cheap and improve the agent-facing contract.
