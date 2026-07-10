"""bounded_calendar_window: parsing, timezone resolution, caps, refusals."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.calendar_core.window import (
    CalendarWindow,
    bounded_calendar_window,
    isoformat_pair,
    parse_iso_datetime,
    require_issued_window,
    resolve_timezone,
    shifted_window,
    window_payload,
)


class TestResolveTimezone:
    def test_iana_zone_resolves(self):
        tz, name = resolve_timezone("America/Chicago")
        assert name == "America/Chicago"
        assert isinstance(tz, ZoneInfo)

    def test_none_resolves_host_local(self):
        tz, name = resolve_timezone(None)
        assert tz is not None
        assert name

    def test_blank_resolves_host_local(self):
        _tz, name = resolve_timezone("  ")
        assert name

    def test_unknown_zone_refuses(self):
        with pytest.raises(ToolError) as exc:
            resolve_timezone("Not/AZone")
        assert exc.value.code == "INVALID_TIMEZONE"


class TestParseIsoDatetime:
    def test_naive_gets_zone(self):
        tz = ZoneInfo("America/Chicago")
        parsed = parse_iso_datetime("2026-07-10T09:00:00", tz, "start")
        assert parsed.tzinfo is tz
        assert parsed.hour == 9

    def test_offset_aware_kept(self):
        parsed = parse_iso_datetime("2026-07-10T09:00:00-05:00", ZoneInfo("UTC"), "start")
        assert parsed.utcoffset().total_seconds() == -5 * 3600

    def test_date_only_is_midnight(self):
        parsed = parse_iso_datetime("2026-07-10", ZoneInfo("UTC"), "start")
        assert (parsed.hour, parsed.minute) == (0, 0)

    def test_zulu_suffix_supported(self):
        parsed = parse_iso_datetime("2026-07-10T09:00:00Z", ZoneInfo("UTC"), "start")
        assert parsed.utcoffset().total_seconds() == 0

    def test_malformed_refuses(self):
        with pytest.raises(ToolError) as exc:
            parse_iso_datetime("July 10th", ZoneInfo("UTC"), "start")
        assert exc.value.code == "INVALID_EVENT_WINDOW"


class TestBoundedCalendarWindow:
    def test_relative_window(self):
        window = bounded_calendar_window(days_back=1, days_ahead=7, timezone_name="UTC")
        assert (window.end - window.start).days == 8
        assert window.timezone_name == "UTC"

    def test_absolute_window(self):
        window = bounded_calendar_window(start="2026-07-10", end="2026-07-17", timezone_name="UTC")
        assert window.start.day == 10
        assert window.end.day == 17

    def test_zero_width_refuses(self):
        with pytest.raises(ToolError) as exc:
            bounded_calendar_window(days_back=0, days_ahead=0)
        assert exc.value.code == "UNBOUNDED_CALENDAR_SCAN"

    def test_negative_days_refuse(self):
        with pytest.raises(ToolError) as exc:
            bounded_calendar_window(days_back=-1, days_ahead=7)
        assert exc.value.code == "INVALID_EVENT_WINDOW"

    def test_start_without_end_refuses(self):
        with pytest.raises(ToolError) as exc:
            bounded_calendar_window(start="2026-07-10", end=None)
        assert exc.value.code == "INVALID_EVENT_WINDOW"

    def test_start_after_end_refuses(self):
        with pytest.raises(ToolError) as exc:
            bounded_calendar_window(start="2026-07-17", end="2026-07-10", timezone_name="UTC")
        assert exc.value.code == "INVALID_EVENT_WINDOW"

    def test_too_wide_refuses(self):
        with pytest.raises(ToolError) as exc:
            bounded_calendar_window(days_back=0, days_ahead=371)
        assert exc.value.code == "CALENDAR_WINDOW_TOO_WIDE"

    def test_custom_cap_applies(self):
        with pytest.raises(ToolError) as exc:
            bounded_calendar_window(days_back=0, days_ahead=63, max_window_days=62)
        assert exc.value.code == "CALENDAR_WINDOW_TOO_WIDE"

    def test_invalid_timezone_refuses(self):
        with pytest.raises(ToolError) as exc:
            bounded_calendar_window(days_ahead=7, timezone_name="Bad/Zone")
        assert exc.value.code == "INVALID_TIMEZONE"

    def test_issued_token_passes_check(self):
        window = bounded_calendar_window(days_ahead=7, timezone_name="UTC")
        require_issued_window(window)  # no raise

    def test_forged_token_refused(self):
        forged = CalendarWindow(
            start=datetime(2026, 7, 10, tzinfo=timezone.utc),
            end=datetime(2026, 7, 17, tzinfo=timezone.utc),
            timezone_name="UTC",
        )
        with pytest.raises(ToolError) as exc:
            require_issued_window(forged)
        assert exc.value.code == "INVALID_EVENT_WINDOW"

    def test_shifted_window_stays_issued(self):
        window = bounded_calendar_window(days_ahead=7, timezone_name="UTC")
        shifted = shifted_window(window, start_delta_days=-400, end_delta_days=-7)
        require_issued_window(shifted)
        assert shifted.start < window.start

    def test_shifted_refuses_forged_input(self):
        forged = CalendarWindow(
            start=datetime(2026, 7, 10, tzinfo=timezone.utc),
            end=datetime(2026, 7, 17, tzinfo=timezone.utc),
            timezone_name="UTC",
        )
        with pytest.raises(ToolError):
            shifted_window(forged, start_delta_days=-1)


class TestOutputHelpers:
    def test_isoformat_pair_dual_zone(self):
        dt = datetime(2026, 7, 10, 14, 0, tzinfo=ZoneInfo("America/Chicago"))
        local_iso, utc_iso = isoformat_pair(dt, ZoneInfo("America/Chicago"))
        assert local_iso.endswith("-05:00")
        assert utc_iso.endswith("+00:00")
        assert "19:00" in utc_iso

    def test_window_payload_shape(self):
        window = bounded_calendar_window(
            start="2026-07-10T09:00:00",
            end="2026-07-11T09:00:00",
            timezone_name="America/Chicago",
        )
        payload = window_payload(window)
        assert payload["timezone"] == "America/Chicago"
        assert set(payload) == {"start", "end", "start_utc", "end_utc", "timezone"}
        assert payload["start_utc"].endswith("+00:00")
