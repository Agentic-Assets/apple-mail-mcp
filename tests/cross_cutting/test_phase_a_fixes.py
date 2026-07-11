"""Tests for Phase 0/A manifest sync and live-performance fixes."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import analytics as analytics_tools
from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools import inbox as inbox_tools
from apple_mail_mcp.tools import manage as manage_tools
from apple_mail_mcp.tools import search as search_tools
from apple_mail_mcp.tools import smart_inbox as smart_inbox_tools


def _run(coro):
    if asyncio.iscoroutine(coro):
        return asyncio.run(coro)
    return coro


class AccountValidationTests(unittest.TestCase):
    def test_validate_account_name_detects_unknown(self):
        with patch(
            "apple_mail_mcp.core.list_mail_account_names",
            return_value=["Work", "Gmail"],
        ):
            from apple_mail_mcp.core import validate_account_name

            self.assertIsNone(validate_account_name("Work"))
            err = validate_account_name("Missing")
            self.assertIn("account_not_found", err)

    def test_list_inbox_unknown_account_returns_fast_error(self):
        # v3.2.x: account_not_found in JSON mode now returns a dict directly
        # (parity with the success-path {emails, errors} shape).
        with patch(
            "apple_mail_mcp.tools.inbox.validate_account_name",
            return_value="Error: account_not_found — 'Missing' is not configured in Mail. Available accounts: Work",
        ):
            payload = _run(
                inbox_tools.list_inbox_emails(
                    account="Missing",
                    max_emails=5,
                    output_format="json",
                )
            )

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["error"], "account_not_found")
        self.assertEqual(payload["account"], "Missing")

    def test_move_email_unknown_account_returns_fast_error(self):
        with patch(
            "apple_mail_mcp.tools.manage.validate_account_name",
            return_value="Error: account_not_found — 'Missing' is not configured in Mail. Available accounts: Work",
        ):
            result = manage_tools.move_email(
                account="Missing",
                to_mailbox="Archive",
                message_ids=["42"],
            )

        self.assertIn("account_not_found", result)
        self.assertIn("Missing", result)

    def test_get_email_by_id_unknown_account_json_error(self):
        with (
            patch(
                "apple_mail_mcp.tools.search.validate_account_name",
                return_value="Error: account_not_found — 'Missing' is not configured in Mail. Available accounts: Work",
            ),
            patch(
                "apple_mail_mcp.tools.search.account_not_found_json",
                return_value='{"error":"account_not_found","account":"Missing","available_accounts":["Work"],"emails":[]}',
            ),
        ):
            result = search_tools.get_email_by_id(
                account="Missing",
                message_id="12345",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["error"], "account_not_found")

    def test_synchronize_unknown_account_returns_error(self):
        with patch(
            "apple_mail_mcp.tools.manage.validate_account_name",
            return_value="Error: account_not_found — 'Missing' is not configured in Mail. Available accounts: Work",
        ):
            result = manage_tools.synchronize_account(account="Missing")

        self.assertIn("account_not_found", result)

    def test_synchronize_known_account_requires_explicit_confirm(self):
        with patch(
            "apple_mail_mcp.tools.manage.validate_account_name",
            return_value=None,
        ):
            result = manage_tools.synchronize_account(account="Work")

        self.assertIn("requires confirm_sync=True", result)

    def test_get_top_senders_unknown_account_returns_fast_error(self):
        with patch(
            "apple_mail_mcp.tools.smart_inbox.validate_account_name",
            return_value="Error: account_not_found — 'Missing' is not configured in Mail. Available accounts: Work",
        ):
            result = smart_inbox_tools.get_top_senders(account="Missing")

        self.assertIn("account_not_found", result)
        self.assertIn("Missing", result)

    def test_list_mailboxes_unknown_account_json_error(self):
        with patch(
            "apple_mail_mcp.tools.inbox.validate_account_name",
            return_value="Error: account_not_found — 'Missing' is not configured in Mail. Available accounts: Work",
        ):
            result = inbox_tools.list_mailboxes(
                account="Missing",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["error"], "account_not_found")
        self.assertEqual(payload["account"], "Missing")

    def test_dashboard_recent_script_skips_content_by_default(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with (
            patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run),
            patch(
                "apple_mail_mcp.tools.analytics.list_mail_account_names",
                return_value=["Work"],
            ),
        ):
            analytics_tools._get_recent_emails_structured(
                max_total=5,
                max_per_account=3,
                include_preview=False,
            )

        self.assertIn("messages 1 thru 3", captured["script"])
        self.assertIn("id of aMessage", captured["script"])
        self.assertIn("message id of aMessage", captured["script"])
        self.assertNotIn("content of aMessage", captured["script"])

    def test_dashboard_recent_script_includes_content_when_requested(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        with (
            patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run),
            patch(
                "apple_mail_mcp.tools.analytics.list_mail_account_names",
                return_value=["Work"],
            ),
        ):
            analytics_tools._get_recent_emails_structured(
                max_total=5,
                max_per_account=3,
                include_preview=True,
            )

        self.assertIn("content of aMessage", captured["script"])

    def test_dashboard_recent_parser_includes_exact_ids(self):
        parsed = analytics_tools._parse_recent_email_lines(
            "Subject|||Sender|||Mon Jun 29 12:00:00 2026|||false|||Work|||INBOX|||123|||<abc@example.com>|||true|||Preview"
        )

        self.assertEqual(
            parsed,
            [
                {
                    "subject": "Subject",
                    "sender": "Sender",
                    "date": "Mon Jun 29 12:00:00 2026",
                    "is_read": False,
                    "account": "Work",
                    "mailbox": "INBOX",
                    "message_id": "123",
                    "internet_message_id": "<abc@example.com>",
                    "was_replied_to": True,
                    "preview": "Preview",
                }
            ],
        )

    def test_dashboard_recent_parser_preserves_legacy_preview_shape(self):
        parsed = analytics_tools._parse_recent_email_lines(
            "Subject|||Sender|||Mon Jun 29 12:00:00 2026|||true|||Work|||Preview"
        )

        self.assertEqual(parsed[0]["mailbox"], "INBOX")
        self.assertEqual(parsed[0]["message_id"], "")
        self.assertEqual(parsed[0]["internet_message_id"], "")
        self.assertEqual(parsed[0]["preview"], "Preview")

    def test_get_inbox_overview_compact_omits_suggestions(self):
        accounts = [
            {
                "account": "Work",
                "unread": 2,
                "total": 10,
                "mailboxes": [],
                "recent": [],
            }
        ]
        text = inbox_tools._format_overview(
            accounts,
            [],
            include_suggestions=False,
            compact=True,
        )
        self.assertIn("Work: 2 unread", text)
        self.assertNotIn("SUGGESTED ACTIONS", text)

    def test_get_inbox_overview_json_shape(self):
        accounts = [
            {
                "account": "Work",
                "unread": 1,
                "total": 5,
                "mailboxes": [("INBOX", 1)],
                "recent": [{"subject": "Hi", "sender": "a@b.com", "date": "Today", "is_read": False}],
            }
        ]
        payload = inbox_tools._format_overview_json(
            accounts,
            [],
            account="Work",
            include_mailboxes=True,
            include_recent=True,
            include_suggestions=True,
        )
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["total_unread"], 1)
        self.assertEqual(payload["account"], "Work")
        self.assertEqual(payload["accounts"][0]["account"], "Work")
        self.assertEqual(payload["accounts"][0]["recent"][0]["subject"], "Hi")
        self.assertTrue(payload["suggestions"])
        self.assertEqual(payload["errors"], [])


class ErrorPrefixTests(unittest.TestCase):
    """Plain-string tool errors must use the ``Error:`` prefix."""

    def test_compose_resolve_account_missing_default_uses_error_prefix(self):
        with patch("apple_mail_mcp.tools.compose._server") as mock_server:
            mock_server.DEFAULT_MAIL_ACCOUNT = None
            account, err = compose_tools._resolve_account(None)
        self.assertIsNone(account)
        self.assertIsNotNone(err)
        self.assertTrue(err.startswith("Error: "))
        self.assertIn("No account specified", err)

    def test_get_email_by_id_timeout_uses_error_prefix(self):
        with (
            patch(
                "apple_mail_mcp.tools.search.validate_account_name",
                return_value=None,
            ),
            patch(
                "apple_mail_mcp.tools.search.run_applescript",
                side_effect=AppleScriptTimeout("simulated"),
            ),
        ):
            result = search_tools.get_email_by_id(
                account="Work",
                message_id="12345",
                output_format="text",
            )
        self.assertTrue(result.startswith("Error: "))
        self.assertIn("timed out", result.lower())

    def test_get_email_by_id_not_found_uses_error_prefix(self):
        with (
            patch(
                "apple_mail_mcp.tools.search.validate_account_name",
                return_value=None,
            ),
            patch(
                "apple_mail_mcp.tools.search.run_applescript",
                return_value="",
            ),
        ):
            result = search_tools.get_email_by_id(
                account="Work",
                message_id="99999",
                output_format="text",
            )
        self.assertTrue(result.startswith("Error: "))
        self.assertIn("No email found", result)


class ValidateSavePathTests(unittest.TestCase):
    @staticmethod
    def _expanduser_factory(home: str):
        resolved_home = Path(home).resolve()

        def fake_expanduser(path: str) -> str:
            if path == "~":
                return str(resolved_home)
            if path.startswith("~/"):
                return str(resolved_home / path[2:])
            return path

        return fake_expanduser

    def test_validate_save_path_accepts_home_subdirectory(self):
        with tempfile.TemporaryDirectory() as home:
            target = Path(home) / "Desktop"
            target.mkdir()
            with patch(
                "apple_mail_mcp.core.os.path.expanduser",
                side_effect=self._expanduser_factory(home),
            ):
                from apple_mail_mcp.core import validate_save_path

                self.assertIsNone(validate_save_path(str(target)))

    def test_validate_save_path_rejects_outside_home(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as outside,
            patch(
                "apple_mail_mcp.core.os.path.expanduser",
                side_effect=self._expanduser_factory(home),
            ),
        ):
            from apple_mail_mcp.core import validate_save_path

            err = validate_save_path(outside)
            self.assertIsNotNone(err)
            self.assertIn("home directory", err)

    def test_validate_save_path_rejects_sensitive_directory(self):
        with tempfile.TemporaryDirectory() as home:
            ssh_dir = Path(home) / ".ssh"
            ssh_dir.mkdir()
            target = ssh_dir / "id_rsa"
            with patch(
                "apple_mail_mcp.core.os.path.expanduser",
                side_effect=self._expanduser_factory(home),
            ):
                from apple_mail_mcp.core import validate_save_path

                err = validate_save_path(str(target))
                self.assertIsNotNone(err)
                self.assertIn("sensitive directory", err)
                self.assertIn(".ssh", err)


if __name__ == "__main__":
    unittest.main()
