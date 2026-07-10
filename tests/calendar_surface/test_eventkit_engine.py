"""eventkit.py through stubbed EventKit/Foundation modules (CI has no PyObjC)."""

import sys
import types
from datetime import datetime, timezone

import pytest

from apple_mail_mcp.calendar_core import eventkit as eventkit_mod
from apple_mail_mcp.calendar_core.eventkit import EventKitCalendarEngine, eventkit_status
from apple_mail_mcp.calendar_core.window import bounded_calendar_window

UTC = timezone.utc


def _stub_modules(monkeypatch, status=3):
    ek = types.ModuleType("EventKit")
    ek.EKEntityTypeEvent = 0
    ek.EKAuthorizationStatusFullAccess = 3

    class _Store:
        @staticmethod
        def authorizationStatusForEntityType_(entity):
            return status

    ek.EKEventStore = _Store
    foundation = types.ModuleType("Foundation")
    monkeypatch.setitem(sys.modules, "EventKit", ek)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    return ek, foundation


class TestEventKitStatus:
    def test_dependency_missing(self, monkeypatch):
        monkeypatch.setattr(eventkit_mod, "load_frameworks", lambda: None)
        available, reason = eventkit_status()
        assert available is False
        assert reason.startswith("dependency_missing")
        assert "mcp-apple-mail[eventkit]" in reason

    def test_full_access(self, monkeypatch):
        _stub_modules(monkeypatch, status=3)
        assert eventkit_status() == (True, "full_access")

    @pytest.mark.parametrize(
        ("status", "reason"),
        [(0, "not_determined"), (1, "restricted"), (2, "denied"), (4, "write_only")],
    )
    def test_non_full_states(self, monkeypatch, status, reason):
        _stub_modules(monkeypatch, status=status)
        assert eventkit_status() == (False, reason)

    def test_status_check_failure_is_reported(self, monkeypatch):
        ek = types.ModuleType("EventKit")

        class _Broken:
            @staticmethod
            def authorizationStatusForEntityType_(entity):
                raise RuntimeError("no tccd")

        ek.EKEventStore = _Broken
        foundation = types.ModuleType("Foundation")
        monkeypatch.setattr(eventkit_mod, "load_frameworks", lambda: (ek, foundation))
        available, reason = eventkit_status()
        assert available is False
        assert reason.startswith("status_check_failed")


class _NSDateStub:
    def __init__(self, ts):
        self.ts = ts

    def timeIntervalSince1970(self):
        return self.ts

    @classmethod
    def dateWithTimeIntervalSince1970_(cls, ts):
        return cls(ts)


class _CalStub:
    def __init__(self, name, identifier="CAL-ID", writable=True):
        self._name = name
        self._id = identifier
        self._writable = writable

    def title(self):
        return self._name

    def calendarIdentifier(self):
        return self._id

    def allowsContentModifications(self):
        return self._writable


class _EventStub:
    def __init__(self, uid, title, start_ts, end_ts, cal, recurring=False):
        self._uid = uid
        self._title = title
        self._start = start_ts
        self._end = end_ts
        self._cal = cal
        self._recurring = recurring

    def startDate(self):
        return _NSDateStub(self._start)

    def endDate(self):
        return _NSDateStub(self._end)

    def isAllDay(self):
        return False

    def title(self):
        return self._title

    def location(self):
        return None

    def notes(self):
        return None

    def URL(self):
        return None

    def hasRecurrenceRules(self):
        return self._recurring

    def calendarItemExternalIdentifier(self):
        return self._uid

    def eventIdentifier(self):
        return f"local-{self._uid}"

    def calendar(self):
        return self._cal

    def attendees(self):
        return []

    def alarms(self):
        return []


class _StoreStub:
    def __init__(self, calendars, events):
        self._calendars = calendars
        self._events = events
        self.predicates = []

    def calendarsForEntityType_(self, entity):
        return self._calendars

    def defaultCalendarForNewEvents(self):
        return self._calendars[0] if self._calendars else None

    def predicateForEventsWithStartDate_endDate_calendars_(self, start, end, calendars):
        self.predicates.append((start.ts, end.ts, calendars))
        return "PREDICATE"

    def eventsMatchingPredicate_(self, predicate):
        return self._events


def _engine(monkeypatch, events, calendars=None):
    calendars = calendars or [_CalStub("Work")]
    store = _StoreStub(calendars, events)

    ek = types.SimpleNamespace(EKEntityTypeEvent=0)
    foundation = types.SimpleNamespace(NSDate=_NSDateStub)
    engine = EventKitCalendarEngine(ek, foundation)
    monkeypatch.setattr(engine, "_event_store", lambda: store)
    return engine, store


class TestEventKitEngine:
    def test_list_calendars_maps_fields(self, monkeypatch):
        engine, _store = _engine(monkeypatch, events=[])
        calendars, errors = engine.list_calendars()
        assert errors == []
        assert calendars == [
            {
                "calendar_id": "CAL-ID",
                "id_kind": "uid",
                "name": "Work",
                "writable": True,
                "description": None,
            }
        ]

    def test_default_calendar_name(self, monkeypatch):
        engine, _store = _engine(monkeypatch, events=[])
        assert engine.default_calendar_name() == "Work"

    def test_fetch_window_maps_records(self, monkeypatch):
        cal = _CalStub("Work")
        start_ts = datetime(2026, 7, 10, 9, tzinfo=UTC).timestamp()
        end_ts = datetime(2026, 7, 10, 10, tzinfo=UTC).timestamp()
        engine, store = _engine(monkeypatch, events=[_EventStub("EK-1", "Standup", start_ts, end_ts, cal, recurring=True)])
        window = bounded_calendar_window(start="2026-07-09", end="2026-07-17", timezone_name="UTC")
        records, errors = engine.fetch_window(window, "Work", scan_cap=300)
        assert errors == []
        record = records[0]
        assert record["event_id"] == "EK-1"
        assert record["recurring_flag"] is True
        assert record["start"] == datetime(2026, 7, 10, 9, tzinfo=UTC)
        # The predicate was scoped to the named calendar.
        assert store.predicates[0][2] is not None

    def test_fetch_window_scan_cap(self, monkeypatch):
        cal = _CalStub("Work")
        ts = datetime(2026, 7, 10, 9, tzinfo=UTC).timestamp()
        events = [_EventStub(f"EK-{i}", "E", ts, ts + 60, cal) for i in range(5)]
        engine, _store = _engine(monkeypatch, events=events)
        window = bounded_calendar_window(start="2026-07-09", end="2026-07-17", timezone_name="UTC")
        records, errors = engine.fetch_window(window, "Work", scan_cap=3)
        assert len(records) == 3
        assert "scan cap" in errors[0]

    def test_fetch_by_ids_filters(self, monkeypatch):
        cal = _CalStub("Work")
        ts = datetime(2026, 7, 10, 9, tzinfo=UTC).timestamp()
        events = [_EventStub(f"EK-{i}", "E", ts, ts + 60, cal) for i in range(3)]
        engine, _store = _engine(monkeypatch, events=events)
        window = bounded_calendar_window(start="2026-07-09", end="2026-07-17", timezone_name="UTC")
        records, _errors = engine.fetch_window(window, "Work", scan_cap=300, event_ids=["EK-1"])
        assert [r["event_id"] for r in records] == ["EK-1"]

    def test_recurring_masters_pass_is_empty(self, monkeypatch):
        engine, _store = _engine(monkeypatch, events=[])
        window = bounded_calendar_window(start="2026-07-09", end="2026-07-17", timezone_name="UTC")
        assert engine.fetch_recurring_masters(window, "Work") == ([], [])
        assert engine.expands_occurrences is True
