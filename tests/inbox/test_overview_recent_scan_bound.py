"""``get_inbox_overview`` recent-slice bound: clamp, no raw enumeration, honest cap.

The defect these lock down: ``max_recent`` is a plain MCP tool argument with no
validation anywhere in the package, and the recent block used to read

    if (count of messages of inboxMailbox) > N then
        set recentMessages to messages 1 thru N of inboxMailbox
    else
        set recentMessages to messages of inboxMailbox
    end if

so ``max_recent=50000`` against a 25K-message Exchange inbox made the guard
false, bound every message, and then read five properties per message. That is a
hang, not an error — the failure mode the bounded-scan contract exists to
prevent, and the worse one because a hang carries no diagnostic.

Three separate things have to hold, and each is easy to get wrong on its own:

1. **Clamped.** No caller-supplied value may widen the scan past
   ``SCAN_BOUNDS["INBOX_HARD_CEILING"]``, the same per-account read ceiling
   ``list_inbox_emails``, ``inbox_dashboard``, and ``get_statistics`` use.
2. **Not floored.** ``messages 1 thru 0`` does *not* bind an empty list — it
   silently returns the **first** message (verified across all four backends),
   so a naive ``max(1, n)`` clamp trades a hang for a fabricated row. A
   genuinely empty mailbox must yield exactly zero rows, and ``max_recent=0``
   must keep meaning "skip the recent block" rather than being rewritten to 1.
3. **Disclosed.** A capped list returned as if it were complete is a quiet
   wrong answer. A clamped request carries ``max_recent_clamped`` in JSON and a
   ``RECENT PREVIEW TRUNCATED`` warning in text.

Fixtures are synthetic (`sender@example.com`); no live Mail is touched. The
``osacompile`` class parses the emitted script offline and is skipped on hosts
without that executable.
"""

import asyncio
import contextlib
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.tools import inbox as inbox_tools
from apple_mail_mcp.tools.inbox.overview import (
    RECENT_SCAN_CEILING,
    _build_overview_one_account_script,
    _clamp_max_recent,
)

CEILING = SCAN_BOUNDS["INBOX_HARD_CEILING"]

# Header only: the account's inbox reported 0 messages and the bounded scan
# emitted no RECENT rows. This is what a genuinely empty mailbox looks like.
EMPTY_MAILBOX_PAYLOAD = "HEADER|||Work|||0|||0"

# Three real rows, fewer than any cap in play.
SMALL_MAILBOX_PAYLOAD = "\n".join(
    [
        "HEADER|||Work|||1|||3",
        "RECENT|||Budget draft|||alice@example.com|||Thursday, May 15, 2026 at 9:00:00 AM|||false",
        "RECENT|||Lunch|||bob@example.com|||Thursday, May 15, 2026 at 8:00:00 AM|||true",
        "RECENT|||Reading list|||carol@example.com|||Wednesday, May 14, 2026 at 7:00:00 AM|||true",
    ]
)


def _run(coro):
    return asyncio.run(coro)


class ClampMaxRecentTests(unittest.TestCase):
    """The pure clamp, including the two values that must NOT move."""

    def test_values_at_or_below_the_ceiling_pass_through(self):
        for value in (1, 2, 10, CEILING - 1, CEILING):
            with self.subTest(value=value):
                self.assertEqual(_clamp_max_recent(value), value)

    def test_values_above_the_ceiling_are_clamped(self):
        for value in (CEILING + 1, 100, 1_000, 50_000, 10**9):
            with self.subTest(value=value):
                self.assertEqual(_clamp_max_recent(value), CEILING)

    def test_zero_and_negative_are_returned_unchanged_not_floored_to_one(self):
        """``messages 1 thru 0`` returns the FIRST message, so 0 must stay 0.

        Flooring a "read nothing" request to 1 would swap a hang for a
        fabricated recent email — a different wrong answer, not a fix.
        """
        for value in (0, -1, -50_000):
            with self.subTest(value=value):
                self.assertEqual(_clamp_max_recent(value), value)

    def test_ceiling_is_the_shared_inbox_constant_not_a_new_magic_number(self):
        self.assertEqual(RECENT_SCAN_CEILING, SCAN_BOUNDS["INBOX_HARD_CEILING"])


class OverviewScriptBoundTests(unittest.TestCase):
    """The emitted AppleScript, which is where the hang actually lived."""

    def _script(self, **kwargs) -> str:
        return _build_overview_one_account_script("Work", **kwargs)

    def test_huge_max_recent_emits_no_raw_full_mailbox_enumeration(self):
        script = self._script(max_recent=50_000)
        self.assertNotIn("set recentMessages to messages of inboxMailbox", script)
        self.assertNotIn("every message of inboxMailbox", script)
        # ``messages of MB`` is the banned spelling; ``count of messages of MB``
        # is a cheap cached property read and is explicitly allowed, so the
        # lookbehind is load-bearing rather than defensive.
        raw = [m.group(0) for m in re.finditer(r"(?<!count of )messages of inboxMailbox", script)]
        self.assertEqual(raw, [], f"raw full-mailbox enumeration survived: {raw}")

    def test_huge_max_recent_slices_at_the_ceiling(self):
        script = self._script(max_recent=50_000)
        self.assertIn(f"messages 1 thru {CEILING} of inboxMailbox", script)
        self.assertNotIn("messages 1 thru 50000", script)

    def test_modest_max_recent_is_left_alone(self):
        script = self._script(max_recent=7)
        self.assertIn("messages 1 thru 7 of inboxMailbox", script)

    def test_no_slice_is_ever_emitted_with_a_literal_zero_upper_bound(self):
        for max_recent in (1, 7, CEILING, 50_000):
            with self.subTest(max_recent=max_recent):
                script = self._script(max_recent=max_recent)
                self.assertNotIn("thru 0 of", script)
                self.assertNotIn("thru 0 ", script)

    def test_recovery_arm_guards_the_count_before_slicing_to_it(self):
        """The stale-count arm must not slice on a zero count (the ``1 thru 0`` trap)."""
        script = self._script(max_recent=CEILING)
        guard = "if _mbCount > 0 then"
        self.assertIn(guard, script)
        recovery_slice = "set candidateMessages to messages 1 thru _mbCount of inboxMailbox"
        self.assertIn(recovery_slice, script)
        self.assertLess(script.index(guard), script.index(recovery_slice))

    def test_zero_max_recent_still_skips_the_recent_block_entirely(self):
        script = self._script(max_recent=0)
        self.assertNotIn("candidateMessages", script)
        self.assertNotIn("RECENT|||", script)

    def test_negative_max_recent_skips_the_recent_block_rather_than_binding_one(self):
        script = self._script(max_recent=-5)
        self.assertNotIn("candidateMessages", script)
        self.assertNotIn("thru -5", script)

    def test_builder_clamps_even_when_called_off_the_tool_path(self):
        """``_run_overview_one``, the facade, and cli/perf reach the builder directly."""
        script = inbox_tools._build_overview_one_account_script("Work", max_recent=9_999)
        self.assertIn(f"messages 1 thru {CEILING} of inboxMailbox", script)


class OverviewClampDisclosureTests(unittest.TestCase):
    """A clamped request must not be reported as a complete one."""

    def test_json_reports_the_clamp_and_the_original_request(self):
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=SMALL_MAILBOX_PAYLOAD):
            result = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="json",
                    include_suggestions=False,
                    max_recent=50_000,
                )
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["max_recent"], CEILING)
        self.assertEqual(result["max_recent_requested"], 50_000)
        self.assertTrue(result["max_recent_clamped"])
        self.assertIn("50000", result["max_recent_clamp_note"])
        self.assertIn(str(CEILING), result["max_recent_clamp_note"])

    def test_text_appends_a_truncation_warning(self):
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=SMALL_MAILBOX_PAYLOAD):
            result = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="text",
                    include_suggestions=False,
                    max_recent=50_000,
                )
            )

        self.assertIsInstance(result, str)
        self.assertIn("RECENT PREVIEW TRUNCATED", result)
        self.assertIn("50000", result)

    def test_unclamped_request_carries_no_clamp_disclosure(self):
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=SMALL_MAILBOX_PAYLOAD):
            payload = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="json",
                    include_suggestions=False,
                    max_recent=5,
                )
            )
            text = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="text",
                    include_suggestions=False,
                    max_recent=5,
                )
            )

        self.assertEqual(payload["max_recent"], 5)
        self.assertNotIn("max_recent_clamped", payload)
        self.assertNotIn("max_recent_requested", payload)
        self.assertNotIn("max_recent_clamp_note", payload)
        self.assertNotIn("RECENT PREVIEW TRUNCATED", text)

    def test_zero_max_recent_is_not_treated_as_a_clamp(self):
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=EMPTY_MAILBOX_PAYLOAD):
            payload = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="json",
                    include_suggestions=False,
                    max_recent=0,
                )
            )

        self.assertEqual(payload["max_recent"], 0)
        self.assertNotIn("max_recent_clamped", payload)

    def test_clamped_request_sends_the_clamped_cap_to_mail(self):
        """The clamp must reach the AppleScript, not just the response envelope."""
        captured: list[str] = []

        def _capture(script, *args, **kwargs):
            captured.append(script)
            return SMALL_MAILBOX_PAYLOAD

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=_capture):
            _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="json",
                    include_suggestions=False,
                    include_draft_state=False,
                    max_recent=50_000,
                )
            )

        self.assertTrue(captured)
        overview_script = captured[0]
        self.assertIn(f"messages 1 thru {CEILING} of inboxMailbox", overview_script)
        self.assertNotIn("set recentMessages to messages of inboxMailbox", overview_script)


class OverviewRowFidelityTests(unittest.TestCase):
    """Bounding must not invent rows on an empty mailbox or lose rows on a small one."""

    def test_empty_mailbox_returns_exactly_zero_recent_rows(self):
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=EMPTY_MAILBOX_PAYLOAD):
            payload = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="json",
                    include_suggestions=False,
                    max_recent=50_000,
                )
            )

        account_row = payload["accounts"][0]
        self.assertNotIn("error", account_row)
        self.assertEqual(account_row["total"], 0)
        self.assertEqual(account_row["recent"], [])

    def test_empty_mailbox_text_says_none_rather_than_showing_one(self):
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=EMPTY_MAILBOX_PAYLOAD):
            text = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="text",
                    include_suggestions=False,
                    max_recent=10,
                )
            )

        self.assertIn("No recent emails found.", text)

    def test_small_mailbox_still_returns_its_real_contents(self):
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=SMALL_MAILBOX_PAYLOAD):
            payload = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="json",
                    include_suggestions=False,
                    max_recent=10,
                )
            )

        recent = payload["accounts"][0]["recent"]
        self.assertEqual(len(recent), 3)
        self.assertEqual([row["subject"] for row in recent], ["Budget draft", "Lunch", "Reading list"])
        self.assertFalse(recent[0]["is_read"])

    def test_small_mailbox_survives_a_clamped_request(self):
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=SMALL_MAILBOX_PAYLOAD):
            payload = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="json",
                    include_suggestions=False,
                    max_recent=50_000,
                )
            )

        self.assertEqual(len(payload["accounts"][0]["recent"]), 3)

    def test_stale_count_error_marker_keeps_its_full_diagnostic(self):
        """``build_bounded_message_scan`` raises an ``ERROR_MAILBOX|||`` marker.

        It reaches the parser through the account's ``HEADER|||…|||ERROR|||``
        arm already containing the delimiter, so the parser must rejoin the
        tail instead of truncating the diagnostic to ``ERROR_MAILBOX``.
        """
        raw = (
            "HEADER|||Work|||3|||9\n"
            "HEADER|||Work|||ERROR|||ERROR_MAILBOX|||Inbox|||bounded slice 1 thru 50 failed while "
            "count of messages reads 9"
        )
        parsed = inbox_tools._parse_overview_account(raw)
        self.assertIn("ERROR_MAILBOX", parsed["error"])
        self.assertIn("bounded slice 1 thru 50 failed", parsed["error"])


@unittest.skipIf(
    shutil.which("osacompile") is None,
    "osacompile not available — AppleScript compile check skipped on this platform",
)
class OverviewScriptCompileTests(unittest.TestCase):
    """The bounded slice is spliced AppleScript; prove every arm still parses.

    The repo's ``check_applescript_compiles`` hook cannot import several tool
    modules and silently skips them, so this is the real coverage for this
    builder rather than a belt-and-braces duplicate.
    """

    def _assert_compiles(self, script: str, label: str) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as handle:
            handle.write(script)
            src = handle.name
        out = src.replace(".applescript", ".scpt")
        try:
            proc = subprocess.run(["osacompile", "-o", out, src], capture_output=True, text=True, timeout=30)
            self.assertEqual(
                proc.returncode,
                0,
                f"osacompile rejected the {label} overview script:\n{proc.stderr or proc.stdout}",
            )
        finally:
            for path in (src, out):
                with contextlib.suppress(OSError):
                    Path(path).unlink()

    def test_overview_script_compiles_across_recent_bounds(self):
        for max_recent in (0, 1, 10, CEILING, 50_000):
            for include_mailboxes in (False, True):
                label = f"max_recent={max_recent} mailboxes={include_mailboxes}"
                with self.subTest(label=label):
                    self._assert_compiles(
                        _build_overview_one_account_script(
                            "Work",
                            max_recent=max_recent,
                            include_mailboxes=include_mailboxes,
                        ),
                        label,
                    )

    def test_recent_block_disabled_still_compiles(self):
        self._assert_compiles(
            _build_overview_one_account_script("Work", include_recent=False, max_recent=50_000),
            "include_recent=False",
        )


if __name__ == "__main__":
    unittest.main()
