"""update_event tool: PATCH semantics, span rules, attendee diffing, dry-run."""

import json
from datetime import datetime, timedelta

import apple_mail_mcp.server as server
from apple_mail_mcp.tools.calendar import update_event

from .conftest import HOST_TZ, FakeReadEngine, raw_event


def _engine_with_event(**kwargs):
    start = kwargs.pop("start", datetime.now(HOST_TZ) + timedelta(days=2))
    return FakeReadEngine(events=[raw_event("UID-1", start=start, **kwargs)])


def _run(**kwargs):
    defaults = {"event_id": "UID-1", "calendar": "Work"}
    defaults.update(kwargs)
    return json.loads(update_event(**defaults))


class TestPatchSemantics:
    def test_only_provided_fields_change(self, fake_engines):
        _read, write = fake_engines(read=_engine_with_event())
        payload = _run(title="Renamed")
        assert payload["updated"] is True
        assert list(payload["changes"]) == ["title"]
        set_lines = write.updated[0]["set_lines"]
        assert "set summary of targetEvent" in set_lines
        assert "set location of targetEvent" not in set_lines

    def test_duration_from_stored_start(self, fake_engines):
        start = datetime.now(HOST_TZ) + timedelta(days=2)
        _read, write = fake_engines(read=_engine_with_event(start=start))
        payload = _run(duration_minutes=90)
        assert "end" in payload["changes"]
        assert "ammNewEnd" in write.updated[0]["set_lines"]

    def test_no_changes_is_noop(self, fake_engines):
        _read, write = fake_engines(read=_engine_with_event())
        payload = _run()
        assert payload["updated"] is False
        assert write.updated == []

    def test_dry_run_previews_diff(self, fake_engines):
        _read, write = fake_engines(read=_engine_with_event())
        payload = _run(title="New", dry_run=True)
        assert payload["dry_run"] is True
        assert payload["changes"]["title"]["to"] == "New"
        assert write.updated == []

    def test_not_found_error(self, fake_engines):
        fake_engines(read=FakeReadEngine())
        payload = _run()
        assert payload["code"] == "EVENT_NOT_FOUND"

    def test_end_and_duration_refused(self, fake_engines):
        fake_engines(read=_engine_with_event())
        payload = _run(end="2026-07-15T10:00:00", duration_minutes=30)
        assert payload["code"] == "INVALID_EVENT_WINDOW"

    def test_alarm_replacement_and_clear(self, fake_engines):
        _read, write = fake_engines(read=_engine_with_event(alarms=[30]))
        _run(alarms_minutes_before=[5])
        assert "trigger interval:-5" in write.updated[0]["set_lines"]
        _run(clear_alarms=True)
        assert "delete every display alarm" in write.updated[1]["set_lines"]


class TestSpanRules:
    def test_recurring_without_span_refused(self, fake_engines):
        fake_engines(read=_engine_with_event(recurrence="FREQ=WEEKLY"))
        payload = _run(title="X")
        assert payload["code"] == "RECURRING_SPAN_REQUIRED"

    def test_this_occurrence_unsupported_names_future_routes(self, fake_engines):
        fake_engines(read=_engine_with_event(recurrence="FREQ=WEEKLY"))
        payload = _run(title="X", span="this_occurrence")
        assert payload["code"] == "RECURRING_SPAN_UNSUPPORTED"
        assert "EventKit" in json.dumps(payload["remediation"])

    def test_all_occurrences_allowed(self, fake_engines):
        _read, write = fake_engines(read=_engine_with_event(recurrence="FREQ=WEEKLY"))
        payload = _run(title="X", span="all_occurrences")
        assert payload["updated"] is True
        assert write.updated

    def test_non_recurring_ignores_span(self, fake_engines):
        fake_engines(read=_engine_with_event())
        payload = _run(title="X", span=None)
        assert payload["updated"] is True


class TestAttendeeDiff:
    def _attended(self):
        return _engine_with_event(
            attendees=[{"name": "Ada", "email": "ada@example.com", "participation_status": "accepted"}]
        )

    def test_echoing_stored_set_is_noop(self, fake_engines):
        """F5: re-sending the current attendee list never forces a re-invite."""
        _read, write = fake_engines(read=self._attended())
        payload = _run(attendees=["Ada@Example.com"])  # same set, different case
        assert payload["updated"] is False
        assert write.updated == []

    def test_new_attendee_requires_confirm(self, fake_engines):
        _read, write = fake_engines(read=self._attended())
        payload = _run(attendees=["ada@example.com", "bob@example.com"])
        assert payload["code"] == "INVITE_SEND_REQUIRES_CONFIRM"
        assert write.updated == []

    def test_new_attendee_with_confirm_adds_only_the_diff(self, fake_engines):
        _read, write = fake_engines(read=self._attended())
        payload = _run(attendees=["ada@example.com", "bob@example.com"], send_invitations=True, title="X")
        assert payload["updated"] is True
        assert payload["changes"]["attendees_added"] == ["bob@example.com"]
        set_lines = write.updated[0]["set_lines"]
        assert 'email:"bob@example.com"' in set_lines
        assert 'email:"ada@example.com"' not in set_lines

    def test_attendee_change_blocked_in_draft_safe(self, fake_engines, monkeypatch):
        fake_engines(read=self._attended())
        monkeypatch.setattr(server, "DRAFT_SAFE", True)
        payload = _run(attendees=["bob@example.com"], send_invitations=True)
        assert payload["code"] == "INVITE_SEND_BLOCKED"


class TestAttendeeRemoval:
    """F8: a removal-only attendee diff writes nothing and must not claim a change."""

    def _attended_two(self):
        return _engine_with_event(
            attendees=[
                {"name": "Ada", "email": "ada@example.com", "participation_status": "accepted"},
                {"name": "Bob", "email": "bob@example.com", "participation_status": "accepted"},
            ]
        )

    def test_clear_attendees_is_noop_with_removal_note(self, fake_engines):
        _read, write = fake_engines(read=self._attended_two())
        payload = _run(attendees=[])
        assert payload["updated"] is False
        assert "removal is unsupported" in payload["note"]
        assert "invitation_delivery" not in payload
        assert write.updated == []

    def test_subset_removal_only_is_noop(self, fake_engines):
        # Requesting a strict subset (drop bob, keep ada) removes nothing.
        _read, write = fake_engines(read=self._attended_two())
        payload = _run(attendees=["ada@example.com"])
        assert payload["updated"] is False
        assert payload.get("attendee_note")
        assert "invitation_delivery" not in payload
        assert write.updated == []

    def test_removal_with_other_change_notes_but_no_invite_disclosure(self, fake_engines):
        _read, write = fake_engines(read=self._attended_two())
        payload = _run(attendees=[], title="Renamed")
        assert payload["updated"] is True
        assert list(payload["changes"]) == ["title"]
        assert "attendees_added" not in payload["changes"]
        assert "invitation_delivery" not in payload
        assert "removal is unsupported" in payload["attendee_note"]
        # No attendee lines written for a removal-only diff.
        assert "make new attendee" not in write.updated[0]["set_lines"]


class TestRecurringWriteWindow:
    """F5: recurring targets widen the write-side lookup back past the master start."""

    def test_recurring_update_widens_write_window(self, fake_engines):
        _read, write = fake_engines(read=_engine_with_event(recurrence="FREQ=WEEKLY"))
        _run(title="X", span="all_occurrences")
        window = write.updated[0]["window"]
        # Read window is 30 back + 90 ahead (120 days); recurring widens the
        # start back by the 400-day lookback horizon (~520-day write window).
        assert (window.end - window.start).days >= 400

    def test_non_recurring_update_keeps_narrow_window(self, fake_engines):
        _read, write = fake_engines(read=_engine_with_event())
        _run(title="X")
        window = write.updated[0]["window"]
        assert (window.end - window.start).days <= 121


class TestTextOutput:
    def test_text_output_is_not_json(self, fake_engines):
        # F4: output_format="text" renders a compact summary, not raw JSON.
        _read, _write = fake_engines(read=_engine_with_event())
        result = update_event(event_id="UID-1", calendar="Work", title="Renamed", output_format="text")
        assert not result.lstrip().startswith("{")
        assert "updated" in result
        assert "title" in result


class TestConflictsAndModes:
    def test_conflict_block_on_reschedule(self, fake_engines):
        target_start = datetime.now(HOST_TZ) + timedelta(days=2)
        other = raw_event(
            "UID-OTHER",
            start=datetime.now(HOST_TZ) + timedelta(days=5),
            end=datetime.now(HOST_TZ) + timedelta(days=5, hours=1),
        )
        read = FakeReadEngine(events=[raw_event("UID-1", start=target_start), other])
        _read, write = fake_engines(read=read)
        new_start = (datetime.now(HOST_TZ) + timedelta(days=5, minutes=15)).isoformat()
        payload = _run(start=new_start, duration_minutes=30, on_conflict="block")
        assert payload["code"] == "EVENT_CONFLICT"
        assert write.updated == []

    def test_conflict_excludes_self(self, fake_engines):
        target_start = datetime.now(HOST_TZ) + timedelta(days=2)
        read = FakeReadEngine(events=[raw_event("UID-1", start=target_start)])
        _read, write = fake_engines(read=read)
        payload = _run(start=(target_start + timedelta(minutes=10)).isoformat(), on_conflict="block")
        assert payload["updated"] is True

    def test_read_only_backstop(self, fake_engines, monkeypatch):
        fake_engines(read=_engine_with_event())
        monkeypatch.setattr(server, "READ_ONLY", True)
        payload = _run(title="X")
        assert payload["code"] == "CALENDAR_WRITE_BLOCKED"

    def test_bad_event_id_refused(self, fake_engines):
        fake_engines()
        payload = _run(event_id="a|||b")
        assert payload["code"] == "INVALID_EVENT_ID"
