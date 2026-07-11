"""Tests for core.reply_state: native was-replied fragment + Drafts snapshot/correlation.

Mirrors the mocking conventions in tests/core/test_core_fetch_replied_ids.py
(injectable ``runner`` seam, no real Mail.app) and the osacompile parse-check
convention in tests/cross_cutting/test_applescript_builders_compile.py
(skipped when ``osacompile`` is unavailable, e.g. on Ubuntu CI).
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core.applescript import AppleScriptTimeout
from apple_mail_mcp.core.reply_state import (
    DRAFTS_MAILBOX_NAMES,
    DraftsSnapshot,
    _DraftRow,
    _extract_bare_email,
    _parse_drafts_snapshot_output,
    build_drafts_snapshot_script,
    drafts_mailbox_block,
    fetch_drafts_snapshot,
    normalize_thread_subject,
    resolve_has_draft,
    was_replied_fragment,
)

# ---------------------------------------------------------------------------
# osacompile availability (mirrors test_applescript_builders_compile.py)
# ---------------------------------------------------------------------------

_OSACOMPILE_AVAILABLE = shutil.which("osacompile") is not None


def _osacompile_check(script: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as src_f:
        src_f.write(script)
        src_path = src_f.name
    out_path = src_path.replace(".applescript", ".scpt")
    try:
        result = subprocess.run(
            ["osacompile", "-o", out_path, src_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout).strip()
        return True, ""
    finally:
        for p in (src_path, out_path):
            with contextlib.suppress(OSError):
                Path(p).unlink()


# ---------------------------------------------------------------------------
# was_replied_fragment
# ---------------------------------------------------------------------------


class WasRepliedFragmentTests(unittest.TestCase):
    def test_default_var_reads_was_replied_to(self):
        fragment = was_replied_fragment()
        self.assertIn("was replied to of aMessage", fragment)
        self.assertIn("wasRepliedToken", fragment)

    def test_custom_var_name_is_used(self):
        fragment = was_replied_fragment(var="aDraftCandidate")
        self.assertIn("was replied to of aDraftCandidate", fragment)

    def test_wrapped_in_try_for_safety(self):
        fragment = was_replied_fragment()
        self.assertIn("try", fragment)
        self.assertIn("end try", fragment)

    def test_compiles_as_applescript_fragment(self):
        if not _OSACOMPILE_AVAILABLE:
            self.skipTest("osacompile not available on this platform")
        script = f"""
        tell application "Mail"
            try
                set aMessage to missing value
                {was_replied_fragment()}
                return wasRepliedToken
            end try
        end tell
        """
        ok, err = _osacompile_check(script)
        self.assertTrue(ok, f"was_replied_fragment produced invalid AppleScript:\n{err}")


# ---------------------------------------------------------------------------
# drafts_mailbox_block
# ---------------------------------------------------------------------------


class DraftsMailboxBlockTests(unittest.TestCase):
    def test_contains_all_locale_fallback_names(self):
        block = drafts_mailbox_block()
        for name in ("Drafts", "Brouillons", "Entwürfe", "Borradores"):
            self.assertIn(f'"{name}"', block)

    def test_names_constant_matches_block_contents(self):
        block = drafts_mailbox_block()
        for name in DRAFTS_MAILBOX_NAMES:
            self.assertIn(f'"{name}"', block)
        self.assertEqual(DRAFTS_MAILBOX_NAMES, ["Drafts", "Brouillons", "Entwürfe", "Borradores"])

    def test_default_var_and_account_names(self):
        block = drafts_mailbox_block()
        self.assertIn("set draftsMailbox to missing value", block)
        self.assertIn("of targetAccount", block)

    def test_custom_var_and_account_names(self):
        block = drafts_mailbox_block(var_name="myDrafts", account_var="myAccount")
        self.assertIn("set myDrafts to missing value", block)
        self.assertIn("of myAccount", block)

    def test_sets_missing_value_when_none_found(self):
        block = drafts_mailbox_block()
        self.assertIn("missing value", block)

    def test_compiles_as_applescript_fragment(self):
        if not _OSACOMPILE_AVAILABLE:
            self.skipTest("osacompile not available on this platform")
        script = f"""
        tell application "Mail"
            try
                set targetAccount to missing value
                {drafts_mailbox_block()}
                return draftsMailbox as string
            end try
        end tell
        """
        ok, err = _osacompile_check(script)
        self.assertTrue(ok, f"drafts_mailbox_block produced invalid AppleScript:\n{err}")


# ---------------------------------------------------------------------------
# build_drafts_snapshot_script
# ---------------------------------------------------------------------------


class BuildDraftsSnapshotScriptTests(unittest.TestCase):
    def test_contains_fallback_names(self):
        script = build_drafts_snapshot_script(account_name="Work", drafts_cap=75, header_cap=10)
        for name in DRAFTS_MAILBOX_NAMES:
            self.assertIn(f'"{name}"', script)

    def test_contains_caps(self):
        script = build_drafts_snapshot_script(account_name="Work", drafts_cap=42, header_cap=7)
        self.assertIn("42", script)
        self.assertIn("headerReadCap to 7", script)

    def test_escapes_account_name(self):
        script = build_drafts_snapshot_script(account_name='Work "Quoted"', drafts_cap=10, header_cap=5)
        self.assertIn('Work \\"Quoted\\"', script)

    def test_emits_draft_row_prefix_and_count_sentinel(self):
        script = build_drafts_snapshot_script(account_name="Work", drafts_cap=10, header_cap=5)
        self.assertIn('"DRAFT|||"', script)
        self.assertIn('"COUNT|||"', script)

    def test_wraps_per_draft_read_in_try(self):
        script = build_drafts_snapshot_script(account_name="Work", drafts_cap=10, header_cap=5)
        self.assertIn("repeat with aDraft in draftMessages", script)
        # A fallback DRAFT row is emitted on failure so one bad draft
        # never silently drops the scanned count.
        self.assertIn('set end of outputLines to "DRAFT|||" & "" & "|||" & "" & "|||" & "" & "|||" & ""', script)

    def test_wraps_whole_scan_in_try(self):
        script = build_drafts_snapshot_script(account_name="Work", drafts_cap=10, header_cap=5)
        self.assertIn('"ERROR|||"', script)

    def test_compiles_as_applescript(self):
        if not _OSACOMPILE_AVAILABLE:
            self.skipTest("osacompile not available on this platform")
        script = build_drafts_snapshot_script(account_name="Work", drafts_cap=75, header_cap=10)
        ok, err = _osacompile_check(script)
        self.assertTrue(ok, f"build_drafts_snapshot_script produced invalid AppleScript:\n{err}")


# ---------------------------------------------------------------------------
# _parse_drafts_snapshot_output
# ---------------------------------------------------------------------------


class ParseDraftsSnapshotOutputTests(unittest.TestCase):
    def test_well_formed_rows(self):
        raw = (
            "DRAFT|||Re: Budget|||alice@example.com|||2026-07-09T10:00:00|||<abc@x> \n"
            "DRAFT|||Hello|||bob@example.com|||2026-07-08T09:00:00|||\n"
            "COUNT|||2"
        )
        rows, scanned, _total = _parse_drafts_snapshot_output(raw)
        self.assertEqual(len(rows), 2)
        self.assertEqual(scanned, 2)
        self.assertEqual(rows[0].subject, "Re: Budget")
        self.assertEqual(rows[0].first_to_recipient, "alice@example.com")
        self.assertEqual(rows[0].date_text, "2026-07-09T10:00:00")
        self.assertIn("abc@x", rows[0].header_blob)

    def test_malformed_row_is_skipped(self):
        raw = "DRAFT|||only two fields\nDRAFT|||Hello|||bob@example.com|||2026-07-08T09:00:00|||\nCOUNT|||1"
        rows, scanned, _total = _parse_drafts_snapshot_output(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].subject, "Hello")
        self.assertEqual(scanned, 1)

    def test_empty_output_returns_empty(self):
        rows, scanned, _total = _parse_drafts_snapshot_output("")
        self.assertEqual(rows, [])
        self.assertEqual(scanned, 0)

    def test_count_only_output_no_rows(self):
        rows, scanned, _total = _parse_drafts_snapshot_output("COUNT|||0")
        self.assertEqual(rows, [])
        self.assertEqual(scanned, 0)

    def test_total_above_cap_marks_snapshot_as_truncated(self):
        raw = "DRAFT|||Hello|||bob@example.com|||2026-07-08T09:00:00|||\nCOUNT|||1\nTOTAL|||51"

        rows, scanned, total = _parse_drafts_snapshot_output(raw)

        self.assertEqual(len(rows), 1)
        self.assertEqual(scanned, 1)
        self.assertEqual(total, 51)

    def test_non_numeric_count_falls_back_to_row_count(self):
        raw = "DRAFT|||Hello|||bob@example.com|||2026-07-08T09:00:00|||\nCOUNT|||not-a-number"
        rows, scanned, _total = _parse_drafts_snapshot_output(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(scanned, 1)

    def test_missing_count_line_falls_back_to_row_count(self):
        raw = "DRAFT|||Hello|||bob@example.com|||2026-07-08T09:00:00|||"
        rows, scanned, _total = _parse_drafts_snapshot_output(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(scanned, 1)

    def test_blank_fallback_row_parses_to_empty_fields(self):
        raw = "DRAFT|||" + "" + "|||" + "" + "|||" + "" + "|||" + ""
        rows, scanned, _total = _parse_drafts_snapshot_output(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].subject, "")
        self.assertEqual(rows[0].first_to_recipient, "")
        self.assertEqual(scanned, 1)


# ---------------------------------------------------------------------------
# normalize_thread_subject
# ---------------------------------------------------------------------------


class NormalizeThreadSubjectTests(unittest.TestCase):
    def test_strips_single_prefix(self):
        self.assertEqual(normalize_thread_subject("Re: Quarterly report"), "quarterly report")

    def test_strips_chained_mixed_case_prefixes(self):
        self.assertEqual(
            normalize_thread_subject("RE: Fwd: FW: Quarterly report"),
            "quarterly report",
        )
        self.assertEqual(
            normalize_thread_subject("re: re: fwd: Quarterly report"),
            "quarterly report",
        )

    def test_casefold_equality(self):
        self.assertEqual(
            normalize_thread_subject("QUARTERLY REPORT"),
            normalize_thread_subject("quarterly report"),
        )

    def test_collapses_and_trims_whitespace(self):
        self.assertEqual(normalize_thread_subject("  Quarterly    report  "), "quarterly report")

    def test_no_prefix_unchanged_besides_case_and_whitespace(self):
        self.assertEqual(normalize_thread_subject("Budget Notes"), "budget notes")

    def test_empty_subject(self):
        self.assertEqual(normalize_thread_subject(""), "")


# ---------------------------------------------------------------------------
# _extract_bare_email
# ---------------------------------------------------------------------------


class ExtractBareEmailTests(unittest.TestCase):
    def test_name_wrapped_address(self):
        self.assertEqual(_extract_bare_email("Alice Smith <alice@example.com>"), "alice@example.com")

    def test_bare_address_unchanged(self):
        self.assertEqual(_extract_bare_email("alice@example.com"), "alice@example.com")

    def test_strips_whitespace(self):
        self.assertEqual(_extract_bare_email("  alice@example.com  "), "alice@example.com")


# ---------------------------------------------------------------------------
# DraftsSnapshot.matches
# ---------------------------------------------------------------------------


def _snapshot(rows: list[_DraftRow], *, status: str = "ok", scanned: int | None = None) -> DraftsSnapshot:
    return DraftsSnapshot(
        status=status,  # type: ignore[arg-type]
        scanned=scanned if scanned is not None else len(rows),
        account="Work",
        rows=tuple(rows),
    )


class DraftsSnapshotMatchesTests(unittest.TestCase):
    def test_header_path_matches_bracketed_message_id(self):
        rows = [_DraftRow(subject="", first_to_recipient="", date_text="", header_blob="<abc@example.com> <def@x>")]
        snapshot = _snapshot(rows)
        self.assertTrue(
            snapshot.matches(
                subject="Anything",
                sender_email="nobody@example.com",
                internet_message_id="<abc@example.com>",
                email_date=None,
            )
        )

    def test_header_path_matches_unbracketed_message_id(self):
        rows = [_DraftRow(subject="", first_to_recipient="", date_text="", header_blob="<abc@example.com>")]
        snapshot = _snapshot(rows)
        self.assertTrue(
            snapshot.matches(
                subject="Anything",
                sender_email="nobody@example.com",
                internet_message_id="abc@example.com",
                email_date=None,
            )
        )

    def test_null_internet_message_id_falls_through_to_subject_path(self):
        rows = [
            _DraftRow(
                subject="Re: Budget",
                first_to_recipient="alice@example.com",
                date_text="2026-07-10T10:00:00",
                header_blob="",
            )
        ]
        snapshot = _snapshot(rows)
        self.assertTrue(
            snapshot.matches(
                subject="Budget",
                sender_email="alice@example.com",
                internet_message_id=None,
                email_date="2026-07-09T09:00:00",
            )
        )

    def test_subject_and_recipient_and_date_path(self):
        rows = [
            _DraftRow(
                subject="RE: Project Kickoff",
                first_to_recipient="Alice Smith <alice@example.com>",
                date_text="2026-07-10T12:00:00",
                header_blob="",
            )
        ]
        snapshot = _snapshot(rows)
        self.assertTrue(
            snapshot.matches(
                subject="Project Kickoff",
                sender_email="Alice Smith <alice@example.com>",
                internet_message_id="<missing@x>",
                email_date="2026-07-10T09:00:00",
            )
        )

    def test_recipient_mismatch_rejects(self):
        rows = [
            _DraftRow(
                subject="Project Kickoff",
                first_to_recipient="bob@example.com",
                date_text="2026-07-10T12:00:00",
                header_blob="",
            )
        ]
        snapshot = _snapshot(rows)
        self.assertFalse(
            snapshot.matches(
                subject="Project Kickoff",
                sender_email="alice@example.com",
                internet_message_id=None,
                email_date="2026-07-10T09:00:00",
            )
        )

    def test_subject_mismatch_rejects(self):
        rows = [
            _DraftRow(
                subject="Something else",
                first_to_recipient="alice@example.com",
                date_text="2026-07-10T12:00:00",
                header_blob="",
            )
        ]
        snapshot = _snapshot(rows)
        self.assertFalse(
            snapshot.matches(
                subject="Project Kickoff",
                sender_email="alice@example.com",
                internet_message_id=None,
                email_date="2026-07-10T09:00:00",
            )
        )

    def test_date_rule_rejects_draft_older_than_email_by_more_than_one_day_via_subject_path(self):
        rows = [
            _DraftRow(
                subject="Project Kickoff",
                first_to_recipient="alice@example.com",
                date_text="2026-07-01T09:00:00",  # far older than the candidate email
                header_blob="",
            )
        ]
        snapshot = _snapshot(rows)
        self.assertFalse(
            snapshot.matches(
                subject="Project Kickoff",
                sender_email="alice@example.com",
                internet_message_id=None,
                email_date="2026-07-10T09:00:00",
            )
        )

    def test_date_rule_bypassed_and_accepted_via_header_path(self):
        # Same stale draft date, but the header blob carries the candidate's
        # Message-ID: rule 1 short-circuits before the date rule ever runs.
        rows = [
            _DraftRow(
                subject="Project Kickoff",
                first_to_recipient="alice@example.com",
                date_text="2026-07-01T09:00:00",
                header_blob="<candidate@example.com>",
            )
        ]
        snapshot = _snapshot(rows)
        self.assertTrue(
            snapshot.matches(
                subject="Project Kickoff",
                sender_email="alice@example.com",
                internet_message_id="<candidate@example.com>",
                email_date="2026-07-10T09:00:00",
            )
        )

    def test_date_within_one_day_slack_accepted(self):
        rows = [
            _DraftRow(
                subject="Project Kickoff",
                first_to_recipient="alice@example.com",
                date_text="2026-07-09T10:00:00",  # exactly within 1 day before
                header_blob="",
            )
        ]
        snapshot = _snapshot(rows)
        self.assertTrue(
            snapshot.matches(
                subject="Project Kickoff",
                sender_email="alice@example.com",
                internet_message_id=None,
                email_date="2026-07-10T09:00:00",
            )
        )

    def test_unparseable_draft_date_treated_as_satisfied(self):
        rows = [
            _DraftRow(
                subject="Project Kickoff",
                first_to_recipient="alice@example.com",
                date_text="(unknown)",
                header_blob="",
            )
        ]
        snapshot = _snapshot(rows)
        self.assertTrue(
            snapshot.matches(
                subject="Project Kickoff",
                sender_email="alice@example.com",
                internet_message_id=None,
                email_date="2026-07-10T09:00:00",
            )
        )

    def test_unparseable_email_date_treated_as_satisfied(self):
        rows = [
            _DraftRow(
                subject="Project Kickoff",
                first_to_recipient="alice@example.com",
                date_text="2026-01-01T09:00:00",
                header_blob="",
            )
        ]
        snapshot = _snapshot(rows)
        self.assertTrue(
            snapshot.matches(
                subject="Project Kickoff",
                sender_email="alice@example.com",
                internet_message_id=None,
                email_date="not-a-real-date",
            )
        )

    def test_error_status_never_matches(self):
        snapshot = DraftsSnapshot(status="error", scanned=0, account="Work", error="boom")
        self.assertFalse(
            snapshot.matches(subject="x", sender_email="a@b.com", internet_message_id=None, email_date=None)
        )

    def test_skipped_status_never_matches(self):
        snapshot = DraftsSnapshot(status="skipped", scanned=0, account="Work")
        self.assertFalse(
            snapshot.matches(subject="x", sender_email="a@b.com", internet_message_id=None, email_date=None)
        )

    def test_empty_rows_never_matches(self):
        snapshot = _snapshot([])
        self.assertFalse(
            snapshot.matches(subject="x", sender_email="a@b.com", internet_message_id=None, email_date=None)
        )

    def test_empty_subject_never_matches_via_subject_path(self):
        rows = [
            _DraftRow(
                subject="", first_to_recipient="alice@example.com", date_text="2026-07-10T09:00:00", header_blob=""
            )
        ]
        snapshot = _snapshot(rows)
        self.assertFalse(
            snapshot.matches(subject="", sender_email="alice@example.com", internet_message_id=None, email_date=None)
        )

    def test_truncated_snapshot_preserves_found_match(self):
        snapshot = DraftsSnapshot(
            status="ok",
            scanned=1,
            total=51,
            account="Work",
            rows=(_DraftRow("Project", "alice@example.com", "", ""),),
        )

        self.assertTrue(
            resolve_has_draft(
                snapshot,
                subject="Project",
                sender_email="alice@example.com",
                internet_message_id=None,
                email_date=None,
            )
        )

    def test_truncated_snapshot_returns_unknown_for_nonmatch_beyond_cap(self):
        snapshot = DraftsSnapshot(status="ok", scanned=50, total=51, account="Work")

        self.assertIsNone(
            resolve_has_draft(
                snapshot,
                subject="Not in first fifty",
                sender_email="alice@example.com",
                internet_message_id=None,
                email_date=None,
            )
        )


# ---------------------------------------------------------------------------
# fetch_drafts_snapshot
# ---------------------------------------------------------------------------


def _fake_runner_returning(output: str):
    def _runner(script, timeout=60):
        return output

    return _runner


def _fake_runner_raising(exc: Exception):
    def _runner(script, timeout=60):
        raise exc

    return _runner


class FetchDraftsSnapshotTests(unittest.TestCase):
    def test_happy_path_returns_ok_with_parsed_rows(self):
        raw = "DRAFT|||Re: Budget|||alice@example.com|||2026-07-09T10:00:00|||\nCOUNT|||1"
        snapshot = fetch_drafts_snapshot(account="Work", runner=_fake_runner_returning(raw), timeout=30)
        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(snapshot.scanned, 1)
        self.assertEqual(snapshot.account, "Work")
        self.assertIsNone(snapshot.error)
        self.assertEqual(len(snapshot.rows), 1)

    def test_none_account_returns_skipped_and_never_raises(self):
        snapshot = fetch_drafts_snapshot(account=None, runner=_fake_runner_returning(""), timeout=30)
        self.assertEqual(snapshot.status, "skipped")
        self.assertEqual(snapshot.scanned, 0)

    def test_empty_string_account_returns_skipped(self):
        snapshot = fetch_drafts_snapshot(account="", runner=_fake_runner_returning(""), timeout=30)
        self.assertEqual(snapshot.status, "skipped")

    def test_applescript_timeout_returns_error_and_never_raises(self):
        snapshot = fetch_drafts_snapshot(
            account="Work",
            runner=_fake_runner_raising(AppleScriptTimeout("timed out")),
            timeout=30,
        )
        self.assertEqual(snapshot.status, "error")
        self.assertIn("timeout", (snapshot.error or "").lower())

    def test_generic_exception_returns_error_and_never_raises(self):
        snapshot = fetch_drafts_snapshot(
            account="Work",
            runner=_fake_runner_raising(RuntimeError("AppleScript error: -1728")),
            timeout=30,
        )
        self.assertEqual(snapshot.status, "error")
        self.assertIn("AppleScript error", snapshot.error or "")

    def test_error_prefixed_output_returns_error_status(self):
        snapshot = fetch_drafts_snapshot(
            account="Work",
            runner=_fake_runner_returning("ERROR|||Could not find Drafts mailbox"),
            timeout=30,
        )
        self.assertEqual(snapshot.status, "error")
        self.assertIn("Could not find Drafts mailbox", snapshot.error or "")

    def test_empty_output_returns_ok_with_zero_scanned(self):
        snapshot = fetch_drafts_snapshot(account="Work", runner=_fake_runner_returning(""), timeout=30)
        self.assertEqual(snapshot.status, "ok")
        self.assertEqual(snapshot.scanned, 0)
        self.assertEqual(snapshot.rows, ())

    def test_default_reply_state_caps_use_fifty_draft_ceiling(self):
        captured: dict[str, str] = {}

        def _runner(script, timeout=60):
            captured["script"] = script
            return "COUNT|||0"

        fetch_drafts_snapshot(account="Work", runner=_runner, timeout=30)
        self.assertEqual(SCAN_BOUNDS["DRAFT_SNAPSHOT_CAP"], 50)
        self.assertIn("if headEnd > 50 then set headEnd to 50", captured["script"])
        self.assertIn(f"headerReadCap to {SCAN_BOUNDS['DRAFT_SNAPSHOT_HEADER_CAP']}", captured["script"])

    def test_explicit_caps_override_defaults(self):
        captured: dict[str, str] = {}

        def _runner(script, timeout=60):
            captured["script"] = script
            return "COUNT|||0"

        fetch_drafts_snapshot(account="Work", runner=_runner, timeout=30, drafts_cap=5, header_cap=2)
        self.assertIn("headerReadCap to 2", captured["script"])

    def test_never_raises_across_a_battery_of_exceptions(self):
        for exc in (RuntimeError("x"), OSError("y"), PermissionError("z"), ValueError("w")):
            snapshot = fetch_drafts_snapshot(account="Work", runner=_fake_runner_raising(exc), timeout=30)
            self.assertEqual(snapshot.status, "error")


# ---------------------------------------------------------------------------
# DraftsSnapshot construction
# ---------------------------------------------------------------------------


class DraftsSnapshotConstructionTests(unittest.TestCase):
    def test_skipped_snapshot_defaults(self):
        snapshot = DraftsSnapshot(status="skipped", scanned=0, account="Work")
        self.assertEqual(snapshot.status, "skipped")
        self.assertIsNone(snapshot.error)
        self.assertEqual(snapshot.rows, ())

    def test_ok_snapshot_with_rows(self):
        row = _DraftRow(subject="Hi", first_to_recipient="a@b.com", date_text="2026-07-10T09:00:00", header_blob="")
        snapshot = DraftsSnapshot(status="ok", scanned=1, account="Work", rows=(row,))
        self.assertEqual(len(snapshot.rows), 1)


if __name__ == "__main__":
    pytest.main([__file__])
