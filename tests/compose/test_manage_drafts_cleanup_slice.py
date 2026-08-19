"""``manage_drafts(action="cleanup_empty")`` bounds and fail-closed contract.

Two defects, both of the same swallowed-error class: the first turned every
cleanup into ``Invalid index.``, the second permanently deleted a non-empty
draft when its body read threw.

A read-only live probe across four Mail backends (local, Exchange, Gmail IMAP,
iCloud) established the platform behavior this module locks in:

* An out-of-range upper bound in ``messages 1 thru N of <mailbox>`` **raises**
  AppleScript error -1719 (``Invalid index.``). It does **not** clamp. Verified
  at ``N = count + 1`` and ``N = count + 5``.
* On an empty mailbox every slice form raises -1719, including
  ``messages 1 thru 1``.
* ``count of messages`` can read stale and too high, so clamping to the count
  is necessary but not sufficient; the slice itself also needs a guard.

``cleanup_empty`` previously emitted a fixed ``messages 1 thru 75 of
draftsMailbox``, so it raised ``Invalid index.`` on every Drafts mailbox holding
fewer than 75 messages — that is, on essentially every real Drafts folder. Its
siblings (``drafts_scripts.py``, ``core/reply_state.py``) already clamp to the
live count with a zero guard; these tests keep ``cleanup_empty`` in line.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools.compose.constants import DRAFT_LIST_CAP


def _capture_cleanup_script(**kwargs: object) -> str:
    """Run ``cleanup_empty`` with AppleScript mocked and return the one script."""
    captured: list[str] = []

    def fake_run(script: str, timeout: int = 120) -> str:
        captured.append(script)
        return "DRAFT CLEANUP - Work (PREVIEW (dry run))"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        compose_tools.manage_drafts(account="Work", action="cleanup_empty", **kwargs)  # type: ignore[arg-type]

    assert len(captured) == 1, f"expected exactly one AppleScript call, got {len(captured)}"
    return captured[0]


class ManageDraftsCleanupSliceBoundsTests(unittest.TestCase):
    """cleanup_empty must clamp its Drafts window to the live message count."""

    def test_cleanup_empty_does_not_emit_a_fixed_unguarded_slice(self):
        """A hardcoded upper bound raises -1719 whenever Drafts holds < 75."""
        script = _capture_cleanup_script()

        self.assertNotIn(
            f"messages 1 thru {DRAFT_LIST_CAP} of draftsMailbox",
            script,
            "cleanup_empty must not slice Drafts with a fixed upper bound; an "
            "out-of-range bound raises AppleScript -1719 instead of clamping.",
        )

    def test_cleanup_empty_clamps_the_slice_to_the_live_draft_count(self):
        """Match the sibling clamp shape in drafts_scripts.py / reply_state.py."""
        script = _capture_cleanup_script()

        self.assertIn("set totalDrafts to count of messages of draftsMailbox", script)
        self.assertIn(f"if headEnd > {DRAFT_LIST_CAP} then set headEnd to {DRAFT_LIST_CAP}", script)
        self.assertIn("messages 1 thru headEnd of draftsMailbox", script)

    def test_cleanup_empty_binds_an_empty_list_when_drafts_is_empty(self):
        """On an empty mailbox even `messages 1 thru 1` raises, so never slice."""
        script = _capture_cleanup_script()

        self.assertIn("if totalDrafts is 0 then", script)

        zero_guard = script.index("if totalDrafts is 0 then")
        slice_index = script.index("messages 1 thru headEnd of draftsMailbox")
        self.assertLess(
            zero_guard,
            slice_index,
            "the zero-draft guard must precede the slice so an empty Drafts "
            "mailbox never reaches `messages 1 thru ...`",
        )

        empty_binding = script.index("set draftMessages to {}")
        self.assertLess(
            zero_guard,
            empty_binding,
            "the zero-draft branch must bind draftMessages to an empty list",
        )
        self.assertLess(
            empty_binding,
            slice_index,
            "the empty-list binding belongs in the zero-draft branch, before the slice",
        )

    def test_cleanup_empty_guards_the_slice_against_a_stale_high_count(self):
        """`count of messages` can read stale-high; the slice keeps its own try."""
        script = _capture_cleanup_script()

        slice_index = script.index("messages 1 thru headEnd of draftsMailbox")
        preceding = script[:slice_index]
        guard_index = preceding.rindex("try")
        between = preceding[guard_index:]
        self.assertNotIn(
            "end try",
            between,
            "the Drafts slice must sit inside its own `try` block so a stale-high "
            "`count of messages` degrades to an empty window instead of failing "
            "the whole cleanup with `Invalid index.`",
        )

    def test_cleanup_empty_slice_shape_is_identical_for_the_delete_path(self):
        """dry_run=False takes the same bounded window as the preview path."""
        preview = _capture_cleanup_script()
        deleting = _capture_cleanup_script(dry_run=False)

        for script in (preview, deleting):
            self.assertIn("messages 1 thru headEnd of draftsMailbox", script)
            self.assertNotIn(f"messages 1 thru {DRAFT_LIST_CAP} of draftsMailbox", script)
        self.assertIn("set isDryRun to true", preview)
        self.assertIn("set isDryRun to false", deleting)


class ManageDraftsCleanupFailClosedClassificationTests(unittest.TestCase):
    """A failed body read must never be treated as evidence of emptiness.

    ``run_applescript`` raises on a nonzero ``osascript`` exit, so a script with
    no ``try`` fails loudly and correctly. The bare ``try`` around
    ``content of aDraft`` converted that loud failure into an exit-0 wrong
    answer: ``draftBody`` stayed ``""``, a draft with a blank subject and an
    unreadable body was classified EMPTY, and ``dry_run=False`` deleted it
    permanently. The in-repo reference for the correct discipline is
    ``compose/cleanup.py`` (``delete_draft_if_identity_matches``), where every
    identity read has an ``on error`` arm that fails closed.

    Scope note: this locks the *throw* path. If the property read **hangs**
    instead of raising (documented for some Mail reads in
    ``search/script.py``), no ``on error`` arm of any shape helps and only the
    call timeout bounds it.
    """

    def _classification_block(self, script: str) -> str:
        """Return the per-draft classification loop body."""
        start = script.index("repeat with aDraft in draftMessages")
        end = script.index("set emptyCount to count of emptyDrafts")
        return script[start:end]

    def test_body_read_binds_a_success_sentinel(self):
        """The read result must be tracked separately from the body value."""
        block = self._classification_block(_capture_cleanup_script())

        self.assertIn("set bodyReadOk to false", block)
        read_index = block.index("set draftBody to content of aDraft")
        sentinel_reset = block.index("set bodyReadOk to false")
        self.assertLess(
            sentinel_reset,
            read_index,
            "bodyReadOk must be reset to false before each body read, otherwise a "
            "previous draft's successful read vouches for this draft",
        )
        self.assertIn("set bodyReadOk to true", block)
        self.assertLess(
            read_index,
            block.index("set bodyReadOk to true"),
            "bodyReadOk may only be set true after the read returns",
        )

    def test_empty_classification_requires_a_successful_body_read(self):
        """No path may add a draft to emptyDrafts without bodyReadOk."""
        block = self._classification_block(_capture_cleanup_script())

        lines = block.splitlines()
        collect_indexes = [i for i, line in enumerate(lines) if "set end of emptyDrafts to aDraft" in line]
        self.assertTrue(collect_indexes, "cleanup must still be able to collect blank drafts")
        for index in collect_indexes:
            governing = next(
                (lines[i] for i in range(index - 1, -1, -1) if lines[i].rstrip().endswith(" then")),
                None,
            )
            self.assertIsNotNone(governing, "the collection statement must sit inside an `if ... then`")
            assert governing is not None
            self.assertIn(
                "bodyReadOk",
                governing,
                "the condition governing empty-classification must require a "
                f"successful body read; found {governing.strip()!r}",
            )

    def test_genuinely_empty_draft_is_still_collected_and_deleted(self):
        """The mirror-image regression: cleanup must still clean up."""
        script = _capture_cleanup_script(dry_run=False)
        block = self._classification_block(script)

        self.assertIn('draftSubject is ""', block)
        self.assertIn('bodyStripped is ""', block)
        self.assertIn("set end of emptyDrafts to aDraft", block)
        self.assertIn("delete aDraft", script)

    def test_unreadable_drafts_are_counted_and_reported(self):
        """Skipped drafts must be accounted for, not silently dropped."""
        script = _capture_cleanup_script()
        block = self._classification_block(script)

        self.assertIn("set skippedCount to 0", script)
        self.assertIn("set skippedCount to skippedCount + 1", block)
        self.assertIn("skippedCount", script[script.index("set reportSummary to") :])

    def test_per_draft_error_arm_counts_instead_of_swallowing(self):
        """The outer per-draft `try` must report, not silently drop the draft."""
        block = self._classification_block(_capture_cleanup_script())

        self.assertIn("on error", block)
        arm = block[block.rindex("on error") :]
        self.assertIn(
            "set skippedCount to skippedCount + 1",
            arm,
            "a draft whose subject or body read threw must be counted as skipped",
        )

    def test_failed_delete_is_counted_and_reported(self):
        """A swallowed `delete aDraft` must not vanish from the report."""
        script = _capture_cleanup_script(dry_run=False)
        start = script.index("repeat with aDraft in emptyDrafts")
        end = script.index("on error errMsg")
        delete_loop = script[start:end]

        self.assertIn("set failedCount to 0", script)
        self.assertIn("set failedCount to failedCount + 1", delete_loop)
        self.assertIn("failedCount", script[script.index("set reportSummary to") :])


@unittest.skipUnless(
    shutil.which("osacompile") is not None,
    "osacompile not available — AppleScript compile check skipped on this platform",
)
class ManageDraftsCleanupScriptCompilesTests(unittest.TestCase):
    """The emitted cleanup script must still parse as AppleScript."""

    def test_cleanup_empty_script_compiles(self):
        script = _capture_cleanup_script()

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "cleanup.applescript"
            src.write_text(script, encoding="utf-8")
            proc = subprocess.run(
                ["osacompile", "-o", str(Path(tmp) / "cleanup.scpt"), str(src)],
                capture_output=True,
                timeout=60,
            )

        self.assertEqual(
            proc.returncode,
            0,
            f"osacompile rejected the cleanup_empty script:\n{proc.stderr.decode('utf-8', 'replace')}",
        )


if __name__ == "__main__":
    unittest.main()
