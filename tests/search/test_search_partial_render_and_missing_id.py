"""Regression tests: a truncated thread and an unreadable id must say so.

Two shipped defects, both of the same family as
``test_search_error_channels.py`` — a failure that renders as a clean, wrong
answer:

* ``get_email_thread`` printed ``FOUND N`` and then dropped rows against that
  count inside a bare AppleScript ``try``. JSON mode carried no count at all,
  so a JSON caller received a short thread with no signal that anything was
  lost, and a conversation could be summarized from an incomplete thread.
* ``get_email_by_id`` answered a nonexistent id with a bare ``{"item": null}``
  and answered a read that threw with a bare, unparseable error string, so
  "that id is not in this mailbox" and "that id could not be read" were
  indistinguishable.

Every test has a mirror-image partner asserting the healthy case still reports
clean, since turning an ordinary complete thread or a genuinely absent id into
an error would be its own regression.

All Mail I/O is mocked at ``tools.search.run_applescript``; the subprocess
layer is poisoned so an accidental live ``osascript`` call fails loudly. The
one exception is the offline ``osacompile`` parse check, which compiles a
captured script from a temp file and never touches Mail.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import search as search_tools


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


_OSACOMPILE = shutil.which("osacompile")


class _NoLiveMailTestCase(unittest.TestCase):
    """Poison the subprocess layer: no test here may reach a real mailbox.

    ``osacompile`` is deliberately still allowed (it only parses a temp file);
    only ``osascript``, the one path that drives Mail.app, fails loudly.
    """

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


class ThreadRenderTruncationTests(_NoLiveMailTestCase):
    """``FOUND N`` was never carried into JSON, so truncation was invisible."""

    def _thread_json(self, raw):
        with patch("apple_mail_mcp.tools.search.run_applescript", return_value=raw):
            result = search_tools.get_email_thread(
                account="Work",
                subject_keyword="Budget Review",
                recent_days=7,
                output_format="json",
                include_draft_state=False,
            )
        return json.loads(result)

    def test_json_carries_the_found_count_beside_what_was_rendered(self):
        payload = self._thread_json(f"THREAD_STRATEGY|||subject|||2\n{_row('401')}\n{_row('402')}")
        self.assertEqual(payload["matched"], 2)
        self.assertEqual(payload["returned"], 2)
        self.assertFalse(payload["render_incomplete"])

    def test_complete_thread_reports_no_shortfall(self):
        """Mirror image: matched == returned must stay error-free."""
        payload = self._thread_json(f"THREAD_STRATEGY|||subject|||1\n{_row('401')}")
        self.assertFalse(payload["render_incomplete"])
        self.assertNotIn("errors", payload)
        self.assertNotIn("error_details", payload)

    def test_unattributed_shortfall_is_reported_not_silently_truncated(self):
        """3 matched, 1 row back, script blamed nobody: still must not read clean."""
        payload = self._thread_json(f"THREAD_STRATEGY|||subject|||3\n{_row('401')}")
        self.assertEqual(payload["matched"], 3)
        self.assertEqual(payload["returned"], 1)
        self.assertTrue(payload["render_incomplete"])
        self.assertEqual(len(payload["errors"]), 1)
        self.assertIn("returned 1 of 3", payload["errors"][0])
        self.assertEqual(payload["error_details"][0]["type"], "render_mismatch")

    def test_attributed_render_failure_carries_the_script_message(self):
        """The render loop's own ``on error`` arm names the loss it counted."""
        raw = "\n".join(
            [
                "THREAD_STRATEGY|||subject|||3",
                _row("401"),
                "ERROR_MAILBOX|||INBOX|||render failed for 2 of 3 thread message(s); results are incomplete",
            ]
        )
        payload = self._thread_json(raw)
        self.assertEqual(payload["matched"], 3)
        self.assertEqual(payload["returned"], 1)
        self.assertTrue(payload["render_incomplete"])
        self.assertEqual(
            payload["errors"], ["INBOX: render failed for 2 of 3 thread message(s); results are incomplete"]
        )
        self.assertEqual(payload["error_details"][0]["type"], "mailbox_error")

    def test_empty_thread_is_still_empty_not_incomplete(self):
        payload = self._thread_json("THREAD_STRATEGY|||subject|||0\n")
        self.assertEqual(payload["matched"], 0)
        self.assertEqual(payload["returned"], 0)
        self.assertFalse(payload["render_incomplete"])
        self.assertNotIn("errors", payload)

    def test_header_without_a_count_falls_back_to_the_rendered_count(self):
        """Backward compatible with the pre-fix two-field header."""
        payload = self._thread_json(f"THREAD_STRATEGY|||subject\n{_row('401')}")
        self.assertEqual(payload["matched"], 1)
        self.assertFalse(payload["render_incomplete"])
        self.assertNotIn("errors", payload)

    def test_script_error_does_not_fabricate_a_shortfall(self):
        payload = self._thread_json('Error: Can\'t get mailbox "INBOX" of account "Work"')
        self.assertEqual(payload["matched"], 0)
        self.assertFalse(payload["render_incomplete"])
        self.assertIn("Can't get mailbox", payload["error"])


class ThreadRenderFailureScriptTests(_NoLiveMailTestCase):
    """Producer side: the render loop must own an observable error arm."""

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

    def test_render_loop_counts_failures_instead_of_swallowing_them(self):
        script = self._captured_script()
        self.assertIn("set threadRenderFailures to threadRenderFailures + 1", script)
        self.assertIn("if threadRenderFailures > 0 then", script)
        self.assertIn('"ERROR_MAILBOX|||INBOX|||" & threadRenderLoss', script)
        self.assertIn('"PARTIAL: " & threadRenderLoss', script)

    def test_script_carries_the_matched_count_in_the_json_header(self):
        script = self._captured_script()
        self.assertIn('"THREAD_STRATEGY|||" & selectedStrategy & "|||" & (threadMatchedCount as string)', script)

    def test_counters_are_initialized_before_the_scan(self):
        script = self._captured_script()
        self.assertIn("set threadMatchedCount to 0", script)
        self.assertIn("set threadRenderFailures to 0", script)

    def test_multi_mailbox_scope_is_escaped_into_the_marker_row(self):
        script = self._captured_script(mailboxes=['Weird"Name', "Archive"])
        self.assertIn('ERROR_MAILBOX|||Weird\\"Name, Archive|||', script)

    @unittest.skipUnless(_OSACOMPILE, "osacompile not available (non-macOS CI)")
    def test_thread_script_still_compiles(self):
        """The new ``on error`` arm and report block must stay valid AppleScript."""
        for variant in ({}, {"mailboxes": ["Archive", "Sent"]}, {"include_preview": False}):
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


class GetEmailByIdAbsentVersusThrewTests(_NoLiveMailTestCase):
    """The singular tool conflated "not here" with "could not be read"."""

    def _by_id(self, raw=None, exc=None, output_format="json"):
        kwargs = {"side_effect": exc} if exc is not None else {"return_value": raw}
        with patch("apple_mail_mcp.tools.search.run_applescript", **kwargs):
            return search_tools.get_email_by_id(
                account="Work",
                message_id="999",
                output_format=output_format,
                include_draft_state=False,
            )

    def test_absent_id_is_missing_with_no_error(self):
        """Mirror image: the id simply is not in the mailbox."""
        payload = json.loads(self._by_id(raw=""))
        self.assertIsNone(payload["item"])
        self.assertFalse(payload["found"])
        self.assertEqual(payload["missing_ids"], ["999"])
        self.assertEqual(payload["errors"], [])
        self.assertNotIn("error_details", payload)

    def test_failed_read_is_json_and_reported(self):
        payload = json.loads(self._by_id(raw="ERROR|||Can't get message id 999"))
        self.assertIsNone(payload["item"])
        self.assertFalse(payload["found"])
        self.assertEqual(payload["missing_ids"], ["999"])
        self.assertEqual(payload["errors"], ["INBOX: Can't get message id 999"])
        self.assertEqual(payload["error_details"][0]["type"], "read_error")
        # Singular ``error`` is what the CLI keys its non-zero exit off; a
        # whole-result failure must not degrade into an exit-0 partial.
        self.assertEqual(payload["error"], "Can't get message id 999")

    def test_absent_id_carries_no_singular_error_key(self):
        """Mirror image: "not here" is not a failure and must stay exit-0."""
        self.assertNotIn("error", json.loads(self._by_id(raw="")))

    def test_timeout_is_json_and_typed(self):
        payload = json.loads(self._by_id(exc=AppleScriptTimeout("timed out")))
        self.assertIsNone(payload["item"])
        self.assertEqual(payload["missing_ids"], ["999"])
        self.assertEqual(payload["error_details"][0]["type"], "timeout")
        self.assertIn("timed out", payload["errors"][0])

    def test_absent_and_threw_are_distinguishable(self):
        absent = json.loads(self._by_id(raw=""))
        threw = json.loads(self._by_id(raw="ERROR|||boom"))
        self.assertEqual(absent["item"], threw["item"])
        self.assertNotEqual(absent["errors"], threw["errors"])

    def test_found_item_keeps_the_existing_shape(self):
        payload = json.loads(self._by_id(raw=_row("999")))
        self.assertEqual(payload["item"]["message_id"], "999")
        self.assertTrue(payload["found"])
        self.assertEqual(payload["missing_ids"], [])
        self.assertEqual(payload["errors"], [])
        self.assertIn("draft_scan", payload)

    def test_text_mode_not_found_message_is_unchanged(self):
        result = self._by_id(raw="", output_format="text")
        self.assertEqual(result, "Error: No email found for message_id=999 in INBOX")

    def test_text_mode_read_error_is_unchanged(self):
        result = self._by_id(raw="ERROR|||Can't get message id 999", output_format="text")
        self.assertEqual(result, "Error: Can't get message id 999")

    def test_text_mode_timeout_is_unchanged(self):
        result = self._by_id(exc=AppleScriptTimeout("boom"), output_format="text")
        self.assertTrue(result.startswith("Error: AppleScript timed out while fetching message_id=999"))


if __name__ == "__main__":
    unittest.main()
