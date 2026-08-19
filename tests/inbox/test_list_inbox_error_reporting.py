"""Regressions for ``list_inbox_emails`` failure reporting (AGENTIC-2363 #6).

The JSON inbox script wrapped its whole body in a bare ``try ... end try``.
A top-level failure (missing account, unreachable mailbox, AppleScript error)
left ``resultLines`` empty, so the script returned ``""``, the parser produced
``[]``, and the response carried ``errors: []`` — a calling agent read a failed
probe as "inbox empty, triage complete" and stopped. The text builder reported
the identical failure inline ("⚠ Error accessing inbox for account ..."), so the
truth existed in one output format and not the other.

These tests pin three things: the JSON path now reports the failure, a genuinely
empty inbox still reports no error, and neither output format can lose its
top-level error handler without a test going red.

``apple_mail_mcp.tools.inbox.run_applescript`` is the only Mail seam used, and
``subprocess.run`` is poisoned so an accidental live ``osascript`` call fails
loudly instead of touching Mail.app.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import inbox as inbox_tools
from apple_mail_mcp.tools.inbox.list_scripts import (
    _build_list_inbox_json_script,
    _build_list_inbox_text_script,
)
from apple_mail_mcp.tools.inbox.parsing import _parse_inbox_error_lines, _parse_pipe_delimited_emails

# Shape of a top-level AppleScript failure, reduced to what triggers the bug.
_MAIL_ERROR_DETAIL = 'Mail got an error: Can\'t get mailbox "INBOX" of account "Work".'
_JSON_FAILURE_OUTPUT = f"__APPLE_MAIL_MCP_ERROR__|||Work|||{_MAIL_ERROR_DETAIL}"
_TEXT_FAILURE_OUTPUT = f"⚠ Error accessing inbox for account Work\n   {_MAIL_ERROR_DETAIL}\n\n"
_GOOD_ROW = "Quarterly update|||sender@example.com|||Thu, Jan 1, 2026|||false|||Work|||42|||false"


def _run(coro):
    return asyncio.run(coro)


class _NoLiveSubprocess:
    """Poison ``subprocess.run`` so an unmocked Mail call fails loudly."""

    def __enter__(self):
        self._patch = patch(
            "subprocess.run",
            side_effect=AssertionError("test attempted a live osascript call; patch the run_applescript seam"),
        )
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False


class ListInboxJsonFailureReportingTests(unittest.TestCase):
    def test_json_swallowed_failure_is_reported_not_returned_as_empty_inbox(self):
        """A failed probe must surface in `errors`, never as a clean empty list."""
        with (
            _NoLiveSubprocess(),
            patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=_JSON_FAILURE_OUTPUT),
        ):
            response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=5,
                    output_format="json",
                    include_draft_state=False,
                )
            )

        self.assertEqual(response["emails"], [])
        self.assertEqual(response["errors"], [f"Work: {_MAIL_ERROR_DETAIL}"])

    def test_genuinely_empty_inbox_reports_no_spurious_error(self):
        """A quiet mailbox must stay a clean empty result: no invented error."""
        with (
            _NoLiveSubprocess(),
            patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=""),
        ):
            response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=5,
                    output_format="json",
                    include_draft_state=False,
                )
            )

        self.assertEqual(response["emails"], [])
        self.assertEqual(response["errors"], [])

    def test_partial_failure_keeps_the_rows_it_did_read(self):
        """An error marker alongside good rows must not discard the rows."""
        raw = f"{_GOOD_ROW}\n{_JSON_FAILURE_OUTPUT}"
        with (
            _NoLiveSubprocess(),
            patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw),
        ):
            response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=5,
                    output_format="json",
                    include_draft_state=False,
                )
            )

        self.assertEqual([row["message_id"] for row in response["emails"]], ["42"])
        self.assertEqual(len(response["errors"]), 1)

    def test_multi_account_failure_is_attributed_to_its_own_account(self):
        """One failing account must not silence or contaminate the healthy one."""

        def runner(script, timeout=None):
            if 'account "Broken"' in script:
                return f"__APPLE_MAIL_MCP_ERROR__|||Broken|||{_MAIL_ERROR_DETAIL}"
            return _GOOD_ROW

        with (
            _NoLiveSubprocess(),
            patch("apple_mail_mcp.tools.inbox._list_mail_accounts", return_value=["Work", "Broken"]),
            patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=runner),
        ):
            response = _run(
                inbox_tools._list_inbox_emails_json(
                    None,
                    5,
                    "all",
                    False,
                    None,
                    include_draft_state=False,
                )
            )

        self.assertEqual([row["message_id"] for row in response["emails"]], ["42"])
        self.assertEqual(response["errors"], [f"Broken: {_MAIL_ERROR_DETAIL}"])


class ListInboxTextJsonParityTests(unittest.TestCase):
    def test_both_builders_emit_a_top_level_error_handler(self):
        """Neither output format may drop its top-level `on error` arm."""
        text_script = _build_list_inbox_text_script("Work", 5, "all", False, False)
        json_script = _build_list_inbox_json_script("Work", 5, "all", False, False)

        self.assertIn("on error errMsg", text_script)
        self.assertIn("⚠ Error accessing inbox for account Work", text_script)

        self.assertIn("on error errMsg", json_script)
        self.assertIn('set end of resultLines to "__APPLE_MAIL_MCP_ERROR__|||Work|||" & errorDetail', json_script)
        # The detail is untrusted text joined into a `|||` row; it must be
        # sanitized or it can shift a later row's fields.
        self.assertIn("set _amm_parts to text items of errorDetail", json_script)

    def test_text_and_json_paths_report_the_same_failure(self):
        """Same underlying failure, same verdict in both output formats."""
        with (
            _NoLiveSubprocess(),
            patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=_TEXT_FAILURE_OUTPUT),
        ):
            text_response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=5,
                    include_draft_state=False,
                )
            )
        with (
            _NoLiveSubprocess(),
            patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=_JSON_FAILURE_OUTPUT),
        ):
            json_response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=5,
                    output_format="json",
                    include_draft_state=False,
                )
            )

        self.assertIn("Error accessing inbox", text_response)
        self.assertTrue(json_response["errors"], "JSON mode must report what text mode reports")

    def test_text_and_json_paths_agree_that_an_empty_inbox_is_clean(self):
        """The parity must hold in the negative direction too."""
        with (
            _NoLiveSubprocess(),
            patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=""),
        ):
            text_response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=5,
                    include_draft_state=False,
                )
            )
        with (
            _NoLiveSubprocess(),
            patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=""),
        ):
            json_response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=5,
                    output_format="json",
                    include_draft_state=False,
                )
            )

        self.assertNotIn("Error accessing inbox", text_response)
        self.assertEqual(json_response["errors"], [])


class ParseInboxErrorLinesTests(unittest.TestCase):
    def test_no_marker_yields_no_errors(self):
        self.assertEqual(_parse_inbox_error_lines(""), [])
        self.assertEqual(_parse_inbox_error_lines(_GOOD_ROW), [])

    def test_marker_is_rendered_as_account_colon_detail(self):
        self.assertEqual(
            _parse_inbox_error_lines(_JSON_FAILURE_OUTPUT),
            [f"Work: {_MAIL_ERROR_DETAIL}"],
        )

    def test_multiple_markers_are_all_collected(self):
        raw = "__APPLE_MAIL_MCP_ERROR__|||Work|||first\n__APPLE_MAIL_MCP_ERROR__|||Other|||second"
        self.assertEqual(_parse_inbox_error_lines(raw), ["Work: first", "Other: second"])

    def test_truncated_marker_still_reports_a_failure(self):
        """Dropping a malformed marker would restore the silent-zero bug."""
        self.assertEqual(
            _parse_inbox_error_lines("__APPLE_MAIL_MCP_ERROR__|||Work"),
            ["Work: unknown error"],
        )
        self.assertEqual(
            _parse_inbox_error_lines("__APPLE_MAIL_MCP_ERROR__|||"),
            ["unknown account: unknown error"],
        )

    def test_error_marker_is_never_parsed_as_an_email_row(self):
        """An error line must not land a non-id value in the message_id slot."""
        rows = _parse_pipe_delimited_emails(f"{_JSON_FAILURE_OUTPUT}\n{_GOOD_ROW}")
        self.assertEqual([row["message_id"] for row in rows], ["42"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
