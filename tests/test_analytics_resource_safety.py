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

import tempfile
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import analytics as analytics_tools


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
    import os
    save_directory = os.path.expanduser("~/Desktop")
    capture = _ScriptCapture(return_value="EXPORTING MAILBOX\n\n✓ Mailbox exported successfully!")
    with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture):
        analytics_tools.export_emails(
            account=account,
            scope="entire_mailbox",
            save_directory=save_directory,
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
        import os
        save_directory = os.path.expanduser("~/Desktop")
        with (
            patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture),
            patch(
                "apple_mail_mcp.tools.analytics._search_mail_records",
                return_value=[{"subject": "Test", "message_id": "42"}],
            ),
        ):
            analytics_tools.export_emails(
                account="Work",
                scope="single_email",
                subject_keyword="Test",
                save_directory=save_directory,
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


class ExportEmailsDefaultsAndWarningTests(unittest.TestCase):
    """Fix #4: entire_mailbox default max_emails=100; warn above 500."""

    def _export(self, **kwargs):
        """Drive export_emails and return (result_text, captured_script)."""
        capture = _ScriptCapture(
            return_value="EXPORTING MAILBOX\n\n✓ Mailbox exported successfully!"
        )
        import os
        defaults = dict(
            account="Work",
            scope="entire_mailbox",
            save_directory=os.path.expanduser("~/Desktop"),
            mailbox="INBOX",
            format="txt",
        )
        defaults.update(kwargs)
        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture):
            result = analytics_tools.export_emails(**defaults)
        return result, capture.last_script

    def test_entire_mailbox_default_max_emails_is_100(self):
        """When max_emails is omitted, entire_mailbox scope uses 100."""
        _result, script = self._export()
        # The generated AppleScript must cap at 100
        self.assertIn("1 thru 100", script)
        self.assertNotIn("1 thru 1000", script)

    def test_entire_mailbox_explicit_max_emails_respected(self):
        """Explicit max_emails is passed through to the generated script."""
        _result, script = self._export(max_emails=50)
        self.assertIn("1 thru 50", script)

    def test_entire_mailbox_no_warning_at_default(self):
        """Default export (max_emails=100) must not emit a performance warning."""
        result, _script = self._export()
        self.assertNotIn("Performance warning", result)

    def test_entire_mailbox_no_warning_at_500(self):
        """max_emails=500 is at the threshold — no warning expected."""
        result, _script = self._export(max_emails=500)
        self.assertNotIn("Performance warning", result)

    def test_entire_mailbox_warning_at_501(self):
        """max_emails=501 exceeds the threshold — warning must be present."""
        result, _script = self._export(max_emails=501)
        self.assertIn("Performance warning", result)
        self.assertIn("full_inbox_export", result)

    def test_entire_mailbox_warning_at_1000(self):
        """Legacy max_emails=1000 exceeds the threshold — warning must be present."""
        result, _script = self._export(max_emails=1000)
        self.assertIn("Performance warning", result)

    def test_entire_mailbox_warning_preserves_export_result(self):
        """When a warning is emitted the actual export result is still present."""
        result, _script = self._export(max_emails=600)
        self.assertIn("✓ Mailbox exported successfully!", result)


if __name__ == "__main__":
    unittest.main()
