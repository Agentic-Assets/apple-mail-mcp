"""Shared pytest hooks for apple-mail-mcp tests."""

import pytest


@pytest.fixture(autouse=True)
def _pass_through_known_test_accounts(monkeypatch):
    """Most tool tests pass account='Work' without mocking Mail account listing."""

    def _validate(account, timeout=30):
        if account == "Missing":
            return (
                f"Error: account_not_found — '{account}' is not configured in Mail. "
                "Available accounts: Work"
            )
        return None

    monkeypatch.setattr("apple_mail_mcp.core.validate_account_name", _validate)
    monkeypatch.setattr("apple_mail_mcp.tools.inbox.validate_account_name", _validate)
    monkeypatch.setattr("apple_mail_mcp.tools.search.validate_account_name", _validate)
    monkeypatch.setattr("apple_mail_mcp.tools.manage.validate_account_name", _validate)
    monkeypatch.setattr("apple_mail_mcp.tools.analytics.validate_account_name", _validate)
    monkeypatch.setattr("apple_mail_mcp.tools.smart_inbox.validate_account_name", _validate)
    monkeypatch.setattr("apple_mail_mcp.tools.compose.validate_account_name", _validate)

    # account_not_found_json (used by JSON error paths in inbox/search) and
    # cli._mailbox_count both shell out to osascript. Stub them so the suite
    # is robust to a hung or absent Mail.app — CI runs on Ubuntu with no
    # osascript, and locally Mail can hang for unrelated reasons.
    monkeypatch.setattr(
        "apple_mail_mcp.core.list_mail_account_names",
        lambda timeout=30: ["Work"],
    )
    monkeypatch.setattr(
        "apple_mail_mcp.cli._mailbox_count",
        lambda account: 0,
    )


@pytest.fixture(autouse=True)
def _calendar_test_guardrails(monkeypatch):
    """Calendar analogue of the account fixture: never touch live Calendar.

    Stubs calendar-name resolution against three known test calendars and
    replaces the calendar engine's osascript seam with a tripwire so any
    unpatched engine call fails loudly instead of talking to Calendar.app.
    Tests that exercise the AppleScript engine patch
    ``apple_mail_mcp.calendar_core.engine.run_applescript`` themselves.
    """

    def _no_live_calendar(script, timeout=120):
        raise AssertionError("test attempted a live Calendar.app osascript call; patch the engine seam")

    monkeypatch.setattr("apple_mail_mcp.calendar_core.engine.run_applescript", _no_live_calendar)
    monkeypatch.setattr(
        "apple_mail_mcp.tools.calendar.list_calendar_names",
        lambda timeout=None: ["Work", "Home", "MCP Test Calendar"],
    )
