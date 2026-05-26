"""Regression tests for manage.py bulk-status-update silent error (Bug 3).

Before the fix, the ID-based update_email_status path tried a bulk
``set read status of every message`` first, then silently fell back to a
per-message loop on error.  The bulk failure was never surfaced to the caller.

Fix: capture ``errMsg`` in the bulk ``on error errMsg number errNum`` clause
and append a ``BULKERR|<message>`` row to ``outputText`` so Python can surface
it to the caller.

These tests:
1. Assert the generated AppleScript contains ``on error errMsg`` (not bare
   ``on error``) in the bulk-action fallback block.
2. Assert that when the Python side receives a ``BULKERR|some message`` row
   in the output, the tool's string response includes the error text.
"""

import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import manage as manage_tools


class _ScriptCapture:
    """Capture script(s) passed to run_applescript."""

    def __init__(self, return_value=""):
        self.scripts: list[str] = []
        self._return_value = return_value

    def __call__(self, script: str, timeout=120):
        self.scripts.append(script)
        if isinstance(self._return_value, list):
            return self._return_value.pop(0) if self._return_value else ""
        return self._return_value

    @property
    def last_script(self) -> str:
        return self.scripts[-1] if self.scripts else ""


class BulkActionScriptShapeTests(unittest.TestCase):
    """Assert the generated AppleScript captures errMsg in the bulk on-error block."""

    def _capture_update_script(self, action: str = "mark_read") -> str:
        capture = _ScriptCapture(
            return_value=(
                "UPDATING EMAIL STATUS BY IDS: Read\n\n"
                "- Read: Some Subject\n"
                "   From: test@example.com\n"
                "   Date: 2024-01-01\n\n"
                "========================================\n"
                "REQUESTED IDS: 1\n"
                "TOTAL UPDATED: 1 email(s)\n"
                "========================================\n"
            )
        )
        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture):
            manage_tools.update_email_status(
                account="Work",
                action=action,
                message_ids=[42],
            )
        return capture.last_script

    def test_bulk_on_error_captures_errmsg(self):
        """The bulk fallback block must use 'on error errMsg' not bare 'on error'."""
        script = self._capture_update_script(action="mark_read")
        # Find the bulk action try block: locate the bulk_action_script pattern
        # then find the on error that follows it.
        bulk_try_pos = script.find("set read status of")
        if bulk_try_pos == -1:
            bulk_try_pos = script.find("set flagged status of targetMessages")
        if bulk_try_pos == -1:
            self.skipTest("Could not locate bulk action in generated script")

        # The on error immediately following the bulk action
        on_error_pos = script.find("on error", bulk_try_pos)
        self.assertGreater(on_error_pos, -1, "Expected 'on error' after bulk action")

        # Read the on error line: it should be "on error errMsg" not bare "on error"
        end_of_line = script.find("\n", on_error_pos)
        on_error_line = script[on_error_pos:end_of_line].strip()
        self.assertIn(
            "errMsg",
            on_error_line,
            f"Bulk fallback 'on error' must capture errMsg; got: {on_error_line!r}",
        )

    def test_bulk_on_error_appends_bulkerr_row(self):
        """The bulk on error block must append a BULKERR| row to outputText."""
        script = self._capture_update_script(action="mark_read")
        bulk_try_pos = script.find("set read status of")
        if bulk_try_pos == -1:
            bulk_try_pos = script.find("set flagged status of targetMessages")
        if bulk_try_pos == -1:
            self.skipTest("Could not locate bulk action in generated script")

        on_error_pos = script.find("on error", bulk_try_pos)
        # Find the end try that closes this block.
        end_try_pos = script.find("end try", on_error_pos)
        error_block = script[on_error_pos:end_try_pos]

        self.assertIn(
            "BULKERR|",
            error_block,
            "Expected 'BULKERR|' row appended to outputText inside bulk on error block; "
            "without it the bulk failure is silently swallowed",
        )

    def test_mark_unread_bulk_on_error_captures_errmsg(self):
        script = self._capture_update_script(action="mark_unread")
        bulk_try_pos = script.find("set read status of")
        if bulk_try_pos == -1:
            self.skipTest("Could not locate bulk action for mark_unread")
        on_error_pos = script.find("on error", bulk_try_pos)
        end_of_line = script.find("\n", on_error_pos)
        on_error_line = script[on_error_pos:end_of_line].strip()
        self.assertIn("errMsg", on_error_line)

    def test_flag_bulk_on_error_captures_errmsg(self):
        script = self._capture_update_script(action="flag")
        bulk_try_pos = script.find("set flagged status of targetMessages to true")
        if bulk_try_pos == -1:
            self.skipTest("Could not locate bulk action for flag")
        on_error_pos = script.find("on error", bulk_try_pos)
        end_of_line = script.find("\n", on_error_pos)
        on_error_line = script[on_error_pos:end_of_line].strip()
        self.assertIn("errMsg", on_error_line)


class BulkActionErrorSurfacingTests(unittest.TestCase):
    """When run_applescript returns a BULKERR| row, the tool must surface it."""

    def test_bulkerr_row_in_output_is_returned_to_caller(self):
        """The tool returns the raw AppleScript output which includes BULKERR| rows."""
        output_with_bulkerr = (
            "UPDATING EMAIL STATUS BY IDS: Read\n\n"
            "BULKERR|bulk set failed: handler not defined\n"
            "- Read: Some Subject\n"
            "   From: test@example.com\n"
            "   Date: 2024-01-01\n\n"
            "========================================\n"
            "REQUESTED IDS: 1\n"
            "TOTAL UPDATED: 1 email(s)\n"
            "========================================\n"
        )

        with patch(
            "apple_mail_mcp.tools.manage.run_applescript",
            return_value=output_with_bulkerr,
        ):
            result = manage_tools.update_email_status(
                account="Work",
                action="mark_read",
                message_ids=[42],
            )

        self.assertIn(
            "BULKERR|",
            result,
            "Tool output must include the BULKERR| row when run_applescript returns one",
        )
        self.assertIn("bulk set failed", result)

    def test_bulkerr_row_with_errnum_is_surfaced(self):
        """BULKERR rows with error number info are also passed through."""
        output_with_bulkerr = (
            "UPDATING EMAIL STATUS BY IDS: Read\n\n"
            "BULKERR|errNum=-1708 errMsg=event not handled\n"
            "========================================\n"
            "REQUESTED IDS: 2\n"
            "TOTAL UPDATED: 0 email(s)\n"
            "========================================\n"
        )

        with patch(
            "apple_mail_mcp.tools.manage.run_applescript",
            return_value=output_with_bulkerr,
        ):
            result = manage_tools.update_email_status(
                account="Work",
                action="mark_unread",
                message_ids=[10, 11],
            )

        self.assertIn("BULKERR|", result)
        self.assertIn("errNum=-1708", result)

    def test_clean_output_has_no_bulkerr(self):
        """Successful run must not include BULKERR| in the response."""
        clean_output = (
            "UPDATING EMAIL STATUS BY IDS: Read\n\n"
            "- Read: Some Subject\n"
            "   From: test@example.com\n"
            "   Date: 2024-01-01\n\n"
            "========================================\n"
            "REQUESTED IDS: 1\n"
            "TOTAL UPDATED: 1 email(s)\n"
            "========================================\n"
        )

        with patch(
            "apple_mail_mcp.tools.manage.run_applescript",
            return_value=clean_output,
        ):
            result = manage_tools.update_email_status(
                account="Work",
                action="mark_read",
                message_ids=[42],
            )

        self.assertNotIn("BULKERR|", result)
