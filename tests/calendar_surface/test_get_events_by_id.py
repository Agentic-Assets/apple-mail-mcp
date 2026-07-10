"""get_events_by_id tool: bounded detail lookups, missing ids, caps."""

import asyncio
import json
from datetime import datetime, timedelta

from apple_mail_mcp.tools.calendar import get_events_by_id

from .conftest import HOST_TZ, FakeReadEngine, raw_event


def _run(**kwargs):
    return json.loads(asyncio.run(get_events_by_id(**kwargs)))


class TestGetEventsById:
    def test_detail_payload(self, fake_engines):
        event = raw_event(
            "UID-1",
            start=datetime.now(HOST_TZ) + timedelta(days=1),
            notes="full notes " * 60,
            attendees=[{"name": "Ada", "email": "ada@example.com", "participation_status": "accepted"}],
            alarms=[15],
        )
        read = FakeReadEngine(events=[event])
        fake_engines(read=read)
        payload = _run(event_ids=["UID-1"], calendar="Work")
        record = payload["events"][0]
        assert record["notes"].startswith("full notes")
        assert len(record["notes"]) > 280  # untruncated
        assert record["attendees"][0]["email"] == "ada@example.com"
        assert record["alarms_minutes_before"] == [15]
        assert payload["missing"] == []
        assert read.fetch_calls[0]["include_detail"] is True
        assert read.fetch_calls[0]["event_ids"] == ["UID-1"]

    def test_missing_ids_reported_not_fatal(self, fake_engines):
        read = FakeReadEngine(events=[raw_event("UID-1", start=datetime.now(HOST_TZ) + timedelta(days=1))])
        fake_engines(read=read)
        payload = _run(event_ids=["UID-1", "GHOST"], calendar="Work")
        assert [e["event_id"] for e in payload["events"]] == ["UID-1"]
        assert payload["missing"] == ["GHOST"]

    def test_over_25_ids_refused(self, fake_engines):
        fake_engines()
        payload = _run(event_ids=[f"UID-{i}" for i in range(26)])
        assert payload["code"] == "INVALID_EVENT_ID"

    def test_bad_id_shape_refused(self, fake_engines):
        fake_engines()
        payload = _run(event_ids=["a|||b"])
        assert payload["code"] == "INVALID_EVENT_ID"

    def test_lookup_window_is_bounded_default(self, fake_engines):
        read = FakeReadEngine()
        fake_engines(read=read)
        _run(event_ids=["UID-1"], calendar="Work")
        window = read.fetch_calls[0]["window"]
        width_days = (window.end - window.start).total_seconds() / 86_400
        assert 119 <= width_days <= 121  # 30 back + 90 ahead

    def test_unscoped_lookup_fans_out(self, fake_engines):
        read = FakeReadEngine()
        fake_engines(read=read)
        payload = _run(event_ids=["UID-1"])
        assert payload["calendars_scanned"] == ["Work", "Home", "MCP Test Calendar"]

    def test_text_output_is_not_json(self, fake_engines):
        # F4: output_format="text" renders a compact summary, not raw JSON.
        read = FakeReadEngine(events=[raw_event("UID-1", start=datetime.now(HOST_TZ) + timedelta(days=1))])
        fake_engines(read=read)
        result = asyncio.run(get_events_by_id(event_ids=["UID-1", "GHOST"], calendar="Work", output_format="text"))
        assert not result.lstrip().startswith("{")
        assert "UID-1" in result
        assert "missing" in result
