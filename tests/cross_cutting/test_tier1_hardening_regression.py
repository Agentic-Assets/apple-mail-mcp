"""Tier-1 hardening regression tests (2026-05-27).

Covers two crash/data-loss vectors uncovered by the post-Gmail-fix audit:

1. ``build_whose_id_list`` accepting unbounded message_ids → Mail's
   AppleScript parser rejects/hangs on `id is X or id is Y or ...`
   chains beyond ~200-500 terms. Now hard-capped at
   ``MAX_WHOSE_IDS`` (50).

2. ``_parse_pipe_delimited_emails`` mis-mapping fields when a subject
   contains ``|||``. AppleScript builders now sanitize subject/sender
   before pipe-join via ``sanitize_pipe_delimited_field``; the parser
   additionally validates ``message_id`` is a digit string to drop any
   row whose ||| sanitization slipped.
"""

from __future__ import annotations

import unittest

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.bounded_scan import (
    MAX_WHOSE_IDS,
    build_whose_id_list,
    iter_id_chunks,
)
from apple_mail_mcp.core import sanitize_pipe_delimited_field


# ---------------------------------------------------------------------------
# 1. build_whose_id_list cap
# ---------------------------------------------------------------------------


class BuildWhoseIdListCapTests(unittest.TestCase):
    def test_accepts_list_at_cap(self):
        ids = [str(i) for i in range(1, MAX_WHOSE_IDS + 1)]
        snippet = build_whose_id_list(ids)
        # MAX_WHOSE_IDS terms → MAX_WHOSE_IDS - 1 ` or ` separators.
        self.assertEqual(snippet.count(" or "), MAX_WHOSE_IDS - 1)
        self.assertTrue(snippet.startswith("id is 1 or"))

    def test_rejects_list_over_cap(self):
        ids = [str(i) for i in range(1, MAX_WHOSE_IDS + 2)]
        with self.assertRaises(ToolError) as ctx:
            build_whose_id_list(ids)
        self.assertEqual(ctx.exception.code, "WHOSE_ID_LIST_TOO_LARGE")
        # Remediation must point at the chunking helper.
        self.assertIn(
            "iter_id_chunks",
            ctx.exception.to_dict().get("remediation", {}).get("helper", ""),
        )

    def test_iter_id_chunks_splits_evenly(self):
        ids = [str(i) for i in range(1, 121)]  # 120 ids, 50-cap → 50/50/20
        chunks = list(iter_id_chunks(ids))
        self.assertEqual([len(c) for c in chunks], [50, 50, 20])
        # Each chunk should be safe to pass back through build_whose_id_list.
        for chunk in chunks:
            snippet = build_whose_id_list(chunk)
            self.assertIn("id is", snippet)

    def test_iter_id_chunks_rejects_oversized_chunk(self):
        with self.assertRaises(ToolError):
            list(iter_id_chunks(["1", "2"], chunk_size=MAX_WHOSE_IDS + 1))


# ---------------------------------------------------------------------------
# 2. ||| parser corruption guard
# ---------------------------------------------------------------------------


class PipeDelimiterSanitizerTests(unittest.TestCase):
    """Snippet-shape contract for the AppleScript sanitizer.

    The sanitizer emits in-place replacement of `|||` and embedded
    newlines on the named AppleScript variable. Test the snippet text
    rather than running AppleScript live.
    """

    def test_emits_replace_for_pipe_trio(self):
        snippet = sanitize_pipe_delimited_field("messageSubject")
        self.assertIn('set AppleScript\'s text item delimiters to "|||"', snippet)
        self.assertIn('set AppleScript\'s text item delimiters to "| | |"', snippet)
        self.assertIn("text items of messageSubject", snippet)
        self.assertIn("set messageSubject to _amm_parts as string", snippet)

    def test_emits_newline_collapse(self):
        snippet = sanitize_pipe_delimited_field("messageSender")
        self.assertIn(
            "set AppleScript's text item delimiters to {return, linefeed, tab}",
            snippet,
        )

    def test_wrapped_in_try(self):
        # The sanitizer must not throw on Mail-returned nulls.
        snippet = sanitize_pipe_delimited_field("X")
        self.assertIn("try", snippet)
        self.assertIn("end try", snippet)


class ParserFieldCountValidationTests(unittest.TestCase):
    """``_parse_pipe_delimited_emails`` must drop rows where the field
    shifted-right corruption would put a non-numeric value into the
    ``mail_app_id`` slot.
    """

    def setUp(self):
        from apple_mail_mcp.tools.inbox import _parse_pipe_delimited_emails
        self.parse = _parse_pipe_delimited_emails

    def test_well_formed_row(self):
        raw = "subj|||from@ex.com|||2026-05-27|||false|||Work|||12345"
        out = self.parse(raw)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["message_id"], "12345")

    def test_corrupted_row_with_pipe_in_subject_is_dropped(self):
        # Simulates the failure mode: a subject `a|||b` slipped past the
        # AppleScript sanitizer (e.g. handler crashed). The fields shift
        # right and the position-5 slot lands on `Work` (not a digit).
        # The parser must DROP this row, not silently mis-map.
        raw = "a|||b|||from@ex.com|||2026-05-27|||false|||Work|||12345"
        out = self.parse(raw)
        self.assertEqual(
            out, [],
            "Corrupted row with non-numeric mail_app_id slot must be dropped",
        )

    def test_empty_mail_id_dropped(self):
        raw = "subj|||from|||2026-05-27|||false|||Work|||"
        self.assertEqual(self.parse(raw), [])

    def test_well_formed_with_internet_message_id(self):
        raw = "subj|||from|||2026-05-27|||false|||Work|||12345|||<x@y>"
        out = self.parse(raw, has_message_id=True)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["internet_message_id"], "<x@y>")

    def test_corrupted_row_with_message_id_path_dropped(self):
        raw = "a|||b|||from|||2026-05-27|||false|||Work|||12345|||<x@y>"
        out = self.parse(raw, has_message_id=True)
        # Field 5 (mail_app_id slot) is `Work` — not numeric → drop.
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# 3. End-to-end: inbox script builders include the sanitizer
# ---------------------------------------------------------------------------


class InboxScriptBuilderEmitsSanitizerTests(unittest.TestCase):
    """The text and JSON script builders must call the sanitizer for
    subject and sender on every emitted row.
    """

    def test_text_script_sanitizes_subject_and_sender(self):
        from apple_mail_mcp.tools.inbox import _build_list_inbox_text_script
        script = _build_list_inbox_text_script(
            account="Work", max_emails=5, read_filter="all",
            include_content=False,
        )
        # Sanitizer emits the distinctive delimiter swap.
        self.assertIn(
            'set AppleScript\'s text item delimiters to "|||"', script,
        )
        self.assertIn(
            'set AppleScript\'s text item delimiters to "| | |"', script,
        )

    def test_json_script_sanitizes_subject_and_sender(self):
        from apple_mail_mcp.tools.inbox import _build_list_inbox_json_script
        script = _build_list_inbox_json_script(
            account="Work", max_emails=5, read_filter="all",
        )
        self.assertIn(
            'set AppleScript\'s text item delimiters to "|||"', script,
        )
        self.assertIn(
            'set AppleScript\'s text item delimiters to "| | |"', script,
        )


if __name__ == "__main__":
    unittest.main()
