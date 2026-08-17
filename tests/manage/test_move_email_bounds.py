"""Bound contracts for ``move_email``: ``max_moves`` validation and slice-index safety.

Three defects this module pins.

**1. ``max_moves`` had no validation on any path.** It was declared
``max_moves: int = 50`` with no floor, no ceiling, and no rejection, and every
downstream consumer read it raw:

* The filter-scan execute path passes it as ``_search_message_ids(limit=max_moves)``,
  which reaches ``search.dispatch``. Before that seam grew a guard,
  ``limit=0`` built ``messages 1 thru 1`` — Mail bound the newest message and
  returned it — and ``helpers._search_message_ids``' ``if len(ids) >= limit: break``
  fired after the first append, so a call the caller sized at zero resolved one
  real id and moved it. Measured pre-guard bounds from ``_build_search_script``:
  ``limit=0 -> scanUpperBound=1``, ``limit=-1 -> scanUpperBound=0``.
* The dry-run path passes ``limit=max_moves + 1``, so the same hazard sits one
  value lower: ``max_moves=-1`` lands at ``limit=0``.
* With the dispatch guard in place those became a raw ``ValueError`` escaping the
  tool, because ``move_email`` catches only ``AppleScriptTimeout``. Strictly
  better than a wrong move, but the wrong shape for an agent caller — and
  ``max_moves=0`` on the *dry-run* path still slipped through (``limit=1``,
  ``scanUpperBound=2``) and rendered a "0 email(s) would move" preview after
  binding two real messages, so preview and execute disagreed about the same
  arguments.

**2. The id-direct path emitted an unguarded ``items 1 thru {max_moves}``.**
That path never touches the dispatch bridge, so nothing protected it. Probed
read-only inside a ``tell application "Mail"`` block on a local list (this site
slices an AppleScript list of message specifiers, not a Mail ``messages``
element, so list semantics apply — not the end-relative clamping of
``messages 1 thru N``):

* ``items 1 thru 0`` raises -1728, which the block's outer ``on error`` turns
  into ``"Error: ... Check that account and mailbox names are correct."`` —
  a bound bug reported as a naming problem.
* ``items 1 thru -1`` returns the ENTIRE list, and its guard
  ``if (count of matchingMessages) > -1`` is always true, so the documented
  safety limit inverted into "move everything matched".
* ``items 1 thru -2`` silently drops the LAST matched id and still prints
  ``(max_moves limit reached)`` — a partial mutation reported as a capped one.

**3. A negative ``older_than_days`` defeated the date-window guard.**
``_date_to_for_older_than`` returns ``None`` for any non-positive value, while
``effective_recent_days`` is zeroed whenever ``older_than_days is not None`` and
the ``UNBOUNDED_SCAN_REQUIRED`` guard only fires when it ``is None``. So
``older_than_days=-1`` reached the search with ``date_from=None`` and
``date_to=None`` — no window at all — and discarded the caller's ``recent_days``
on the way. A request to file mail *older than* N days then targeted the NEWEST
messages, the same shape as the ``manage_trash`` ``apply_to_all`` purge defect.

The invariant these tests protect: ``move_email`` never moves a message under a
bound the caller did not set, never emits a zero or negative slice index, and
never silently caps a mutation without saying so.
"""

import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS
from apple_mail_mcp.tools import manage as manage_tools
from apple_mail_mcp.tools.manage.helpers import _date_to_for_older_than

_IDS = ["101", "102", "103"]

_RECORDS = [
    {
        "message_id": "101",
        "subject": "Quarterly notice",
        "sender": "sender@example.com",
        "received_date": "2026-01-02T09:00:00",
    },
    {
        "message_id": "102",
        "subject": "Follow up",
        "sender": "other@example.com",
        "received_date": "2026-01-03T09:00:00",
    },
]


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


def _id_direct_script(max_moves: int, *, dry_run: bool = False) -> str:
    capture = _ScriptCapture("MOVED")
    with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture):
        manage_tools.move_email(
            account="Work",
            to_mailbox="Archive",
            message_ids=list(_IDS),
            max_moves=max_moves,
            dry_run=dry_run,
        )
    return capture.last_script


def _statements(script: str) -> str:
    """Return the script with AppleScript ``--`` comment lines removed.

    Scopes the slice-index assertions to statement position, so a comment that
    quotes a bad bound (``items 1 thru 0``) can never be read as an emitted one.
    Mirrors ``_delete_statements`` in ``test_manage_trash_safety.py``.
    """
    return "\n".join(line for line in script.splitlines() if not line.strip().startswith("--"))


def _slice_lines(script: str) -> list[str]:
    """Return the statements that read ``max_moves`` as a slice or guard bound."""
    wanted = ("items 1 thru", "count of matchingMessages", "moveCount >=")
    return [line.strip() for line in _statements(script).splitlines() if any(token in line for token in wanted)]


class MaxMovesRefusalTests(unittest.TestCase):
    """Defect 1: a non-positive ``max_moves`` must be refused, not acted on."""

    _NONPOSITIVE = (0, -1, -2)

    def _assert_structured_refusal(self, result: str, requested: int) -> None:
        payload = json.loads(result)
        self.assertEqual(payload["code"], "UNBOUNDED_SCAN_REQUIRED")
        self.assertIn("max_moves", payload["message"])
        self.assertIn(str(requested), payload["message"])
        self.assertIn("preferred", payload["remediation"])

    def test_nonpositive_max_moves_refused_on_id_direct_path(self):
        for max_moves in self._NONPOSITIVE:
            with self.subTest(max_moves=max_moves):
                with patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run:
                    result = manage_tools.move_email(
                        account="Work",
                        to_mailbox="Archive",
                        message_ids=list(_IDS),
                        max_moves=max_moves,
                    )
                mock_run.assert_not_called()
                self._assert_structured_refusal(result, max_moves)

    def test_nonpositive_max_moves_refused_on_filter_execute_path(self):
        for max_moves in self._NONPOSITIVE:
            with self.subTest(max_moves=max_moves):
                with (
                    patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run,
                    patch("apple_mail_mcp.tools.manage._search_mail_records") as mock_search,
                ):
                    result = manage_tools.move_email(
                        account="Work",
                        to_mailbox="Archive",
                        older_than_days=30,
                        allow_filter_scan=True,
                        max_moves=max_moves,
                        dry_run=False,
                    )
                mock_run.assert_not_called()
                mock_search.assert_not_called()
                self._assert_structured_refusal(result, max_moves)

    def test_nonpositive_max_moves_refused_on_filter_dry_run_path(self):
        """The dry-run path sized the scan at ``max_moves + 1``, so 0 slipped through."""
        for max_moves in self._NONPOSITIVE:
            with self.subTest(max_moves=max_moves):
                with (
                    patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run,
                    patch("apple_mail_mcp.tools.manage._search_mail_records") as mock_search,
                ):
                    result = manage_tools.move_email(
                        account="Work",
                        to_mailbox="Archive",
                        older_than_days=30,
                        allow_filter_scan=True,
                        max_moves=max_moves,
                        dry_run=True,
                    )
                mock_run.assert_not_called()
                mock_search.assert_not_called()
                self._assert_structured_refusal(result, max_moves)

    def test_refusal_never_surfaces_as_a_raw_exception(self):
        """A nonsense bound is an actionable refusal, not a ValueError from dispatch."""
        for dry_run in (False, True):
            for max_moves in self._NONPOSITIVE:
                with self.subTest(dry_run=dry_run, max_moves=max_moves):
                    with patch("apple_mail_mcp.tools.manage.run_applescript"):
                        result = manage_tools.move_email(
                            account="Work",
                            to_mailbox="Archive",
                            older_than_days=30,
                            allow_filter_scan=True,
                            max_moves=max_moves,
                            dry_run=dry_run,
                        )
                    self.assertIsInstance(result, str)
                    self.assertNotIn("Traceback", result)

    def test_refusal_precedes_the_account_probe(self):
        """A bound this broken must cost no Mail round trip at all."""
        with (
            patch("apple_mail_mcp.tools.manage.validate_account_name") as mock_validate,
            patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run,
        ):
            manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                message_ids=list(_IDS),
                max_moves=0,
            )
        mock_validate.assert_not_called()
        mock_run.assert_not_called()


class IdDirectSliceIndexTests(unittest.TestCase):
    """Defect 2: ``items 1 thru {max_moves}`` must never take a bad index."""

    def test_never_emits_a_zero_slice_index(self):
        """`items 1 thru 0` raises -1728 and reads back as a mailbox-naming error."""
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                script = _statements(_id_direct_script(0, dry_run=dry_run))
                self.assertNotIn("items 1 thru 0", script)
                self.assertNotIn("> 0 then", script)
                self.assertNotIn("moveCount >= 0", script)

    def test_never_emits_a_negative_slice_index(self):
        """`items 1 thru -1` returns the whole list; `-2` silently drops the last id."""
        for max_moves in (-1, -2):
            for dry_run in (False, True):
                with self.subTest(max_moves=max_moves, dry_run=dry_run):
                    script = _statements(_id_direct_script(max_moves, dry_run=dry_run))
                    self.assertNotRegex(script, r"items 1 thru\s+-\d")
                    self.assertNotRegex(script, r"matchingMessages\)\s*>\s*-\d")
                    self.assertNotRegex(script, r"moveCount >=\s*-\d")

    def test_every_emitted_slice_bound_is_at_least_one(self):
        for max_moves in (-2, -1, 0, 1, 10, 50, 10000):
            for dry_run in (False, True):
                script = _id_direct_script(max_moves, dry_run=dry_run)
                if not script:
                    continue  # refused before any script was built
                for line in _slice_lines(script):
                    with self.subTest(max_moves=max_moves, dry_run=dry_run, line=line):
                        self.assertNotRegex(line, r"(thru|>|>=)\s*(-\d+|0)\b")


class MaxMovesClampTests(unittest.TestCase):
    """Defect 1, upper half: an oversized bound is clamped, and the clamp is disclosed.

    The ceiling is ``bounded_scan.MAX_WHOSE_IDS`` (50), the cap already enforced on
    this tool's id-direct path by ``_check_message_ids_cap``. A ``max_moves`` above it
    is unreachable on either path today — the filter scan resolves at most
    ``SCAN_BOUNDS["SEARCH_HARD_CEILING"]`` (also 50) ids — so clamping there removes a
    nonsense value without narrowing any working range.
    """

    def test_oversized_max_moves_is_capped_at_the_id_ceiling(self):
        script = _statements(_id_direct_script(10000))
        self.assertNotIn("items 1 thru 10000", script)
        self.assertNotIn("> 10000 then", script)
        self.assertIn(f"items 1 thru {MAX_WHOSE_IDS} of matchingMessages", script)

    def test_clamped_max_moves_is_disclosed_not_silent(self):
        """A capped mutation must not read back as the caller's own number."""
        capture = _ScriptCapture("MOVED")
        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture):
            result = manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                message_ids=list(_IDS),
                max_moves=10000,
            )
        disclosure = f"max_moves=10000 requested, clamped to {MAX_WHOSE_IDS}"
        self.assertTrue(
            disclosure in capture.last_script or disclosure in result,
            f"expected {disclosure!r} in the emitted script or the returned text",
        )

    def test_in_range_max_moves_emits_no_clamp_disclosure(self):
        for max_moves in (1, 10, MAX_WHOSE_IDS):
            with self.subTest(max_moves=max_moves):
                self.assertNotIn("requested, clamped to", _id_direct_script(max_moves))

    def test_clamp_reaches_the_filter_scan_search_limit(self):
        capture = _ScriptCapture("MOVED")
        with (
            patch("apple_mail_mcp.tools.manage._search_mail_records", return_value=list(_RECORDS)) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture),
        ):
            manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                older_than_days=30,
                allow_filter_scan=True,
                max_moves=10000,
                dry_run=False,
            )
        self.assertEqual(mock_search.call_args.kwargs["limit"], MAX_WHOSE_IDS)

    def test_clamp_reaches_the_recursive_id_direct_call(self):
        """The filter path resolves ids then recurses; the recursion must carry the clamp."""
        capture = _ScriptCapture("MOVED")
        with (
            patch("apple_mail_mcp.tools.manage._search_mail_records", return_value=list(_RECORDS)),
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture),
        ):
            manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                older_than_days=30,
                allow_filter_scan=True,
                max_moves=10000,
                dry_run=False,
            )
        self.assertNotIn("items 1 thru 10000", capture.last_script)
        self.assertIn(f"items 1 thru {MAX_WHOSE_IDS} of matchingMessages", capture.last_script)


class OlderThanDaysWindowTests(unittest.TestCase):
    """Defect 3: a negative ``older_than_days`` must not erase the date window."""

    def test_negative_older_than_days_never_reaches_an_unwindowed_search(self):
        for older_than_days in (-1, -365):
            with self.subTest(older_than_days=older_than_days):
                with (
                    patch("apple_mail_mcp.tools.manage._search_mail_records", return_value=[]) as mock_search,
                    patch("apple_mail_mcp.tools.manage.run_applescript"),
                ):
                    manage_tools.move_email(
                        account="Work",
                        to_mailbox="Archive",
                        older_than_days=older_than_days,
                        recent_days=0,
                        allow_filter_scan=True,
                        max_moves=10,
                        dry_run=False,
                    )
                if mock_search.called:
                    kwargs = mock_search.call_args.kwargs
                    self.assertFalse(
                        kwargs["date_from"] is None and kwargs["date_to"] is None,
                        "a negative older_than_days produced a search with no date window",
                    )

    def test_negative_older_than_days_does_not_discard_recent_days(self):
        """`effective_recent_days` is zeroed whenever older_than_days is not None."""
        with (
            patch("apple_mail_mcp.tools.manage._search_mail_records", return_value=[]) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript"),
        ):
            manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                older_than_days=-1,
                recent_days=7.0,
                allow_filter_scan=True,
                max_moves=10,
                dry_run=False,
            )
        if mock_search.called:
            self.assertIsNotNone(
                mock_search.call_args.kwargs["date_from"],
                "recent_days=7 was discarded because older_than_days was not None",
            )

    def test_positive_older_than_days_still_sets_date_to(self):
        """Regression: the working path is unchanged."""
        with (
            patch("apple_mail_mcp.tools.manage._search_mail_records", return_value=[]) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript"),
        ):
            manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                older_than_days=30,
                allow_filter_scan=True,
                max_moves=10,
                dry_run=False,
            )
        kwargs = mock_search.call_args.kwargs
        self.assertEqual(kwargs["date_to"], _date_to_for_older_than(30))
        self.assertIsNone(kwargs["date_from"])


class OrdinaryMaxMovesRegressionTests(unittest.TestCase):
    """This is a mutation tool: narrowing its working range would itself be a defect."""

    def test_max_moves_ten_id_direct_is_byte_for_byte_unchanged(self):
        script = _id_direct_script(10)
        self.assertIn("if (count of matchingMessages) > 10 then", script)
        self.assertIn("set matchingMessages to items 1 thru 10 of matchingMessages", script)
        self.assertIn("if moveCount >= 10 then", script)

    def test_default_max_moves_is_still_fifty(self):
        capture = _ScriptCapture("MOVED")
        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture):
            manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                message_ids=list(_IDS),
            )
        self.assertIn("set matchingMessages to items 1 thru 50 of matchingMessages", capture.last_script)
        self.assertNotIn("requested, clamped to", capture.last_script)

    def test_max_moves_ten_filter_execute_passes_ten_as_the_search_limit(self):
        capture = _ScriptCapture("MOVED")
        with (
            patch("apple_mail_mcp.tools.manage._search_mail_records", return_value=list(_RECORDS)) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=capture),
        ):
            manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                older_than_days=30,
                allow_filter_scan=True,
                max_moves=10,
                dry_run=False,
            )
        self.assertEqual(mock_search.call_args.kwargs["limit"], 10)
        self.assertIn("items 1 thru 10 of matchingMessages", capture.last_script)

    def test_max_moves_ten_filter_dry_run_still_probes_one_extra(self):
        with (
            patch("apple_mail_mcp.tools.manage._search_mail_records", return_value=list(_RECORDS)) as mock_search,
            patch("apple_mail_mcp.tools.manage.run_applescript"),
        ):
            result = manage_tools.move_email(
                account="Work",
                to_mailbox="Archive",
                older_than_days=30,
                allow_filter_scan=True,
                max_moves=10,
                dry_run=True,
            )
        self.assertEqual(mock_search.call_args.kwargs["limit"], 11)
        self.assertIn("DRY RUN - PREVIEW MOVE", result)

    def test_dry_run_id_direct_still_emits_no_move_command(self):
        script = _id_direct_script(10, dry_run=True)
        self.assertNotIn("move aMessage to destMailbox", script)
        self.assertIn("DRY RUN - PREVIEW MOVE BY IDS", script)

    def test_execute_id_direct_still_emits_the_move_command(self):
        script = _id_direct_script(10, dry_run=False)
        self.assertIn("move aMessage to destMailbox", script)


if __name__ == "__main__":
    unittest.main()
