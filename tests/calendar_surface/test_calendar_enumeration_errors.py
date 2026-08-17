"""Regression tests: a failed calendar enumeration must not read as "nothing on".

``helpers.list_calendar_names`` used to drop the error list that
``engine.list_calendars`` already returns. Every unscoped calendar read
resolves its scope through that helper, so a failed enumeration produced an
empty name list, a fan-out across zero calendars, and a confident "0 events"
with ``calendar_errors: []``.

Each test has a mirror-image partner: a host that genuinely has no calendars,
or a day that genuinely has no events, must still answer empty with no
spurious error. Engine-boundary mocking only; the autouse guardrail in
``tests/conftest.py`` already poisons the live Calendar.app osascript seam.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.tools.calendar import check_availability, get_events_by_id, list_calendars, list_events
from apple_mail_mcp.tools.calendar.helpers import list_calendar_names

from .conftest import HOST_TZ, FakeReadEngine, raw_event


@pytest.fixture
def real_name_resolution(monkeypatch):
    """Install a fake engine but keep the REAL ``list_calendar_names``.

    ``fake_engines`` stubs the resolver itself, which is exactly the seam
    under test here, so these tests wire the engine directly instead.
    """

    def install(engine):
        monkeypatch.setattr("apple_mail_mcp.tools.calendar.get_engine", lambda: engine)
        monkeypatch.setattr("apple_mail_mcp.tools.calendar.list_calendar_names", list_calendar_names)
        return engine

    return install


def _soon(hours: int = 24) -> datetime:
    return datetime.now(HOST_TZ) + timedelta(hours=hours)


class TestListCalendarNames:
    def test_failed_enumeration_raises_instead_of_returning_empty(self, real_name_resolution):
        real_name_resolution(FakeReadEngine(calendars=[], list_errors=["Work: -1728 Can't get calendar"]))
        with pytest.raises(ToolError) as excinfo:
            list_calendar_names()
        assert excinfo.value.code == "CALENDAR_ENUMERATION_FAILED"
        assert "Work: -1728 Can't get calendar" in str(excinfo.value.remediation["calendar_errors"])

    def test_host_with_no_calendars_returns_empty_without_error(self, real_name_resolution):
        """Mirror image: zero calendars and zero errors is not a failure."""
        real_name_resolution(FakeReadEngine(calendars=[], list_errors=[]))
        assert list_calendar_names() == []

    def test_partial_enumeration_still_returns_the_usable_names(self, real_name_resolution):
        """Some names readable: keep serving them rather than failing the call."""
        real_name_resolution(FakeReadEngine(list_errors=["Shared: unreadable"]))
        assert list_calendar_names() == ["Work", "Home", "MCP Test Calendar"]


class TestReadToolsSurfaceTheFailure:
    def test_list_events_reports_the_failure_not_an_empty_day(self, real_name_resolution):
        real_name_resolution(FakeReadEngine(calendars=[], list_errors=["Work: unreadable"]))
        payload = json.loads(asyncio.run(list_events()))
        assert payload["code"] == "CALENDAR_ENUMERATION_FAILED"
        assert "events" not in payload

    def test_get_events_by_id_reports_the_failure(self, real_name_resolution):
        real_name_resolution(FakeReadEngine(calendars=[], list_errors=["Work: unreadable"]))
        payload = json.loads(asyncio.run(get_events_by_id(event_ids=["UID-1"])))
        assert payload["code"] == "CALENDAR_ENUMERATION_FAILED"

    def test_check_availability_reports_the_failure(self, real_name_resolution):
        real_name_resolution(FakeReadEngine(calendars=[], list_errors=["Work: unreadable"]))
        payload = json.loads(
            asyncio.run(
                check_availability(
                    start="2026-07-13",
                    end="2026-07-15",
                    timezone="America/Chicago",
                    calendars=["Work"],
                )
            )
        )
        assert payload["code"] == "CALENDAR_ENUMERATION_FAILED"


class TestEmptyStaysEmpty:
    def test_quiet_day_still_reports_zero_events_with_no_error(self, real_name_resolution):
        """Mirror image: real calendars, no events in the window, no error."""
        real_name_resolution(FakeReadEngine(events=[]))
        payload = json.loads(asyncio.run(list_events()))
        assert payload["total_matched"] == 0
        assert payload["calendar_errors"] == []
        assert payload["calendars_scanned"] == ["Work", "Home", "MCP Test Calendar"]
        assert "code" not in payload

    def test_host_with_no_calendars_reports_zero_events_with_no_error(self, real_name_resolution):
        real_name_resolution(FakeReadEngine(calendars=[], list_errors=[]))
        payload = json.loads(asyncio.run(list_events()))
        assert payload["total_matched"] == 0
        assert payload["calendars_scanned"] == []
        assert payload["calendar_errors"] == []
        assert "code" not in payload

    def test_populated_day_is_unaffected(self, real_name_resolution):
        real_name_resolution(FakeReadEngine(events=[raw_event("UID-1", start=_soon())]))
        payload = json.loads(asyncio.run(list_events()))
        assert payload["total_matched"] == 1
        assert payload["calendar_errors"] == []


class TestListCalendarsToolUnchanged:
    """``list_calendars`` reads the engine directly and already reported errors."""

    def test_still_reports_enumeration_errors_in_its_payload(self, real_name_resolution):
        real_name_resolution(FakeReadEngine(calendars=[], list_errors=["Work: unreadable"]))
        payload = json.loads(list_calendars())
        assert payload["calendars"] == []
        assert payload["calendar_errors"] == ["Work: unreadable"]
