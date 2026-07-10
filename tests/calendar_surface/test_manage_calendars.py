"""manage_calendars tool: create/rename/delete with the confirm chain."""

import json

import apple_mail_mcp.server as server
from apple_mail_mcp.tools.calendar import manage_calendars

from .conftest import FakeWriteEngine


def _run(**kwargs):
    return json.loads(manage_calendars(**kwargs))


class TestCreate:
    def test_create_happy(self, fake_engines):
        _read, write = fake_engines()
        payload = _run(action="create", name="Projects")
        assert payload["created"] is True
        assert write.calendar_ops == [{"op": "create", "name": "Projects"}]

    def test_create_collision_refused(self, fake_engines):
        fake_engines()
        payload = _run(action="create", name="Work")
        assert payload["code"] == "CALENDAR_ALREADY_EXISTS"

    def test_create_requires_name(self, fake_engines):
        fake_engines()
        assert manage_calendars(action="create").startswith("Error:")

    def test_create_allowed_under_draft_safe(self, fake_engines, monkeypatch):
        _read, write = fake_engines()
        monkeypatch.setattr(server, "DRAFT_SAFE", True)
        payload = _run(action="create", name="Projects")
        assert payload["created"] is True

    def test_create_blocked_read_only(self, fake_engines, monkeypatch):
        fake_engines()
        monkeypatch.setattr(server, "READ_ONLY", True)
        payload = _run(action="create", name="Projects")
        assert payload["code"] == "CALENDAR_WRITE_BLOCKED"


class TestRename:
    def test_rename_exact_name(self, fake_engines):
        _read, write = fake_engines()
        payload = _run(action="rename", name="Work", new_name="Job")
        assert payload["renamed"] is True
        assert write.calendar_ops[0]["new_name"] == "Job"

    def test_rename_by_calendar_id(self, fake_engines):
        _read, write = fake_engines()
        payload = _run(action="rename", calendar_id="UID-HOME", new_name="Household")
        assert payload["from"] == "Home"

    def test_rename_fuzzy_disabled(self, fake_engines):
        fake_engines()
        payload = _run(action="rename", name="work", new_name="Job")  # inexact case
        assert payload["code"] == "CALENDAR_NOT_FOUND"
        assert "exact" in payload["message"]

    def test_rename_collision_refused(self, fake_engines):
        fake_engines()
        payload = _run(action="rename", name="Work", new_name="Home")
        assert payload["code"] == "CALENDAR_ALREADY_EXISTS"


class TestDeleteChain:
    def test_dry_run_default_reports_cascade_count(self, fake_engines):
        _read, write = fake_engines(write=FakeWriteEngine(event_counts={"Work": 42}))
        payload = _run(action="delete", name="Work")
        assert payload["dry_run"] is True
        assert payload["event_count"] == 42
        assert payload["deleted"] is False
        assert "force_nonempty" in payload["next_step"]
        assert write.calendar_ops == []

    def test_delete_without_confirm_refused(self, fake_engines):
        _read, write = fake_engines()
        payload = _run(action="delete", name="Work", dry_run=False)
        assert payload["code"] == "CALENDAR_CONFIRMATION_REQUIRED"
        assert write.calendar_ops == []

    def test_nonempty_needs_force(self, fake_engines):
        _read, write = fake_engines(write=FakeWriteEngine(event_counts={"Work": 5}))
        payload = _run(action="delete", name="Work", dry_run=False, confirm_delete_calendar=True)
        assert payload["code"] == "CALENDAR_CONFIRMATION_REQUIRED"
        assert "force_nonempty" in payload["message"]
        assert write.calendar_ops == []

    def test_full_chain_deletes(self, fake_engines):
        _read, write = fake_engines(write=FakeWriteEngine(event_counts={"Work": 5}))
        payload = _run(
            action="delete",
            name="Work",
            dry_run=False,
            confirm_delete_calendar=True,
            force_nonempty=True,
        )
        assert payload["deleted"] is True
        assert write.calendar_ops == [{"op": "delete", "name": "Work"}]

    def test_empty_calendar_needs_no_force(self, fake_engines):
        _read, write = fake_engines(write=FakeWriteEngine(event_counts={"Work": 0}))
        payload = _run(action="delete", name="Work", dry_run=False, confirm_delete_calendar=True)
        assert payload["deleted"] is True

    def test_delete_blocked_draft_safe(self, fake_engines, monkeypatch):
        fake_engines()
        monkeypatch.setattr(server, "DRAFT_SAFE", True)
        monkeypatch.setattr(server, "CALENDAR_ALLOW_DESTRUCTIVE", False)
        payload = _run(action="delete", name="Work")
        assert payload["code"] == "CALENDAR_DELETE_BLOCKED"

    def test_delete_exact_selector_only(self, fake_engines):
        fake_engines()
        payload = _run(action="delete", name="wor")
        assert payload["code"] == "CALENDAR_NOT_FOUND"

    def test_invalid_action(self, fake_engines):
        fake_engines()
        assert manage_calendars(action="destroy").startswith("Error:")


class TestTextOutput:
    def test_create_text_is_not_json(self, fake_engines):
        # F4: output_format="text" renders a compact summary, not raw JSON.
        _read, _write = fake_engines()
        result = manage_calendars(action="create", name="Projects", output_format="text")
        assert not result.lstrip().startswith("{")
        assert "created calendar" in result

    def test_delete_dry_run_text_is_not_json(self, fake_engines):
        _read, _write = fake_engines(write=FakeWriteEngine(event_counts={"Work": 3}))
        result = manage_calendars(action="delete", name="Work", output_format="text")
        assert not result.lstrip().startswith("{")
        assert "3 event(s)" in result
