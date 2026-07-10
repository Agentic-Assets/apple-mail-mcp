# Phase 7: Apple Calendar adversarial-review fixes (v3.10.0)

Branch `feat/apple-calendar-tools`, applied 2026-07-10. Remediates findings F1
through F8 from `phase5-code-review-2026-07-10.md`. Each finding was
independently verified against the source before any change. All fixes fold
into the unreleased `## 3.10.0` CHANGELOG entry (no separate version bump: the
release gate only requires the latest heading to match `pyproject` and the
Unreleased body to hold no bullets, both satisfied).

Style contract held: no em dashes, no banned words in code, comments, or prose.

## Finding -> action -> evidence

| Finding | Severity | Disposition | Action | New tests |
|---------|----------|-------------|--------|-----------|
| **F1** all-day tz date shift | major, confirmed | FIXED | `applescript_date_block(..., all_day=True)` skips `astimezone` and carries the requested-zone calendar date; threaded through `engine.create_event` (covers create + batch) and `build_event_set_lines` (covers update). Reproduced day-9 bug, verified day-10 after fix. | `test_calendar_date_block.py` (7 tests) |
| **F2** silent recurring lookback | major, confirmed | FIXED (disclosure) | New `recurring_lookback_disclosure(engine, expand_recurring)` in `helpers.py`; `list_events` and `check_availability` now emit `recurring_lookback_days` + `recurring_coverage_note` when the AppleScript recurring pass runs. Docstrings + `calendar-safety-limits.md` + `calendar-operator` skill aligned. `RECURRING_LOOKBACK_DAYS` NOT widened. | `test_list_events.py::TestRecurringExpansion` (3 tests) |
| **F3** delete access-denied soft | minor, confirmed | FIXED | `AppleScriptCalendarEngine.delete_events` re-routes collected `ERROR_EVENT` lines containing `-1743`/`not authorized` through `_map_write_error` -> `CALENDAR_ACCESS_DENIED`, like create/update. Non-auth per-event errors stay soft. | `test_calendar_engine.py::TestWriteErrorMapping` (2 tests) |
| **F4** dishonest `text` output | minor, confirmed | FIXED (renderers) | Added compact text renderers for `get_events_by_id`, `update_event`, `delete_events`, `batch_create_events`, `manage_calendars`, matching the existing `render_events_text` pattern. | text tests in 5 tool test files (6 tests) |
| **F5** recurring write EVENT_NOT_FOUND | minor, plausible | FIXED (widen + document) | Removing the start-date bound was **rejected**: the calendar lint (`test_calendar_scripts.py::test_every_event_whose_predicates_are_date_bounded`) forbids an unbounded `whose`, and Calendar.app `whose` cost tracks total store size. Instead `update_event`/`delete_events` widen the write-side window back by `RECURRING_LOOKBACK_DAYS` for recurring targets (still date-bounded); docstrings document the horizon. | `test_update_event.py::TestRecurringWriteWindow` (2), `test_delete_events.py::TestRecurringWriteWindow` (2) |
| **F6** test doubles hide the shift | minor, confirmed | FIXED | Direct unit tests on `applescript_date_block` for all-day and timed datetimes in zones east (Asia/Tokyo) and west (Pacific/Honolulu, America/Los_Angeles) of the host; host-independent by computing timed expectations from the same `astimezone` and asserting all-day emits the requested date on any host. | (F6 == `test_calendar_date_block.py`) |
| **F7** query matches only preview | minor, confirmed | FIXED (document) | `list_events` docstring now states `query` matches title, location, and the first 280 chars of notes (`notes_preview`, not full notes). Full-notes match is not trivially available (list rows carry no full notes), so the cheap doc path was chosen. | covered by existing query test |
| **F8** false attendees_changed | minor, confirmed | FIXED | `update_event` sets `attendees_changed` only when `add_attendees` is non-empty; a removal-only/empty diff sets `attendee_removal_ignored` and emits an explicit "attendee removal is unsupported" note instead of the invitation-delivery disclosure. | `test_update_event.py::TestAttendeeRemoval` (3 tests) |

Rejected alternative recorded: **F5** option A ("remove the start-date bound
from the write-side uid lookup") was verified unsafe against `scripts_write.py`
and the calendar lint and is not applied; the widen-and-document path fixes the
common case (series within 400 days) while preserving the bounded-`whose`
invariant. Widening is near-free because Calendar.app `whose` cost tracks store
size, not window width.

## Files touched

Source:
- `plugin/apple_mail_mcp/calendar_core/scripts_read.py` (F1: `all_day` flag)
- `plugin/apple_mail_mcp/calendar_core/scripts_write.py` (F1: `build_event_set_lines` all-day date blocks)
- `plugin/apple_mail_mcp/calendar_core/engine.py` (F1: create thread-through; F3: delete access-denied)
- `plugin/apple_mail_mcp/tools/calendar/helpers.py` (F2: `recurring_lookback_disclosure`)
- `plugin/apple_mail_mcp/tools/calendar/events_list.py` (F2 disclosure + payload; F7 docstring)
- `plugin/apple_mail_mcp/tools/calendar/availability.py` (F2 disclosure + payload)
- `plugin/apple_mail_mcp/tools/calendar/events_get.py` (F4 renderer)
- `plugin/apple_mail_mcp/tools/calendar/events_update.py` (F4 renderer; F5 widen; F8 attendee logic; docstring)
- `plugin/apple_mail_mcp/tools/calendar/events_delete.py` (F4 renderer; F5 widen; docstring)
- `plugin/apple_mail_mcp/tools/calendar/events_batch.py` (F4 renderer)
- `plugin/apple_mail_mcp/tools/calendar/calendars_manage.py` (F4 renderer)

Tests:
- `tests/calendar_surface/test_calendar_date_block.py` (new, F6/F1)
- `tests/calendar_surface/test_calendar_engine.py` (F3)
- `tests/calendar_surface/test_update_event.py` (F8, F5, F4)
- `tests/calendar_surface/test_delete_events.py` (F5, F4)
- `tests/calendar_surface/test_list_events.py` (F2)
- `tests/calendar_surface/test_get_events_by_id.py` (F4)
- `tests/calendar_surface/test_batch_create.py` (F4)
- `tests/calendar_surface/test_manage_calendars.py` (F4)
- `tests/calendar_surface/conftest.py` (FakeWriteEngine records delete window for F5)

Docs / skills / meta:
- `CHANGELOG.md` (Fixed/Changed under 3.10.0)
- `plugin/skills/references/calendar-safety-limits.md` (canonical) + synced copies
- `plugin/skills/calendar-operator/SKILL.md`
- `tools/expected_test_count.txt` (1365 -> 1389)
- rebuilt `apple-mail-plugin.zip`, `apple-mail.plugin`, `apple-mail-mcp-v3.10.0.mcpb`

## Gate status (all green)

- `ruff check plugin/apple_mail_mcp/`: All checks passed
- `ruff format --check plugin/apple_mail_mcp/`: 99 files already formatted
- `mypy --strict plugin/apple_mail_mcp/`: Success, no issues (99 files)
- `PYTEST_ADDOPTS='' pytest tests/`: full suite green; 1389 collected, matches `tools/expected_test_count.txt`
- calendar_surface: 336 passed (was 312; +24 new)
- `bash tools/gates/dev-check.sh release`: exit 0 (artifacts rebuilt + validated, tasks layout, repo root, pytest, test-count, wrapper surface all OK)
- module line budget: OK, no module over 600 LOC (largest touched: `helpers.py` 469, `events_update.py` 409)
- AppleScript scripts still `osacompile`-clean (`test_calendar_scripts.py::TestScriptsCompile`)
