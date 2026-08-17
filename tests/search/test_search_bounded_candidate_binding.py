"""Bounded-candidate-binding contract for the ``search_emails`` scan script.

AGENTIC-2355. ``_build_search_script`` binds its per-mailbox candidate set with
``messages 1 thru scanUpperBound of currentMailbox``. That slice raises when the
mailbox holds fewer messages than the cap — the ordinary case for small folders
and for most of a ``mailbox="All"`` fan-out — so the builder needs a recovery
arm. The historical arm was ``set candidateMessages to messages of
currentMailbox``, AppleScript's other spelling of ``every message of
currentMailbox``: it materializes the whole mailbox, and on a 24K+ Exchange
inbox that presents as a hang rather than a failure. It fired precisely when the
bounded slice had already gone wrong, so the fallback abandoned the bound exactly
when the bound mattered most.

Two invariants are locked here:

1.  No arm enumerates. The recovery arm re-slices against
    ``count of messages of currentMailbox`` (a cheap property read, not an
    enumeration) and gives up only if that also fails.
2.  Giving up is *visible*. The historical inner ``try ... end try`` had no
    ``on error`` arm at all, so a double failure left ``candidateMessages``
    empty, left ``scanReadFailures`` at 0 (the per-message loop never ran, so
    ``_SCAN_FAILURE_REPORT`` emitted nothing), and rendered as an authoritative
    ``FOUND: 0`` / ``has_more: false`` with no errors — indistinguishable from a
    genuinely empty mailbox. It now emits an ``ERROR_MAILBOX`` marker, which
    ``records._parse_search_records`` routes into ``error_details`` as a
    ``mailbox_error``.

An empty mailbox must stay silent: a count of 0 binds no slice and reports no
error, because "0 scanned because there are 0" is a true empty result.
"""

import asyncio
import json
import re
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import search as search_tools
from apple_mail_mcp.tools.search.script import _build_search_script

# Argument shapes that reach each of the three ``message_collection`` branches
# in ``_build_search_script`` (subject fast path, combined per-message filter,
# and the no-condition passthrough). Every branch splices the same
# ``bounded_candidate_script``, so each one must satisfy the invariants.
SCRIPT_VARIANTS = {
    "subject_fast_path": dict(subject_terms=["invoice"], read_status="all"),
    "subject_fast_path_with_date_floor": dict(subject_terms=["invoice"], read_status="all", date_from="2026-01-01"),
    "combined_filters": dict(
        subject_terms=None,
        sender="sender@example.com",
        has_attachments=True,
        read_status="unread",
        date_from="2026-01-01",
        date_to="2026-02-01",
    ),
    "body_text": dict(subject_terms=None, read_status="all", body_text="quarterly"),
    "no_conditions": dict(subject_terms=None, read_status="all"),
}

# The marker line the emitted recovery arm writes when even the clamped re-slice
# fails. Shaped exactly as `records._parse_search_records` receives it.
BIND_FAILURE_MARKER = (
    "ERROR_MAILBOX|||INBOX|||bounded candidate slice unavailable (Can't get message 1.); "
    "0 of 21 requested message(s) scanned, so this mailbox contributed no results"
)

_BUILDER_DEFAULTS = dict(
    account="Work",
    mailbox="INBOX",
    subject_terms=None,
    sender=None,
    has_attachments=None,
    read_status="all",
    date_from=None,
    date_to=None,
    include_content=False,
    content_length=0,
    offset=0,
    limit=20,
    body_text=None,
)


def _build(**overrides) -> str:
    script, _body_capped, _mailbox_capped = _build_search_script(**{**_BUILDER_DEFAULTS, **overrides})
    return script


def _run(coro):
    return asyncio.run(coro)


def _raw_enumeration_lines(script: str) -> list[str]:
    """Return emitted lines that enumerate a whole mailbox.

    Mirrors the spelling rule in
    ``tests/core/test_no_unbounded_whose.RAW_MESSAGES_ENUMERATION`` rather than
    importing it: that module statically scans *source files* and keys its
    ratchet by path, while this one inspects a *generated* script. Sharing the
    object would entangle two different failure messages for no gain, and the
    rule is one line.

    ``count of messages of MB`` is excluded because it is a cheap property read,
    not an enumeration — it is in fact the guard the fix relies on.
    """
    return [line.strip() for line in script.splitlines() if re.search(r"(?<!count of )\bmessages of \w+", line)]


def _candidate_bind_block(script: str) -> str:
    """Slice out the emitted candidate-binding block for structural checks.

    Runs from the ``matchingMessages`` reset that opens
    ``bounded_candidate_script`` to the ``scanReadFailures`` initializer that
    immediately follows it (``script._SCAN_FAILURE_INIT``), so the ``try`` /
    ``on error`` counts below cannot pick up the per-message loop's handlers or
    the enclosing per-mailbox handler.
    """
    start = script.index("set matchingMessages to {}")
    end = script.index("set scanReadFailures to 0", start)
    return script[start:end]


class BoundedCandidateBindingTests(unittest.TestCase):
    """Static assertions on the emitted AppleScript."""

    def test_success_path_still_binds_the_bounded_slice(self):
        """The fix must not disturb the path that already worked."""
        for name, overrides in SCRIPT_VARIANTS.items():
            with self.subTest(variant=name):
                script = _build(**overrides)
                self.assertIn(
                    "set candidateMessages to messages 1 thru scanUpperBound of currentMailbox",
                    script,
                    "The primary bind must stay a bounded newest-first slice.",
                )
                self.assertIn("set scanUpperBound to ", script)

    def test_no_arm_enumerates_the_whole_mailbox(self):
        for name, overrides in SCRIPT_VARIANTS.items():
            with self.subTest(variant=name):
                script = _build(**overrides)
                self.assertNotIn(
                    "set candidateMessages to messages of currentMailbox",
                    script,
                    "The unbounded fallback is back. `messages of MB` is identical to "
                    "`every message of MB` and hangs on a 24K+ mailbox.",
                )
                self.assertEqual(
                    _raw_enumeration_lines(script),
                    [],
                    "No emitted line may enumerate a mailbox.",
                )

    def test_no_arm_enumerates_for_mailbox_all_fan_out(self):
        """``mailbox="All"`` visits many small folders, so it hits the recovery arm most."""
        script = _build(mailbox="All", subject_terms=["invoice"], offset=5)
        self.assertEqual(_raw_enumeration_lines(script), [])
        self.assertIn("set candidateMessages to messages 1 thru scanUpperBound of currentMailbox", script)

    def test_recovery_arm_reslices_against_the_mailbox_count(self):
        script = _build(subject_terms=["invoice"])
        block = _candidate_bind_block(script)
        self.assertIn("set boundedSliceCount to count of messages of currentMailbox", block)
        self.assertIn("if boundedSliceCount > scanUpperBound then", block)
        self.assertIn("set boundedSliceCount to scanUpperBound", block)
        self.assertIn(
            "set candidateMessages to messages 1 thru boundedSliceCount of currentMailbox",
            block,
            "The recovery arm must bind a slice, clamped to whichever of the cap and the mailbox count is smaller.",
        )

    def test_recovery_arm_guards_the_empty_mailbox(self):
        """``messages 1 thru 0`` raises, and an empty mailbox is not a failure."""
        block = _candidate_bind_block(_build(subject_terms=["invoice"]))
        self.assertIn("if boundedSliceCount > 0 then", block)
        count_pos = block.index("set boundedSliceCount to count of messages of currentMailbox")
        guard_pos = block.index("if boundedSliceCount > 0 then")
        slice_pos = block.index("set candidateMessages to messages 1 thru boundedSliceCount of currentMailbox")
        self.assertLess(count_pos, guard_pos)
        self.assertLess(guard_pos, slice_pos)

    def test_bind_failure_emits_an_error_mailbox_diagnostic(self):
        for name, overrides in SCRIPT_VARIANTS.items():
            with self.subTest(variant=name):
                block = _candidate_bind_block(_build(**overrides))
                self.assertIn("on error candidateBindError", block)
                self.assertIn(
                    'set end of recordLines to "ERROR_MAILBOX|||" & mailboxName',
                    block,
                    "A bind that cannot be bounded must surface as a mailbox_error, not as an empty candidate set.",
                )
                # The marker must be the sanitized mailbox name, not the raw
                # `name of currentMailbox`: an unsanitized name can carry the
                # `|||` field delimiter and shift every parsed field.
                self.assertNotIn('"ERROR_MAILBOX|||" & (name of currentMailbox)', block)

    def test_no_candidate_bind_try_lacks_an_error_arm(self):
        """The silent-empty bug was a ``try ... end try`` with no ``on error``."""
        for name, overrides in SCRIPT_VARIANTS.items():
            with self.subTest(variant=name):
                block = _candidate_bind_block(_build(**overrides))
                opens = len(re.findall(r"^\s*try\s*$", block, flags=re.MULTILINE))
                closes = len(re.findall(r"^\s*end try\s*$", block, flags=re.MULTILINE))
                handlers = len(re.findall(r"^\s*on error\b", block, flags=re.MULTILINE))
                self.assertEqual(opens, closes, f"Unbalanced try/end try in:\n{block}")
                self.assertEqual(
                    opens,
                    handlers,
                    "Every `try` in the candidate-binding block needs an `on error` arm; a "
                    f"bare one turns a bind failure into a confident empty result.\n{block}",
                )

    def test_error_marker_precedes_the_end_of_the_recovery_arm(self):
        """Ordering: attempt the clamped slice first, emit the diagnostic only after."""
        block = _candidate_bind_block(_build(subject_terms=["invoice"]))
        slice_pos = block.index("set candidateMessages to messages 1 thru boundedSliceCount of currentMailbox")
        handler_pos = block.index("on error candidateBindError")
        marker_pos = block.index('set end of recordLines to "ERROR_MAILBOX|||" & mailboxName')
        self.assertLess(slice_pos, handler_pos)
        self.assertLess(handler_pos, marker_pos)


class BindFailureIsDistinguishableFromEmptyTests(unittest.TestCase):
    """End-to-end: the diagnostic reaches the caller, and only when earned."""

    def _search(self, payload: str, **overrides):
        """Run ``search_emails`` with the mailbox scan stubbed to return ``payload``.

        Only the search scan is fed ``payload``; the bounded Drafts snapshot that
        annotates reply state runs through the same patch and must not receive
        search rows.
        """

        def fake_run(script, timeout=120):
            return payload if "set matchingMessages to {}" in script else ""

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            return _run(
                search_tools.search_emails(
                    account="Work",
                    subject_keyword="invoice",
                    max_results=None,
                    include_draft_state=False,
                    **overrides,
                )
            )

    def test_bind_failure_surfaces_as_a_mailbox_error_in_json(self):
        response = json.loads(self._search(BIND_FAILURE_MARKER, output_format="json"))

        self.assertEqual(response["items"], [])
        self.assertEqual(response["returned"], 0)
        self.assertFalse(response["has_more"])
        details = response["error_details"]
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]["type"], "mailbox_error")
        self.assertEqual(details[0]["mailbox"], "INBOX")
        self.assertEqual(details[0]["account"], "Work")
        self.assertIn("bounded candidate slice unavailable", details[0]["message"])

    def test_bind_failure_surfaces_in_text_output_too(self):
        """Text is the default ``output_format``; a silent zero there is the bug."""
        text = self._search(BIND_FAILURE_MARKER)

        self.assertIn("FOUND: 0", text)
        self.assertIn("PARTIAL", text)
        self.assertIn("mailbox issue", text)
        self.assertIn("bounded candidate slice unavailable", text)

    def test_genuinely_empty_mailbox_reports_a_clean_zero(self):
        """The diagnostic must not fire on a real empty result, or it means nothing."""
        response = json.loads(self._search("", output_format="json"))
        text = self._search("")

        self.assertEqual(response["items"], [])
        self.assertNotIn("error_details", response)
        self.assertNotIn("errors", response)
        self.assertIn("FOUND: 0", text)
        self.assertNotIn("PARTIAL", text)


if __name__ == "__main__":
    unittest.main()
