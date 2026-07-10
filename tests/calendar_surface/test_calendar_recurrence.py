"""recurrence.py: bounded RRULE expansion for the allowlist grammar."""

from datetime import datetime, timezone

import pytest

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.calendar_core.recurrence import expand_occurrences, expansion_supported, parse_rrule

UTC = timezone.utc


def _expand(rule_text, master, win_start, win_end, ceiling=750):
    return expand_occurrences(
        master_start=master,
        rule=parse_rrule(rule_text),
        window_start=win_start,
        window_end=win_end,
        ceiling=ceiling,
    )


class TestDailyWeekly:
    def test_daily_expansion(self):
        master = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
        occurrences = _expand(
            "FREQ=DAILY", master, datetime(2026, 7, 10, tzinfo=UTC), datetime(2026, 7, 13, tzinfo=UTC)
        )
        assert [o.day for o in occurrences] == [10, 11, 12]
        assert all(o.hour == 9 for o in occurrences)

    def test_daily_interval(self):
        master = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
        occurrences = _expand(
            "FREQ=DAILY;INTERVAL=3", master, datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 10, tzinfo=UTC)
        )
        assert [o.day for o in occurrences] == [1, 4, 7]

    def test_weekly_byday(self):
        master = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)  # a Monday
        occurrences = _expand(
            "FREQ=WEEKLY;BYDAY=MO,WE",
            master,
            datetime(2026, 7, 6, tzinfo=UTC),
            datetime(2026, 7, 19, tzinfo=UTC),
        )
        assert [(o.day, o.weekday()) for o in occurrences] == [(6, 0), (8, 2), (13, 0), (15, 2)]

    def test_weekly_without_byday_uses_master_weekday(self):
        master = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)  # a Tuesday
        occurrences = _expand(
            "FREQ=WEEKLY", master, datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 22, tzinfo=UTC)
        )
        assert all(o.weekday() == 1 for o in occurrences)
        assert len(occurrences) == 3

    def test_series_started_before_window_is_projected_in(self):
        # The AppleScript window predicate misses these; expansion recovers them.
        master = datetime(2025, 1, 6, 9, 0, tzinfo=UTC)
        occurrences = _expand(
            "FREQ=WEEKLY", master, datetime(2026, 7, 6, tzinfo=UTC), datetime(2026, 7, 13, tzinfo=UTC)
        )
        assert occurrences
        assert all(datetime(2026, 7, 6, tzinfo=UTC) <= o for o in occurrences)


class TestMonthlyYearly:
    def test_monthly_bymonthday(self):
        master = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        occurrences = _expand(
            "FREQ=MONTHLY;BYMONTHDAY=1,15",
            master,
            datetime(2026, 3, 1, tzinfo=UTC),
            datetime(2026, 4, 30, tzinfo=UTC),
        )
        assert [(o.month, o.day) for o in occurrences] == [(3, 1), (3, 15), (4, 1), (4, 15)]

    def test_monthly_skips_invalid_day(self):
        master = datetime(2026, 1, 31, 8, 0, tzinfo=UTC)
        occurrences = _expand(
            "FREQ=MONTHLY", master, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 4, 30, tzinfo=UTC)
        )
        # February has no 31st; the occurrence is skipped, not shifted.
        assert [(o.month, o.day) for o in occurrences] == [(1, 31), (3, 31)]

    def test_yearly_anniversary(self):
        master = datetime(2020, 7, 4, 12, 0, tzinfo=UTC)
        occurrences = _expand(
            "FREQ=YEARLY", master, datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 12, 31, tzinfo=UTC)
        )
        assert [(o.year, o.month, o.day) for o in occurrences] == [(2026, 7, 4), (2027, 7, 4)]


class TestCountUntilCeiling:
    def test_count_limits_total_occurrences(self):
        master = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
        occurrences = _expand(
            "FREQ=DAILY;COUNT=5", master, datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC)
        )
        assert len(occurrences) == 5

    def test_count_consumed_before_window(self):
        master = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
        occurrences = _expand(
            "FREQ=DAILY;COUNT=3", master, datetime(2026, 7, 10, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC)
        )
        assert occurrences == []

    def test_until_stops_expansion(self):
        master = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
        occurrences = _expand(
            "FREQ=DAILY;UNTIL=20260705",
            master,
            datetime(2026, 7, 1, tzinfo=UTC),
            datetime(2026, 8, 1, tzinfo=UTC),
        )
        assert len(occurrences) == 5

    def test_ceiling_raises_too_dense(self):
        master = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
        with pytest.raises(ToolError) as exc:
            _expand(
                "FREQ=DAILY",
                master,
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 12, 31, tzinfo=UTC),
                ceiling=10,
            )
        assert exc.value.code == "CALENDAR_WINDOW_TOO_DENSE"


class TestUnsupportedCombos:
    @pytest.mark.parametrize("rule", ["FREQ=MONTHLY;BYDAY=MO", "FREQ=YEARLY;BYDAY=FR"])
    def test_positional_byday_flagged_unsupported(self, rule):
        parsed = parse_rrule(rule)
        assert expansion_supported(parsed) is False
        result = expand_occurrences(
            master_start=datetime(2026, 7, 1, tzinfo=UTC),
            rule=parsed,
            window_start=datetime(2026, 7, 1, tzinfo=UTC),
            window_end=datetime(2026, 8, 1, tzinfo=UTC),
            ceiling=750,
        )
        assert result is None

    def test_supported_combos_report_supported(self):
        assert expansion_supported(parse_rrule("FREQ=WEEKLY;BYDAY=MO")) is True
        assert expansion_supported(parse_rrule("FREQ=MONTHLY;BYMONTHDAY=1")) is True
