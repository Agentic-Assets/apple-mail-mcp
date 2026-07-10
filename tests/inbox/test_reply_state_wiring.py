"""Tests for ``apple_mail_mcp.tools.reply_state_wiring``.

Covers the generic ``has_draft`` correlation + ``draft_scan`` aggregation
helper shared by ``inbox/list_emails.py``, ``inbox/overview.py``, and
``analytics/dashboard.py`` (see
``tasks/active/reply-state-annotation/plan-2026-07-10.md``). Mocks only the
injectable ``runner`` seam (``core.reply_state.fetch_drafts_snapshot``'s
``AppleScriptRunner`` parameter); no real Mail.app.
"""

from __future__ import annotations

import unittest

from apple_mail_mcp.core.reply_state import DraftsSnapshot
from apple_mail_mcp.tools.reply_state_wiring import (
    MAX_DRAFT_SNAPSHOT_ACCOUNTS,
    annotate_rows_with_reply_state,
    build_draft_scan_status,
)


def _drafts_runner(responses: dict[str, str], calls: list[str] | None = None):
    """Return a fake AppleScriptRunner keyed by ``account "<name>"`` substring."""

    def runner(script: str, timeout: int | None = 60) -> str:
        if calls is not None:
            calls.append(script)
        for account, raw in responses.items():
            if f'account "{account}"' in script:
                return raw
        return "COUNT|||0"

    return runner


class BuildDraftScanStatusTests(unittest.TestCase):
    def test_empty_snapshots_is_skipped(self):
        status = build_draft_scan_status({})
        self.assertEqual(status, {"status": "skipped", "scanned": 0, "accounts": []})

    def test_all_ok_snapshots_aggregate_scanned_counts(self):
        snapshots = {
            "Work": DraftsSnapshot(status="ok", scanned=3, account="Work"),
            "Personal": DraftsSnapshot(status="ok", scanned=2, account="Personal"),
        }
        status = build_draft_scan_status(snapshots)
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["scanned"], 5)
        self.assertEqual(len(status["accounts"]), 2)
        self.assertNotIn("error", status)

    def test_one_errored_account_flips_status_to_error(self):
        snapshots = {
            "Work": DraftsSnapshot(status="ok", scanned=3, account="Work"),
            "Slow": DraftsSnapshot(status="error", scanned=0, account="Slow", error="timeout: 60s"),
        }
        status = build_draft_scan_status(snapshots)
        self.assertEqual(status["status"], "error")
        self.assertIn("Slow", status["error"])
        self.assertIn("timeout", status["error"])

    def test_skipped_account_snapshot_does_not_set_error_message(self):
        snapshots = {"Work": DraftsSnapshot(status="skipped", scanned=0, account="Work")}
        status = build_draft_scan_status(snapshots)
        self.assertEqual(status["status"], "error")  # not "ok": not every account is "ok"
        self.assertNotIn("error", status)  # skipped carries no .error message to fold in


class AnnotateRowsWithReplyStateTests(unittest.TestCase):
    def test_include_draft_state_false_sets_null_and_never_calls_runner(self):
        calls: list[str] = []
        rows = [{"account": "Work", "subject": "Hi", "sender": "a@b.com", "date": None}]
        cache = annotate_rows_with_reply_state(
            rows, runner=_drafts_runner({}, calls), timeout=30, include_draft_state=False
        )
        self.assertIsNone(rows[0]["has_draft"])
        self.assertEqual(cache, {})
        self.assertEqual(calls, [])

    def test_empty_rows_with_account_override_never_calls_runner(self):
        calls: list[str] = []
        cache = annotate_rows_with_reply_state(
            [], runner=_drafts_runner({}, calls), timeout=30, include_draft_state=True, account="Work"
        )
        self.assertEqual(cache, {})
        self.assertEqual(calls, [])

    def test_matching_draft_sets_has_draft_true(self):
        raw = "DRAFT|||Re: Budget|||alice@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1"
        rows = [{"account": "Work", "subject": "Budget", "sender": "alice@example.com", "date": None}]
        annotate_rows_with_reply_state(rows, runner=_drafts_runner({"Work": raw}), timeout=30, include_draft_state=True)
        self.assertTrue(rows[0]["has_draft"])

    def test_non_matching_draft_sets_has_draft_false(self):
        raw = "DRAFT|||Something else|||bob@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1"
        rows = [{"account": "Work", "subject": "Budget", "sender": "alice@example.com", "date": None}]
        annotate_rows_with_reply_state(rows, runner=_drafts_runner({"Work": raw}), timeout=30, include_draft_state=True)
        self.assertFalse(rows[0]["has_draft"])

    def test_errored_scan_sets_has_draft_null_not_false(self):
        rows = [{"account": "Work", "subject": "Budget", "sender": "alice@example.com", "date": None}]
        annotate_rows_with_reply_state(
            rows,
            runner=_drafts_runner({"Work": "ERROR|||Could not find Drafts mailbox"}),
            timeout=30,
            include_draft_state=True,
        )
        self.assertIsNone(rows[0]["has_draft"])

    def test_account_override_ignores_row_account_field(self):
        raw = "DRAFT|||Budget|||alice@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1"
        rows = [{"subject": "Budget", "sender": "alice@example.com", "date": None}]  # no "account" key
        annotate_rows_with_reply_state(
            rows, runner=_drafts_runner({"Work": raw}), timeout=30, include_draft_state=True, account="Work"
        )
        self.assertTrue(rows[0]["has_draft"])

    def test_multi_account_fan_out_capped_at_five(self):
        calls: list[str] = []
        accounts = [f"Acct{i}" for i in range(6)]
        rows = [{"account": a, "subject": "x", "sender": "y@z.com", "date": None} for a in accounts]
        responses = {a: "COUNT|||0" for a in accounts}
        cache = annotate_rows_with_reply_state(
            rows, runner=_drafts_runner(responses, calls), timeout=30, include_draft_state=True
        )
        self.assertEqual(len(cache), MAX_DRAFT_SNAPSHOT_ACCOUNTS)
        # The 6th account never made the cap; its row must be null, not False.
        capped_accounts = set(cache.keys())
        uncapped = [r for r in rows if r["account"] not in capped_accounts]
        self.assertEqual(len(uncapped), 1)
        self.assertIsNone(uncapped[0]["has_draft"])
        for row in rows:
            if row["account"] in capped_accounts:
                self.assertIsNotNone(row["has_draft"])

    def test_shared_snapshot_cache_reused_across_calls(self):
        calls: list[str] = []
        runner = _drafts_runner({"Work": "COUNT|||0"}, calls)
        cache: dict[str, DraftsSnapshot] = {}
        annotate_rows_with_reply_state(
            [{"account": "Work", "subject": "a", "sender": "b@c.com", "date": None}],
            runner=runner,
            timeout=30,
            include_draft_state=True,
            account="Work",
            snapshots=cache,
        )
        annotate_rows_with_reply_state(
            [{"account": "Work", "subject": "d", "sender": "e@f.com", "date": None}],
            runner=runner,
            timeout=30,
            include_draft_state=True,
            account="Work",
            snapshots=cache,
        )
        # Only one Drafts snapshot AppleScript call across both invocations.
        self.assertEqual(len(calls), 1)

    def test_null_internet_message_id_falls_back_to_subject_sender(self):
        raw = "DRAFT|||Budget|||alice@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1"
        rows = [
            {
                "account": "Work",
                "subject": "Budget",
                "sender": "alice@example.com",
                "date": None,
                "internet_message_id": None,
            }
        ]
        annotate_rows_with_reply_state(rows, runner=_drafts_runner({"Work": raw}), timeout=30, include_draft_state=True)
        self.assertTrue(rows[0]["has_draft"])


if __name__ == "__main__":
    unittest.main()
