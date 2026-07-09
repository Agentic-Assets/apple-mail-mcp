"""Regression tests for the analytics.py batch-export file-handle leak (Bug 1).

The entire_mailbox export path in export_emails opens each file with
``open for access POSIX file filePath``.  Before the fix the ``on error``
handler at the per-message level skipped ``close access fileRef``, leaking
a kernel file descriptor on any write failure.

These tests:
1. Assert that the generated AppleScript contains ``close access fileRef``
   inside the per-message ``on error`` handler (script-text assertion).
2. Count ``open for access`` vs ``close access`` occurrences to ensure every
   open is matched by a close on both the happy path AND the error path.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.tools import analytics as analytics_tools

DESKTOP_PATH = str(Path("~/Desktop").expanduser())


class _ScriptCapture:
    """Capture script passed to run_applescript and return a fixed value."""

    def __init__(self, return_value=""):
        self.scripts: list[str] = []
        self._return_value = return_value

    def __call__(self, script: str, timeout=120):
        self.scripts.append(script)
        return self._return_value

    @property
    def last_script(self) -> str:
        return self.scripts[-1] if self.scripts else ""


def _generate_entire_mailbox_script(account: str = "Work") -> str:
    """Drive export_emails(scope='entire_mailbox') and return the captured AppleScript."""
    capture = _ScriptCapture(return_value="EXPORTING MAILBOX\n\n✓ Mailbox exported successfully!")
    with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture):
        analytics_tools.export_emails(
            account=account,
            scope="entire_mailbox",
            save_directory=DESKTOP_PATH,
            mailbox="INBOX",
            format="txt",
        )
    return capture.last_script


class EntireMailboxScriptResourceSafetyTests(unittest.TestCase):
    """Verify the entire_mailbox batch path closes every file handle it opens."""

    def setUp(self):
        self.script = _generate_entire_mailbox_script()

    def test_script_was_generated(self):
        self.assertTrue(self.script, "Expected a non-empty AppleScript to be generated")

    def test_on_error_handler_contains_close_access_fileref(self):
        """The per-message on error block must close fileRef before continuing."""
        # Find the per-message on error handler.  We locate it by finding
        # "Continue with next email" or the batch-level error handler and then
        # checking for close access fileRef nearby in the right block.
        #
        # Strategy: find the first "on error" that follows "open for access" in
        # the repeat loop section, and assert close access fileRef appears
        # between that "on error" and the matching "end try".
        open_pos = self.script.find("open for access POSIX file filePath with write permission")
        self.assertGreater(open_pos, -1, "Expected 'open for access POSIX file filePath' in script")

        # Find the "on error" that follows the open inside the repeat block.
        on_error_pos = self.script.find("on error", open_pos)
        self.assertGreater(on_error_pos, -1, "Expected 'on error' after the open for access line")

        # Find the matching end try after that on error.
        end_try_pos = self.script.find("end try", on_error_pos)
        self.assertGreater(end_try_pos, -1, "Expected 'end try' to close the per-message error block")

        error_block = self.script[on_error_pos:end_try_pos]
        self.assertIn(
            "close access fileRef",
            error_block,
            "Expected 'close access fileRef' inside the per-message on error handler; "
            "without it a write failure leaks a kernel file descriptor",
        )

    def test_close_access_count_matches_or_exceeds_open_for_access_count(self):
        """Every 'open for access' must have a matching 'close access'.

        In the batch loop, each happy-path iteration calls open + close.
        The error path must also call close.  So the number of close access
        statements in the script must be >= the number of open for access
        statements (each open is matched; the error-path close is additional).
        """
        open_count = self.script.count("open for access")
        close_count = self.script.count("close access")
        self.assertGreaterEqual(
            close_count,
            open_count,
            f"Found {open_count} 'open for access' but only {close_count} 'close access' "
            "occurrences — file handles can leak on error paths",
        )

    def test_error_block_close_uses_guarded_try(self):
        """The close inside the error handler must itself be wrapped in try...end try.

        This prevents a secondary error (e.g. fileRef not yet defined if open
        failed) from masking the original error.
        """
        open_pos = self.script.find("open for access POSIX file filePath with write permission")
        on_error_pos = self.script.find("on error", open_pos)
        end_try_pos = self.script.find("end try", on_error_pos)
        error_block = self.script[on_error_pos:end_try_pos]

        # The close access should appear inside a nested try...end try block.
        close_pos_in_block = error_block.find("close access fileRef")
        self.assertGreater(close_pos_in_block, -1)

        # There should be a "try" before the close and an "end try" after it
        # (or at end of block), within the error block.
        inner_try_pos = error_block.find("try", 0)
        self.assertGreater(
            inner_try_pos,
            -1,
            "Expected an inner 'try' guard around 'close access fileRef' in the error handler",
        )
        self.assertLess(
            inner_try_pos,
            close_pos_in_block,
            "The inner 'try' must appear before 'close access fileRef'",
        )


class SingleEmailExportCloseTest(unittest.TestCase):
    """Sanity-check: the single-email path already has correct close-on-error."""

    def test_single_email_error_path_also_closes_file(self):
        capture = _ScriptCapture(return_value="EXPORTING EMAIL\n\n✓ Email exported successfully!")
        # For the single-email path we need search_emails to return a record.
        with (
            patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture),
        ):
            analytics_tools.export_emails(
                account="Work",
                scope="single_email",
                message_id="42",
                save_directory=DESKTOP_PATH,
                mailbox="INBOX",
                format="txt",
            )

        script = capture.last_script
        # Single-email path should have a close in its on error block too.
        on_error_pos = script.find("on error errMsg")
        self.assertGreater(on_error_pos, -1)
        end_try_pos = script.find("end try", on_error_pos)
        error_block = script[on_error_pos:end_try_pos]
        self.assertIn("close access", error_block)


class ListEmailAttachmentsDictionaryTests(unittest.TestCase):
    def test_attachment_size_uses_mail_file_size_property(self):
        capture = _ScriptCapture(return_value="ATTACHMENTS FOR: Test")

        with (
            patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture),
        ):
            analytics_tools.list_email_attachments(
                account="Work",
                message_ids=["42"],
            )

        self.assertIn("file size of anAttachment", capture.last_script)
        self.assertNotIn("set attachmentSize to size of anAttachment", capture.last_script)


class ExportEmailsBoundedDefaultsTests(unittest.TestCase):
    """v3.9.3: entire_mailbox pages a bounded slice (default 25); hard cap at 50, no walk."""

    def _export(self, **kwargs):
        """Drive export_emails and return (result_text, captured_script)."""
        capture = _ScriptCapture(return_value="EXPORTING MAILBOX\n\n✓ Mailbox exported successfully!")
        defaults = dict(
            account="Work",
            scope="entire_mailbox",
            save_directory=DESKTOP_PATH,
            mailbox="INBOX",
            format="txt",
        )
        defaults.update(kwargs)
        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture):
            result = analytics_tools.export_emails(**defaults)
        return result, capture.last_script

    def test_entire_mailbox_default_page_is_25(self):
        """When max_emails is omitted, entire_mailbox pages 25 (pageEnd = offset + 25)."""
        _result, script = self._export()
        self.assertIn("set pageEnd to 0 + 25", script)

    def test_entire_mailbox_never_binds_full_message_list(self):
        """The bounded page slice must never materialize the whole mailbox."""
        _result, script = self._export(max_emails=25)
        self.assertIn("messages pageStart thru pageEnd of targetMailbox", script)
        self.assertNotIn("set mailboxMessages to messages of targetMailbox", script)

    def test_max_emails_above_50_rejected_before_applescript(self):
        """Hard cap: anything over 50 refuses up front, no Mail work."""
        result, script = self._export(max_emails=51)
        self.assertIn("must be between 1 and 50", result)
        self.assertEqual(script, "")

    def test_max_emails_50_is_accepted(self):
        """The cap boundary (50) still runs and pages 50."""
        result, script = self._export(max_emails=50)
        self.assertIn("set pageEnd to 0 + 50", script)
        self.assertNotIn("must be between 1 and 50", result)

    def test_no_performance_warning_path_remains(self):
        """The old 'warn above 500' path is gone; large values are rejected, not warned."""
        result, _script = self._export(max_emails=100)
        self.assertNotIn("Performance warning", result)
        self.assertIn("must be between 1 and 50", result)


class ExportEmailsRoadmapTests(unittest.TestCase):
    def test_invalid_pdf_format_fails_before_applescript(self):
        with patch("apple_mail_mcp.tools.analytics.run_applescript") as mock_run:
            result = analytics_tools.export_emails(
                account="Work",
                scope="entire_mailbox",
                format="pdf",
                save_directory=DESKTOP_PATH,
            )

        mock_run.assert_not_called()
        self.assertEqual(result, "Error: Invalid format 'pdf'. Supported: txt, html")

    def test_entire_mailbox_offset_and_date_window_are_bounded(self):
        capture = _ScriptCapture(return_value="EXPORTING MAILBOX\n\n✓ Mailbox exported successfully!")
        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture):
            analytics_tools.export_emails(
                account="Work",
                scope="entire_mailbox",
                save_directory=DESKTOP_PATH,
                max_emails=5,
                offset=2,
                date_from="2026-07-01",
                date_to="2026-07-09",
            )

        script = capture.last_script
        # Bounded page slice: pageStart=offset+1, pageEnd=offset+max_emails; never the full list.
        self.assertIn("set pageStart to 2 + 1", script)
        self.assertIn("set pageEnd to 2 + 5", script)
        self.assertIn("messages pageStart thru pageEnd of targetMailbox", script)
        self.assertNotIn("set mailboxMessages to messages of targetMailbox", script)
        self.assertIn("if messageDate < fromDate then set shouldExport to false", script)
        self.assertIn("if messageDate > toDate then set shouldExport to false", script)

    def test_filtered_export_discovers_ids_then_exports_by_mailbox(self):
        records = [
            {"message_id": "101", "mailbox": "INBOX"},
            {"message_id": "202", "mailbox": "Archive"},
        ]
        capture = _ScriptCapture(return_value="EXPORTING MESSAGES BY ID\n\nExported: 1")
        with (
            patch("apple_mail_mcp.tools.search._search_mail_records_sync", return_value=records) as mock_search,
            patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture),
        ):
            result = analytics_tools.export_emails(
                account="Work",
                scope="filtered",
                sender_exact="person@example.com",
                save_directory=DESKTOP_PATH,
                max_emails=2,
            )

        self.assertIn("FILTERED EXPORT", result)
        mock_search.assert_called_once()
        self.assertEqual(mock_search.call_args.kwargs["sender_exact"], "person@example.com")
        self.assertEqual(len(capture.scripts), 2)
        self.assertIn('mailbox "INBOX" of targetAccount', capture.scripts[0])
        self.assertIn('mailbox "Archive" of targetAccount', capture.scripts[1])

    def test_thread_export_maps_ids_to_openable_mailboxes_not_all_mail(self):
        # Gmail reports thread messages in the virtual "All Mail" container; the
        # export must ignore that name and look ids up in INBOX + Sent instead.
        payload = {
            "items": [
                {"message_id": "101", "mailbox": "All Mail"},
                {"message_id": "202", "mailbox": "All Mail"},
            ]
        }
        capture = _ScriptCapture(return_value="Exported: 2")
        with (
            patch("apple_mail_mcp.tools.search.get_email_thread", return_value=json.dumps(payload)) as mock_thread,
            patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture),
        ):
            result = analytics_tools.export_emails(
                account="Work",
                scope="thread",
                message_id="101",
                save_directory=DESKTOP_PATH,
                max_emails=10,
            )

        self.assertIn("THREAD EXPORT", result)
        self.assertEqual(mock_thread.call_args.kwargs["mailboxes"], ["INBOX", "Sent"])
        # <=50 ids => one bounded multi-mailbox script.
        self.assertEqual(len(capture.scripts), 1)
        script = capture.scripts[0]
        self.assertIn('mailbox "INBOX" of targetAccount', script)
        self.assertIn('mailbox "Sent Mail" of targetAccount', script)
        self.assertNotIn("All Mail", script)
        self.assertIn("whose id is requestedId", script)

    def test_correspondent_export_matches_sender_recipients_and_sent(self):
        capture = _ScriptCapture(return_value="EXPORTING CORRESPONDENT\n\nExported: 2")
        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture):
            result = analytics_tools.export_emails(
                account="Work",
                scope="correspondent",
                email_address="person@example.com",
                save_directory=DESKTOP_PATH,
                max_emails=5,
                offset=1,
                date_from="2026-07-01",
                include_sent=True,
            )

        self.assertIn("Exported: 2", result)
        script = capture.last_script
        self.assertIn("messageHasCorrespondent", script)
        self.assertIn("sender of aMessage contains emailNeedle", script)
        self.assertIn("recipients of aMessage", script)
        self.assertIn("to recipients of aMessage", script)
        self.assertIn("cc recipients of aMessage", script)
        self.assertIn("bcc recipients of aMessage", script)
        self.assertIn("set end of searchMailboxes to sentMailbox", script)
        self.assertIn("correspondent_export", script)
        self.assertIn("if globalMatchedCount > 1 then", script)
        self.assertIn("if totalExportCount >= 5 then exit repeat", script)

    def test_correspondent_export_can_skip_sent_mailbox(self):
        capture = _ScriptCapture(return_value="EXPORTING CORRESPONDENT\n\nExported: 1")
        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture):
            analytics_tools.export_emails(
                account="Work",
                scope="correspondent",
                email_address="person@example.com",
                save_directory=DESKTOP_PATH,
                max_emails=5,
                date_from="2026-07-01",
                include_sent=False,
            )

        self.assertNotIn("set end of searchMailboxes to sentMailbox", capture.last_script)


if __name__ == "__main__":
    unittest.main()
