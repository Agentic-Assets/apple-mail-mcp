"""check_availability tool: busy folding, slot finding, caps."""

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apple_mail_mcp.tools.calendar import check_availability

from .conftest import FakeReadEngine, raw_event

CHI = ZoneInfo("America/Chicago")
# A fixed Monday..Friday window keeps weekday math deterministic.
MONDAY = datetime(2026, 7, 13, 0, 0, tzinfo=CHI)


def _run(**kwargs):
    defaults = {
        "start": "2026-07-13",
        "end": "2026-07-15",
        "timezone": "America/Chicago",
        "calendars": ["Work"],
    }
    defaults.update(kwargs)
    return json.loads(asyncio.run(check_availability(**defaults)))


def _busy_event(uid, start_hour, end_hour, day=13, **kwargs):
    return raw_event(
        uid,
        start=datetime(2026, 7, day, start_hour, 0, tzinfo=CHI),
        end=datetime(2026, 7, day, end_hour, 0, tzinfo=CHI),
        **kwargs,
    )


class TestBusyBlocks:
    def test_busy_blocks_reported_and_merged(self, fake_engines):
        read = FakeReadEngine(events=[_busy_event("UID-1", 9, 10), _busy_event("UID-2", 9, 11)])
        fake_engines(read=read)
        payload = _run()
        assert payload["busy_block_count"] == 2
        # 09:00-10:00 overlaps merged busy; the first free slot is 11:00.
        assert payload["free_slots"][0]["start"].startswith("2026-07-13T11:00")

    def test_all_day_events_do_not_block_by_default(self, fake_engines):
        read = FakeReadEngine(events=[_busy_event("UID-1", 0, 23, all_day=True)])
        fake_engines(read=read)
        payload = _run()
        assert payload["busy_block_count"] == 0
        assert payload["free_slots"][0]["start"].startswith("2026-07-13T09:00")

    def test_all_day_blocks_when_opted_in(self, fake_engines):
        read = FakeReadEngine(events=[_busy_event("UID-1", 0, 23, all_day=True)])
        fake_engines(read=read)
        payload = _run(ignore_all_day_events=False)
        assert payload["busy_block_count"] == 1

    def test_overlapping_event_from_before_window_counts(self, fake_engines):
        # Started the previous day, ends inside the window: the fetch pad catches it.
        event = raw_event(
            "UID-PAD",
            start=datetime(2026, 7, 12, 22, 0, tzinfo=CHI),
            end=datetime(2026, 7, 13, 10, 0, tzinfo=CHI),
        )
        read = FakeReadEngine(events=[event])
        fake_engines(read=read)
        payload = _run()
        assert payload["busy_block_count"] == 1
        assert payload["free_slots"][0]["start"].startswith("2026-07-13T10:00")


class TestFreeSlots:
    def test_slots_respect_working_hours(self, fake_engines):
        fake_engines(read=FakeReadEngine())
        payload = _run(working_hours_start="10:00", working_hours_end="12:00", slot_minutes=60)
        starts = [slot["start"] for slot in payload["free_slots"] if slot["start"].startswith("2026-07-13")]
        assert starts == ["2026-07-13T10:00:00-05:00", "2026-07-13T11:00:00-05:00"]

    def test_weekdays_only_skips_weekend(self, fake_engines):
        fake_engines(read=FakeReadEngine())
        payload = _run(start="2026-07-18", end="2026-07-20", max_slots=5)  # Sat..Mon
        assert all(slot["start"].startswith("2026-07-20") for slot in payload["free_slots"])

    def test_weekend_included_when_opted_in(self, fake_engines):
        fake_engines(read=FakeReadEngine())
        payload = _run(start="2026-07-18", end="2026-07-19", weekdays_only=False, max_slots=3)
        assert payload["free_slots"]
        assert payload["free_slots"][0]["start"].startswith("2026-07-18")

    def test_max_slots_cap(self, fake_engines):
        fake_engines(read=FakeReadEngine())
        payload = _run(max_slots=3)
        assert len(payload["free_slots"]) == 3


class TestValidation:
    def test_window_wider_than_62_days_refused(self, fake_engines):
        fake_engines()
        payload = _run(start="2026-07-01", end="2026-09-15")
        assert payload["code"] == "CALENDAR_WINDOW_TOO_WIDE"

    def test_invalid_slot_params_refused(self, fake_engines):
        fake_engines()
        payload = _run(slot_minutes=2)
        assert payload["code"] == "INVALID_SLOT_PARAMS"

    def test_start_after_end_refused(self, fake_engines):
        fake_engines()
        payload = _run(start="2026-07-15", end="2026-07-13")
        assert payload["code"] == "INVALID_EVENT_WINDOW"

    def test_resolved_timezone_echoed(self, fake_engines):
        fake_engines(read=FakeReadEngine())
        payload = _run()
        assert payload["resolved_timezone"] == "America/Chicago"
        assert payload["window"]["timezone"] == "America/Chicago"
