"""Regression tests: a thread candidate read that threw must be counted.

Sibling of ``test_search_partial_render_and_missing_id.py``, which fixed the
*render* loop. This file covers the loop before it.

``get_email_thread`` collects candidates in a per-mailbox ``try`` wrapping a
per-message ``try``. Both were bare. A message that threw while being *matched*
never entered ``threadMessages``, so it was never counted in ``FOUND N`` —
which means the matched-vs-returned reconciliation cannot see it: ``matched``
and ``returned`` are consistently wrong *together*. The caller got a thread
short by however many candidate reads failed, with ``render_incomplete: false``
and a clean success banner, and a conversation could be summarized from it.

The two losses therefore have to stay distinguishable, because they have
different causes and different consequences:

* ``candidate_scan_error`` — lost before matching; invisible to
  ``matched``/``returned``; the thread may be missing messages outright.
* ``mailbox_error`` (render) — matched and counted, then failed to render; it
  *is* the gap between ``matched`` and ``returned``.

Also pinned here: the ``SCAN_CEILING`` trap. ``records._mailbox_error_texts``
deliberately drops ceiling rows (a saturated scan is a bound, not a failure),
so keying the error branch off the raw ``mailbox_errors`` list would build an
empty ``errors`` list and suppress the render reconciliation entirely.

All Mail I/O is mocked at ``tools.search.run_applescript`` and the subprocess
layer is poisoned, so no test here can reach a real mailbox. The one exception
is the offline ``osacompile`` parse check, which compiles a captured script
from a temp file and never touches Mail.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.tools import search as search_tools

_OSACOMPILE = shutil.which("osacompile")

_CANDIDATE_ROW = (
    "ERROR_MAILBOX|||INBOX|||candidate scan failed for 3 of 40 scanned message(s) before thread "
    "matching; those messages were never counted in FOUND, so this thread may be missing messages"
)
_MAILBOX_ROW = (
    "ERROR_MAILBOX|||INBOX|||candidate scan failed for 2 mailbox(es) before thread matching; "
    "those mailboxes contributed no thread messages"
)
_RENDER_ROW = "ERROR_MAILBOX|||INBOX|||render failed for 2 of 3 thread message(s); results are incomplete"
_CEILING_ROW = "SCAN_CEILING|||INBOX|||50"


def _row(message_id: str, subject: str = "Re: Budget Review") -> str:
    """One synthetic pipe-delimited thread row (public repo: no real mail)."""
    return "|||".join(
        [
            message_id,
            f"<thread-{message_id}@example.com>",
            subject,
            "sender@example.com",
            "INBOX",
            "Work",
            "false",
            "2026-03-07T10:00:00",
            "",
        ]
    )


class _NoLiveMailTestCase(unittest.TestCase):
    """Poison the subprocess layer: no test here may reach a real mailbox."""

    def setUp(self):
        real_run = subprocess.run

        def guarded(*args, **kwargs):
            argv = args[0] if args else kwargs.get("args")
            if isinstance(argv, (list, tuple)) and argv and "osascript" in str(argv[0]):
                raise AssertionError("test attempted a live osascript call")
            return real_run(*args, **kwargs)

        patcher = patch("apple_mail_mcp.core.applescript.subprocess.run", side_effect=guarded)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _thread(self, raw, output_format="json", **kwargs):
        with patch("apple_mail_mcp.tools.search.run_applescript", return_value=raw):
            return search_tools.get_email_thread(
                account="Work",
                subject_keyword="Budget Review",
                recent_days=7,
                output_format=output_format,
                include_draft_state=False,
                **kwargs,
            )

    def _thread_json(self, raw, **kwargs):
        return json.loads(self._thread(raw, **kwargs))


class ThreadCandidateFailureScriptTests(_NoLiveMailTestCase):
    """Producer side: both candidate ``try`` blocks must own an error arm."""

    def _captured_script(self, **kwargs):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "THREAD_STRATEGY|||subject|||0\n"

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            search_tools.get_email_thread(
                account="Work",
                subject_keyword="Budget Review",
                recent_days=7,
                output_format="json",
                include_draft_state=False,
                **kwargs,
            )
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_counters_are_initialized_before_the_scan(self):
        script = self._captured_script()
        for line in (
            "set threadCandidateScanned to 0",
            "set threadCandidateFailures to 0",
            "set threadMailboxFailures to 0",
        ):
            self.assertIn(line, script)

    def test_per_message_candidate_try_counts_instead_of_swallowing(self):
        script = self._captured_script()
        self.assertIn("set threadCandidateFailures to threadCandidateFailures + 1", script)

    def test_per_mailbox_candidate_try_counts_instead_of_swallowing(self):
        script = self._captured_script()
        self.assertIn("set threadMailboxFailures to threadMailboxFailures + 1", script)

    def test_scanned_denominator_accumulates_across_mailboxes(self):
        script = self._captured_script(mailboxes=["Archive", "Sent"])
        self.assertIn(
            "set threadCandidateScanned to threadCandidateScanned + (count of candidateMessages)",
            script,
        )

    def test_report_emits_both_json_and_text_channels(self):
        script = self._captured_script()
        self.assertIn('"ERROR_MAILBOX|||INBOX|||" & threadCandidateLoss', script)
        self.assertIn('"PARTIAL: " & threadCandidateLoss', script)
        self.assertIn('"ERROR_MAILBOX|||INBOX|||" & threadMailboxLoss', script)
        self.assertIn('"PARTIAL: " & threadMailboxLoss', script)

    def test_report_runs_before_the_found_banner(self):
        """The caveat has to precede the count it undermines."""
        script = self._captured_script()
        self.assertLess(
            script.index("if threadCandidateFailures > 0 then"),
            script.index("FOUND "),
        )

    def test_candidate_message_is_worded_apart_from_the_render_message(self):
        script = self._captured_script()
        self.assertIn('"candidate scan failed for "', script)
        self.assertIn('"render failed for "', script)

    def test_multi_mailbox_scope_is_escaped_into_the_marker_row(self):
        script = self._captured_script(mailboxes=['Weird"Name', "Archive"])
        self.assertIn('"ERROR_MAILBOX|||Weird\\"Name, Archive|||" & threadCandidateLoss', script)

    @unittest.skipUnless(_OSACOMPILE, "osacompile not available (non-macOS CI)")
    def test_thread_script_still_compiles(self):
        """The two new ``on error`` arms and the report block must parse."""
        for variant in ({}, {"mailboxes": ["Archive", "Sent"]}, {"include_preview": False}, {"mailbox": "All"}):
            with self.subTest(variant=variant):
                script = self._captured_script(**variant)
                with tempfile.TemporaryDirectory() as tmp:
                    source = Path(tmp) / "thread.applescript"
                    source.write_text(script, encoding="utf-8")
                    proc = subprocess.run(
                        ["osacompile", "-o", "/dev/null", str(source)],
                        capture_output=True,
                        check=False,
                    )
                self.assertEqual(
                    proc.returncode,
                    0,
                    f"osacompile rejected the thread script:\n{proc.stderr.decode('utf-8', 'replace')}",
                )


class ThreadCandidateFailureReportingTests(_NoLiveMailTestCase):
    """Consumer side: the loss must reach the caller in JSON and in text."""

    def test_candidate_failure_is_reported_although_counts_reconcile(self):
        """The whole point: matched == returned and the thread is still short."""
        payload = self._thread_json(f"THREAD_STRATEGY|||subject|||1\n{_row('401')}\n{_CANDIDATE_ROW}")
        self.assertEqual(payload["matched"], 1)
        self.assertEqual(payload["returned"], 1)
        self.assertFalse(payload["render_incomplete"])
        self.assertTrue(payload["candidate_scan_incomplete"])
        self.assertEqual(len(payload["errors"]), 1)
        self.assertIn("candidate scan failed for 3 of 40", payload["errors"][0])
        self.assertEqual(payload["error_details"][0]["type"], "candidate_scan_error")

    def test_per_mailbox_candidate_failure_is_reported_too(self):
        payload = self._thread_json(f"THREAD_STRATEGY|||subject|||1\n{_row('401')}\n{_MAILBOX_ROW}")
        self.assertTrue(payload["candidate_scan_incomplete"])
        self.assertEqual(payload["error_details"][0]["type"], "candidate_scan_error")
        self.assertIn("2 mailbox(es)", payload["errors"][0])

    def test_candidate_and_render_failures_stay_distinguishable(self):
        raw = "\n".join(["THREAD_STRATEGY|||subject|||3", _row("401"), _CANDIDATE_ROW, _RENDER_ROW])
        payload = self._thread_json(raw)
        types = [detail["type"] for detail in payload["error_details"]]
        self.assertEqual(types, ["candidate_scan_error", "mailbox_error"])
        self.assertTrue(payload["candidate_scan_incomplete"])
        self.assertTrue(payload["render_incomplete"])

    def test_render_failure_alone_is_not_typed_as_a_candidate_failure(self):
        """Mirror image: the already-fixed defect must keep its own shape."""
        payload = self._thread_json(f"THREAD_STRATEGY|||subject|||3\n{_row('401')}\n{_RENDER_ROW}")
        self.assertFalse(payload["candidate_scan_incomplete"])
        self.assertTrue(payload["render_incomplete"])
        self.assertEqual(payload["error_details"][0]["type"], "mailbox_error")

    def test_healthy_thread_stays_silent(self):
        """Mirror image: no spurious rows on an ordinary complete thread."""
        payload = self._thread_json(f"THREAD_STRATEGY|||subject|||2\n{_row('401')}\n{_row('402')}")
        self.assertFalse(payload["candidate_scan_incomplete"])
        self.assertFalse(payload["render_incomplete"])
        self.assertNotIn("errors", payload)
        self.assertNotIn("error_details", payload)

    def test_empty_thread_is_still_empty_not_incomplete(self):
        payload = self._thread_json("THREAD_STRATEGY|||subject|||0\n")
        self.assertFalse(payload["candidate_scan_incomplete"])
        self.assertNotIn("errors", payload)

    def test_text_mode_carries_the_partial_line_through(self):
        raw = "EMAIL THREAD VIEW\n\nPARTIAL: candidate scan failed for 3 of 40 scanned message(s)\nFOUND 1"
        self.assertIn("PARTIAL: candidate scan failed for 3 of 40", self._thread(raw, output_format="text"))


class ThreadScanCeilingTrapTests(_NoLiveMailTestCase):
    """A ceiling marker is a bound, not a failure, and must not eat the reconciliation."""

    def test_ceiling_only_rows_do_not_suppress_the_render_mismatch(self):
        """The latent trap: `if mailbox_errors:` would build an EMPTY errors list."""
        payload = self._thread_json(f"THREAD_STRATEGY|||subject|||3\n{_row('401')}\n{_CEILING_ROW}")
        self.assertTrue(payload["render_incomplete"])
        self.assertEqual(len(payload["errors"]), 1)
        self.assertIn("returned 1 of 3", payload["errors"][0])
        self.assertEqual(payload["error_details"][0]["type"], "render_mismatch")

    def test_ceiling_only_rows_add_no_errors_when_nothing_was_lost(self):
        payload = self._thread_json(f"THREAD_STRATEGY|||subject|||1\n{_row('401')}\n{_CEILING_ROW}")
        self.assertFalse(payload["render_incomplete"])
        self.assertFalse(payload["candidate_scan_incomplete"])
        self.assertNotIn("errors", payload)
        self.assertNotIn("error_details", payload)

    def test_a_real_failure_beside_a_ceiling_row_reports_only_the_failure(self):
        raw = "\n".join(["THREAD_STRATEGY|||subject|||1", _row("401"), _CEILING_ROW, _CANDIDATE_ROW])
        payload = self._thread_json(raw)
        self.assertEqual(len(payload["errors"]), 1)
        self.assertEqual([detail["type"] for detail in payload["error_details"]], ["candidate_scan_error"])


if __name__ == "__main__":
    unittest.main()
