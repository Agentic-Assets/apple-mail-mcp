"""Safety contracts for ``manage_trash``: dry-run honoring and date-window fidelity.

Two defects this module pins:

1. ``action="empty_trash"`` ignored ``dry_run`` entirely. The emitted AppleScript
   ran ``delete aMessage`` unconditionally, so a caller who passed
   ``confirm_empty=True`` and relied on the documented ``dry_run=True`` default
   permanently deleted mail while asking for a preview.
2. ``action="delete_permanent"`` with ``apply_to_all=True`` accepted
   ``older_than_days`` (which also satisfies the ``UNBOUNDED_SCAN_REQUIRED``
   gate) and applied none of it. The hand-rolled script bound a bare
   newest-first ``messages 1 thru N of trashMailbox`` slice, so a request to
   purge mail *older than* a year permanently deleted the *newest* messages.

3. The same no-window purge stayed reachable through a **non-positive**
   ``older_than_days``. ``older_than_days=-1`` is not ``None``, so it satisfied
   the ``UNBOUNDED_SCAN_REQUIRED`` guard and zeroed ``effective_recent_days``,
   while ``_date_to_for_older_than`` returns ``None`` for any value ``<= 0``.
   The bounded search therefore ran with ``date_from=None`` **and**
   ``date_to=None`` — no window at all — after silently discarding the caller's
   ``recent_days``, so the newest messages in Trash were permanently deleted.

The invariant these tests protect: ``delete_permanent`` must never permanently
delete a message outside the caller's requested date window, and no dry-run path
may emit a ``delete`` command.
"""

import json
import unittest
from typing import Any, NamedTuple
from unittest.mock import MagicMock, patch

from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.tools import manage as manage_tools
from apple_mail_mcp.tools.manage.helpers import _date_from_for_recent_days, _date_to_for_older_than


class _ScriptCapture:
    """Capture every script passed to ``run_applescript``."""

    def __init__(self, return_value: str = "ok"):
        self.scripts: list[str] = []
        self._return_value = return_value

    def __call__(self, script: str, timeout: int = 120) -> str:
        self.scripts.append(script)
        return self._return_value

    @property
    def last_script(self) -> str:
        return self.scripts[-1] if self.scripts else ""


def _delete_statements(script: str) -> list[str]:
    """Return AppleScript lines that issue a ``delete`` command.

    Matches statement position only, so prose inside a string literal
    ("Would permanently delete: ...") and the ``deleteCount`` accumulator do not
    count as deletions.
    """
    return [line.strip() for line in script.splitlines() if line.strip().startswith("delete ")]


class EmptyTrashDryRunTests(unittest.TestCase):
    """Defect 1: empty_trash must honor dry_run."""

    def test_empty_trash_dry_run_emits_no_delete_command(self):
        capture = _ScriptCapture("EMPTYING TRASH")

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture):
            manage_tools.manage_trash(
                account="Work",
                action="empty_trash",
                confirm_empty=True,
            )

        self.assertEqual(len(capture.scripts), 1)
        self.assertEqual(
            _delete_statements(capture.last_script),
            [],
            "empty_trash dry-run must not emit any AppleScript delete command",
        )
        self.assertNotIn("delete aMessage", capture.last_script)

    def test_empty_trash_dry_run_labels_output_as_preview(self):
        capture = _ScriptCapture("EMPTYING TRASH")

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture):
            manage_tools.manage_trash(
                account="Work",
                action="empty_trash",
                confirm_empty=True,
            )

        script = capture.last_script
        self.assertIn("DRY RUN", script)
        self.assertNotIn('set outputText to "EMPTYING TRASH"', script)
        # The preview must say plainly that nothing was deleted and how to act.
        self.assertIn("nothing was deleted", script)
        self.assertIn("dry_run=False", script)

    def test_empty_trash_dry_run_still_requires_confirm_empty(self):
        """dry_run does not weaken the confirm_empty gate."""
        with patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run:
            result = manage_tools.manage_trash(
                account="Work",
                action="empty_trash",
                confirm_empty=False,
            )

        mock_run.assert_not_called()
        self.assertIn("confirm_empty=True", result)

    def test_empty_trash_not_dry_run_still_deletes(self):
        """Regression: the fix must not turn empty_trash into a tool that never deletes."""
        capture = _ScriptCapture("EMPTYING TRASH")

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture):
            manage_tools.manage_trash(
                account="Work",
                action="empty_trash",
                confirm_empty=True,
                dry_run=False,
            )

        script = capture.last_script
        self.assertIn("delete aMessage", _delete_statements(script))
        self.assertNotIn("DRY RUN", script)


class DeletePermanentDateWindowTests(unittest.TestCase):
    """Defect 2: delete_permanent must apply the caller's date window."""

    _RECORDS = [
        {
            "message_id": "101",
            "subject": "Old notice",
            "sender": "sender@example.com",
            "received_date": "2024-01-02T09:00:00",
        }
    ]

    def test_apply_to_all_passes_older_than_days_as_date_to(self):
        capture = _ScriptCapture("deleted")

        with (
            patch(
                "apple_mail_mcp.tools.manage._search_mail_records",
                return_value=list(self._RECORDS),
            ) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture),
        ):
            manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                apply_to_all=True,
                allow_filter_scan=True,
                older_than_days=365,
                dry_run=False,
                max_deletes=3,
            )

        mock_search.assert_called_once()
        kwargs = mock_search.call_args.kwargs
        self.assertEqual(kwargs["mailbox"], "Trash")
        self.assertEqual(kwargs["date_to"], _date_to_for_older_than(365))
        self.assertIsNone(kwargs["date_from"])

    def test_apply_to_all_does_not_bind_bare_newest_first_slice(self):
        """The purge must target resolved ids, not `messages 1 thru N of trashMailbox`."""
        capture = _ScriptCapture("deleted")

        with (
            patch(
                "apple_mail_mcp.tools.manage._search_mail_records",
                return_value=list(self._RECORDS),
            ),
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture),
        ):
            manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                apply_to_all=True,
                allow_filter_scan=True,
                older_than_days=365,
                dry_run=False,
                max_deletes=3,
            )

        for script in capture.scripts:
            self.assertNotIn("messages 1 thru 3 of trashMailbox", script)
        self.assertIn("id is 101", capture.last_script)

    def test_apply_to_all_dry_run_applies_window_and_emits_no_delete(self):
        capture = _ScriptCapture("preview")

        with (
            patch(
                "apple_mail_mcp.tools.manage._search_mail_records",
                return_value=list(self._RECORDS),
            ) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture),
        ):
            result = manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                apply_to_all=True,
                allow_filter_scan=True,
                older_than_days=365,
                dry_run=True,
                max_deletes=3,
            )

        self.assertEqual(mock_search.call_args.kwargs["date_to"], _date_to_for_older_than(365))
        for script in capture.scripts:
            self.assertEqual(_delete_statements(script), [])
        self.assertIn("WARNING: filter scan enabled", result)

    def test_apply_to_all_without_older_than_days_uses_recent_window(self):
        """Without older_than_days the recent_days window becomes date_from."""
        capture = _ScriptCapture("deleted")

        with (
            patch(
                "apple_mail_mcp.tools.manage._search_mail_records",
                return_value=list(self._RECORDS),
            ) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture),
        ):
            manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                apply_to_all=True,
                allow_filter_scan=True,
                recent_days=7,
                dry_run=False,
                max_deletes=3,
            )

        kwargs = mock_search.call_args.kwargs
        self.assertIsNotNone(kwargs["date_from"])
        self.assertIsNone(kwargs["date_to"])

    def test_apply_to_all_no_matches_in_window_deletes_nothing(self):
        """An empty window result must under-delete and say so, never fall back to a slice."""
        with (
            patch("apple_mail_mcp.tools.manage._search_mail_records", return_value=[]),
            patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run,
        ):
            result = manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                apply_to_all=True,
                allow_filter_scan=True,
                older_than_days=365,
                dry_run=False,
            )

        mock_run.assert_not_called()
        self.assertIn("No matching emails found in Trash", result)

    def test_apply_to_all_not_dry_run_still_deletes(self):
        """Regression: resolved-id purge must still emit the delete command."""
        capture = _ScriptCapture("deleted")

        with (
            patch(
                "apple_mail_mcp.tools.manage._search_mail_records",
                return_value=list(self._RECORDS),
            ),
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture),
        ):
            manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                apply_to_all=True,
                allow_filter_scan=True,
                older_than_days=365,
                dry_run=False,
            )

        self.assertIn("delete aMessage", _delete_statements(capture.last_script))

    def test_message_ids_path_not_dry_run_still_deletes(self):
        """Regression: the audited id-direct purge is unchanged."""
        capture = _ScriptCapture("deleted")

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture):
            manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                message_ids=["101"],
                dry_run=False,
            )

        self.assertIn("delete aMessage", _delete_statements(capture.last_script))


class MaxDeletesClampTests(unittest.TestCase):
    """Defect 3: ``max_deletes`` had no floor and no ceiling.

    Live probe facts (read-only, 4 Mail backends, byte-identical results):

    * An out-of-range upper bound raises -1719 ``Invalid index.``; it does not clamp.
    * ``messages 1 thru 0`` does NOT raise on a non-empty mailbox — AppleScript
      clamps index 0 to 1 and returns exactly ONE message.
    * ``messages 1 thru -1`` is end-relative: the range spans the ENTIRE mailbox.

    Both trash slice sites are shaped ``if messageCount > {max_deletes} then
    ... 1 thru {max_deletes}``, so ``max_deletes=-1`` passed the guard and bound the
    whole mailbox — the documented "safety limit" inverted into "delete everything" —
    and ``max_deletes=0`` deleted one message when the caller asked for zero.
    """

    _RECORDS = [
        {
            "message_id": "101",
            "subject": "Old notice",
            "sender": "sender@example.com",
            "received_date": "2024-01-02T09:00:00",
        }
    ]

    def _empty_trash_script(self, max_deletes: int) -> str:
        capture = _ScriptCapture("EMPTYING TRASH")
        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture):
            manage_tools.manage_trash(
                account="Work",
                action="empty_trash",
                confirm_empty=True,
                dry_run=False,
                max_deletes=max_deletes,
            )
        return capture.last_script

    def _id_purge_script(self, max_deletes: int) -> str:
        capture = _ScriptCapture("deleted")
        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture):
            manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                message_ids=["101"],
                dry_run=False,
                max_deletes=max_deletes,
            )
        return capture.last_script

    def test_negative_max_deletes_never_emits_end_relative_slice(self):
        """`1 thru -1` spans the whole mailbox; a negative bound must never reach AppleScript."""
        for script in (self._empty_trash_script(-1), self._id_purge_script(-1)):
            self.assertNotIn("thru -1", script)
            self.assertNotIn("> -1", script)
            self.assertNotRegex(script, r"thru\s+-\d")
            # No script at all: a non-positive bound is refused outright rather than
            # floored, so there is no emitted slice to inspect. Asserted explicitly
            # so this stays a statement about behavior and not a pass on empty text.
            self.assertEqual(script, "")

    def test_zero_max_deletes_never_emits_zero_slice(self):
        """`messages 1 thru 0` silently returns one message instead of none."""
        for script in (self._empty_trash_script(0), self._id_purge_script(0)):
            self.assertNotIn("thru 0", script)
            self.assertNotIn("> 0 then", script)
            self.assertEqual(script, "")

    def test_oversized_max_deletes_is_capped_at_the_per_path_ceiling(self):
        """The ceiling differs by path because the paths cannot reach the same number.

        ``empty_trash`` slices Mail's own ``messages`` element of the Trash mailbox,
        so ``TRASH_SCAN`` (100) is reachable there. The id path caps ``message_ids`` at
        ``MAX_WHOSE_IDS`` (50), so 51-100 is dead range and a 100 bound would advertise
        a slice this path can never take.
        """
        for script in (self._empty_trash_script(10000), self._id_purge_script(10000)):
            self.assertNotIn("thru 10000", script)
            self.assertNotIn("> 10000", script)
        self.assertIn(f"thru {SCAN_BOUNDS['TRASH_SCAN']}", self._empty_trash_script(10000))
        self.assertIn(f"thru {MAX_WHOSE_IDS}", self._id_purge_script(10000))

    def test_clamped_max_deletes_is_disclosed_not_silent(self):
        """A clamped bound must never read back as the caller's own number."""
        script = self._empty_trash_script(10000)
        self.assertIn("max_deletes=10000 requested, clamped to 100", script)
        self.assertIn("(limited by max_deletes=", script)

    def test_in_range_max_deletes_emits_no_clamp_disclosure(self):
        self.assertNotIn("requested, clamped to", self._empty_trash_script(5))

    def test_clamped_max_deletes_propagates_into_recursive_id_purge(self):
        """apply_to_all resolves ids, then recurses; the recursion must carry the clamp.

        Written originally against ``max_deletes=-1``, when a non-positive bound was
        floored to 1 and the assertion read ``limit == 1``. Non-positive bounds are now
        refused outright (see ``NonPositiveMaxDeletesRefusalTests``), so nothing is
        propagated for them at all; the case below exercises the clamp that survives —
        the oversized ceiling — which is where propagation can still break. The
        negative case is asserted separately to reach neither seam.
        """
        capture = _ScriptCapture("deleted")

        with (
            patch(
                "apple_mail_mcp.tools.manage._search_mail_records",
                return_value=list(self._RECORDS),
            ) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture),
        ):
            manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                apply_to_all=True,
                allow_filter_scan=True,
                older_than_days=365,
                dry_run=False,
                max_deletes=10000,
            )

        self.assertEqual(mock_search.call_args.kwargs["limit"], MAX_WHOSE_IDS)
        for script in capture.scripts:
            self.assertNotIn("thru 10000", script)
            self.assertIn(f"items 1 thru {MAX_WHOSE_IDS} of matchingMessages", script)

    def test_negative_max_deletes_propagates_nothing_because_it_is_refused(self):
        """The old floor-to-1 behavior on this path is gone: nothing runs."""
        with (
            patch("apple_mail_mcp.tools.manage._search_mail_records") as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run,
        ):
            result = manage_tools.manage_trash(
                account="Work",
                action="delete_permanent",
                apply_to_all=True,
                allow_filter_scan=True,
                older_than_days=365,
                dry_run=False,
                max_deletes=-1,
            )

        mock_search.assert_not_called()
        mock_run.assert_not_called()
        self.assertIn("UNBOUNDED_SCAN_REQUIRED", result)

    def test_in_range_max_deletes_is_unchanged(self):
        script = self._empty_trash_script(5)
        self.assertIn("messages 1 thru 5 of trashMailbox", script)


class InvalidActionTests(unittest.TestCase):
    """Defect 4: an unrecognized ``action`` fell through to a trash move.

    The ``message_ids`` path validated the action and returned
    ``Error: Invalid action '…'``. The filter path had no such check: its branch
    chain was ``empty_trash`` -> ``delete_permanent`` -> unguarded fallthrough, so a
    typo performed a destructive move instead of failing. Both paths must now give
    byte-identical errors, and neither may reach AppleScript.
    """

    _EXPECTED = "Error: Invalid action 'bogus'. Use: move_to_trash, delete_permanent, empty_trash"

    def test_bogus_action_on_filter_path_errors_and_runs_no_applescript(self):
        with (
            patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run,
            patch("apple_mail_mcp.tools.manage._search_mail_records") as mock_search,
        ):
            result = manage_tools.manage_trash(
                account="Work",
                action="bogus",
                apply_to_all=True,
                allow_filter_scan=True,
            )

        mock_run.assert_not_called()
        mock_search.assert_not_called()
        self.assertEqual(result, self._EXPECTED)

    def test_bogus_action_never_reaches_a_destructive_path(self):
        """Every plausible typo must fail closed, including the empty string."""
        for action in ("bogus", "", "delete", "empty", "trash", "delete_permanently", "move_to_trash "):
            with (
                patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run,
                patch("apple_mail_mcp.tools.manage._search_mail_records") as mock_search,
            ):
                result = manage_tools.manage_trash(
                    account="Work",
                    action=action,
                    apply_to_all=True,
                    allow_filter_scan=True,
                    older_than_days=365,
                    dry_run=False,
                )

            with self.subTest(action=action):
                mock_run.assert_not_called()
                mock_search.assert_not_called()
                self.assertEqual(
                    result, f"Error: Invalid action '{action}'. Use: move_to_trash, delete_permanent, empty_trash"
                )

    def test_both_paths_report_the_same_invalid_action_error(self):
        with patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run:
            by_ids = manage_tools.manage_trash(account="Work", action="bogus", message_ids=["101"])
        mock_run.assert_not_called()

        with (
            patch("apple_mail_mcp.tools.manage.run_applescript"),
            patch("apple_mail_mcp.tools.manage._search_mail_records"),
        ):
            by_filter = manage_tools.manage_trash(
                account="Work",
                action="bogus",
                apply_to_all=True,
                allow_filter_scan=True,
            )

        self.assertEqual(by_ids, by_filter)
        self.assertEqual(by_ids, self._EXPECTED)

    def test_valid_actions_are_not_rejected(self):
        capture = _ScriptCapture("ok")
        with (
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture),
            patch(
                "apple_mail_mcp.tools.manage._search_mail_records",
                return_value=[{"message_id": "101", "subject": "Old", "sender": "sender@example.com"}],
            ),
        ):
            results = [
                manage_tools.manage_trash(account="Work", action="empty_trash", confirm_empty=True),
                manage_tools.manage_trash(
                    account="Work",
                    action="delete_permanent",
                    apply_to_all=True,
                    allow_filter_scan=True,
                ),
                manage_tools.manage_trash(
                    account="Work",
                    action="move_to_trash",
                    apply_to_all=True,
                    allow_filter_scan=True,
                ),
            ]

        for result in results:
            self.assertNotIn("Invalid action", result)

    def test_invalid_action_is_rejected_before_the_unbounded_scan_guard(self):
        """A typo must not be reported as a scan-window problem."""
        with (
            patch("apple_mail_mcp.tools.manage.run_applescript"),
            patch("apple_mail_mcp.tools.manage._search_mail_records"),
        ):
            result = manage_tools.manage_trash(
                account="Work",
                action="bogus",
                apply_to_all=True,
                allow_filter_scan=True,
                recent_days=0,
            )

        self.assertEqual(result, self._EXPECTED)
        self.assertNotIn("UNBOUNDED_SCAN_REQUIRED", result)


class NonPositiveOlderThanDaysTests(unittest.TestCase):
    """Defect 5: a non-positive ``older_than_days`` reopened the no-window purge.

    Three interlocking behaviors produced it, none wrong on its own:

    1. The refusal guard reads ``older_than_days is None and recent_days <= 0``.
       ``-1 is None`` is False, so the guard stays silent.
    2. ``effective_recent_days = recent_days if older_than_days is None else 0``
       zeroes the recent window because ``-1`` is not ``None``, discarding the
       caller's ``recent_days``.
    3. ``_date_to_for_older_than(-1)`` returns ``None`` for any value ``<= 0``.

    Net: ``date_from=None`` and ``date_to=None``. The bounded search resolves the
    NEWEST messages in Trash and ``delete_permanent`` erases them permanently —
    the original defect 2, reached through a negative argument.

    Unlike ``move_email``, ``manage_trash`` guards its filter path on
    ``apply_to_all`` rather than on a falsy ``older_than_days``, so
    ``older_than_days=0`` was **not** already caught here: with
    ``apply_to_all=True`` zero reached the same unwindowed purge. Both signs are
    covered below.
    """

    _RECORDS = [
        {
            "message_id": "101",
            "subject": "Old notice",
            "sender": "sender@example.com",
            "received_date": "2024-01-02T09:00:00",
        }
    ]

    def _search_kwargs(self, **call_kwargs):
        """Run manage_trash with mocked seams; return (search kwargs or None, scripts, result)."""
        capture = _ScriptCapture("ok")

        with (
            patch(
                "apple_mail_mcp.tools.manage._search_mail_records",
                return_value=list(self._RECORDS),
            ) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture),
        ):
            result = manage_tools.manage_trash(account="Work", **call_kwargs)

        kwargs = None if mock_search.call_args is None else mock_search.call_args.kwargs
        return kwargs, capture.scripts, result

    def _assert_windowed(self, kwargs, label):
        self.assertIsNotNone(kwargs, f"{label}: expected a bounded search, got none")
        self.assertFalse(
            kwargs["date_from"] is None and kwargs["date_to"] is None,
            f"{label}: search ran with date_from=None AND date_to=None — no window at all",
        )

    def test_negative_older_than_days_never_reaches_an_unwindowed_delete_permanent(self):
        """The permanently destructive path must never see date_from=None and date_to=None."""
        for older_than_days in (-1, -365):
            kwargs, scripts, _ = self._search_kwargs(
                action="delete_permanent",
                apply_to_all=True,
                allow_filter_scan=True,
                older_than_days=older_than_days,
                dry_run=False,
                max_deletes=3,
            )
            with self.subTest(older_than_days=older_than_days):
                self._assert_windowed(kwargs, f"delete_permanent older_than_days={older_than_days}")
                # A negative age filter degrades to the caller's recent window.
                self.assertEqual(kwargs["date_from"], _date_from_for_recent_days(2.0))
                self.assertIsNone(kwargs["date_to"])
                self.assertTrue(scripts, "the windowed purge should still act on resolved ids")

    def test_negative_older_than_days_with_recent_days_zero_refuses_the_scan(self):
        """With no recent window either, the honest answer is UNBOUNDED_SCAN_REQUIRED."""
        kwargs, scripts, result = self._search_kwargs(
            action="delete_permanent",
            apply_to_all=True,
            allow_filter_scan=True,
            older_than_days=-1,
            recent_days=0,
            dry_run=False,
            max_deletes=3,
        )

        self.assertIsNone(kwargs, "no search may run without a date window")
        self.assertEqual(scripts, [], "no AppleScript may run without a date window")
        self.assertIn("UNBOUNDED_SCAN_REQUIRED", result)

    def test_negative_older_than_days_preserves_the_callers_recent_days(self):
        """`recent_days` must not be silently discarded by a non-positive age filter."""
        kwargs, _, _ = self._search_kwargs(
            action="delete_permanent",
            apply_to_all=True,
            allow_filter_scan=True,
            older_than_days=-1,
            recent_days=2.0,
            dry_run=False,
            max_deletes=3,
        )

        self._assert_windowed(kwargs, "delete_permanent older_than_days=-1 recent_days=2.0")
        self.assertEqual(kwargs["date_from"], _date_from_for_recent_days(2.0))
        self.assertEqual(kwargs["recent_days"], 2.0)

    def test_zero_older_than_days_is_not_a_window_bypass(self):
        """`manage_trash` gates on apply_to_all, so zero was never caught by a falsy check."""
        for action in ("delete_permanent", "move_to_trash"):
            kwargs, _, _ = self._search_kwargs(
                action=action,
                apply_to_all=True,
                allow_filter_scan=True,
                older_than_days=0,
                dry_run=False,
                max_deletes=3,
            )
            with self.subTest(action=action):
                self._assert_windowed(kwargs, f"{action} older_than_days=0")
                self.assertEqual(kwargs["date_from"], _date_from_for_recent_days(2.0))

    def test_move_to_trash_negative_older_than_days_never_scans_without_a_window(self):
        kwargs, _, _ = self._search_kwargs(
            action="move_to_trash",
            apply_to_all=True,
            allow_filter_scan=True,
            older_than_days=-1,
            dry_run=False,
            max_deletes=3,
        )

        self._assert_windowed(kwargs, "move_to_trash older_than_days=-1")
        self.assertEqual(kwargs["date_from"], _date_from_for_recent_days(2.0))
        self.assertIsNone(kwargs["date_to"])

    def test_move_to_trash_negative_older_than_days_with_recent_days_zero_refuses(self):
        kwargs, scripts, result = self._search_kwargs(
            action="move_to_trash",
            apply_to_all=True,
            allow_filter_scan=True,
            older_than_days=-1,
            recent_days=0,
            dry_run=False,
            max_deletes=3,
        )

        self.assertIsNone(kwargs)
        self.assertEqual(scripts, [])
        self.assertIn("UNBOUNDED_SCAN_REQUIRED", result)

    def test_move_to_trash_dry_run_preview_is_windowed_too(self):
        """A preview built from an unwindowed scan previews the wrong messages."""
        kwargs, scripts, _ = self._search_kwargs(
            action="move_to_trash",
            apply_to_all=True,
            allow_filter_scan=True,
            older_than_days=-1,
            dry_run=True,
            max_deletes=3,
        )

        self._assert_windowed(kwargs, "move_to_trash dry-run older_than_days=-1")
        for script in scripts:
            self.assertEqual(_delete_statements(script), [])

    def test_negative_older_than_days_without_apply_to_all_still_fails_closed(self):
        """Regression: a negative age filter is not a filter, so the no-target error stands."""
        kwargs, scripts, result = self._search_kwargs(
            action="move_to_trash",
            allow_filter_scan=True,
            older_than_days=-1,
            dry_run=False,
            max_deletes=3,
        )

        self.assertIsNone(kwargs)
        self.assertEqual(scripts, [])
        self.assertIn("apply_to_all=True", result)

    def test_positive_older_than_days_is_unchanged(self):
        """Regression: the fix must not disturb the window this branch already emits."""
        for action in ("delete_permanent", "move_to_trash"):
            kwargs, scripts, _ = self._search_kwargs(
                action=action,
                apply_to_all=True,
                allow_filter_scan=True,
                older_than_days=365,
                dry_run=False,
                max_deletes=3,
            )
            with self.subTest(action=action):
                self.assertEqual(kwargs["date_to"], _date_to_for_older_than(365))
                self.assertIsNone(kwargs["date_from"])
                self.assertEqual(kwargs["recent_days"], 0)
                self.assertIn("delete aMessage" if action == "delete_permanent" else "move aMessage", scripts[-1])


class _SealedRun(NamedTuple):
    """Outcome of a ``manage_trash`` call with every route to Mail severed."""

    result: str
    scripts: list[str]
    validate: MagicMock
    run: MagicMock
    search: MagicMock


_SEALED_RECORDS = [
    {
        "message_id": "101",
        "subject": "Old notice",
        "sender": "sender@example.com",
        "received_date": "2024-01-02T09:00:00",
    }
]

# Every way a caller can reach each of the three actions. The refusal under test
# sits ahead of the branch that picks between them, so all six must answer the
# same way; a guard placed inside any single branch would leave the others open.
_TARGET_PATHS: dict[str, dict[str, Any]] = {
    "empty_trash": {"action": "empty_trash", "confirm_empty": True, "dry_run": False},
    "delete_permanent by ids": {"action": "delete_permanent", "message_ids": ["101"], "dry_run": False},
    "move_to_trash by ids": {"action": "move_to_trash", "message_ids": ["101"], "dry_run": False},
    "delete_permanent by filter": {
        "action": "delete_permanent",
        "apply_to_all": True,
        "allow_filter_scan": True,
        "older_than_days": 365,
        "dry_run": False,
    },
    "move_to_trash by filter": {
        "action": "move_to_trash",
        "apply_to_all": True,
        "allow_filter_scan": True,
        "older_than_days": 365,
        "dry_run": False,
    },
    "move_to_trash by filter (dry run)": {
        "action": "move_to_trash",
        "apply_to_all": True,
        "allow_filter_scan": True,
        "older_than_days": 365,
        "dry_run": True,
    },
}


def _sealed_manage_trash(**kwargs: Any) -> _SealedRun:
    """Run ``manage_trash`` with every route to Mail.app physically severed.

    Four seams, not one. ``run_applescript`` and ``_search_mail_records`` are
    captured so a test can assert they were never reached, ``validate_account_name``
    is stubbed so the account probe cannot fire, and ``subprocess.run`` is poisoned
    so that if a future refactor bypasses any of the three the test fails loudly
    instead of talking to a real mailbox. ``manage_trash`` is the one tool in this
    package whose mistakes are irreversible; it never runs unsealed here.
    """
    capture = _ScriptCapture("ok")
    with (
        patch("subprocess.run", side_effect=AssertionError("test attempted a live osascript call")),
        patch("apple_mail_mcp.tools.manage.validate_account_name", return_value=None) as mock_validate,
        patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture) as mock_run,
        patch(
            "apple_mail_mcp.tools.manage._search_mail_records",
            return_value=list(_SEALED_RECORDS),
        ) as mock_search,
    ):
        result = manage_tools.manage_trash(account="Work", **kwargs)
    return _SealedRun(
        result=result,
        scripts=capture.scripts,
        validate=mock_validate,
        run=mock_run,
        search=mock_search,
    )


class NonPositiveMaxDeletesRefusalTests(unittest.TestCase):
    """Defect 6: a non-positive ``max_deletes`` was clamped up to 1 instead of refused.

    The clamp added earlier on this branch — ``max(1, min(max_deletes, TRASH_SCAN))``
    — closed the AppleScript index hazards documented in ``MaxDeletesClampTests`` but
    picked the wrong answer for the floor on the one tool whose mistakes cannot be
    undone. ``manage_trash(action="delete_permanent", message_ids=[...],
    max_deletes=0, dry_run=False)`` permanently deleted **one** message, and
    ``empty_trash`` with ``max_deletes=0`` deleted one message out of Trash.

    That is the ``messages 1 thru 0`` defect itself — a zero bound yielding exactly
    one message — reimplemented in Python, where no AppleScript probe can catch it.
    The two sibling tools hardened on this branch both refuse: ``move_email`` returns
    a structured ``UNBOUNDED_SCAN_REQUIRED`` and ``manage_drafts(action=
    "cleanup_empty")`` returns a plain-text refusal. ``manage_trash`` is the last of
    the three that should be lenient.

    An oversized bound keeps its clamp: "as many as you can" has an obvious intent
    to honor partially. "Zero" and "negative" have none.
    """

    _NONPOSITIVE = (0, -1)

    def _assert_structured_refusal(self, result: str, requested: int, label: str) -> dict[str, Any]:
        payload = json.loads(result)
        self.assertEqual(payload["code"], "UNBOUNDED_SCAN_REQUIRED", label)
        self.assertTrue(payload["error"], label)
        self.assertIn("max_deletes", payload["message"], label)
        self.assertIn(f"max_deletes={requested}", payload["message"], label)
        self.assertIn("preferred", payload["remediation"], label)
        self.assertIn("note", payload["remediation"], label)
        return payload

    def test_non_positive_max_deletes_is_refused_on_every_path(self):
        """All three actions, both target paths, both signs: refuse and touch nothing."""
        for label, call_kwargs in _TARGET_PATHS.items():
            for max_deletes in self._NONPOSITIVE:
                run = _sealed_manage_trash(max_deletes=max_deletes, **call_kwargs)
                with self.subTest(path=label, max_deletes=max_deletes):
                    self._assert_structured_refusal(run.result, max_deletes, label)
                    run.run.assert_not_called()
                    self.assertEqual(run.scripts, [], f"{label}: no AppleScript may be built")

    def test_refusal_reaches_no_search_on_the_destructive_paths(self):
        """A nonsense bound must not cost even a read of the mailbox it would purge."""
        for label, call_kwargs in _TARGET_PATHS.items():
            for max_deletes in self._NONPOSITIVE:
                run = _sealed_manage_trash(max_deletes=max_deletes, **call_kwargs)
                with self.subTest(path=label, max_deletes=max_deletes):
                    run.search.assert_not_called()

    def test_refusal_precedes_the_account_probe(self):
        """``validate_account_name`` is a live Mail round trip; a bad bound must cost zero."""
        for label, call_kwargs in _TARGET_PATHS.items():
            for max_deletes in self._NONPOSITIVE:
                run = _sealed_manage_trash(max_deletes=max_deletes, **call_kwargs)
                with self.subTest(path=label, max_deletes=max_deletes):
                    run.validate.assert_not_called()

    def test_empty_trash_is_not_exempt(self):
        """``empty_trash`` skips the scan-window guard, so it needs this one explicitly.

        Its slice is ``messages 1 thru {max_deletes} of trashMailbox`` against Mail's
        own element list, which is exactly where index 0 clamps to 1 and returns one
        real message. A caller who passes ``max_deletes=0`` here is asking for a
        no-op, not for one permanent deletion.
        """
        for max_deletes in self._NONPOSITIVE:
            run = _sealed_manage_trash(
                action="empty_trash",
                confirm_empty=True,
                dry_run=False,
                max_deletes=max_deletes,
            )
            with self.subTest(max_deletes=max_deletes):
                self._assert_structured_refusal(run.result, max_deletes, "empty_trash")
                self.assertEqual(run.scripts, [])

    def test_refusal_quotes_the_ceiling_reachable_on_the_called_path(self):
        """The advertised range must be the one that path can actually honor.

        ``empty_trash`` slices Mail's ``messages`` element, so ``TRASH_SCAN`` (100) is
        reachable. The id path caps ids at ``MAX_WHOSE_IDS`` (50) via
        ``_check_message_ids_cap`` and the filter path resolves at most
        ``SEARCH_HARD_CEILING`` (50) records before recursing into that same capped
        path, so 51-100 is unreachable on both. Quoting a flat 1-100 everywhere would
        send the caller back with a number the id path silently ignores.
        """
        expected = {
            "empty_trash": SCAN_BOUNDS["TRASH_SCAN"],
            "delete_permanent by ids": MAX_WHOSE_IDS,
            "move_to_trash by ids": MAX_WHOSE_IDS,
            "delete_permanent by filter": MAX_WHOSE_IDS,
            "move_to_trash by filter": MAX_WHOSE_IDS,
            "move_to_trash by filter (dry run)": MAX_WHOSE_IDS,
        }
        for label, call_kwargs in _TARGET_PATHS.items():
            run = _sealed_manage_trash(max_deletes=0, **call_kwargs)
            payload = json.loads(run.result)
            with self.subTest(path=label):
                self.assertIn(f"between 1 and {expected[label]}", payload["message"])
                self.assertIn(f"valid range 1-{expected[label]}", payload["remediation"]["preferred"])

    def test_refusal_payload_matches_move_email(self):
        """The two destructive tools must answer an identical condition identically.

        Same code, same envelope keys, same remediation keys, and the same sentence
        shape with the parameter name swapped. An agent that learned the answer from
        one tool must not have to learn it again from the other.

        Compared on ``manage_trash``'s id path, whose ceiling is ``MAX_WHOSE_IDS``
        just like ``move_email``'s, so the two messages are identical down to the
        number. ``empty_trash``'s 1-100 is checked separately in
        ``test_refusal_quotes_the_ceiling_reachable_on_the_called_path``.
        """
        trash = json.loads(
            _sealed_manage_trash(
                action="delete_permanent",
                message_ids=["101"],
                dry_run=False,
                max_deletes=0,
            ).result
        )

        with (
            patch("subprocess.run", side_effect=AssertionError("test attempted a live osascript call")),
            patch("apple_mail_mcp.tools.manage.validate_account_name", return_value=None),
            patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run,
        ):
            move = json.loads(
                manage_tools.move_email(
                    account="Work",
                    to_mailbox="Archive",
                    message_ids=["101"],
                    max_moves=0,
                )
            )
        mock_run.assert_not_called()

        self.assertEqual(sorted(trash), sorted(move))
        self.assertEqual(trash["code"], move["code"])
        self.assertEqual(trash["error"], move["error"])
        self.assertEqual(sorted(trash["remediation"]), sorted(move["remediation"]))
        self.assertEqual(
            trash["message"].replace("manage_trash", "TOOL").replace("max_deletes", "BOUND"),
            move["message"].replace("move_email", "TOOL").replace("max_moves", "BOUND"),
        )

    def test_refusal_is_a_string_not_a_raised_exception(self):
        """Only ``AppleScriptTimeout`` is caught downstream; a refusal must not raise."""
        for label, call_kwargs in _TARGET_PATHS.items():
            run = _sealed_manage_trash(max_deletes=-1, **call_kwargs)
            with self.subTest(path=label):
                self.assertIsInstance(run.result, str)
                self.assertNotIn("Traceback", run.result)


class MaxDeletesPerPathCeilingTests(unittest.TestCase):
    """The upper clamp survives, but its ceiling is now the one each path can reach.

    Verified against the code, not assumed:

    * id-direct path: ``_check_message_ids_cap`` returns ``WHOSE_ID_LIST_TOO_LARGE``
      above ``MAX_WHOSE_IDS`` (50), so at most 50 messages can ever be targeted.
    * filter-scan path: ``search/script.py`` applies
      ``scan_cap = min(scan_cap, SCAN_BOUNDS["SEARCH_HARD_CEILING"])`` (50) to the
      ``messages 1 thru scanUpperBound`` candidate slice, so at most 50 records come
      back, and ``_search_message_ids`` then recurses into the 50-capped id path.
    * ``empty_trash``: slices Mail's ``messages`` element of the Trash mailbox
      directly with no id list involved, so ``TRASH_SCAN`` (100) is genuinely
      reachable there and keeps its ceiling.

    So 51-100 was dead range on two of the three paths, and the flat "1..100" the
    docstring advertised was wrong advice for both.
    """

    def _empty_trash_script(self, max_deletes: int) -> str:
        run = _sealed_manage_trash(
            action="empty_trash",
            confirm_empty=True,
            dry_run=False,
            max_deletes=max_deletes,
        )
        return run.scripts[-1] if run.scripts else ""

    def _id_purge_script(self, max_deletes: int) -> str:
        run = _sealed_manage_trash(
            action="delete_permanent",
            message_ids=["101"],
            dry_run=False,
            max_deletes=max_deletes,
        )
        return run.scripts[-1] if run.scripts else ""

    def test_empty_trash_keeps_the_trash_scan_ceiling(self):
        script = self._empty_trash_script(10000)
        cap = SCAN_BOUNDS["TRASH_SCAN"]
        self.assertIn(f"messages 1 thru {cap} of trashMailbox", script)
        self.assertNotIn("thru 10000", script)

    def test_empty_trash_clamp_disclosure_is_unchanged(self):
        cap = SCAN_BOUNDS["TRASH_SCAN"]
        script = self._empty_trash_script(10000)
        self.assertIn(f"max_deletes=10000 requested, clamped to {cap}; valid range 1-{cap}", script)

    def test_id_path_clamps_to_the_whose_id_cap(self):
        """51-100 is unreachable here, so the emitted bound must not claim otherwise."""
        script = self._id_purge_script(10000)
        self.assertIn(f"items 1 thru {MAX_WHOSE_IDS} of matchingMessages", script)
        self.assertNotIn("thru 10000", script)
        self.assertNotIn(f"thru {SCAN_BOUNDS['TRASH_SCAN']} of matchingMessages", script)

    def test_filter_path_search_limit_clamps_to_the_whose_id_cap(self):
        run = _sealed_manage_trash(
            action="delete_permanent",
            apply_to_all=True,
            allow_filter_scan=True,
            older_than_days=365,
            dry_run=False,
            max_deletes=10000,
        )
        self.assertEqual(run.search.call_args.kwargs["limit"], MAX_WHOSE_IDS)
        self.assertIn(f"items 1 thru {MAX_WHOSE_IDS} of matchingMessages", run.scripts[-1])

    def test_a_bound_inside_the_per_path_ceiling_is_honored_verbatim(self):
        """The clamp must not narrow any range a path can actually serve."""
        self.assertIn(
            f"messages 1 thru {SCAN_BOUNDS['TRASH_SCAN']} of trashMailbox",
            self._empty_trash_script(SCAN_BOUNDS["TRASH_SCAN"]),
        )
        self.assertIn(
            f"items 1 thru {MAX_WHOSE_IDS} of matchingMessages",
            self._id_purge_script(MAX_WHOSE_IDS),
        )
        for script in (
            self._empty_trash_script(SCAN_BOUNDS["TRASH_SCAN"]),
            self._id_purge_script(MAX_WHOSE_IDS),
        ):
            self.assertNotIn("requested, clamped to", script)


class OrdinaryMaxDeletesRegressionTests(unittest.TestCase):
    """This is a destructive tool: narrowing its working range would itself be a defect.

    The refusal changes only the non-positive branch. Every ordinary bound must emit
    the same script and the same guard it emitted before.
    """

    def test_default_max_deletes_is_still_five(self):
        run = _sealed_manage_trash(action="empty_trash", confirm_empty=True, dry_run=False)
        self.assertIn("messages 1 thru 5 of trashMailbox", run.scripts[-1])
        self.assertNotIn("requested, clamped to", run.scripts[-1])

    def test_empty_trash_ordinary_bound_is_unchanged(self):
        run = _sealed_manage_trash(
            action="empty_trash",
            confirm_empty=True,
            dry_run=False,
            max_deletes=5,
        )
        script = run.scripts[-1]
        self.assertIn("if messageCount > 5 then", script)
        self.assertIn("set trashMessages to messages 1 thru 5 of trashMailbox", script)
        self.assertIn("delete aMessage", _delete_statements(script))

    def test_id_purge_ordinary_bound_is_unchanged(self):
        run = _sealed_manage_trash(
            action="delete_permanent",
            message_ids=["101"],
            dry_run=False,
            max_deletes=5,
        )
        script = run.scripts[-1]
        self.assertIn("if (count of matchingMessages) > 5 then", script)
        self.assertIn("set matchingMessages to items 1 thru 5 of matchingMessages", script)
        self.assertIn("delete aMessage", _delete_statements(script))

    def test_move_to_trash_by_ids_ordinary_bound_is_unchanged(self):
        run = _sealed_manage_trash(
            action="move_to_trash",
            message_ids=["101"],
            dry_run=False,
            max_deletes=5,
        )
        script = run.scripts[-1]
        self.assertIn("set matchingMessages to items 1 thru 5 of matchingMessages", script)
        self.assertIn("move aMessage to trashMailbox", script)

    def test_filter_execute_ordinary_bound_still_passes_the_search_limit(self):
        run = _sealed_manage_trash(
            action="delete_permanent",
            apply_to_all=True,
            allow_filter_scan=True,
            older_than_days=365,
            dry_run=False,
            max_deletes=3,
        )
        self.assertEqual(run.search.call_args.kwargs["limit"], 3)
        self.assertIn("delete aMessage", _delete_statements(run.scripts[-1]))

    def test_filter_dry_run_ordinary_bound_still_probes_one_extra(self):
        run = _sealed_manage_trash(
            action="move_to_trash",
            apply_to_all=True,
            allow_filter_scan=True,
            older_than_days=365,
            dry_run=True,
            max_deletes=3,
        )
        self.assertEqual(run.search.call_args.kwargs["limit"], 4)
        for script in run.scripts:
            self.assertEqual(_delete_statements(script), [])

    def test_one_is_a_valid_bound_not_a_clamped_zero(self):
        """``max_deletes=1`` is a real request and must behave exactly as asked."""
        run = _sealed_manage_trash(
            action="delete_permanent",
            message_ids=["101"],
            dry_run=False,
            max_deletes=1,
        )
        script = run.scripts[-1]
        self.assertIn("set matchingMessages to items 1 thru 1 of matchingMessages", script)
        self.assertNotIn("requested, clamped to", script)
        self.assertNotIn("UNBOUNDED_SCAN_REQUIRED", run.result)


if __name__ == "__main__":
    unittest.main()
