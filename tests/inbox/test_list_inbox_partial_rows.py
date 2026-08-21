"""Regressions for ``list_inbox_emails`` per-message row drops.

The script-level swallow in ``list_scripts.py`` grew an ``on error`` arm, but
the per-message ``try`` one level in stayed bare. A row whose ``subject`` /
``sender`` / ``date received`` read threw was skipped silently:

* **text mode** emitted ``__COUNT__|||sentCount`` next to a header built from
  ``messageCount`` — the two numbers were both on the wire and never compared,
  so the caller saw a header claiming N messages above fewer than N rendered
  rows and nothing marking the difference;
* **JSON mode** simply returned a shorter list.

Sharpest with ``read_status="unread"``, because ``unread_counts.py``'s own
docstring points callers at that call as the ground truth to use when Mail's
cached unread count is suspect — so the designated fallback authority
under-reported too.

The fix compares the rendered count against the count the loop expected to
render, and gives the per-message ``try`` a counting ``on error`` arm so the
drop is attributable: text mode appends a ``PARTIAL:`` line (the same shape
``list_emails`` already uses for a timed-out account), JSON mode emits the
in-band ``__APPLE_MAIL_MCP_ERROR__`` marker its script-level arm already uses.

``apple_mail_mcp.tools.inbox.run_applescript`` is the only Mail seam used.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.tools import inbox as inbox_tools
from apple_mail_mcp.tools.inbox.list_scripts import (
    _build_list_inbox_json_script,
    _build_list_inbox_text_script,
)
from apple_mail_mcp.tools.inbox.parsing import _strip_count_marker


def _run(coro):
    return asyncio.run(coro)


def _text_block(subject: str, sender: str) -> str:
    """One rendered text-mode email block, matching the real builder's shape."""
    return (
        f"__ROW__|||{subject}|||{sender}|||Date|||<id@example.com>|||false\n"
        f"✉ {subject}\n"
        f"   From: {sender}\n"
        "   Date: Date\n"
        "\n"
    )


class StripCountMarkerDiscrepancyTests(unittest.TestCase):
    """The two numbers were always on the wire; only the comparison was missing."""

    def test_short_render_is_reported(self):
        raw = _text_block("S1", "a@example.com") + "__COUNT__|||1|||3|||2\n"
        clean, count = _strip_count_marker(raw)

        self.assertEqual(count, 1, "the rendered count must stay honest, never inflated")
        self.assertIn("PARTIAL:", clean)
        self.assertIn("1 of 3", clean)
        self.assertIn("2 row(s) failed to read", clean)
        self.assertNotIn("__COUNT__", clean)

    def test_complete_render_reports_nothing(self):
        raw = _text_block("S1", "a@example.com") + "__COUNT__|||3|||3|||0\n"
        clean, count = _strip_count_marker(raw)

        self.assertEqual(count, 3)
        self.assertNotIn("PARTIAL", clean)

    def test_legacy_single_field_marker_cannot_invent_a_discrepancy(self):
        """A one-field marker carries no expectation; assume it was complete."""
        clean, count = _strip_count_marker("Line 1\n__COUNT__|||7\n")

        self.assertEqual(count, 7)
        self.assertNotIn("PARTIAL", clean)

    def test_failure_count_alone_is_enough_to_report(self):
        """Belt and suspenders: a counted throw reports even if the maths agree."""
        clean, _ = _strip_count_marker("__COUNT__|||2|||2|||1\n")
        self.assertIn("PARTIAL:", clean)

    def test_no_marker_stays_silent(self):
        clean, count = _strip_count_marker("Line 1\nLine 2")
        self.assertEqual(count, 0)
        self.assertNotIn("PARTIAL", clean)

    def test_empty_input_unchanged(self):
        self.assertEqual(_strip_count_marker(""), ("", 0))


class ListInboxTextPartialTests(unittest.TestCase):
    """End-to-end: the caller must be able to see the short list is short."""

    def _list(self, raw: str, **kwargs) -> str:
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            return _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=10,
                    include_draft_state=False,
                    **kwargs,
                )
            )

    def test_dropped_rows_surface_to_the_caller(self):
        raw = "".join(
            [
                "━━━━━━━━━━\n",
                "📧 ACCOUNT: Work (3 messages)\n",
                "━━━━━━━━━━\n\n",
                _text_block("S1", "a@example.com"),
                "__COUNT__|||1|||3|||2\n",
            ]
        )
        result = self._list(raw)

        self.assertIn("PARTIAL:", result)
        self.assertIn("1 of 3", result)
        # The honest rendered count, not the pre-drop message count.
        self.assertIn("TOTAL EMAILS: 1", result)

    def test_unread_ground_truth_call_reports_its_own_under_count(self):
        """`read_status="unread"` is the fallback authority; it must not lie."""
        raw = _text_block("S1", "a@example.com") + "__COUNT__|||1|||4|||3\n"
        result = self._list(raw, read_status="unread")

        self.assertIn("PARTIAL:", result)
        self.assertIn("1 of 4", result)

    def test_complete_list_carries_no_partial_banner(self):
        raw = _text_block("S1", "a@example.com") + _text_block("S2", "b@example.com") + "__COUNT__|||2|||2|||0\n"
        result = self._list(raw)

        self.assertNotIn("PARTIAL", result)
        self.assertIn("TOTAL EMAILS: 2", result)
        self.assertIn("S1", result)
        self.assertIn("S2", result)

    def test_partial_banner_survives_reply_state_annotation(self):
        """The annotator walks `__ROW__` blocks; it must not eat the banner."""
        raw = (
            "__ROW__|||S1|||a@example.com|||Date|||<id@example.com>|||true\n"
            "✉ S1\n"
            "   From: a@example.com\n"
            "   Date: Date\n"
            "\n"
            "__COUNT__|||1|||2|||1\n"
        )
        result = self._list(raw)

        self.assertIn("[REPLIED]", result)
        self.assertIn("PARTIAL:", result)


class ListInboxJsonPartialTests(unittest.TestCase):
    def test_per_message_failures_reach_the_errors_list(self):
        raw = "\n".join(
            [
                "S1|||a@example.com|||Date|||false|||Work|||101|||false",
                "__APPLE_MAIL_MCP_ERROR__|||Work|||read failed for 2 of 3 message(s); this list is incomplete",
            ]
        )
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=10,
                    output_format="json",
                    include_draft_state=False,
                )
            )

        self.assertEqual([row["message_id"] for row in response["emails"]], ["101"])
        self.assertEqual(
            response["errors"],
            ["Work: read failed for 2 of 3 message(s); this list is incomplete"],
        )

    def test_unreadable_message_id_drop_is_reported(self):
        """The parser drops an id-less row on purpose; the drop must be visible.

        A row whose ``id of aMessage`` read threw is still emitted, but
        ``_parse_pipe_delimited_emails`` refuses it (a non-numeric id could map
        a destructive op onto the wrong message). Without the counter that
        refusal was a second invisible row drop.
        """
        raw = "\n".join(
            [
                "S1|||a@example.com|||Date|||false|||Work|||101|||false",
                "S2|||b@example.com|||Date|||false|||Work||||||false",
                "__APPLE_MAIL_MCP_ERROR__|||Work|||message id unreadable for 1 of 2 message(s); "
                "those rows were dropped and this list is incomplete",
            ]
        )
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            response = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=10,
                    output_format="json",
                    include_draft_state=False,
                )
            )

        self.assertEqual([row["message_id"] for row in response["emails"]], ["101"])
        self.assertEqual(len(response["errors"]), 1)
        self.assertIn("message id unreadable for 1 of 2", response["errors"][0])


class ListInboxRowFailureScriptTests(unittest.TestCase):
    """Pin the emitted script: a counted throw, not a swallowed one."""

    def test_text_script_counts_row_failures_and_emits_both_numbers(self):
        script = _build_list_inbox_text_script("Work", 5, "unread", False, False)

        self.assertIn("set rowFailures to 0", script)
        self.assertIn("set rowFailures to rowFailures + 1", script)
        self.assertIn('"__COUNT__|||" & sentCount & "|||" & expectedCount & "|||" & rowFailures', script)

    def test_json_script_reports_row_failures_in_band(self):
        script = _build_list_inbox_json_script("Work", 5, "unread", False, False)

        self.assertIn("set rowFailures to 0", script)
        self.assertIn("set rowFailures to rowFailures + 1", script)
        self.assertIn('"__APPLE_MAIL_MCP_ERROR__|||Work|||read failed for "', script)

    def test_json_script_counts_unreadable_message_ids_separately(self):
        """An id read failure drops the row in Python; keep it its own counter."""
        script = _build_list_inbox_json_script("Work", 5, "all", False, False)

        self.assertIn("set idReadFailures to 0", script)
        self.assertIn("set idReadFailures to idReadFailures + 1", script)
        self.assertIn('"__APPLE_MAIL_MCP_ERROR__|||Work|||message id unreadable for "', script)

    def test_expected_count_is_clamped_to_max_emails(self):
        """A legitimately capped list must not be reported as a partial one."""
        for builder in (_build_list_inbox_text_script, _build_list_inbox_json_script):
            with self.subTest(builder=builder.__name__):
                script = builder("Work", 5, "all", False, False)
                self.assertIn("set expectedCount to messageCount", script)
                self.assertIn("if expectedCount > 5 then set expectedCount to 5", script)

    def test_per_message_try_is_not_bare_in_either_builder(self):
        """The specific regression: the inner try must keep its error arm."""
        for builder in (_build_list_inbox_text_script, _build_list_inbox_json_script):
            with self.subTest(builder=builder.__name__):
                script = builder("Work", 5, "all", False, False)
                loop_start = script.index("repeat with aMessage in inboxMessages")
                loop_end = script.index("end repeat", loop_start)
                self.assertIn("on error", script[loop_start:loop_end])


@unittest.skipIf(
    shutil.which("osacompile") is None,
    "osacompile not available — AppleScript compile check skipped on this platform",
)
class ListInboxRowFailureCompileTests(unittest.TestCase):
    """The counting arm is spliced AppleScript; prove it still parses."""

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
                f"osacompile rejected the {label} inbox script:\n{proc.stderr or proc.stdout}",
            )
        finally:
            for path in (src, out):
                with contextlib.suppress(OSError):
                    Path(path).unlink()

    def test_both_builders_compile_across_read_filters(self):
        for read_filter in ("all", "unread", "read"):
            for include_content in (False, True):
                for include_message_id in (False, True):
                    label = f"{read_filter} content={include_content} message_id={include_message_id}"
                    with self.subTest(label=label):
                        self._assert_compiles(
                            _build_list_inbox_text_script("Work", 5, read_filter, include_content, include_message_id),
                            f"text {label}",
                        )
                        self._assert_compiles(
                            _build_list_inbox_json_script("Work", 5, read_filter, include_content, include_message_id),
                            f"json {label}",
                        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
