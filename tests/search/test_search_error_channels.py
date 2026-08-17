"""Regression tests: a failed search scan must not read as an empty result.

Each tool here already had an error channel that the call site discarded, so
an AppleScript failure came back as a clean "found nothing". Every test has a
mirror-image partner asserting that a genuinely empty result still reports
empty with no spurious error — turning "no mail" into "error" would be a
regression for every quiet mailbox.

All Mail I/O is mocked at ``tools.search.run_applescript``; the subprocess
layer is poisoned so an accidental live ``osascript`` call fails loudly.
"""

import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import search as search_tools
from apple_mail_mcp.tools.search.records import (
    _mailbox_error_texts,
    _parse_search_records,
    _read_failure_row,
    _script_error_message,
)

_ROW = "|||".join(
    [
        "401",
        "<thread@example.com>",
        "Re: Budget Review",
        "sender@example.com",
        "INBOX",
        "Work",
        "false",
        "2026-03-07T10:00:00",
        "",
    ]
)


class _NoLiveMailTestCase(unittest.TestCase):
    """Poison the subprocess layer: no test here may reach a real mailbox."""

    def setUp(self):
        patcher = patch(
            "apple_mail_mcp.core.applescript.subprocess.run",
            side_effect=AssertionError("test attempted a live osascript call"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class ScriptErrorRecognizerTests(_NoLiveMailTestCase):
    """``_script_error_message`` separates a sentinel from ordinary output."""

    def test_recognizes_error_prefix_sentinel(self):
        self.assertEqual(_script_error_message("Error: Can't get mailbox 1"), "Can't get mailbox 1")

    def test_recognizes_pipe_sentinel(self):
        self.assertEqual(_script_error_message("ERROR|||boom"), "boom")

    def test_record_output_is_not_an_error(self):
        self.assertIsNone(_script_error_message(_ROW))

    def test_empty_output_is_not_an_error(self):
        self.assertIsNone(_script_error_message(""))

    def test_parser_still_drops_a_sentinel_line_to_zero_records(self):
        """The premise behind the call-site checks: the parser yields [] here."""
        records, mailbox_errors = _parse_search_records("Error: Can't get mailbox 1")
        self.assertEqual(records, [])
        self.assertEqual(mailbox_errors, [])

    def test_mailbox_error_texts_render_scope_and_message(self):
        self.assertEqual(
            _mailbox_error_texts([{"mailbox": "INBOX", "message": "restricted"}]),
            ["INBOX: restricted"],
        )


class GetEmailThreadErrorChannelTests(_NoLiveMailTestCase):
    """get_email_thread JSON checked neither ``ERROR|||`` nor ``Error:``."""

    def _thread_json(self, raw):
        with patch("apple_mail_mcp.tools.search.run_applescript", return_value=raw):
            result = search_tools.get_email_thread(
                account="Work",
                subject_keyword="Budget Review",
                recent_days=7,
                output_format="json",
                include_draft_state=False,
            )
        return json.loads(result)

    def test_script_error_surfaces_instead_of_empty_thread(self):
        payload = self._thread_json('Error: Can\'t get mailbox "INBOX" of account "Work"')
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["returned"], 0)
        self.assertIn("error", payload)
        self.assertIn("Can't get mailbox", payload["error"])
        self.assertEqual(payload["errors"], [payload["error"]])

    def test_empty_thread_reports_no_error(self):
        payload = self._thread_json("THREAD_STRATEGY|||subject\n")
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["returned"], 0)
        self.assertNotIn("error", payload)
        self.assertNotIn("errors", payload)
        self.assertNotIn("error_details", payload)

    def test_populated_thread_reports_no_error(self):
        payload = self._thread_json(f"THREAD_STRATEGY|||subject\n{_ROW}")
        self.assertEqual(payload["returned"], 1)
        self.assertNotIn("error", payload)
        self.assertNotIn("errors", payload)

    def test_mailbox_errors_are_no_longer_discarded(self):
        raw = "\n".join(
            [
                "THREAD_STRATEGY|||subject",
                _ROW,
                "ERROR_MAILBOX|||Archive|||mailbox not found",
            ]
        )
        payload = self._thread_json(raw)
        self.assertEqual(payload["returned"], 1)
        self.assertEqual(payload["errors"], ["Archive: mailbox not found"])
        self.assertEqual(
            payload["error_details"],
            [{"mailbox": "Archive", "type": "mailbox_error", "message": "mailbox not found"}],
        )

    def test_text_mode_still_returns_the_raw_error_string(self):
        with patch("apple_mail_mcp.tools.search.run_applescript", return_value="Error: boom"):
            result = search_tools.get_email_thread(
                account="Work",
                subject_keyword="Budget Review",
                recent_days=7,
            )
        self.assertEqual(result, "Error: boom")


class GetEmailByIdsAbsentVersusThrewTests(_NoLiveMailTestCase):
    """get_email_by_ids conflated "not in this mailbox" with "read threw"."""

    def _by_ids_json(self, raw, message_ids=("101",)):
        with patch("apple_mail_mcp.tools.search.run_applescript", return_value=raw):
            result = search_tools.get_email_by_ids(
                account="Work",
                message_ids=list(message_ids),
                output_format="json",
                include_draft_state=False,
            )
        return json.loads(result)

    def test_absent_id_is_missing_with_no_error(self):
        """Mirror image: the id simply is not in the mailbox."""
        payload = self._by_ids_json("")
        self.assertEqual(payload["missing_ids"], ["101"])
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["errors"], [])

    def test_failed_read_is_missing_and_reported(self):
        """The id matched but its read threw: still missing, now also an error."""
        payload = self._by_ids_json(
            "ERROR_MAILBOX|||INBOX|||read failed for 1 of 1 matched message(s); results are incomplete"
        )
        self.assertEqual(payload["missing_ids"], ["101"])
        self.assertEqual(payload["items"], [])
        self.assertEqual(len(payload["errors"]), 1)
        self.assertIn("read failed for 1 of 1", payload["errors"][0])
        self.assertTrue(payload["errors"][0].startswith("INBOX: "))

    def test_partial_read_keeps_records_and_reports_the_loss(self):
        raw = "\n".join(
            [
                _ROW,
                "ERROR_MAILBOX|||INBOX|||read failed for 1 of 2 matched message(s); results are incomplete",
            ]
        )
        payload = self._by_ids_json(raw, message_ids=("401", "402"))
        self.assertEqual([item["message_id"] for item in payload["items"]], ["401"])
        self.assertEqual(payload["missing_ids"], ["402"])
        self.assertEqual(len(payload["errors"]), 1)

    def test_successful_read_reports_no_error(self):
        payload = self._by_ids_json(_ROW, message_ids=("401",))
        self.assertEqual(payload["missing_ids"], [])
        self.assertEqual(payload["errors"], [])

    def test_text_mode_reports_the_partial_read(self):
        with patch(
            "apple_mail_mcp.tools.search.run_applescript",
            return_value="ERROR_MAILBOX|||INBOX|||read failed for 1 of 1 matched message(s)",
        ):
            result = search_tools.get_email_by_ids(
                account="Work",
                message_ids=["101"],
                output_format="text",
                include_draft_state=False,
            )
        self.assertIn("PARTIAL: INBOX: read failed for 1 of 1", result)
        self.assertIn("Missing message_ids: 101", result)


class ReadFailureRowScriptTests(_NoLiveMailTestCase):
    """The producer side of the absent-versus-threw distinction."""

    def test_fragment_compares_emitted_rows_against_matched_messages(self):
        fragment = _read_failure_row("INBOX")
        self.assertIn("set matchedCount to count of targetMessages", fragment)
        self.assertIn("if (count of recordLines) < matchedCount then", fragment)
        self.assertIn('"ERROR_MAILBOX|||INBOX|||read failed for "', fragment)

    def test_fragment_escapes_the_mailbox_name(self):
        self.assertIn('ERROR_MAILBOX|||Weird\\"Name', _read_failure_row('Weird"Name'))

    def test_by_ids_script_emits_the_report(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            search_tools.get_email_by_ids(
                account="Work",
                message_ids=["101"],
                output_format="json",
                include_draft_state=False,
            )

        self.assertEqual(len(captured), 1)
        self.assertIn("set matchedCount to count of targetMessages", captured[0])
        self.assertIn("ERROR_MAILBOX|||INBOX|||read failed for ", captured[0])

    def test_report_row_round_trips_through_the_parser(self):
        records, mailbox_errors = _parse_search_records(
            "ERROR_MAILBOX|||INBOX|||read failed for 1 of 3 matched message(s); results are incomplete"
        )
        self.assertEqual(records, [])
        self.assertEqual(mailbox_errors[0]["mailbox"], "INBOX")
        self.assertIn("read failed for 1 of 3", mailbox_errors[0]["message"])


if __name__ == "__main__":
    unittest.main()
