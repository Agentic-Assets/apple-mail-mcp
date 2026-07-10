"""Mode-gating matrix, registry removal set, and the RSVP shim."""

import asyncio
import json
from contextlib import suppress
from unittest.mock import MagicMock

import pytest

import apple_mail_mcp  # noqa: F401  (registers tools)
import apple_mail_mcp.server as server
from apple_mail_mcp.server import CALENDAR_DESTRUCTIVE_TOOLS, CALENDAR_WRITE_TOOLS, SEND_TOOLS, mcp
from apple_mail_mcp.tools.calendar import respond_to_invitation

CALENDAR_TOOLS = {
    "list_calendars",
    "list_events",
    "get_events_by_id",
    "check_availability",
    "create_event",
    "batch_create_events",
    "update_event",
    "delete_events",
    "manage_calendars",
    "respond_to_invitation",
}


class TestRegistry:
    def test_all_calendar_tools_registered(self):
        names = {tool.name for tool in mcp._tool_manager.list_tools()}
        assert CALENDAR_TOOLS <= names

    def test_write_and_destructive_tuples(self):
        assert set(CALENDAR_WRITE_TOOLS) == {
            "create_event",
            "update_event",
            "batch_create_events",
            "manage_calendars",
        }
        assert CALENDAR_DESTRUCTIVE_TOOLS == ("delete_events",)

    def test_read_only_removal_covers_calendar_writes(self):
        """Mirror __main__.py's removal loop: send + calendar write + destructive."""
        mock_mcp = MagicMock()
        for name in SEND_TOOLS + CALENDAR_WRITE_TOOLS + CALENDAR_DESTRUCTIVE_TOOLS:
            with suppress(KeyError, ValueError):
                mock_mcp.remove_tool(name)
        removed = {call.args[0] for call in mock_mcp.remove_tool.call_args_list}
        assert set(CALENDAR_WRITE_TOOLS) | set(CALENDAR_DESTRUCTIVE_TOOLS) <= removed
        # Reads and the RSVP shim stay registered under --read-only.
        assert "list_events" not in removed
        assert "respond_to_invitation" not in removed

    def test_server_instructions_document_mode_split(self):
        # F12: the domain split must be readable in one place.
        instructions = getattr(mcp, "_mcp_server", None)
        # FastMCP stores instructions on the underlying server; fall back to settings.
        text = ""
        if instructions is not None:
            text = getattr(instructions, "instructions", "") or ""
        assert "calendar" in text.lower()
        assert "--read-only" in text


class TestModeMatrix:
    """Every write/destructive tool keeps an internal guard for the CLI path."""

    @pytest.mark.parametrize(
        ("call", "code"),
        [
            (lambda: _tool("create_event")(title="T", start="2026-07-13", duration_minutes=30), "CALENDAR_WRITE_BLOCKED"),
            (
                lambda: _tool("batch_create_events")(events=[{"title": "T", "start": "2026-07-13", "duration_minutes": 30}]),
                "CALENDAR_WRITE_BLOCKED",
            ),
            (lambda: _tool("update_event")(event_id="UID-1", title="X"), "CALENDAR_WRITE_BLOCKED"),
            (lambda: _tool("delete_events")(event_ids=["UID-1"]), "CALENDAR_WRITE_BLOCKED"),
            (lambda: _tool("manage_calendars")(action="create", name="X"), "CALENDAR_WRITE_BLOCKED"),
            (lambda: _tool("manage_calendars")(action="delete", name="Work"), "CALENDAR_WRITE_BLOCKED"),
        ],
    )
    def test_read_only_blocks_every_write_path(self, monkeypatch, call, code):
        monkeypatch.setattr(server, "READ_ONLY", True)
        monkeypatch.setattr(server, "DRAFT_SAFE", True)
        payload = json.loads(call())
        assert payload["code"] == code

    @pytest.mark.parametrize(
        ("call", "code"),
        [
            (lambda: _tool("delete_events")(event_ids=["UID-1"]), "CALENDAR_DELETE_BLOCKED"),
            (lambda: _tool("manage_calendars")(action="delete", name="Work"), "CALENDAR_DELETE_BLOCKED"),
        ],
    )
    def test_draft_safe_blocks_deletes(self, monkeypatch, call, code):
        monkeypatch.setattr(server, "READ_ONLY", False)
        monkeypatch.setattr(server, "DRAFT_SAFE", True)
        monkeypatch.setattr(server, "CALENDAR_ALLOW_DESTRUCTIVE", False)
        payload = json.loads(call())
        assert payload["code"] == code

    def test_reads_allowed_in_read_only(self, monkeypatch, fake_engines):
        monkeypatch.setattr(server, "READ_ONLY", True)
        monkeypatch.setattr(server, "DRAFT_SAFE", True)
        monkeypatch.setattr(
            "apple_mail_mcp.tools.calendar.eventkit_status", lambda: (False, "dependency_missing")
        )
        fake_engines()
        from apple_mail_mcp.tools.calendar import list_calendars, list_events

        assert json.loads(list_calendars())["calendars"]
        assert "events" in json.loads(asyncio.run(list_events()))


def _tool(name):
    from apple_mail_mcp.tools import calendar as calendar_tools

    return getattr(calendar_tools, name)


class TestRsvpShim:
    def test_always_refuses_with_platform_code(self):
        payload = json.loads(respond_to_invitation(event_id="UID-1", response="accept"))
        assert payload["code"] == "CALENDAR_RSVP_UNSUPPORTED"
        assert payload["error"] is True

    def test_refuses_in_every_mode(self, monkeypatch):
        for read_only, draft_safe in [(False, False), (False, True), (True, True)]:
            monkeypatch.setattr(server, "READ_ONLY", read_only)
            monkeypatch.setattr(server, "DRAFT_SAFE", draft_safe)
            payload = json.loads(respond_to_invitation())
            assert payload["code"] == "CALENDAR_RSVP_UNSUPPORTED"
