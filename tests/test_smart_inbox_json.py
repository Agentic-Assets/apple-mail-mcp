"""Tests for the ``output_format="json"`` surface on smart_inbox tools.

Phase 2c (3.2 robustness) added a structured-dict output to:

- ``smart_inbox.get_needs_response`` — replaces the inline AppleScript
  nested loop with a flat ``MSG|||...`` emitter + Python-side O(1) set
  matching against the replied-IDs returned by ``fetch_replied_ids``.
- ``smart_inbox.get_awaiting_reply`` — the existing two-script pattern
  now also speaks JSON.

These tests pin the dict shape (not the text formatting), error paths,
and the Python replied-matching join.
"""

import unittest
from unittest.mock import patch

from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import smart_inbox as smart_inbox_tools


class GetNeedsResponseTextOutputTests(unittest.TestCase):
    """Text mode after the refactor still produces the same human-readable shape."""

    def test_text_mode_renders_high_and_normal_sections(self):
        inbox_raw = (
            "MSG|||<flagged-1@example.com>|||URGENT review|||boss@example.com|||2026-05-20|||true|||false\n"
            "MSG|||<question-1@example.com>|||Got a minute?|||alice@example.com|||2026-05-20|||false|||true\n"
            "MSG|||<normal-1@example.com>|||FYI status|||bob@example.com|||2026-05-19|||false|||false"
        )

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript", return_value=inbox_raw
        ):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=2,
                max_results=10,
            )

        self.assertIsInstance(result, str)
        # Flagged + question entries appear before the normal one.
        flagged_idx = result.index("URGENT review")
        question_idx = result.index("Got a minute?")
        normal_idx = result.index("FYI status")
        self.assertLess(flagged_idx, normal_idx)
        self.assertLess(question_idx, normal_idx)
        # Priority labels match the legacy AppleScript format.
        self.assertIn("HIGH (flagged)", result)
        self.assertIn("MEDIUM (contains question)", result)
        self.assertIn("NORMAL", result)
        # Header is still emitted.
        self.assertIn("EMAILS NEEDING RESPONSE", result)
        self.assertIn("Account: Work | Mailbox: INBOX", result)
        self.assertIn("Found 3 email(s) needing response.", result)


class GetNeedsResponseJsonTests(unittest.TestCase):
    """Verify the ``output_format='json'`` dict shape."""

    def test_json_mode_returns_dict_with_expected_keys(self):
        inbox_raw = (
            "MSG|||<flagged-1@example.com>|||URGENT review|||boss@example.com|||2026-05-20|||true|||false\n"
            "MSG|||<question-1@example.com>|||Got a minute?|||alice@example.com|||2026-05-20|||false|||true\n"
            "MSG|||<normal-1@example.com>|||FYI status|||bob@example.com|||2026-05-19|||false|||false"
        )
        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript", return_value=inbox_raw
        ):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=2,
                max_results=10,
                output_format="json",
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["account"], "Work")
        self.assertEqual(result["mailbox"], "INBOX")
        self.assertEqual(result["days_back"], 2)
        self.assertEqual(result["max_results"], 10)
        # high_priority captures flagged + question rows; normal_priority the rest.
        high_subjects = [e["subject"] for e in result["high_priority"]]
        normal_subjects = [e["subject"] for e in result["normal_priority"]]
        self.assertEqual(high_subjects, ["URGENT review", "Got a minute?"])
        self.assertEqual(normal_subjects, ["FYI status"])
        # Per-entry shape.
        for entry in (*result["high_priority"], *result["normal_priority"]):
            self.assertIn("subject", entry)
            self.assertIn("sender", entry)
            self.assertIn("date", entry)
            self.assertIn("priority", entry)
            self.assertIn("already_replied", entry)
            self.assertIn("message_id", entry)
            self.assertFalse(entry["already_replied"])
        self.assertEqual(result["skipped_replied_count"], 0)
        self.assertEqual(result["errors"], [])

    def test_json_mode_marks_already_replied_when_replied_set_matches(self):
        # Two inbox candidates; the second was already replied to.
        inbox_raw = (
            "MSG|||<keep-1@example.com>|||Project sync|||alice@example.com|||2026-05-20|||false|||false\n"
            "MSG|||<replied-1@example.com>|||Old thread|||bob@example.com|||2026-05-19|||false|||false"
        )
        replied_raw = "<replied-1@example.com>"
        sequence = [inbox_raw, replied_raw]

        def fake_run(script, timeout=120):
            return sequence.pop(0) if sequence else ""

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run
        ):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=2,
                max_results=10,
                include_already_replied=True,
                check_already_replied=True,
                output_format="json",
            )

        all_entries = result["normal_priority"] + result["high_priority"]
        marked = {e["subject"]: e["already_replied"] for e in all_entries}
        self.assertEqual(
            marked, {"Project sync": False, "Old thread": True}
        )
        # The already-replied entry gets the [ALREADY REPLIED] prefix on its priority.
        replied_entry = next(e for e in all_entries if e["subject"] == "Old thread")
        self.assertIn("[ALREADY REPLIED]", replied_entry["priority"])

    def test_json_mode_skips_replied_when_include_replied_false(self):
        inbox_raw = (
            "MSG|||<keep-1@example.com>|||Project sync|||alice@example.com|||2026-05-20|||false|||false\n"
            "MSG|||<replied-1@example.com>|||Old thread|||bob@example.com|||2026-05-19|||false|||false"
        )
        replied_raw = "<replied-1@example.com>"
        sequence = [inbox_raw, replied_raw]

        def fake_run(script, timeout=120):
            return sequence.pop(0) if sequence else ""

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run
        ):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=2,
                max_results=10,
                include_already_replied=False,
                check_already_replied=True,
                output_format="json",
            )

        all_entries = result["normal_priority"] + result["high_priority"]
        subjects = [e["subject"] for e in all_entries]
        self.assertEqual(subjects, ["Project sync"])
        self.assertEqual(result["skipped_replied_count"], 1)

    def test_json_mode_timeout_returns_error_dict(self):
        def boom(script, timeout=120):
            raise AppleScriptTimeout("simulated")

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=boom
        ):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=1,
                max_results=5,
                output_format="json",
            )

        self.assertIsInstance(result, dict)
        self.assertIn("error", result)
        self.assertIn("timed out", result["error"])
        self.assertEqual(result["high_priority"], [])
        self.assertEqual(result["normal_priority"], [])
        self.assertEqual(result["errors"], [result["error"]])

    def test_invalid_output_format_returns_error_string(self):
        # Validation runs before any AppleScript dispatch, so no patching needed.
        result = smart_inbox_tools.get_needs_response(
            account="Work",
            days_back=1,
            output_format="yaml",
        )
        self.assertIsInstance(result, str)
        self.assertTrue(result.startswith("Error"))
        self.assertIn("invalid output_format", result)

    def test_json_mode_respects_max_results_cap(self):
        # Five candidates, all normal priority. max_results=2 must trim.
        inbox_raw = "\n".join(
            f"MSG|||<m{i}@example.com>|||Subj {i}|||u{i}@example.com|||2026-05-{20 - i:02d}|||false|||false"
            for i in range(5)
        )
        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript", return_value=inbox_raw
        ):
            result = smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=2,
                max_results=2,
                output_format="json",
            )

        total = len(result["high_priority"]) + len(result["normal_priority"])
        self.assertEqual(total, 2)


class GetAwaitingReplyJsonTests(unittest.TestCase):
    def _two_script_runner(self, inbox_raw: str, sent_raw: str):
        def runner(script, timeout=120):
            return inbox_raw if "inboxMailbox" in script else sent_raw

        return runner

    def test_json_mode_returns_dict_with_expected_shape(self):
        inbox_raw = ""  # No replies received yet.
        sent_raw = (
            "SENT|||101|||<proj-alpha@example.com>|||Project Alpha|||bob@example.com|||2026-05-20\n"
            "SENT|||102|||<need-update@example.com>|||Need update|||alice@example.com|||2026-05-19"
        )

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            side_effect=self._two_script_runner(inbox_raw, sent_raw),
        ):
            result = smart_inbox_tools.get_awaiting_reply(
                account="Work",
                days_back=7,
                max_results=5,
                exclude_noreply=False,
                output_format="json",
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["account"], "Work")
        self.assertEqual(result["days_back"], 7)
        self.assertEqual(result["max_results"], 5)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["awaiting"]), 2)
        first = result["awaiting"][0]
        self.assertIn("subject", first)
        self.assertIn("recipient", first)
        self.assertIn("sent_at", first)
        self.assertIn("message_id", first)
        self.assertIn("mail_app_id", first)
        self.assertEqual(first["subject"], "Project Alpha")
        self.assertEqual(first["recipient"], "bob@example.com")
        self.assertEqual(first["mail_app_id"], "101")
        self.assertEqual(first["message_id"], "<proj-alpha@example.com>")

    def test_json_mode_filters_replied_and_noreply(self):
        # Sent: one to a real recipient (kept), one to no-reply (dropped),
        # one whose inbox has an In-Reply-To referencing it (dropped).
        inbox_raw = "INBOXHDR|||in-reply-to|||<replied-id@example.com>"
        sent_raw = (
            "SENT|||1|||<keep@example.com>|||Real subject|||friend@example.com|||2026-05-20\n"
            "SENT|||2|||<noreply@example.com>|||Notice|||noreply@vendor.com|||2026-05-20\n"
            "SENT|||3|||<replied-id@example.com>|||Got reply|||alice@example.com|||2026-05-19"
        )

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            side_effect=self._two_script_runner(inbox_raw, sent_raw),
        ):
            result = smart_inbox_tools.get_awaiting_reply(
                account="Work",
                days_back=7,
                max_results=10,
                exclude_noreply=True,
                output_format="json",
            )

        subjects = [row["subject"] for row in result["awaiting"]]
        self.assertEqual(subjects, ["Real subject"])

    def test_json_mode_includes_noreply_when_exclude_noreply_false(self):
        inbox_raw = ""
        sent_raw = (
            "SENT|||1|||<keep@example.com>|||Real subject|||friend@example.com|||2026-05-20\n"
            "SENT|||2|||<noreply@example.com>|||Notice|||noreply@vendor.com|||2026-05-20"
        )

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            side_effect=self._two_script_runner(inbox_raw, sent_raw),
        ):
            result = smart_inbox_tools.get_awaiting_reply(
                account="Work",
                days_back=7,
                max_results=10,
                exclude_noreply=False,
                output_format="json",
            )

        subjects = [row["subject"] for row in result["awaiting"]]
        self.assertEqual(subjects, ["Real subject", "Notice"])

    def test_json_mode_timeout_returns_error_dict(self):
        def boom(script, timeout=120):
            raise AppleScriptTimeout("simulated")

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=boom
        ):
            result = smart_inbox_tools.get_awaiting_reply(
                account="Work",
                days_back=7,
                max_results=5,
                output_format="json",
            )

        self.assertIsInstance(result, dict)
        self.assertIn("error", result)
        self.assertIn("timed out", result["error"])
        self.assertEqual(result["awaiting"], [])

    def test_invalid_output_format_returns_error_string(self):
        result = smart_inbox_tools.get_awaiting_reply(
            account="Work", days_back=7, output_format="yaml"
        )
        self.assertIsInstance(result, str)
        self.assertTrue(result.startswith("Error"))
        self.assertIn("invalid output_format", result)


class NeedsResponseRowParsingTests(unittest.TestCase):
    """Pin the ``MSG|||...`` parser against malformed and pipe-bearing input."""

    def test_parser_skips_malformed_lines(self):
        raw = (
            "MSG|||<a@example.com>|||Good|||alice@example.com|||2026-05-20|||false|||false\n"
            "MSG|||not-enough-fields\n"
            "MSG|||<b@example.com>|||Another|||bob@example.com|||2026-05-19|||true|||true"
        )
        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)
        subjects = [r.subject for r in rows]
        self.assertEqual(subjects, ["Good", "Another"])
        self.assertTrue(rows[1].is_flagged)
        self.assertTrue(rows[1].has_question)

    def test_parser_ignores_non_msg_prefixed_lines(self):
        raw = (
            "ERROR|||not for us\n"
            "MSG|||<a@example.com>|||S|||u@example.com|||2026|||false|||false\n"
            "random noise"
        )
        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].subject, "S")


class GetTopSendersJsonTests(unittest.TestCase):
    """JSON output_format on ``get_top_senders``."""

    _SENDER_RAW = (
        "ROW|||alice@example.com\n"
        "ROW|||alice@example.com\n"
        "ROW|||alice@example.com\n"
        "ROW|||bob@example.com\n"
        "ROW|||bob@example.com\n"
        "ROW|||carol@example.com\n"
        "TOTAL|||6\n"
        "MAILBOX_COUNT|||1200"
    )

    def test_invalid_output_format_returns_text_error(self):
        result = smart_inbox_tools.get_top_senders(
            account="Work", days_back=7, output_format="xml"
        )
        self.assertIsInstance(result, str)
        self.assertIn("Error: invalid output_format", result)

    def test_json_mode_returns_dict_with_stable_keys(self):
        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            return_value=self._SENDER_RAW,
        ):
            result = smart_inbox_tools.get_top_senders(
                account="Work",
                days_back=7,
                top_n=5,
                output_format="json",
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["account"], "Work")
        self.assertEqual(result["mailbox"], "INBOX")
        self.assertEqual(result["days_back"], 7)
        self.assertEqual(result["top_n"], 5)
        self.assertFalse(result["group_by_domain"])
        self.assertEqual(result["total_analysed"], 6)
        self.assertEqual(result["mailbox_count"], 1200)
        self.assertEqual(result["unique_senders"], 3)
        self.assertEqual(result["errors"], [])
        keys = {entry["key"]: entry for entry in result["senders"]}
        self.assertEqual(keys["alice@example.com"]["count"], 3)
        self.assertEqual(keys["alice@example.com"]["percent"], 50)
        self.assertEqual(keys["bob@example.com"]["count"], 2)
        self.assertEqual(keys["carol@example.com"]["count"], 1)

    def test_json_mode_top_n_caps_results(self):
        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            return_value=self._SENDER_RAW,
        ):
            result = smart_inbox_tools.get_top_senders(
                account="Work",
                days_back=7,
                top_n=2,
                output_format="json",
            )

        self.assertEqual(len(result["senders"]), 2)
        self.assertEqual(result["senders"][0]["key"], "alice@example.com")
        self.assertEqual(result["senders"][1]["key"], "bob@example.com")

    def test_json_mode_timeout_returns_error_dict(self):
        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            side_effect=AppleScriptTimeout(120),
        ):
            result = smart_inbox_tools.get_top_senders(
                account="Work",
                days_back=7,
                output_format="json",
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["error"], "timeout")
        self.assertEqual(result["senders"], [])
        self.assertTrue(result["errors"])
        self.assertIn("timed out", result["errors"][0])

    def test_json_mode_unbounded_scan_returns_dict(self):
        result = smart_inbox_tools.get_top_senders(
            account="Work",
            days_back=0,
            output_format="json",
        )
        self.assertIsInstance(result, dict)
        self.assertEqual(result["code"], "UNBOUNDED_SCAN_REQUIRED")
        self.assertEqual(result["senders"], [])
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
