"""Tests for the AGENTIC-988 bounded-export hardening of ``export_emails``.

Live testing against a real Gmail-backed Mail account surfaced two bugs:

1. ``single_email`` scope never created its save directory (every other
   scope runs ``mkdir -p``), so the export failed when the directory did
   not already exist.
2. ``thread`` scope grouped exported ids under whatever mailbox name
   ``get_email_thread`` reported for each message. On Gmail-backed
   accounts that name is the virtual "All Mail" store, which Mail.app
   cannot open directly (``Mailbox not found: All Mail``) and which must
   never be scanned (it is the entire remote store).

The product owner also mandated that exports be bounded by design:
``max_emails`` is hard-capped at 50 across every scope, ``message_ids``
is capped at 50 ids per call, ``entire_mailbox`` always binds a bounded
page-slice window (never the full mailbox), and ``correspondent``'s
scan bound is capped at 250.

These tests mock ``run_applescript`` and capture the generated
AppleScript (mirroring ``tests/analytics/test_analytics_resource_safety.py``)
rather than driving real Mail.app.
"""

import json
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.tools import analytics as analytics_tools

DESKTOP_PATH = str(Path("~/Desktop").expanduser())


class _ScriptCapture:
    """Capture every script passed to run_applescript; return a fixed value."""

    def __init__(self, return_value: str = ""):
        self.scripts: list[str] = []
        self._return_value = return_value

    def __call__(self, script: str, timeout: int | None = 120) -> str:
        self.scripts.append(script)
        return self._return_value

    @property
    def last_script(self) -> str:
        return self.scripts[-1] if self.scripts else ""


def _export(**kwargs):
    """Drive export_emails with a mocked run_applescript; return (result, capture)."""
    capture = _ScriptCapture(return_value=kwargs.pop("_return_value", "EXPORTING\n\n✓ Exported!"))
    defaults = dict(
        account="Work",
        save_directory=DESKTOP_PATH,
    )
    defaults.update(kwargs)
    with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture):
        result = analytics_tools.export_emails(**defaults)
    return result, capture


# ---------------------------------------------------------------------------
# Hard cap: max_emails <= 50 across every scope
# ---------------------------------------------------------------------------


def test_max_emails_over_50_rejected_for_entire_mailbox():
    result, capture = _export(scope="entire_mailbox", max_emails=51)

    assert not capture.scripts, "run_applescript must not be called once max_emails is rejected"
    assert result.startswith("Error:")
    assert "50" in result


def test_max_emails_over_50_rejected_for_correspondent():
    result, capture = _export(
        scope="correspondent",
        email_address="person@example.com",
        max_emails=51,
        date_from="2026-07-01",
    )

    assert not capture.scripts
    assert result.startswith("Error:")
    assert "50" in result


def test_max_emails_at_50_is_accepted():
    result, capture = _export(scope="entire_mailbox", max_emails=50)

    assert capture.scripts, "max_emails=50 is the boundary and must be accepted"
    assert not result.startswith("Error: max_emails")


# ---------------------------------------------------------------------------
# Hard cap: message_ids <= 50 per call
# ---------------------------------------------------------------------------


def test_message_ids_over_50_rejected():
    ids = [str(i) for i in range(1, 52)]  # 51 ids
    result, capture = _export(message_ids=ids)

    assert not capture.scripts, "run_applescript must not be called once message_ids is rejected"
    assert result.startswith("Error:")
    assert "50" in result


def test_message_ids_at_50_is_accepted():
    ids = [str(i) for i in range(1, 51)]  # 50 ids
    result, capture = _export(message_ids=ids)

    assert capture.scripts
    assert not result.startswith("Error: message_ids is limited")


# ---------------------------------------------------------------------------
# Upfront validation (format / sort) — unchanged contracts, still gate before AppleScript
# ---------------------------------------------------------------------------


def test_invalid_pdf_format_rejected_before_applescript():
    result, capture = _export(scope="entire_mailbox", format="pdf")

    assert not capture.scripts
    assert result == "Error: Invalid format 'pdf'. Supported: txt, html"


def test_oldest_first_sort_rejected():
    result, capture = _export(scope="entire_mailbox", sort="oldest_first")

    assert not capture.scripts
    assert result == "Error: Invalid sort. Use: newest_first or date_desc"


# ---------------------------------------------------------------------------
# BUG #1 — single_email must create its save directory
# ---------------------------------------------------------------------------


def test_single_email_creates_save_directory_before_write():
    result, capture = _export(
        scope="single_email",
        message_id="42",
        mailbox="INBOX",
        format="txt",
        _return_value="EXPORTING EMAIL\n\n✓ Email exported successfully!",
    )

    script = capture.last_script
    assert script, "expected single_email to generate an AppleScript"
    assert 'do shell script "mkdir -p " & quoted form of "' in script
    # The mkdir must run before the file write, not after.
    mkdir_pos = script.find("mkdir -p")
    write_pos = script.find("open for access POSIX file filePath with write permission")
    assert mkdir_pos != -1
    assert write_pos != -1
    assert mkdir_pos < write_pos
    assert not result.startswith("Error:")


# ---------------------------------------------------------------------------
# BUG (hardening) — entire_mailbox must always bind a bounded page slice,
# never the full mailbox
# ---------------------------------------------------------------------------


def test_entire_mailbox_uses_bounded_page_slice_not_all_messages():
    _result, capture = _export(scope="entire_mailbox", max_emails=10, offset=5)

    script = capture.last_script
    assert script
    assert "messages pageStart thru pageEnd of targetMailbox" in script
    assert "set mailboxMessages to messages of targetMailbox" not in script
    # Bounded page arithmetic must reference the actual offset/max_emails.
    assert "set pageStart to 5 + 1" in script
    assert "set pageEnd to 5 + 10" in script


def test_entire_mailbox_default_max_emails_is_25_not_100():
    _result, capture = _export(scope="entire_mailbox")

    script = capture.last_script
    assert "set pageEnd to 0 + 25" in script


# ---------------------------------------------------------------------------
# BUG #2 — thread export must never scan Gmail's virtual "All Mail" mailbox
# ---------------------------------------------------------------------------


def test_thread_export_ignores_all_mail_and_uses_bounded_candidates():
    payload = {
        "items": [
            {"message_id": "101", "mailbox": "All Mail"},
            {"message_id": "202", "mailbox": "All Mail"},
        ]
    }

    with patch("apple_mail_mcp.tools.search.get_email_thread", return_value=json.dumps(payload)):
        result, capture = _export(
            scope="thread",
            message_id="101",
            include_sent=True,
            _return_value="Exported: 2\nLocation: /tmp/thread_export",
        )

    script = capture.last_script
    assert script
    assert "All Mail" not in script
    assert 'mailbox "INBOX" of targetAccount' in script
    assert 'mailbox "Sent Mail" of targetAccount' in script
    assert "Mailbox not found" not in result
    assert result.startswith("THREAD EXPORT")


def test_thread_export_skips_sent_candidates_when_include_sent_false():
    payload = {"items": [{"message_id": "101", "mailbox": "All Mail"}]}

    with patch("apple_mail_mcp.tools.search.get_email_thread", return_value=json.dumps(payload)):
        _result, capture = _export(
            scope="thread",
            message_id="101",
            include_sent=False,
        )

    script = capture.last_script
    assert 'mailbox "INBOX" of targetAccount' in script
    assert "Sent Mail" not in script
    assert "All Mail" not in script


# ---------------------------------------------------------------------------
# correspondent — scan bound capped at 250
# ---------------------------------------------------------------------------


def test_correspondent_scan_bound_capped_at_250():
    _result, capture = _export(
        scope="correspondent",
        email_address="person@example.com",
        max_emails=10,
        offset=300,  # max_emails + offset = 310, must be clamped to 250
        date_from="2026-07-01",
        _return_value="EXPORTING CORRESPONDENT\n\nExported: 0",
    )

    script = capture.last_script
    assert script
    assert "messages 1 thru 250 of currentMailbox" in script
    assert "messages 1 thru 310 of currentMailbox" not in script
