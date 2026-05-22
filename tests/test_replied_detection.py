"""Tests for the Message-ID based replied-detection added in 3.1.8.

Covers three tool surfaces:

- ``smart_inbox.get_needs_response`` — replaces fragile subject-substring
  matching with In-Reply-To / References header parsing.
- ``inbox.list_inbox_emails`` — gains ``exclude_replied`` and
  ``flag_replied`` parameters.
- ``search.search_emails`` — same.

These tests follow the existing pattern of mocking ``run_applescript`` and
asserting on the generated script text plus the rendered output.
"""

import asyncio
import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import inbox as inbox_tools
from apple_mail_mcp.tools import search as search_tools
from apple_mail_mcp.tools import smart_inbox as smart_inbox_tools


def _run(coro):
    if asyncio.iscoroutine(coro):
        return asyncio.run(coro)
    return coro


class GetNeedsResponseScriptTests(unittest.TestCase):
    """Verify get_needs_response now emits Message-ID based replied detection."""

    def test_script_builds_replied_ids_from_sent_headers(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "EMAILS NEEDING RESPONSE"

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run
        ):
            smart_inbox_tools.get_needs_response(
                account="Work", days_back=1, max_results=1
            )

        script = captured["script"]
        # repliedIds variable + In-Reply-To header parsing must be present.
        self.assertIn("set repliedIds to {}", script)
        self.assertIn("In-Reply-To:", script)
        self.assertIn("References:", script)
        # Inbox message-id lookup uses Mail.app's `message id of aMessage`.
        self.assertIn("message id of aMessage", script)
        # Fallback subject list is still built for resilience.
        self.assertIn("sentSubjects", script)

    def test_include_already_replied_false_skips_and_emits_filter_footer(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "ok"

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run
        ):
            smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=1,
                max_results=5,
                include_already_replied=False,
            )

        script = captured["script"]
        self.assertIn("set skippedRepliedCount to 0", script)
        # Skip branch increments the counter and drops the email.
        self.assertIn("skippedRepliedCount + 1", script)
        # Footer message references the toggle.
        self.assertIn("include_already_replied=True", script)
        # Default-false path uses keep=false to skip.
        self.assertIn("if alreadyReplied and not false then", script)

    def test_include_already_replied_true_keeps_and_annotates(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "ok"

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run
        ):
            smart_inbox_tools.get_needs_response(
                account="Work",
                days_back=1,
                max_results=5,
                include_already_replied=True,
            )

        script = captured["script"]
        # When include_already_replied=True, the keep-branch is unconditional.
        self.assertIn("if alreadyReplied and not true then", script)
        # The [ALREADY REPLIED] prefix is templated into the script.
        self.assertIn("[ALREADY REPLIED]", script)


class ListInboxRepliedTests(unittest.TestCase):
    def test_exclude_replied_requests_message_id_in_script(self):
        captured = {"scripts": []}

        def fake_run(script, timeout=120):
            captured["scripts"].append(script)
            return ""

        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run
        ):
            _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=3,
                    output_format="json",
                    exclude_replied=True,
                )
            )

        # First script: inbox JSON. Second: replied-id probe.
        self.assertGreaterEqual(len(captured["scripts"]), 2)
        inbox_script = captured["scripts"][0]
        replied_script = captured["scripts"][1]
        self.assertIn("message id of aMessage", inbox_script)
        # The replied-id probe builds the repliedIds list and parses headers.
        self.assertIn("set repliedIds to {}", replied_script)
        self.assertIn("In-Reply-To:", replied_script)

    def test_exclude_replied_filters_matching_emails_json(self):
        # 1st call: inbox JSON for Work. 2nd call: fetch_replied_ids script
        # that emits one ID per line. 3rd call would not be made.
        inbox_raw = (
            "S1|||a@example.com|||Date|||false|||Work|||<id-1@example.com>\n"
            "S2|||b@example.com|||Date|||false|||Work|||<id-2@example.com>"
        )
        replied_raw = "<id-2@example.com>"
        sequence = [inbox_raw, replied_raw]

        def fake_run(script, timeout=120):
            return sequence.pop(0) if sequence else ""

        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run
        ):
            response = json.loads(
                _run(
                    inbox_tools.list_inbox_emails(
                        account="Work",
                        max_emails=10,
                        output_format="json",
                        exclude_replied=True,
                    )
                )
            )

        subjects = [r["subject"] for r in response]
        self.assertEqual(subjects, ["S1"])

    def test_flag_replied_annotates_json_with_already_replied_field(self):
        inbox_raw = (
            "S1|||a@example.com|||Date|||false|||Work|||<id-1@example.com>\n"
            "S2|||b@example.com|||Date|||false|||Work|||<id-2@example.com>"
        )
        replied_raw = "<id-2@example.com>"
        sequence = [inbox_raw, replied_raw]

        def fake_run(script, timeout=120):
            return sequence.pop(0) if sequence else ""

        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run
        ):
            response = json.loads(
                _run(
                    inbox_tools.list_inbox_emails(
                        account="Work",
                        max_emails=10,
                        output_format="json",
                        exclude_replied=False,
                        flag_replied=True,
                    )
                )
            )

        marked = {r["subject"]: r.get("already_replied", False) for r in response}
        self.assertEqual(marked, {"S1": False, "S2": True})


class SearchEmailsRepliedTests(unittest.TestCase):
    def test_exclude_replied_filters_matching_records(self):
        # Search script returns 8-pipe records; minimal mock.
        search_raw = (
            "1|||<id-1@example.com>|||Sub 1|||a@x.com|||INBOX|||Work|||false|||2026-05-01T00:00:00|||"
            "\n"
            "2|||<id-2@example.com>|||Sub 2|||b@x.com|||INBOX|||Work|||false|||2026-05-02T00:00:00|||"
        )
        replied_raw = "<id-2@example.com>"
        sequence = [search_raw, replied_raw]

        def fake_run(script, timeout=120):
            return sequence.pop(0) if sequence else ""

        with patch(
            "apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run
        ):
            result = _run(
                search_tools.search_emails(
                    account="Work",
                    recent_days=2.0,
                    output_format="json",
                    exclude_replied=True,
                )
            )

        payload = json.loads(result)
        subjects = [item["subject"] for item in payload["items"]]
        self.assertEqual(subjects, ["Sub 1"])

    def test_flag_replied_marks_records_already_replied_in_json(self):
        search_raw = (
            "1|||<id-1@example.com>|||Sub 1|||a@x.com|||INBOX|||Work|||false|||2026-05-01T00:00:00|||"
            "\n"
            "2|||<id-2@example.com>|||Sub 2|||b@x.com|||INBOX|||Work|||false|||2026-05-02T00:00:00|||"
        )
        replied_raw = "<id-2@example.com>"
        sequence = [search_raw, replied_raw]

        def fake_run(script, timeout=120):
            return sequence.pop(0) if sequence else ""

        with patch(
            "apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run
        ):
            result = _run(
                search_tools.search_emails(
                    account="Work",
                    recent_days=2.0,
                    output_format="json",
                    exclude_replied=False,
                    flag_replied=True,
                )
            )

        payload = json.loads(result)
        marked = {item["subject"]: item.get("already_replied", False) for item in payload["items"]}
        self.assertEqual(marked, {"Sub 1": False, "Sub 2": True})

    def test_flag_replied_text_output_includes_replied_marker(self):
        search_raw = (
            "1|||<id-1@example.com>|||Sub 1|||a@x.com|||INBOX|||Work|||false|||2026-05-01T00:00:00|||"
            "\n"
            "2|||<id-2@example.com>|||Sub 2|||b@x.com|||INBOX|||Work|||false|||2026-05-02T00:00:00|||"
        )
        replied_raw = "<id-2@example.com>"
        sequence = [search_raw, replied_raw]

        def fake_run(script, timeout=120):
            return sequence.pop(0) if sequence else ""

        with patch(
            "apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run
        ):
            result = _run(
                search_tools.search_emails(
                    account="Work",
                    recent_days=2.0,
                    output_format="text",
                    exclude_replied=False,
                    flag_replied=True,
                )
            )

        # The text formatter prefixes the subject of replied emails.
        self.assertIn("[REPLIED] Sub 2", result)
        self.assertNotIn("[REPLIED] Sub 1", result)


class CoreRepliedIdsScriptTests(unittest.TestCase):
    """Verify the shared core helper emits well-formed AppleScript."""

    def test_fetch_replied_ids_script_includes_header_parsing(self):
        from apple_mail_mcp.core import fetch_replied_ids_script

        script = fetch_replied_ids_script("Work", sent_cap=50)
        self.assertIn('account "Work"', script)
        self.assertIn("set repliedIds to {}", script)
        self.assertIn("In-Reply-To:", script)
        self.assertIn("References:", script)
        # Cap is templated into the script.
        self.assertIn("sentUpperBound to 50", script)
        # No `whose` clause against the sent mailbox.
        self.assertNotIn("whose", script)

    def test_fetch_replied_ids_normalizes_angle_brackets(self):
        from apple_mail_mcp.core import fetch_replied_ids

        # Three IDs: with brackets, without brackets, empty line — empty
        # should be ignored, the unbracketed should be wrapped.
        raw = "<id-a@example.com>\nid-b@example.com\n\n"

        with patch(
            "apple_mail_mcp.core.run_applescript", return_value=raw
        ):
            ids = fetch_replied_ids("Work", sent_cap=50)

        self.assertEqual(ids, {"<id-a@example.com>", "<id-b@example.com>"})


if __name__ == "__main__":
    unittest.main()
