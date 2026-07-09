"""Tests for the disabled ``full_inbox_export`` tool.

The tool no longer walks the mailbox. It must immediately return a
structured ``UNBOUNDED_EXPORT_DISABLED`` refusal that points callers at
bounded alternatives, and it must never touch AppleScript.
"""

import asyncio
import inspect
import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import analytics as analytics_tools


def _run(coro):
    return asyncio.run(coro)


class FullInboxExportDisabledTests(unittest.TestCase):
    def test_refuses_with_default_args(self):
        with patch("apple_mail_mcp.tools.analytics.run_applescript") as mock_run:
            raw = _run(analytics_tools.full_inbox_export())

        mock_run.assert_not_called()
        payload = json.loads(raw)
        self.assertTrue(payload["error"])
        self.assertEqual(payload["code"], "UNBOUNDED_EXPORT_DISABLED")

    def test_refuses_regardless_of_args_passed(self):
        with patch("apple_mail_mcp.tools.analytics.run_applescript") as mock_run:
            raw = _run(
                analytics_tools.full_inbox_export(
                    account="Work",
                    mailbox="Archive",
                    fields=["subject", "sender"],
                    max_emails=25,
                    batch_size=5,
                    output_format="ndjson",
                    timeout=10,
                    ctx=object(),
                )
            )

        mock_run.assert_not_called()
        payload = json.loads(raw)
        self.assertEqual(payload["code"], "UNBOUNDED_EXPORT_DISABLED")

    def test_message_explains_why_and_mentions_disabled(self):
        raw = _run(analytics_tools.full_inbox_export())
        payload = json.loads(raw)
        message = payload["message"].lower()
        self.assertIn("disabled", message)
        self.assertIn("mail.app cpu", message)

    def test_remediation_points_at_bounded_alternatives(self):
        raw = _run(analytics_tools.full_inbox_export())
        payload = json.loads(raw)
        remediation = payload["remediation"]

        self.assertIn("export_emails", remediation["preferred"])
        self.assertIn("entire_mailbox", remediation["preferred"])
        self.assertIn("list_inbox_emails", remediation["list"])
        self.assertIn("search_emails", remediation["search"])

    def test_never_calls_applescript_runner(self):
        """No Mail.app work happens even though run_applescript is importable."""
        with patch("apple_mail_mcp.tools.analytics.run_applescript") as mock_run:
            _run(analytics_tools.full_inbox_export(account="Work", max_emails=5000))
        mock_run.assert_not_called()

    def test_is_awaitable_coroutine_function(self):
        self.assertTrue(inspect.iscoroutinefunction(analytics_tools.full_inbox_export))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
