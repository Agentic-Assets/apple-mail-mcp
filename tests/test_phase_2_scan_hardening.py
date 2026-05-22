"""Phase 2 scan-path hardening: compose caps, timeouts."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools import inbox as inbox_tools
from apple_mail_mcp.tools import manage as manage_tools
from apple_mail_mcp.tools import search as search_tools
from apple_mail_mcp.tools import smart_inbox as smart_inbox_tools
from apple_mail_mcp.tools import analytics as analytics_tools


def _make_subprocess_result(returncode=0, stdout=b"ok", stderr=b""):
    from unittest.mock import MagicMock

    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


class ComposeScanCapTests(unittest.TestCase):
    def test_manage_drafts_list_caps_draft_enumeration(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 0 draft(s)"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            compose_tools.manage_drafts(account="Work", action="list")

        self.assertEqual(len(captured), 1)
        self.assertIn("messages 1 thru 100", captured[0])
        self.assertNotIn("every message of draftsMailbox", captured[0])

    def test_reply_to_email_subject_lookup_uses_whose_and_cap(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                subject_keyword="Invoice",
                reply_body="Thanks",
            )

        # Bounded-slice-then-filter: bind a capped newest-first window
        # FIRST (messages 1 thru 100), THEN apply the in-memory whose
        # filter against `candidateMessages`.
        self.assertIn("messages 1 thru 100 of inboxMailbox", captured[0])
        self.assertIn(
            "candidateMessages whose subject contains \"Invoice\"",
            captured[0],
        )
        self.assertIn("date received >= recentCutoffDate", captured[0])

    def test_reply_to_email_message_id_skips_subject_scan(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Thanks",
            )

        self.assertIn("whose id is 12345", captured[0])
        self.assertNotIn("messages 1 thru 100 of inboxMailbox", captured[0])

    def test_forward_email_subject_lookup_uses_whose_and_cap(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "ok"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            compose_tools.forward_email(
                account="Work",
                subject_keyword="Invoice",
                to="other@example.com",
            )

        self.assertIn("messages 1 thru 100 of targetMailbox", captured[0])
        self.assertIn(
            "candidateMessages whose subject contains \"Invoice\"",
            captured[0],
        )
        self.assertIn("date received >= recentCutoffDate", captured[0])

    def test_forward_email_forwards_timeout_to_run_applescript(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["timeout"] = timeout
            return "ok"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            compose_tools.forward_email(
                account="Work",
                subject_keyword="Invoice",
                to="other@example.com",
                timeout=240,
            )

        self.assertEqual(captured["timeout"], 240)


class MessageIdsTests(unittest.TestCase):
    def test_move_email_with_message_ids_uses_exact_id_condition(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "moved"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                message_ids=["101", "202"],
                dry_run=True,
            )

        self.assertEqual(result, "moved")
        self.assertIn("id is 101", captured["script"])
        self.assertIn("id is 202", captured["script"])
        self.assertIn("DRY RUN - PREVIEW MOVE BY IDS", captured["script"])
        self.assertNotIn("move aMessage to destMailbox", captured["script"])

    def test_manage_trash_with_message_ids_uses_exact_id_condition(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "trashed"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.manage_trash(
                account="Work",
                action="move_to_trash",
                message_ids=["555"],
                dry_run=False,
            )

        self.assertEqual(result, "trashed")
        self.assertIn("id is 555", captured["script"])
        self.assertIn("MOVING EMAILS TO TRASH BY IDS", captured["script"])
        self.assertIn("move aMessage to trashMailbox", captured["script"])

    def test_manage_trash_permanent_delete_with_message_ids_dry_run_skips_delete(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "preview"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                message_ids=["555"],
            )

        self.assertEqual(result, "preview")
        self.assertIn("id is 555", captured["script"])
        self.assertIn("DRY RUN - PREVIEW PERMANENT DELETE BY IDS", captured["script"])
        self.assertIn("Would permanently delete", captured["script"])
        self.assertNotIn("delete aMessage", captured["script"])

    def test_save_email_attachment_with_message_ids_uses_exact_id_condition(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "saved"

        home = __import__("os").path.expanduser("~")
        save_path = f"{home}/Downloads/test-file.bin"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            manage_tools.save_email_attachment(
                account="Work",
                attachment_name="file.bin",
                save_path=save_path,
                message_ids=["777"],
            )

        self.assertIn("id is 777", captured["script"])
        self.assertNotIn("subject contains", captured["script"])


class TimeoutForwardingTests(unittest.TestCase):
    def test_get_email_by_id_forwards_timeout(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["timeout"] = timeout
            return ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            search_tools.get_email_by_id(
                account="Work",
                message_id="99",
                timeout=180,
            )

        self.assertEqual(captured["timeout"], 180)

    def test_get_email_by_id_handles_timeout(self):
        with patch(
            "apple_mail_mcp.tools.search.run_applescript",
            side_effect=AppleScriptTimeout("slow"),
        ):
            result = search_tools.get_email_by_id(
                account="Work",
                message_id="99",
            )

        self.assertIn("timed out", result.lower())

    def test_save_email_attachment_forwards_timeout(self):
        captured = {}

        def fake_search(**kwargs):
            captured["search_timeout"] = kwargs["timeout"]
            return [{"message_id": "777"}]

        def fake_run(script, timeout=120):
            captured["run_timeout"] = timeout
            return "saved"

        home = __import__("os").path.expanduser("~")
        save_path = f"{home}/Downloads/test-file.bin"

        with (
            patch("apple_mail_mcp.tools.manage._search_mail_records", side_effect=fake_search),
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run),
        ):
            manage_tools.save_email_attachment(
                account="Work",
                subject_keyword="Invoice",
                attachment_name="file.bin",
                save_path=save_path,
                timeout=90,
            )

        self.assertEqual(captured["search_timeout"], 90)
        self.assertEqual(captured["run_timeout"], 90)

    def test_save_email_attachment_handles_timeout(self):
        home = __import__("os").path.expanduser("~")
        save_path = f"{home}/Downloads/test-file.bin"

        with patch(
            "apple_mail_mcp.tools.manage._search_mail_records",
            side_effect=AppleScriptTimeout("slow"),
        ):
            result = manage_tools.save_email_attachment(
                account="Work",
                subject_keyword="Invoice",
                attachment_name="file.bin",
                save_path=save_path,
            )

        self.assertIn("timed out", result.lower())

    def test_get_mailbox_unread_counts_forwards_timeout(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["timeout"] = timeout
            return "Work:3"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            inbox_tools.get_mailbox_unread_counts(summary_only=True, timeout=60)

        self.assertEqual(captured["timeout"], 60)

    def test_get_mailbox_unread_counts_summary_only_scopes_to_account(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "Work:3"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            result = inbox_tools.get_mailbox_unread_counts(
                account="Work",
                summary_only=True,
            )

        self.assertEqual(result, {"Work": 3})
        self.assertIn('if accountName is not "Work" then', captured["script"])
        self.assertIn("set shouldIncludeAccount to false", captured["script"])
        self.assertIn("if shouldIncludeAccount then", captured["script"])

    def test_get_mailbox_unread_counts_handles_timeout(self):
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=AppleScriptTimeout("slow"),
        ):
            result = inbox_tools.get_mailbox_unread_counts(summary_only=True)

        self.assertEqual(result.get("error"), "timed_out")


class DashboardAccountScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_inbox_dashboard_passes_selected_account_to_unread_and_recent(self):
        captured = {}

        def fake_unread(**kwargs):
            captured["unread"] = kwargs
            return {"Work": 4}

        async def fake_recent(**kwargs):
            captured["recent"] = kwargs
            return []

        def fake_ui(**kwargs):
            captured["ui"] = kwargs
            return {"ok": True}

        fake_ui_module = SimpleNamespace(create_inbox_dashboard_ui=fake_ui)

        with (
            patch("apple_mail_mcp.UI_AVAILABLE", True),
            patch.dict(sys.modules, {"ui": fake_ui_module}),
            patch(
                "apple_mail_mcp.tools.inbox.get_mailbox_unread_counts",
                side_effect=fake_unread,
            ),
            patch(
                "apple_mail_mcp.tools.analytics._get_recent_emails_structured_async",
                side_effect=fake_recent,
            ),
        ):
            result = await analytics_tools.inbox_dashboard(
                account="Work",
                max_total=5,
                max_per_account=3,
                timeout=45,
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured["unread"]["account"], "Work")
        self.assertTrue(captured["unread"]["summary_only"])
        self.assertEqual(captured["unread"]["timeout"], 45)
        self.assertEqual(captured["recent"]["account"], "Work")
        self.assertEqual(captured["recent"]["max_total"], 5)
        self.assertEqual(captured["recent"]["max_per_account"], 3)
        self.assertEqual(captured["recent"]["timeout"], 45)

    async def test_inbox_dashboard_json_does_not_require_ui_package(self):
        async def fake_recent(**kwargs):
            return [{"subject": "Hello"}]

        with (
            patch("apple_mail_mcp.UI_AVAILABLE", False),
            patch(
                "apple_mail_mcp.tools.inbox.get_mailbox_unread_counts",
                return_value={"Work": 4},
            ),
            patch(
                "apple_mail_mcp.tools.analytics._get_recent_emails_structured_async",
                side_effect=fake_recent,
            ),
        ):
            result = await analytics_tools.inbox_dashboard(
                account="Work",
                max_total=5,
                max_per_account=3,
                output_format="json",
            )

        self.assertEqual(result["account"], "Work")
        self.assertEqual(result["accounts"], {"Work": 4})
        self.assertEqual(result["recent_emails"], [{"subject": "Hello"}])
        self.assertEqual(result["errors"], [])


class HeavyScanGuardTests(unittest.TestCase):
    def test_overview_flags_skip_mailbox_and_recent_applescript_loops(self):
        script = inbox_tools._build_overview_one_account_script(
            "Work",
            include_mailboxes=False,
            include_recent=False,
        )

        self.assertNotIn("set accountMailboxes to every mailbox", script)
        self.assertNotIn("set recentMessages to", script)
        self.assertNotIn("RECENT|||", script)
        self.assertNotIn("MAILBOX|||", script)
        self.assertIn("HEADER|||", script)

    def test_needs_response_uses_bounded_slice_not_unbounded_whose(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "ok"

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            smart_inbox_tools.get_needs_response(account="Work", days_back=7)

        self.assertIn("set mailboxUpperBound to 30", captured["script"])
        self.assertIn("messages 1 thru mailboxUpperBound of targetMailbox", captured["script"])
        self.assertNotIn("every message of targetMailbox whose", captured["script"])
        self.assertNotIn("set mailboxMessages to messages of targetMailbox", captured["script"])

    def test_awaiting_reply_uses_bounded_slices_not_unbounded_whose(self):
        captured: dict[str, list[str]] = {"scripts": []}

        def fake_run(script, timeout=120):
            captured["scripts"].append(script)
            return "ok"

        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=fake_run):
            smart_inbox_tools.get_awaiting_reply(account="Work", days_back=7)

        self.assertEqual(len(captured["scripts"]), 2)
        inbox_script, sent_script = captured["scripts"]
        self.assertIn("set inboxUpperBound to 30", inbox_script)
        self.assertIn("messages 1 thru inboxUpperBound of inboxMailbox", inbox_script)
        self.assertIn("set sentUpperBound to 20", sent_script)
        self.assertIn("messages 1 thru sentUpperBound of sentMailbox", sent_script)
        self.assertNotIn("every message of inboxMailbox whose", inbox_script)
        self.assertNotIn("every message of sentMailbox whose", sent_script)

    def test_statistics_uses_bounded_slices_not_unbounded_date_whose(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "ok"

        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run):
            analytics_tools.get_statistics(
                account="Work",
                scope="account_overview",
                days_back=2,
            )

        self.assertIn("set mailboxUpperBound to 100", captured["script"])
        self.assertIn("1 thru 10", captured["script"])
        self.assertIn("messages 1 thru mailboxUpperBound of aMailbox", captured["script"])
        self.assertNotIn("every message of aMailbox whose date received", captured["script"])
        self.assertNotIn("set mailboxMessages to messages of aMailbox", captured["script"])

    def test_save_email_attachment_subject_lookup_avoids_unbounded_whose(self):
        captured = {}

        def fake_search(**kwargs):
            captured["search"] = kwargs
            return [{"message_id": "777"}]

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "saved"

        home = __import__("os").path.expanduser("~")
        save_path = f"{home}/Downloads/test-file.bin"

        with (
            patch("apple_mail_mcp.tools.manage._search_mail_records", side_effect=fake_search),
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run),
        ):
            manage_tools.save_email_attachment(
                account="Work",
                subject_keyword="Invoice",
                attachment_name="file.bin",
                save_path=save_path,
            )

        self.assertEqual(captured["search"]["account"], "Work")
        self.assertEqual(captured["search"]["mailbox"], "INBOX")
        self.assertEqual(captured["search"]["subject_terms"], ["Invoice"])
        self.assertEqual(captured["search"]["limit"], 1)
        self.assertEqual(captured["search"]["timeout"], 45)
        self.assertNotIn(
            'every message of inboxMailbox whose subject contains "Invoice"',
            captured["script"],
        )
        self.assertIn("id is 777", captured["script"])

    def test_export_single_email_subject_lookup_avoids_unbounded_whose(self):
        captured = {}

        def fake_search(**kwargs):
            captured["search"] = kwargs
            return [{"message_id": "888"}]

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "exported"

        home = __import__("os").path.expanduser("~")

        with (
            patch("apple_mail_mcp.tools.analytics._search_mail_records", side_effect=fake_search),
            patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run),
        ):
            analytics_tools.export_emails(
                account="Work",
                scope="single_email",
                subject_keyword="Invoice",
                save_directory=f"{home}/Downloads",
            )

        self.assertEqual(captured["search"]["account"], "Work")
        self.assertEqual(captured["search"]["mailbox"], "INBOX")
        self.assertEqual(captured["search"]["subject_terms"], ["Invoice"])
        self.assertEqual(captured["search"]["limit"], 1)
        self.assertEqual(captured["search"]["timeout"], 45)
        self.assertNotIn(
            'every message of targetMailbox whose subject contains "Invoice"',
            captured["script"],
        )
        self.assertIn("id is 888", captured["script"])


if __name__ == "__main__":
    unittest.main()
