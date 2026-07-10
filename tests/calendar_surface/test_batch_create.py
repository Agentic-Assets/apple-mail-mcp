"""batch_create_events tool: caps, all-or-nothing validation, partial writes."""

import json
from datetime import datetime

import apple_mail_mcp.server as server
from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.tools.calendar import batch_create_events

from .conftest import HOST_TZ, FakeReadEngine, FakeWriteEngine, raw_event


def _items(n=2):
    return [
        {"title": f"Block {i}", "start": f"2026-07-{13 + i:02}T09:00:00", "duration_minutes": 60}
        for i in range(n)
    ]


def _run(events=None, **kwargs):
    defaults = {"calendar": "Work", "timezone": "America/Chicago"}
    defaults.update(kwargs)
    return json.loads(batch_create_events(events=events if events is not None else _items(), **defaults))


class TestValidation:
    def test_empty_batch_refused(self, fake_engines):
        fake_engines()
        assert batch_create_events(events=[]).startswith("Error:")

    def test_over_cap_refused(self, fake_engines):
        fake_engines()
        payload = _run(events=_items(26))
        assert payload["code"] == "BATCH_TOO_LARGE"

    def test_attendees_in_item_refused(self, fake_engines):
        fake_engines()
        items = _items(2)
        items[1]["attendees"] = ["a@example.com"]
        payload = _run(events=items)
        assert payload["code"] == "INVALID_EVENT_WINDOW"
        assert "events[1]" in payload["message"]

    def test_recurrence_in_item_refused(self, fake_engines):
        fake_engines()
        items = _items(1)
        items[0]["recurrence"] = "FREQ=DAILY"
        payload = _run(events=items)
        assert payload["code"] == "INVALID_EVENT_WINDOW"

    def test_unknown_key_refused(self, fake_engines):
        fake_engines()
        items = _items(1)
        items[0]["colour"] = "red"
        payload = _run(events=items)
        assert payload["code"] == "INVALID_EVENT_WINDOW"

    def test_one_bad_item_blocks_all_writes(self, fake_engines):
        _read, write = fake_engines()
        items = _items(2)
        items[1]["start"] = "not-a-date"
        payload = _run(events=items)
        assert payload["code"] == "INVALID_EVENT_WINDOW"
        assert write.created == []


class TestDryRunAndWrites:
    def test_dry_run_previews_without_writing(self, fake_engines):
        _read, write = fake_engines()
        payload = _run(dry_run=True)
        assert payload["dry_run"] is True
        assert len(payload["would_create"]) == 2
        assert write.created == []

    def test_partial_write_failure_reported(self, fake_engines):
        class _FlakyWrite(FakeWriteEngine):
            def create_event(self, **kwargs):
                if kwargs["title"] == "Block 1":
                    raise ToolError(code="CALENDAR_READ_ONLY", message="nope")
                return super().create_event(**kwargs)

        fake_engines(write=_FlakyWrite())
        payload = _run()
        assert payload["created_count"] == 1
        assert payload["failed_count"] == 1
        assert "CALENDAR_READ_ONLY" in payload["failed"][0]["error"]

    def test_item_timezone_overrides_batch(self, fake_engines):
        _read, write = fake_engines()
        items = [{"title": "Zoned", "start": "2026-07-13T09:00:00", "duration_minutes": 30, "timezone": "UTC"}]
        _run(events=items)
        assert write.created[0]["start"].utcoffset().total_seconds() == 0


class TestConflicts:
    def test_block_refuses_whole_batch(self, fake_engines):
        read = FakeReadEngine(
            events=[
                raw_event(
                    "UID-BUSY",
                    start=datetime(2026, 7, 13, 9, 0, tzinfo=HOST_TZ),
                    end=datetime(2026, 7, 13, 10, 0, tzinfo=HOST_TZ),
                )
            ]
        )
        _read, write = fake_engines(read=read)
        items = [{"title": "Clash", "start": "2026-07-13T09:30:00", "duration_minutes": 30}]
        payload = json.loads(batch_create_events(events=items, calendar="Work", on_conflict="block"))
        assert payload["code"] == "EVENT_CONFLICT"
        assert write.created == []

    def test_mode_gate_blocks_read_only(self, fake_engines, monkeypatch):
        _read, write = fake_engines()
        monkeypatch.setattr(server, "READ_ONLY", True)
        payload = _run()
        assert payload["code"] == "CALENDAR_WRITE_BLOCKED"
        assert write.created == []


class TestTextOutput:
    def test_dry_run_text_is_not_json(self, fake_engines):
        # F4: output_format="text" renders a compact summary, not raw JSON.
        _read, _write = fake_engines()
        result = batch_create_events(
            events=_items(2), calendar="Work", timezone="America/Chicago", dry_run=True, output_format="text"
        )
        assert not result.lstrip().startswith("{")
        assert "would create 2" in result
