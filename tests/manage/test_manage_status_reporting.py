"""``update_email_status`` honesty contracts: mutation counting and cap refusal.

Two defects are locked down here.

**1. ``TOTAL UPDATED`` counted reads, not mutations.**
Both branches mutated first and incremented the counter only after three
property reads (``subject`` / ``sender`` / ``date received``) succeeded inside a
bare ``try``. A message whose status was changed but whose subject could not be
read was mutated on the server and reported as *not* updated — and with no
``on error`` arm, nothing said so. ``TOTAL UPDATED: 0`` could mean "nothing
matched" or "everything was flagged and every read threw". For a mutation tool
that is the worst possible ambiguity: the caller re-runs it, or believes the
mutation never happened.

The fix increments the counter at the mutation site, gates the display on an
explicit per-message success flag, and counts read failures on their own
counters surfaced as guarded summary lines. ``update_email_status`` returns its
AppleScript output verbatim (see ``test_mail_search_tools`` ->
``test_update_email_status_with_message_ids_uses_exact_id_condition``, which
asserts ``result == "updated"``), so those lines are human-readable rather than
``|||``-delimited machine markers.

**2. ``_search_message_ids`` resolved one id for ``limit=0``.**
The bound was checked *after* the append, so a "resolve nothing" request came
back with one message id — which every caller feeds straight into a mutation.
Fixed in the shared helper (protecting all four call sites) plus a positive-cap
refusal at the ``update_email_status`` boundary.

Every assertion below is on generated script text or mocked output. Nothing here
touches Mail.
"""

import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import manage as manage_tools
from apple_mail_mcp.tools.manage.helpers import _search_message_ids


def _capture_script(**kwargs) -> str:
    """Return the AppleScript ``update_email_status`` would have run."""
    scripts: list[str] = []

    def fake_run(script, timeout=120):
        scripts.append(script)
        return "ok"

    with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
        manage_tools.update_email_status(account="Work", **kwargs)
    if not scripts:
        raise AssertionError("update_email_status returned before running any script")
    return scripts[-1]


def _id_branch_script(action: str = "mark_read") -> str:
    return _capture_script(action=action, message_ids=["101", "202"])


def _filter_branch_script(action: str = "flag") -> str:
    # mark_read / mark_unread delegate to the id branch via _search_message_ids;
    # only flag / unflag emit the filter-scan script.
    return _capture_script(
        action=action,
        older_than_days=30,
        allow_filter_scan=True,
        max_updates=5,
    )


def _loop_body(script: str, header: str) -> str:
    """Slice a non-nested ``repeat`` body out of a generated script."""
    start = script.find(header)
    if start == -1:
        raise AssertionError(f"loop header not found: {header!r}")
    end = script.find("end repeat", start)
    if end == -1:
        raise AssertionError(f"no 'end repeat' after {header!r}")
    return script[start:end]


def _guard_line_for(script: str, emit_fragment: str) -> str:
    """Return the line immediately preceding the line that emits *emit_fragment*."""
    lines = script.splitlines()
    for index, line in enumerate(lines):
        if emit_fragment in line and "outputText" in line:
            return lines[index - 1].strip()
    raise AssertionError(f"no emit line containing {emit_fragment!r}")


class IdBranchMutationCountingTests(unittest.TestCase):
    """The id branch must count what it mutated, not what it could describe."""

    def test_counter_increments_before_any_property_read(self):
        script = _id_branch_script()
        body = _loop_body(script, "repeat with aMessage in targetMessages")

        increment = body.find("set updateCount to updateCount + 1")
        first_read = body.find("set messageSubject to subject of aMessage")
        gate = body.find("if mutatedThisMessage then")

        self.assertGreater(increment, -1, "mutation loop must increment updateCount")
        self.assertGreater(first_read, -1, "mutation loop must still describe messages")
        self.assertLess(gate, increment, "the counter must be gated on the mutation success flag")
        self.assertLess(
            increment,
            first_read,
            "updateCount must be incremented at the mutation site, before subject/sender/date "
            "reads — counting after the reads reports a display metric as a mutation metric",
        )

    def test_display_is_gated_on_an_explicit_success_flag(self):
        script = _id_branch_script()
        # Tri-state discipline: success is recorded in its own flag, never inferred
        # from an empty/falsy value.
        self.assertIn("set mutatedThisMessage to bulkSucceeded", script)
        self.assertIn("set mutatedThisMessage to true", script)
        self.assertIn("set bulkSucceeded to false", script)
        self.assertIn("set bulkSucceeded to true", script)

    def test_mutation_loop_has_no_bare_try(self):
        script = _id_branch_script()
        body = _loop_body(script, "repeat with aMessage in targetMessages")
        opens = [ln for ln in body.splitlines() if ln.strip() == "try"]
        arms = [ln for ln in body.splitlines() if ln.strip().startswith("on error")]
        self.assertEqual(
            len(opens),
            len(arms),
            "every try in the mutation loop needs an on error arm; a bare try turns a "
            f"loud failure into an exit-0 wrong answer. body was:\n{body}",
        )
        self.assertEqual(len(opens), 2, "expected one try for the mutation and one for the description")

    def test_per_message_mutation_failure_is_counted(self):
        script = _id_branch_script()
        self.assertIn("set updateFailureCount to updateFailureCount + 1", script)

    def test_detail_read_failure_is_counted_separately_from_mutation(self):
        script = _id_branch_script()
        self.assertIn("set detailFailureCount to detailFailureCount + 1", script)

    def test_matched_denominator_is_reported(self):
        script = _id_branch_script()
        self.assertIn("REQUESTED IDS: ", script)
        self.assertIn("MATCHED MESSAGES: ", script)
        self.assertIn("TOTAL UPDATED: ", script)

    def test_bulk_error_marker_is_preserved(self):
        script = _id_branch_script()
        self.assertIn("BULKERR|errNum=", script)

    def test_failure_lines_are_guarded_so_a_clean_run_stays_quiet(self):
        script = _id_branch_script()
        self.assertEqual(
            _guard_line_for(script, "UPDATE FAILURES: "),
            "if updateFailureCount > 0 then",
        )
        self.assertEqual(
            _guard_line_for(script, "DETAILS UNAVAILABLE: "),
            "if detailFailureCount > 0 then",
        )

    def test_all_actions_keep_the_counting_order(self):
        for action in ("mark_read", "mark_unread", "flag", "unflag"):
            with self.subTest(action=action):
                body = _loop_body(_id_branch_script(action), "repeat with aMessage in targetMessages")
                self.assertLess(
                    body.find("set updateCount to updateCount + 1"),
                    body.find("set messageSubject to subject of aMessage"),
                )


class FilterBranchMutationCountingTests(unittest.TestCase):
    """The filter-scan branch must mutate, count, then describe — and report a denominator."""

    def test_counter_increments_before_any_property_read(self):
        script = _filter_branch_script()
        body = _loop_body(script, "repeat with aMessage in matchingMessages")

        mutation = body.find("set flagged status of aMessage")
        increment = body.find("set updateCount to updateCount + 1")
        first_read = body.find("set messageSubject to subject of aMessage")

        self.assertGreater(mutation, -1)
        self.assertLess(mutation, increment, "the counter must follow the mutation it counts")
        self.assertLess(
            increment,
            first_read,
            "a message mutated on the server must be counted even when its subject read throws",
        )

    def test_mutation_loop_has_no_bare_try(self):
        script = _filter_branch_script()
        body = _loop_body(script, "repeat with aMessage in matchingMessages")
        opens = [ln for ln in body.splitlines() if ln.strip() == "try"]
        arms = [ln for ln in body.splitlines() if ln.strip().startswith("on error")]
        self.assertEqual(len(opens), len(arms), f"bare try in mutation loop:\n{body}")
        self.assertEqual(len(opens), 2)

    def test_candidate_selection_loop_has_no_bare_try(self):
        script = _filter_branch_script()
        body = _loop_body(script, "repeat with aMessage in candidateMessages")
        opens = [ln for ln in body.splitlines() if ln.strip() == "try"]
        arms = [ln for ln in body.splitlines() if ln.strip().startswith("on error")]
        self.assertEqual(
            len(opens),
            len(arms),
            "a bare try in the selection loop silently drops candidates from consideration: "
            f"they are never mutated and never reported. body was:\n{body}",
        )
        self.assertIn("set selectionFailureCount to selectionFailureCount + 1", body)

    def test_denominator_lines_distinguish_no_match_from_total_read_failure(self):
        script = _filter_branch_script()
        self.assertIn("CANDIDATES EXAMINED: ", script)
        self.assertIn("MATCHED MESSAGES: ", script)
        self.assertIn("TOTAL UPDATED: ", script)

    def test_failure_lines_are_guarded_so_a_clean_run_stays_quiet(self):
        script = _filter_branch_script()
        self.assertEqual(
            _guard_line_for(script, "SELECTION READ FAILURES: "),
            "if selectionFailureCount > 0 then",
        )
        self.assertEqual(
            _guard_line_for(script, "UPDATE FAILURES: "),
            "if updateFailureCount > 0 then",
        )
        self.assertEqual(
            _guard_line_for(script, "DETAILS UNAVAILABLE: "),
            "if detailFailureCount > 0 then",
        )

    def test_unflag_keeps_the_counting_order(self):
        body = _loop_body(_filter_branch_script("unflag"), "repeat with aMessage in matchingMessages")
        self.assertLess(
            body.find("set updateCount to updateCount + 1"),
            body.find("set messageSubject to subject of aMessage"),
        )


class StatusOutputPassthroughTests(unittest.TestCase):
    """The tool returns AppleScript output verbatim, so failure lines must survive it."""

    _CLEAN = (
        "UPDATING EMAIL STATUS BY IDS: Marked as read\n\n"
        "- Marked as read: Quarterly sync notes\n"
        "   From: sender@example.com\n"
        "   Date: Monday, January 1, 2024\n\n"
        "========================================\n"
        "REQUESTED IDS: 1\n"
        "MATCHED MESSAGES: 1\n"
        "TOTAL UPDATED: 1 email(s)\n"
        "========================================\n"
    )

    _DEGRADED = (
        "UPDATING EMAIL STATUS BY IDS: Marked as read\n\n"
        "========================================\n"
        "REQUESTED IDS: 3\n"
        "MATCHED MESSAGES: 3\n"
        "TOTAL UPDATED: 3 email(s)\n"
        "DETAILS UNAVAILABLE: 3 updated message(s) could not be described above; "
        "the status change still applied\n"
        "========================================\n"
    )

    def test_degraded_run_surfaces_the_mutation_count_and_the_failure(self):
        with patch("apple_mail_mcp.tools.manage.run_applescript", return_value=self._DEGRADED):
            result = manage_tools.update_email_status(account="Work", action="mark_read", message_ids=["1", "2", "3"])
        self.assertIn("TOTAL UPDATED: 3 email(s)", result)
        self.assertIn("DETAILS UNAVAILABLE: 3", result)

    def test_clean_run_stays_quiet(self):
        with patch("apple_mail_mcp.tools.manage.run_applescript", return_value=self._CLEAN):
            result = manage_tools.update_email_status(account="Work", action="mark_read", message_ids=["1"])
        self.assertIn("TOTAL UPDATED: 1 email(s)", result)
        for noise in ("UPDATE FAILURES", "DETAILS UNAVAILABLE", "SELECTION READ FAILURES", "BULKERR"):
            self.assertNotIn(noise, result, f"a clean run must not mention {noise}")


class SearchMessageIdsLimitTests(unittest.TestCase):
    """``_search_message_ids`` feeds mutations; a non-positive limit must resolve nothing."""

    @staticmethod
    def _records(count: int) -> list[dict[str, object]]:
        return [{"message_id": 100 + i, "subject": f"Synthetic {i}"} for i in range(count)]

    def _resolve(self, limit: int, records: list[dict[str, object]] | None = None):
        recorded = {"called": False}

        def fake_records(**kwargs):
            recorded["called"] = True
            return self._records(5) if records is None else records

        with patch("apple_mail_mcp.tools.manage._search_mail_records", side_effect=fake_records):
            ids = _search_message_ids(account="Work", mailbox="INBOX", limit=limit)
        return ids, recorded["called"]

    def test_limit_zero_resolves_no_ids_and_runs_no_search(self):
        ids, called = self._resolve(0)
        self.assertEqual(
            ids,
            [],
            "limit=0 must resolve zero ids; every caller feeds this list into a mutation, "
            "so returning one id deletes/moves/flags mail nobody asked to touch",
        )
        self.assertFalse(called, "a zero-limit request must not even run the search")

    def test_negative_limit_resolves_no_ids(self):
        ids, called = self._resolve(-1)
        self.assertEqual(ids, [])
        self.assertFalse(called)

    def test_limit_one_still_resolves_exactly_one(self):
        ids, called = self._resolve(1)
        self.assertEqual(ids, ["100"], "the boundary the fix touches must not regress to zero")
        self.assertTrue(called)

    def test_limit_below_record_count_is_respected(self):
        ids, _ = self._resolve(3)
        self.assertEqual(ids, ["100", "101", "102"])

    def test_limit_above_record_count_returns_all(self):
        ids, _ = self._resolve(50)
        self.assertEqual(ids, ["100", "101", "102", "103", "104"])

    def test_records_without_message_id_do_not_consume_the_limit(self):
        records: list[dict[str, object]] = [
            {"message_id": None, "subject": "Synthetic skipped"},
            {"subject": "Synthetic missing key"},
            {"message_id": 900, "subject": "Synthetic kept"},
            {"message_id": 901, "subject": "Synthetic kept too"},
        ]
        ids, _ = self._resolve(2, records=records)
        self.assertEqual(ids, ["900", "901"])


class MaxUpdatesBoundaryTests(unittest.TestCase):
    """``update_email_status`` refuses a non-positive cap before touching Mail."""

    def test_zero_max_updates_is_refused_before_any_applescript(self):
        with patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run:
            result = manage_tools.update_email_status(
                account="Work",
                action="flag",
                older_than_days=30,
                allow_filter_scan=True,
                max_updates=0,
            )
        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "INVALID_ACTION_CAP")
        self.assertIn("max_updates", payload["message"])

    def test_negative_max_updates_is_refused(self):
        with patch("apple_mail_mcp.tools.manage.run_applescript") as mock_run:
            result = manage_tools.update_email_status(
                account="Work", action="mark_read", message_ids=["42"], max_updates=-5
            )
        mock_run.assert_not_called()
        self.assertEqual(json.loads(result)["code"], "INVALID_ACTION_CAP")

    def test_max_updates_of_one_is_accepted(self):
        with patch("apple_mail_mcp.tools.manage.run_applescript", return_value="ok") as mock_run:
            result = manage_tools.update_email_status(
                account="Work", action="mark_read", message_ids=["42"], max_updates=1
            )
        mock_run.assert_called_once()
        self.assertEqual(result, "ok", "the mirror image: a valid cap must still run normally")

    def test_default_max_updates_is_accepted(self):
        with patch("apple_mail_mcp.tools.manage.run_applescript", return_value="ok") as mock_run:
            manage_tools.update_email_status(account="Work", action="mark_read", message_ids=["42"])
        mock_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
