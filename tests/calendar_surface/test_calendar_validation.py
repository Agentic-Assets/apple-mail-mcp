"""validation.py: calendar resolution, event ids, RRULEs, alarms, attendees, slots."""

import pytest

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.calendar_core.validation import (
    normalize_event_ids,
    resolve_calendar_name,
    validate_alarms,
    validate_attendee_emails,
    validate_event_id,
    validate_rrule,
    validate_slot_params,
)

NAMES = ["Work", "Home", "Work Travel", "family shared"]


class TestResolveCalendarName:
    def test_exact_match_wins(self):
        assert resolve_calendar_name("Work", NAMES) == "Work"

    def test_case_insensitive_exact(self):
        assert resolve_calendar_name("home", NAMES) == "Home"

    def test_unique_substring(self):
        assert resolve_calendar_name("family", NAMES) == "family shared"

    def test_exact_beats_substring_ambiguity(self):
        # "Work" is a substring of both Work and Work Travel; exact wins first.
        assert resolve_calendar_name("Work", NAMES) == "Work"

    def test_ambiguous_substring_refuses_with_candidates(self):
        with pytest.raises(ToolError) as exc:
            resolve_calendar_name("Trav", ["Work Travel", "Home Travel"])
        assert exc.value.code == "AMBIGUOUS_CALENDAR_SELECTOR"
        assert set(exc.value.remediation["candidates"]) == {"Work Travel", "Home Travel"}

    def test_ambiguous_case_insensitive_exact_refuses(self):
        with pytest.raises(ToolError) as exc:
            resolve_calendar_name("work", ["Work", "WORK"])
        assert exc.value.code == "AMBIGUOUS_CALENDAR_SELECTOR"

    def test_no_match_lists_close_candidates(self):
        with pytest.raises(ToolError) as exc:
            resolve_calendar_name("Wrok", NAMES)
        assert exc.value.code == "CALENDAR_NOT_FOUND"
        assert "Work" in exc.value.remediation["candidates"]

    def test_empty_name_refuses(self):
        with pytest.raises(ToolError) as exc:
            resolve_calendar_name("  ", NAMES)
        assert exc.value.code == "CALENDAR_NOT_FOUND"


class TestEventIds:
    def test_valid_uuid_like_id(self):
        assert validate_event_id(" ABC-123-def ") == "ABC-123-def"

    @pytest.mark.parametrize(
        "bad",
        ["", "   ", "a|||b", "a\x00b", 'has"quote', "back\\slash", "x" * 513],
    )
    def test_bad_shapes_refuse(self, bad):
        with pytest.raises(ToolError) as exc:
            validate_event_id(bad)
        assert exc.value.code == "INVALID_EVENT_ID"

    def test_normalize_dedupes_and_orders(self):
        assert normalize_event_ids(["A", "B", "A"], max_ids=25) == ["A", "B"]

    def test_normalize_empty_refuses(self):
        with pytest.raises(ToolError):
            normalize_event_ids([], max_ids=25)

    def test_normalize_cap_uses_cap_code(self):
        with pytest.raises(ToolError) as exc:
            normalize_event_ids([f"id-{i}" for i in range(30)], max_ids=25, cap_code="TOO_MANY_DELETES")
        assert exc.value.code == "TOO_MANY_DELETES"


class TestRRuleAllowlist:
    @pytest.mark.parametrize(
        "rule",
        [
            "FREQ=DAILY",
            "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE,FR",
            "FREQ=MONTHLY;BYMONTHDAY=1,15",
            "FREQ=YEARLY;BYMONTH=7;BYMONTHDAY=4",
            "RRULE:FREQ=DAILY;COUNT=10",
            "FREQ=WEEKLY;UNTIL=20261231",
            "FREQ=WEEKLY;UNTIL=20261231T000000Z",
        ],
    )
    def test_allowlisted_rules_pass(self, rule):
        assert validate_rrule(rule).startswith("FREQ=")

    @pytest.mark.parametrize(
        "rule",
        [
            "",
            "FREQ=HOURLY",
            "FREQ=SECONDLY",
            "FREQ=WEEKLY;BYSETPOS=1",
            "FREQ=MONTHLY;BYDAY=2TU",
            "FREQ=DAILY;INTERVAL=0",
            "FREQ=DAILY;INTERVAL=1000",
            "FREQ=DAILY;COUNT=0",
            "FREQ=DAILY;COUNT=5;UNTIL=20261231",
            "FREQ=DAILY;UNTIL=tomorrow",
            "FREQ=WEEKLY;BYDAY=XX",
            "FREQ=MONTHLY;BYMONTHDAY=32",
            "FREQ=YEARLY;BYMONTH=13",
            "garbage",
            "FREQ=DAILY;FREQ=WEEKLY",
        ],
    )
    def test_outside_grammar_refuses(self, rule):
        with pytest.raises(ToolError) as exc:
            validate_rrule(rule)
        assert exc.value.code == "INVALID_RECURRENCE_RULE"

    def test_canonical_form_is_stable(self):
        assert validate_rrule("freq=weekly;byday=mo") == "FREQ=WEEKLY;BYDAY=MO"


class TestAlarms:
    def test_valid_alarms(self):
        assert validate_alarms([0, 10, 40320]) == [0, 10, 40320]

    def test_too_many_alarms(self):
        with pytest.raises(ToolError) as exc:
            validate_alarms([1, 2, 3, 4, 5, 6])
        assert exc.value.code == "INVALID_ALARM"

    @pytest.mark.parametrize("bad", [-1, 40321, True])
    def test_out_of_range_refuses(self, bad):
        with pytest.raises(ToolError) as exc:
            validate_alarms([bad])
        assert exc.value.code == "INVALID_ALARM"


class TestAttendees:
    def test_normalizes_and_dedupes(self):
        result = validate_attendee_emails(["Test@Example.com", "test@example.com", "b@example.org"])
        assert result == ["test@example.com", "b@example.org"]

    @pytest.mark.parametrize("bad", ["", "nope", "a b@example.com", "user@nodot"])
    def test_malformed_refuses(self, bad):
        with pytest.raises(ToolError) as exc:
            validate_attendee_emails([bad])
        assert exc.value.code == "INVALID_ATTENDEE_EMAIL"

    def test_attendee_cap(self):
        with pytest.raises(ToolError) as exc:
            validate_attendee_emails([f"user{i}@example.com" for i in range(51)])
        assert exc.value.code == "TOO_MANY_ATTENDEES"


class TestSlotParams:
    def test_valid_params(self):
        slot, start, end = validate_slot_params(
            slot_minutes=30, working_hours_start="09:00", working_hours_end="17:30", max_slots=20
        )
        assert (slot, start, end) == (30, 540, 1050)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"slot_minutes": 4},
            {"slot_minutes": 481},
            {"max_slots": 0},
            {"max_slots": 51},
            {"working_hours_start": "9am"},
            {"working_hours_start": "25:00"},
            {"working_hours_start": "17:00", "working_hours_end": "09:00"},
        ],
    )
    def test_out_of_range_refuses(self, kwargs):
        base = {
            "slot_minutes": 30,
            "working_hours_start": "09:00",
            "working_hours_end": "17:00",
            "max_slots": 20,
        }
        base.update(kwargs)
        with pytest.raises(ToolError) as exc:
            validate_slot_params(**base)
        assert exc.value.code == "INVALID_SLOT_PARAMS"
