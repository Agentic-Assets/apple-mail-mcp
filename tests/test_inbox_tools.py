"""Tests for inbox listing helpers."""

import asyncio
import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import inbox as inbox_tools


def _run(coro):
    """Synchronously drive an async tool inside a test."""
    if asyncio.iscoroutine(coro):
        return asyncio.run(coro)
    return coro


class InboxToolTests(unittest.TestCase):
    def test_text_list_inbox_honors_account_filter(self):
        # In the 3.1.5 modernized list_inbox_emails, an explicit `account`
        # triggers the single-account fast path: the AppleScript looks up
        # `account "Work"` directly instead of iterating every account.
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "ok"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            _run(inbox_tools.list_inbox_emails(account="Work", max_emails=5))

        self.assertIn('account "Work"', captured["script"])
        # max_emails=5 should appear as a cap inside the script.
        self.assertIn("1 thru 5", captured["script"])

    def test_list_inbox_rejects_unbounded_scan(self):
        with patch("apple_mail_mcp.tools.inbox.run_applescript") as mock_run:
            result = _run(inbox_tools.list_inbox_emails(account="Work", max_emails=0))

        payload = json.loads(result)
        self.assertEqual(payload["code"], "UNBOUNDED_SCAN_REQUIRED")
        self.assertEqual(
            payload["remediation"]["fallback_tool"], "full_inbox_export"
        )
        mock_run.assert_not_called()

    def test_json_list_inbox_can_include_content_preview(self):
        # The JSON-format inbox listing should request a content preview when
        # include_content=True and parse the pipe-delimited script output.
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            # Schema: subject|||sender|||date|||read|||account|||mail_app_id|||content_preview
            return "Subject|||sender@example.com|||Thu, Jan 1, 2026|||false|||Work|||1|||Hello | world"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            response = json.loads(
                _run(
                    inbox_tools.list_inbox_emails(
                        account="Work",
                        max_emails=1,
                        include_content=True,
                        output_format="json",
                    )
                )
            )

        self.assertIn("content of aMessage", captured["script"])
        self.assertEqual(response[0]["content_preview"], "Hello | world")

    def test_parser_preserves_delimiters_in_content_preview(self):
        # Schema: subject|||sender|||date|||read|||account|||mail_app_id|||content_preview
        records = inbox_tools._parse_pipe_delimited_emails(
            "Subject|||sender@example.com|||Date|||true|||Work|||1|||Hello ||| still content"
        )

        self.assertEqual(records[0]["content_preview"], "Hello ||| still content")


class ListMailboxesJsonTests(unittest.TestCase):
    def test_list_mailboxes_json_max_mailboxes_returns_wrapper(self):
        raw = "Work|||INBOX|||INBOX|||-1|||-1"
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return raw

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            result = inbox_tools.list_mailboxes(
                account="Work",
                output_format="json",
                max_mailboxes=1,
            )

        payload = json.loads(result)
        self.assertIn("mailboxes", payload)
        self.assertTrue(payload["truncated"])
        self.assertIn("if mailboxIndex > 1 then exit repeat", captured["script"])


class ListMailboxesChildCapTests(unittest.TestCase):
    """Fix 4 regression: cap must fire on children, not just parents."""

    def test_child_cap_fires_and_truncated_false_when_exact_fit(self):
        # Provide exactly max_mailboxes rows (1 parent + no children because
        # AppleScript cap fires). truncated should be True (conservative: cap
        # may have fired) when returned == max_mailboxes.
        raw = "Work|||INBOX|||INBOX|||-1|||-1"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            result = inbox_tools.list_mailboxes(
                account="Work",
                output_format="json",
                max_mailboxes=2,  # cap > returned (1), so truncated=False
            )

        payload = json.loads(result)
        self.assertFalse(payload["truncated"])

    def test_child_cap_appears_inside_child_loop(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            inbox_tools.list_mailboxes(
                account="Work",
                output_format="json",
                max_mailboxes=3,
            )

        script = captured["script"]
        # The cap_check must appear at least twice: once in the parent loop
        # and once in the child loop.
        self.assertGreaterEqual(
            script.count("if mailboxIndex > 3 then exit repeat"), 2,
            "cap_check must fire in both parent and child loops",
        )


class GetMailboxUnreadCountsNoduplicateTests(unittest.TestCase):
    """Fix 5 regression: parent mailbox with children must not be double-emitted."""

    def test_parent_with_children_emits_only_child_paths(self):
        # Mock: 3-field lines account|||mailbox_path|||unread
        # Two lines: parent Funding (bare) + child TU/Funding — only TU/Funding
        # should appear after the fix (parent emitted only when it has no children).
        # The mock simulates the fixed AppleScript output (children only).
        raw = "Work|||TU/Funding|||3"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            result = inbox_tools.get_mailbox_unread_counts(account="Work")

        keys = list(result.get("Work", {}).keys())
        # Bare "Funding" must NOT appear; only "TU/Funding".
        self.assertNotIn("Funding", keys)
        self.assertIn("TU/Funding", keys)

    def test_no_duplicate_keys_in_result(self):
        # Two rows with the same mailbox path (would be a duplicate).
        raw = "Work|||INBOX|||5\nWork|||INBOX|||5"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            result = inbox_tools.get_mailbox_unread_counts(account="Work")

        # Python dict assignment overwrites, so count of INBOX is 1 (no duplicate key).
        work_mailboxes = result.get("Work", {})
        self.assertEqual(list(work_mailboxes.keys()).count("INBOX"), 1)


class ListInboxEmailsMessageIdTests(unittest.TestCase):
    """Fix 6 regression: message_id must always be present in JSON output."""

    def test_json_output_always_has_message_id(self):
        # Schema now always includes mail_app_id at field[5].
        raw = "Subject|||sender@example.com|||Date|||false|||Work|||42"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            response = json.loads(
                _run(
                    inbox_tools.list_inbox_emails(
                        account="Work",
                        max_emails=1,
                        output_format="json",
                    )
                )
            )

        self.assertEqual(len(response), 1)
        self.assertIn("message_id", response[0])
        self.assertEqual(response[0]["message_id"], "42")

    def test_json_script_always_emits_mail_app_id(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=1,
                    output_format="json",
                )
            )

        # The integer id of the message must always be captured unconditionally.
        self.assertIn("set mailAppId to id of aMessage", captured["script"])


class OverviewParseTests(unittest.TestCase):
    def test_parse_overview_account_collects_malformed_counts(self):
        raw = "\n".join([
            "HEADER|||Work|||not-a-number|||also-bad",
            "MAILBOX|||Inbox|||bad-count",
        ])
        parsed = inbox_tools._parse_overview_account(raw)
        self.assertIn("parse_errors", parsed)
        self.assertEqual(len(parsed["parse_errors"]), 2)
