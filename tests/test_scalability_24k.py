"""Scalability hardening for 24K-mailbox safety (v3.1.9 + v3.1.10).

Covers:
- Subject-keyword fallback in reply/forward refuses recent_days<=0 and
  returns a structured UNBOUNDED_SCAN_REQUIRED ToolError envelope (the
  legacy allow_full_scan opt-in was retired in the whose-elimination
  refactor — callers must pass message_id or fall back to
  full_inbox_export).
- get_statistics and get_top_senders require allow_full_scan when days_back=0.
- list_inbox_emails accepts deprecated aliases `limit` / `unread_only` and
  surfaces a warning.
- v3.1.10: list_inbox_emails(include_read=False) binds a bounded newest-first
  slice BEFORE applying `whose read status is false`, so a 24K Exchange inbox
  is not materialized to evaluate the filter.
- v3.1.10: _build_search_script scan_cap scales with recent_days so narrow
  filters (sender, subject_terms) over a wider date window actually inspect
  enough messages to find matches.
"""

import asyncio
import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools import analytics as analytics_tools
from apple_mail_mcp.tools import inbox as inbox_tools
from apple_mail_mcp.tools import search as search_tools
from apple_mail_mcp.tools import smart_inbox as smart_inbox_tools


class ComposeFullScanGateTests(unittest.TestCase):
    def test_reply_subject_without_date_bound_is_blocked(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as runner:
            result = compose_tools.reply_to_email(
                account="Work",
                subject_keyword="Invoice",
                reply_body="Hi",
                recent_days=0,
            )
        # Post-allow_full_scan-retirement: tools return a JSON-encoded
        # ToolError envelope (string) steering callers toward message_id
        # or full_inbox_export. The tool signature is `-> str`, so the
        # response must be a string whose JSON body carries the envelope.
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertIsInstance(parsed, dict)
        self.assertTrue(parsed.get("error"))
        self.assertEqual(parsed.get("code"), "UNBOUNDED_SCAN_REQUIRED")
        self.assertIn("recent_days", parsed.get("message", ""))
        self.assertEqual(
            parsed.get("remediation", {}).get("fallback_tool"),
            "full_inbox_export",
        )
        runner.assert_not_called()

    def test_forward_subject_without_date_bound_is_blocked(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as runner:
            result = compose_tools.forward_email(
                account="Work",
                subject_keyword="Invoice",
                to="x@example.com",
                recent_days=0,
            )
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertIsInstance(parsed, dict)
        self.assertTrue(parsed.get("error"))
        self.assertEqual(parsed.get("code"), "UNBOUNDED_SCAN_REQUIRED")
        self.assertEqual(
            parsed.get("remediation", {}).get("fallback_tool"),
            "full_inbox_export",
        )
        runner.assert_not_called()

    def test_reply_message_id_path_unaffected(self):
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
                message_id="9876",
                reply_body="Hi",
                recent_days=0,
            )
        # message_id path skips the subject-scan branch entirely.
        self.assertTrue(captured, "Expected reply AppleScript to be invoked")
        self.assertIn("whose id is 9876", captured[0])


class StatisticsFullScanGateTests(unittest.TestCase):
    def test_statistics_blocks_days_back_zero_text(self):
        # v3.1.11+: ``allow_full_scan`` retired — days_back<=0 returns a
        # JSON-encoded ``UNBOUNDED_SCAN_REQUIRED`` ToolError envelope as a
        # string (consistent with the `-> str` tool signature).
        result = analytics_tools.get_statistics(
            account="Work",
            scope="account_overview",
            days_back=0,
        )
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertIsInstance(parsed, dict)
        self.assertTrue(parsed.get("error"))
        self.assertEqual(parsed.get("code"), "UNBOUNDED_SCAN_REQUIRED")
        self.assertIn("days_back", parsed.get("message", ""))
        remediation = parsed.get("remediation") or {}
        self.assertEqual(remediation.get("fallback_tool"), "full_inbox_export")

    def test_statistics_blocks_days_back_zero_json(self):
        result = analytics_tools.get_statistics(
            account="Work",
            scope="account_overview",
            days_back=0,
            output_format="json",
        )
        # JSON-mode error returns the same ToolError envelope as a string.
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertEqual(parsed.get("code"), "UNBOUNDED_SCAN_REQUIRED")

    def test_statistics_no_longer_accepts_allow_full_scan(self):
        # v3.1.11: allow_full_scan removed from the signature.
        with self.assertRaises(TypeError):
            analytics_tools.get_statistics(
                account="Work",
                scope="account_overview",
                days_back=0,
                allow_full_scan=True,
            )


class TopSendersFullScanGateTests(unittest.TestCase):
    def test_top_senders_blocks_days_back_zero(self):
        # v3.1.11+: ``allow_full_scan`` retired — days_back<=0 returns a
        # JSON-encoded ``UNBOUNDED_SCAN_REQUIRED`` ToolError envelope as a
        # string (consistent with the `-> str` tool signature).
        result = smart_inbox_tools.get_top_senders(
            account="Work",
            days_back=0,
        )
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertIsInstance(parsed, dict)
        self.assertTrue(parsed.get("error"))
        self.assertEqual(parsed.get("code"), "UNBOUNDED_SCAN_REQUIRED")
        remediation = parsed.get("remediation") or {}
        self.assertEqual(remediation.get("fallback_tool"), "full_inbox_export")

    def test_top_senders_no_longer_accepts_allow_full_scan(self):
        # v3.1.11: allow_full_scan removed from the signature.
        with self.assertRaises(TypeError):
            smart_inbox_tools.get_top_senders(
                account="Work",
                days_back=0,
                allow_full_scan=True,
            )


class ListInboxAliasTests(unittest.TestCase):
    def _run(self, **kwargs):
        return asyncio.run(inbox_tools.list_inbox_emails(**kwargs))

    def test_limit_alias_emits_warning_and_maps_to_max_emails(self):
        captured_max = []

        async def fake_text(account, max_emails, *a, **k):
            captured_max.append(max_emails)
            return f"listed {max_emails}"

        with patch(
            "apple_mail_mcp.tools.inbox._list_inbox_emails_text",
            side_effect=fake_text,
        ):
            result = self._run(account="Work", limit=5)

        self.assertEqual(captured_max, [5])
        self.assertIn("WARNING", result)
        self.assertIn("limit", result)
        self.assertIn("max_emails", result)

    def test_unread_only_alias_maps_to_include_read_false(self):
        captured = {}

        async def fake_text(account, max_emails, include_read, *a, **k):
            captured["include_read"] = include_read
            return "listed"

        with patch(
            "apple_mail_mcp.tools.inbox._list_inbox_emails_text",
            side_effect=fake_text,
        ):
            result = self._run(account="Work", unread_only=True)

        self.assertEqual(captured["include_read"], False)
        self.assertIn("WARNING", result)
        self.assertIn("unread_only", result)

    def test_warnings_attached_to_json_output(self):
        # v3.2.x: _list_inbox_emails_json returns a dict directly, and the
        # public tool also returns the dict (no json.dumps). warnings are
        # attached in place.
        async def fake_json(*a, **k):
            return {"emails": [{"subject": "x"}], "errors": []}

        with patch(
            "apple_mail_mcp.tools.inbox._list_inbox_emails_json",
            side_effect=fake_json,
        ):
            parsed = self._run(account="Work", limit=2, output_format="json")

        self.assertIsInstance(parsed, dict)
        self.assertIn("warnings", parsed)
        self.assertTrue(
            any("limit" in w for w in parsed["warnings"]),
            f"Expected limit warning in {parsed['warnings']}",
        )

    def test_passing_both_max_emails_and_limit_errors(self):
        result = self._run(account="Work", max_emails=10, limit=5)
        self.assertTrue(result.startswith("Error:"))
        self.assertIn("not both", result)

    def test_canonical_params_no_warning(self):
        async def fake_text(*a, **k):
            return "rows"

        with patch(
            "apple_mail_mcp.tools.inbox._list_inbox_emails_text",
            side_effect=fake_text,
        ):
            result = self._run(account="Work", max_emails=5, include_read=False)
        self.assertNotIn("WARNING", result)


class ListInboxUnreadFilterBoundedTests(unittest.TestCase):
    """v3.1.10: unread filter must bind a bounded slice before `whose`."""

    def _text_script(self, max_emails: int) -> str:
        return inbox_tools._build_list_inbox_text_script(
            account="Work",
            max_emails=max_emails,
            include_read=False,
            include_content=False,
        )

    def _json_script(self, max_emails: int) -> str:
        return inbox_tools._build_list_inbox_json_script(
            account="Work",
            max_emails=max_emails,
            include_read=False,
        )

    def test_text_script_binds_slice_before_whose(self):
        script = self._text_script(max_emails=50)
        idx_slice = script.find("messages 1 thru ")
        idx_whose = script.find("whose read status is false")
        self.assertGreater(idx_slice, 0, "expected bounded slice in script")
        self.assertGreater(idx_whose, 0, "expected `whose read status is false` clause")
        self.assertLess(
            idx_slice,
            idx_whose,
            "bounded slice must appear BEFORE the `whose` filter, "
            "otherwise Mail materializes the entire 24K mailbox",
        )

    def test_json_script_binds_slice_before_whose(self):
        script = self._json_script(max_emails=50)
        idx_slice = script.find("messages 1 thru ")
        idx_whose = script.find("whose read status is false")
        self.assertGreater(idx_slice, 0)
        self.assertGreater(idx_whose, 0)
        self.assertLess(idx_slice, idx_whose)

    def test_scan_cap_scales_with_max_emails(self):
        # max_emails=50 → scan_cap = max(50*10, 100) = 500
        script = self._text_script(max_emails=50)
        self.assertIn("messages 1 thru 500", script)

    def test_scan_cap_has_floor_of_100(self):
        # max_emails=5 → scan_cap = max(5*10, 100) = 100
        script = self._text_script(max_emails=5)
        self.assertIn("messages 1 thru 100", script)

    def test_scan_cap_ceiling_at_1000(self):
        # max_emails=500 → scan_cap = min(max(500*10, 100), 1000) = 1000
        script = self._text_script(max_emails=500)
        self.assertIn("messages 1 thru 1000", script)

    def test_candidate_messages_variable_present(self):
        # The fix binds `candidateMessages` and then filters from it.
        for builder in (self._text_script, self._json_script):
            script = builder(max_emails=50)
            self.assertIn("set candidateMessages to", script)
            self.assertIn("candidateMessages whose read status is false", script)


class SearchScanCapScalingTests(unittest.TestCase):
    """v3.1.10: scan_cap scales with recent_days so narrow filters find matches."""

    def _script(self, recent_days: float, limit: int = 20, offset: int = 0) -> str:
        return search_tools._build_search_script(
            account="Work",
            mailbox="INBOX",
            subject_terms=None,
            sender="boss@example.com",
            has_attachments=None,
            read_status="all",
            date_from=None,
            date_to=None,
            include_content=False,
            content_length=300,
            offset=offset,
            limit=limit,
            body_text=None,
            recent_days=recent_days,
        )

    def test_default_recent_days_2_scales_to_300(self):
        # Phase A: window cap comes from
        # bounded_scan.compute_scan_upper_bound(2.0) = 200 + 2*50 = 300,
        # floored at limit+1+offset (=21) → scan_cap=300.
        script = self._script(recent_days=2.0)
        self.assertIn("> 300", script)

    def test_recent_days_7_caps_at_500(self):
        # compute_scan_upper_bound(7.0) = 200 + 350 = 550, clamped to 500.
        script = self._script(recent_days=7.0)
        self.assertIn("> 500", script)

    def test_recent_days_30_caps_at_500(self):
        # recent_days=30 → 200 + 1500 = 1700, clamped to 500.
        script = self._script(recent_days=30.0)
        self.assertIn("> 500", script)

    def test_recent_days_zero_uses_floor(self):
        # recent_days=0 keeps base scan_cap = limit + 1 + offset = 21
        # (the recent_days>0 branch is skipped; compute_* helper is not consulted).
        script = self._script(recent_days=0.0)
        self.assertIn("> 21", script)

    def test_floor_dominates_when_limit_is_huge(self):
        # limit=600 → base_cap=601 > compute(2.0)=300, scan_cap=601.
        script = self._script(recent_days=2.0, limit=600)
        self.assertIn("> 601", script)


if __name__ == "__main__":
    unittest.main()
