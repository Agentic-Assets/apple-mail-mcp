"""``inbox_dashboard`` recent-scan failure surfacing and scan-cap clamping.

Two defect classes are locked here.

**Silent zero (AGENTIC-2344 / AGENTIC-2355 class).** The recent-email script
wrapped its whole body in a bare ``try`` with no ``on error`` arm, so any
AppleScript throw (missing account, no inbox mailbox, permission failure,
per-message read failure) returned ``""``, which the parser turned into ``[]``,
which the tool reported as ``"recent_emails": [], "errors": []``. An agent
reading that payload concludes the mailbox is empty. The repo's established
mitigation is an ``ERROR_MAILBOX|||`` marker row that the Python layer diverts
into ``error_details`` (see ``tools/search/script.py`` / ``records.py``).

**Unclamped ``max_per_account``.** Verified live (read-only, 4 Mail backends):
``messages 1 thru 0`` does **not** raise on a non-empty mailbox — index 0 clamps
to 1 and the range normalizes ascending, so it returns exactly one message,
which downstream renders as a genuine "recent email". ``thru -1`` is
end-relative and spans the entire mailbox. Both are silent wrong answers, so the
bound is clamped in the script builder (every caller reaches it) rather than in
the tool signature.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import analytics as analytics_tools
from apple_mail_mcp.tools.analytics import dashboard as dashboard_module

_HARD_CEILING = SCAN_BOUNDS["INBOX_HARD_CEILING"]


def _run(coro):
    return asyncio.run(coro)


def _dashboard_json(runner, **kwargs):
    """Run ``inbox_dashboard`` in JSON mode with ``run_applescript`` mocked."""
    options = {"account": "Work", "output_format": "json", "include_draft_state": False}
    options.update(kwargs)
    with (
        patch("apple_mail_mcp.tools.inbox.get_mailbox_unread_counts", return_value={"Work": 1}),
        patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=runner),
    ):
        return _run(analytics_tools.inbox_dashboard(**options))


# ---------------------------------------------------------------------------
# Defect 2 — max_per_account has no floor and no ceiling
# ---------------------------------------------------------------------------


class RecentScanCapClampTests(unittest.TestCase):
    def test_zero_max_per_account_never_emits_thru_zero(self):
        """``messages 1 thru 0`` returns ONE message, not zero. Never emit it.

        0 is floored to 1, so the emitted bound is explicit rather than relying
        on AppleScript's index-0 clamp to mean "one message".
        """
        script = dashboard_module._build_recent_one_account_script("Work", 0, False)

        self.assertNotIn("thru 0", script)
        self.assertIn("messages 1 thru 1 of inboxMailbox", script)

    def test_negative_max_per_account_never_emits_a_negative_bound(self):
        """``thru -1`` is end-relative: a negative bound spans the whole mailbox."""
        script = dashboard_module._build_recent_one_account_script("Work", -5, False)

        self.assertNotIn("thru -", script)
        self.assertNotIn("-5", script)

    def test_oversized_max_per_account_clamps_to_inbox_hard_ceiling(self):
        script = dashboard_module._build_recent_one_account_script("Work", 5000, False)

        self.assertNotIn("5000", script)
        self.assertIn(f"messages 1 thru {_HARD_CEILING} of inboxMailbox", script)

    def test_in_range_bound_is_left_alone(self):
        script = dashboard_module._build_recent_one_account_script("Work", 3, False)

        self.assertIn("messages 1 thru 3 of inboxMailbox", script)

    def test_clamp_applies_to_every_builder_caller(self):
        """The clamp lives in the builder, so the sync/async helpers inherit it."""
        captured: dict[str, str] = {}

        def fake_run(script, timeout=None):
            captured["script"] = script
            return ""

        with (
            patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run),
            patch("apple_mail_mcp.tools.analytics.list_mail_account_names", return_value=["Work"]),
        ):
            analytics_tools._get_recent_emails_structured(max_total=5, max_per_account=0)

        self.assertNotIn("thru 0", captured["script"])

    def test_no_raw_mailbox_enumeration_fallback(self):
        """The else-arm must be a bounded slice, not `messages of inboxMailbox`."""
        script = dashboard_module._build_recent_one_account_script("Work", 10, False)

        self.assertNotIn("set inboxMessages to messages of inboxMailbox", script)
        self.assertNotIn("every message of inboxMailbox", script)


# ---------------------------------------------------------------------------
# Defect 1 — every failure renders as an authoritative empty inbox
# ---------------------------------------------------------------------------


class RecentScanDiagnosticScriptShapeTests(unittest.TestCase):
    def test_outer_try_has_an_error_arm_that_emits_a_marker(self):
        script = dashboard_module._build_recent_one_account_script("Work", 10, False)

        self.assertIn("ERROR_MAILBOX|||", script)
        self.assertIn("on error", script)

    def test_per_message_read_failures_are_counted_and_reported(self):
        script = dashboard_module._build_recent_one_account_script("Work", 10, False)

        self.assertIn("set scanReadFailures to scanReadFailures + 1", script)
        self.assertIn("per-message scan failed for ", script)

    def test_marker_row_fields_are_pipe_sanitized(self):
        """A value containing ``|||`` would shift every downstream field."""
        script = dashboard_module._build_recent_one_account_script("Work", 10, False)

        self.assertIn("set _amm_parts to text items of accountName", script)
        self.assertIn("set _amm_parts to text items of mailboxError", script)


class RecentScanDiagnosticParseTests(unittest.TestCase):
    def test_marker_row_is_diverted_not_parsed_as_an_email(self):
        raw = 'ERROR_MAILBOX|||Work|||Can\'t get mailbox "INBOX" of account "Work"'

        rows, errors = dashboard_module._split_recent_email_output(raw)

        self.assertEqual(rows, [])
        self.assertEqual(
            errors,
            [
                {
                    "account": "Work",
                    "type": "mailbox_error",
                    "message": 'Can\'t get mailbox "INBOX" of account "Work"',
                }
            ],
        )

    def test_marker_row_never_becomes_a_fabricated_email_row(self):
        raw = "ERROR_MAILBOX|||Work|||boom"

        self.assertEqual(dashboard_module._parse_recent_email_lines(raw), [])

    def test_a_subject_of_error_mailbox_cannot_spoof_a_diagnostic(self):
        """Field 0 of a data row is the subject, so the marker needs a shape check."""
        row = "ERROR_MAILBOX|||sender@example.com|||Date|||false|||Work|||INBOX|||101|||<a@example.com>|||false|||"

        rows, errors = dashboard_module._split_recent_email_output(row)

        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["subject"], "ERROR_MAILBOX")
        self.assertEqual(rows[0]["message_id"], "101")

    def test_rows_and_markers_coexist(self):
        row = "Subject|||sender@example.com|||Date|||false|||Work|||INBOX|||101|||<a@example.com>|||false|||"
        rows, errors = dashboard_module._split_recent_email_output(row + "\nERROR_MAILBOX|||Work|||partial")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message_id"], "101")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["message"], "partial")


class DashboardDiagnosticSurfacingTests(unittest.TestCase):
    def test_json_payload_surfaces_a_mailbox_error(self):
        def runner(script, timeout=None):
            return "ERROR_MAILBOX|||Work|||No inbox mailbox found for account Work"

        payload = _dashboard_json(runner)

        self.assertEqual(payload["recent_emails"], [])
        self.assertEqual(payload["errors"], ["Work"])
        self.assertEqual(
            payload["error_details"],
            [
                {
                    "account": "Work",
                    "type": "mailbox_error",
                    "message": "No inbox mailbox found for account Work",
                }
            ],
        )

    def test_scan_timeout_is_reported_not_silently_empty(self):
        def runner(script, timeout=None):
            raise AppleScriptTimeout("osascript timed out")

        payload = _dashboard_json(runner)

        self.assertEqual(payload["recent_emails"], [])
        self.assertEqual(payload["errors"], ["Work"])
        self.assertEqual([item["type"] for item in payload["error_details"]], ["timeout"])

    def test_sync_helper_records_timeout_for_the_caller(self):
        errors: list[dict[str, str]] = []

        with (
            patch(
                "apple_mail_mcp.tools.analytics.run_applescript",
                side_effect=AppleScriptTimeout("osascript timed out"),
            ),
            patch("apple_mail_mcp.tools.analytics.list_mail_account_names", return_value=["Work"]),
        ):
            rows = analytics_tools._get_recent_emails_structured(
                max_total=5,
                max_per_account=3,
                error_details=errors,
            )

        self.assertEqual(rows, [])
        self.assertEqual([item["type"] for item in errors], ["timeout"])
        self.assertEqual(errors[0]["account"], "Work")


class QuietMailboxIsNotAnErrorTests(unittest.TestCase):
    """Mirror-image regression guard: 'no mail' must never render as 'error'."""

    def test_empty_inbox_reports_an_empty_result_with_no_diagnostic(self):
        def runner(script, timeout=None):
            return ""

        payload = _dashboard_json(runner)

        self.assertEqual(payload["recent_emails"], [])
        self.assertEqual(payload["errors"], [])
        self.assertNotIn("error_details", payload)

    def test_empty_mailbox_skips_the_slice_instead_of_throwing(self):
        """On an empty mailbox every slice form raises -1719, so guard on zero."""
        script = dashboard_module._build_recent_one_account_script("Work", 10, False)

        self.assertIn("if inboxTotal > 0 then", script)

    def test_populated_inbox_still_parses_rows(self):
        row = "Subject|||sender@example.com|||Date|||false|||Work|||INBOX|||101|||<a@example.com>|||true|||"

        payload = _dashboard_json(lambda script, timeout=None: row)

        self.assertEqual(len(payload["recent_emails"]), 1)
        self.assertEqual(payload["errors"], [])
        self.assertTrue(payload["recent_emails"][0]["was_replied_to"])


@unittest.skipIf(shutil.which("osacompile") is None, "osacompile not available")
class RecentScanScriptCompilesTests(unittest.TestCase):
    """The dashboard builder is not covered by the shared osacompile sweep.

    ``_build_recent_one_account_script`` takes required params with no entry in
    that sweep's ``_SAMPLE_KWARGS``, so its discovery helper skips it. Compile
    it here instead: the added ``on error`` arms and slice guard are exactly the
    parse-level regression class (osascript -2740) that sweep exists to catch.
    """

    def _assert_compiles(self, script: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "script.applescript"
            src.write_text(script, encoding="utf-8")
            result = subprocess.run(
                ["osacompile", "-o", str(Path(tmp) / "out.scpt"), str(src)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, f"osacompile failed:\n{result.stderr}")

    def test_script_compiles_without_preview(self):
        self._assert_compiles(dashboard_module._build_recent_one_account_script("Work", 10, False))

    def test_script_compiles_with_preview(self):
        self._assert_compiles(dashboard_module._build_recent_one_account_script("Work", 10, True))

    def test_clamped_edge_cases_compile(self):
        for cap in (0, -5, 5000):
            with self.subTest(cap=cap):
                self._assert_compiles(dashboard_module._build_recent_one_account_script("Work", cap, False))


if __name__ == "__main__":
    unittest.main()
