"""Export must not report a count it did not write (AGENTIC-2363 item 3).

Every ``export_emails`` builder wraps its per-message work in a ``try`` so one
bad message cannot abort a bounded page. Two of those arms were bare, and the
counts were incremented *before* the file write, so a swallowed write failure
produced output like ``✓ Mailbox exported successfully! / Exported: 50`` over a
directory holding 49 files. In the ``correspondent`` scope the same
pre-increment advanced the ``offset`` bookkeeping, so the caller's next page
stepped over a message that was never written, with nothing anywhere in the
output to show it had existed.

WHAT THESE TESTS CAN AND CANNOT PROVE
-------------------------------------
The counting happens inside AppleScript, in Mail's address space. These tests
assert on the *emitted script* (ordering, guards, which variable is reported)
and on the tool's *surfacing* of a failure report, and they compile every
builder with ``osacompile``. That is the strongest offline evidence available:
"the emitted script no longer pre-increments the reported count" is a weaker
claim than "an export was observed reporting an honest count", and only the
first is testable without a live mailbox.

No test here runs ``osascript`` or touches Mail.app. Tool-level tests mock
``run_applescript`` and additionally poison ``subprocess.run`` so an accidental
live call fails loudly, and every path is written to a pytest ``tmp_path``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from apple_mail_mcp.tools import analytics as analytics_tools
from apple_mail_mcp.tools.analytics import export_helpers as helpers

_OSACOMPILE = shutil.which("osacompile")


# ---------------------------------------------------------------------------
# Builders, driven directly (no Mail, no tool boundary, no path validation)
# ---------------------------------------------------------------------------


def _common(tmp_path: Path) -> dict[str, str]:
    return {
        "safe_account": "Work",
        "safe_format": "txt",
        "safe_save_dir": str(tmp_path),
    }


def _mailbox_script(tmp_path: Path, *, offset: int = 0, max_emails: int = 25) -> str:
    return helpers.build_entire_mailbox_export_script(
        mailbox="INBOX",
        max_emails=max_emails,
        offset=offset,
        sort="newest_first",
        date_setup="",
        date_filter="",
        **_common(tmp_path),
    )


def _correspondent_script(tmp_path: Path, *, offset: int = 0, max_emails: int = 25) -> str:
    return helpers.build_correspondent_export_script(
        safe_email_address="person@example.com",
        mailbox="INBOX",
        scan_upper_bound=250,
        max_emails=max_emails,
        offset=offset,
        include_sent=True,
        date_setup="",
        date_filter="",
        **_common(tmp_path),
    )


def _exact_id_script(tmp_path: Path) -> str:
    return helpers.build_exact_message_export_script(mailbox="INBOX", message_ids=["101", "202"], **_common(tmp_path))


def _multi_mailbox_script(tmp_path: Path) -> str:
    return helpers.build_multi_mailbox_id_export_script(
        candidate_mailboxes=["INBOX", "Sent"],
        message_ids=["101"],
        **_common(tmp_path),
    )


def _pos(script: str, needle: str) -> int:
    index = script.find(needle)
    assert index != -1, f"expected {needle!r} in the emitted script"
    return index


def _emitted_only_on_failure(script: str, needle: str) -> bool:
    """True when every line emitting *needle* sits inside ``if exportFailureCount > 0``.

    Walks back from each emitting line to the nearest ``if exportFailureCount > 0
    then`` and fails if an ``end if`` closes the guard first. This is what keeps a
    clean export from growing a spurious failure report.
    """
    lines = script.splitlines()
    found = False
    for index, line in enumerate(lines):
        if needle not in line:
            continue
        found = True
        guarded = False
        for previous in reversed(lines[:index]):
            stripped = previous.strip()
            if stripped == "end if":
                break
            if stripped.startswith("if exportFailureCount > 0 then"):
                guarded = True
                break
        if not guarded:
            return False
    assert found, f"expected the script to emit {needle!r} at all"
    return True


# ---------------------------------------------------------------------------
# Claim 1 — the reported count must follow the write
# ---------------------------------------------------------------------------


def test_mailbox_export_reports_only_files_that_reached_disk(tmp_path):
    script = _mailbox_script(tmp_path)

    # The reported count is written-and-closed, never attempted.
    assert 'set outputText to outputText & "Exported: " & writtenCount & return' in script
    assert '"Exported: " & exportCount' not in script

    write_pos = _pos(script, "write exportContent to fileRef as «class utf8»")
    close_pos = _pos(script, "close access fileRef")
    counted_pos = _pos(script, "set writtenCount to writtenCount + 1")
    assert write_pos < counted_pos, "the honest count must be incremented after the write"
    assert close_pos < counted_pos, "the honest count must be incremented after the handle is closed"

    # exportCount survives only as the filename index, which necessarily precedes
    # the write because it names the file. It must not be what gets reported.
    index_pos = _pos(script, "set exportCount to exportCount + 1")
    assert index_pos < write_pos
    assert 'set fileName to exportCount & "_" & messageSubject' in script


@pytest.mark.parametrize("builder", [_exact_id_script, _multi_mailbox_script])
def test_by_id_exports_report_written_count_not_attempted_count(builder, tmp_path):
    script = builder(tmp_path)

    assert 'set outputText to outputText & "Exported: " & writtenCount & return' in script
    assert '"Exported: " & exportCount' not in script
    assert _pos(script, "write exportContent to fileRef") < _pos(script, "set writtenCount to writtenCount + 1")


def test_correspondent_reports_written_count_not_attempted_count(tmp_path):
    script = _correspondent_script(tmp_path)

    assert 'set outputText to outputText & "Exported: " & writtenCount & return' in script
    assert '"Exported: " & totalExportCount' not in script
    # totalExportCount still names the file and gates the page, so it still
    # advances per attempt — it just is not what the caller is told.
    assert 'set fileName to totalExportCount & "_" & mailboxName' in script
    assert "if totalExportCount >= 25 then exit repeat" in script
    # The per-mailbox line is a written count too, not an attempt count.
    assert _pos(script, "write exportContent to fileRef") < _pos(
        script, "set mailboxExportCount to mailboxExportCount + 1"
    )


# ---------------------------------------------------------------------------
# A swallowed failure must reach the caller
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "builder,id_expression",
    [
        (_mailbox_script, "failedMessageId"),
        (_correspondent_script, "failedMessageId"),
        (_exact_id_script, "requestedIdText"),
        (_multi_mailbox_script, "requestedIdText"),
    ],
)
def test_every_export_failure_arm_reports_instead_of_swallowing(builder, id_expression, tmp_path):
    script = builder(tmp_path)

    # No bare `on error` arm is left anywhere in an export builder: each one
    # binds the error, counts it, and names the message it lost.
    assert "on error exportErr" in script
    assert "set exportFailureCount to exportFailureCount + 1" in script
    assert (
        f'set outputText to outputText & "Error exporting message_id " & {id_expression} & ": " & exportErr' in script
    )

    # The summary is self-consistent with the per-message lines.
    assert 'set outputText to outputText & "Failed: " & exportFailureCount & return' in script
    assert '"PARTIAL: "' in script


def test_mailbox_export_success_banner_degrades_when_a_write_failed(tmp_path):
    script = _mailbox_script(tmp_path)

    assert "if exportFailureCount > 0 then" in script
    assert '"⚠ Mailbox exported with errors"' in script
    assert '"✓ Mailbox exported successfully!"' in script
    # The checkmark must be the else branch, not an unconditional banner.
    assert _pos(script, '"⚠ Mailbox exported with errors"') < _pos(script, '"✓ Mailbox exported successfully!"')


# ---------------------------------------------------------------------------
# Claim 2 — the offset/paging counter must not advance past an unwritten message
# ---------------------------------------------------------------------------


def test_correspondent_paging_stops_at_the_first_message_it_could_not_write(tmp_path):
    script = _correspondent_script(tmp_path, offset=7)

    # offset counts matched messages, so the scan halts rather than walking on
    # past a match that produced no file.
    assert "set exportHalted to true" in script
    halt_pos = _pos(script, "set exportHalted to true")
    arm_pos = _pos(script, "on error exportErr")
    assert arm_pos < halt_pos, "the halt belongs to the failure arm"
    assert "exit repeat" in script[halt_pos : halt_pos + 200], "the failure arm must leave the message loop"
    # The outer mailbox loop honours the halt too, so no later mailbox consumes
    # matched positions the caller has not been given.
    assert "if exportHalted then exit repeat" in script
    assert _pos(script, "if exportHalted then exit repeat") < _pos(script, "repeat with aMessage in mailboxMessages")


def test_correspondent_halts_only_for_a_message_that_consumed_an_offset_slot(tmp_path):
    """A read failure before the offset gate must report but not wedge the scope.

    Halting on a message that was never counted as matched would be permanent:
    the retry re-scans from the same place and lands on the same bad message.
    Only a message that passed the ``offset`` gate and then failed to produce a
    file is unrecoverable, so only that one halts.
    """
    script = _correspondent_script(tmp_path, offset=7)

    assert "if exportAttempted then" in script
    assert _pos(script, "if exportAttempted then") < _pos(script, "set exportHalted to true")

    # The flag resets every iteration and is only raised past the offset gate,
    # so a throw in the date/correspondent read leaves it false.
    reset_pos = _pos(script, "set exportAttempted to false")
    gate_pos = _pos(script, "if globalMatchedCount > 7 then")
    raised_pos = _pos(script, "set exportAttempted to true")
    match_read_pos = _pos(script, "set shouldExport to my messageHasCorrespondent")
    assert reset_pos < match_read_pos < gate_pos < raised_pos
    assert _pos(script, "repeat with aMessage in mailboxMessages") < reset_pos


def test_correspondent_resume_offset_is_derived_from_the_written_count(tmp_path):
    script = _correspondent_script(tmp_path, offset=7)

    # offset + writtenCount is exactly the position of the failed message, so a
    # retry re-attempts it instead of skipping it. Deriving the hint from
    # totalExportCount (attempts) would reintroduce the skip.
    assert '"PARTIAL: " & "export halted at the first message that could not be written' in script
    assert "& (7 + writtenCount) &" in script
    assert "(7 + totalExportCount)" not in script


def test_positional_page_export_does_not_halt_on_a_single_failure(tmp_path):
    """entire_mailbox pages by position, so a failure cannot desynchronize it.

    ``pageStart``/``pageEnd`` come from ``offset``/``max_emails`` alone, so the
    next page boundary does not depend on how many messages were written. The
    failure is reported (with the id) instead of costing the rest of the page.
    """
    script = _mailbox_script(tmp_path, offset=5, max_emails=10)

    assert "set pageStart to 5 + 1" in script
    assert "set pageEnd to 5 + 10" in script
    assert "set exportHalted to true" not in script
    assert '"PARTIAL: " & "the message_id(s) reported above were not written' in script


# ---------------------------------------------------------------------------
# Mirror image — a clean export must not grow a false alarm
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("builder", [_mailbox_script, _correspondent_script, _exact_id_script, _multi_mailbox_script])
def test_clean_export_emits_no_failure_report(builder, tmp_path):
    """Nothing exported and nothing failed must still read as a quiet success."""
    script = builder(tmp_path)

    assert _emitted_only_on_failure(script, '"Failed: "'), "Failed: must be guarded by exportFailureCount > 0"
    assert _emitted_only_on_failure(script, '"PARTIAL: "'), "PARTIAL: must be guarded by exportFailureCount > 0"
    # The honest count line itself is unconditional: an empty mailbox reports
    # "Exported: 0", which is a measurement, not a failure.
    assert 'set outputText to outputText & "Exported: " & writtenCount & return' in script
    # Counters start at zero, so a mailbox with nothing to export reports zero
    # rather than inheriting a previous call's state.
    assert "set writtenCount to 0" in script
    assert "set exportFailureCount to 0" in script
    assert "set exportHalted to false" in script


# ---------------------------------------------------------------------------
# Tool boundary — export_emails must not swallow the report it was handed
# ---------------------------------------------------------------------------


class _PoisonedSubprocess:
    """Any real subprocess call during a mocked tool test is a bug, loudly."""

    def __call__(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError(f"live subprocess call attempted during a mocked export test: {args!r}")


def _run_export(tmp_path, monkeypatch, *, applescript_output: str, **kwargs) -> str:
    """Call export_emails with AppleScript mocked and the subprocess layer poisoned."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path), raising=True)
    save_dir = tmp_path / "export"
    defaults = dict(account="Work", save_directory=str(save_dir))
    defaults.update(kwargs)
    with (
        patch("apple_mail_mcp.tools.analytics.run_applescript", return_value=applescript_output),
        patch("subprocess.run", new=_PoisonedSubprocess()),
    ):
        return analytics_tools.export_emails(**defaults)


def test_partial_export_report_reaches_the_caller_intact(tmp_path, monkeypatch):
    """The failure report a failing write produces must survive the tool boundary.

    ``analytics/export.py`` returns the AppleScript output verbatim rather than
    parsing it, which is why the report is emitted as readable lines instead of
    a machine marker. This locks that pass-through: an honest count, the id that
    was lost, and a PARTIAL line must all reach the caller.
    """
    failing_output = (
        "EXPORTING MAILBOX\n\n"
        "Exported message_id: 101\n"
        "Error exporting message_id 202: File permission error\n"
        "⚠ Mailbox exported with errors\n\n"
        "Mailbox: INBOX\n"
        "Offset: 0\n"
        "Exported: 1\n"
        "Failed: 1\n"
        "PARTIAL: the message_id(s) reported above were not written.\n"
    )

    result = _run_export(tmp_path, monkeypatch, applescript_output=failing_output, scope="entire_mailbox", max_emails=2)

    assert "Exported: 1" in result
    assert "Exported: 2" not in result, "the caller must never be told a count that was only attempted"
    assert "Failed: 1" in result
    assert "PARTIAL:" in result
    assert "Error exporting message_id 202" in result


def test_clean_export_result_carries_no_failure_text(tmp_path, monkeypatch):
    """Mirror image at the tool boundary: an empty export stays quiet."""
    empty_output = (
        "EXPORTING MAILBOX\n\n"
        "✓ Mailbox exported successfully!\n\n"
        "Mailbox: INBOX\n"
        "Total emails in mailbox: 0\n"
        "Offset: 0\n"
        "Exported: 0\n"
    )

    result = _run_export(tmp_path, monkeypatch, applescript_output=empty_output, scope="entire_mailbox", max_emails=5)

    assert "Exported: 0" in result
    assert "PARTIAL:" not in result
    assert "Failed:" not in result
    assert "Error" not in result
    assert not result.startswith("Error:")


# ---------------------------------------------------------------------------
# Syntax — the rewritten arms must still compile
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_OSACOMPILE is None, reason="osacompile unavailable on this platform")
@pytest.mark.parametrize("builder", [_mailbox_script, _correspondent_script, _exact_id_script, _multi_mailbox_script])
def test_export_builders_still_compile(builder, tmp_path):
    """``exit repeat`` inside an ``on error`` handler is easy to get wrong.

    osacompile parses offline against a temp file; it never contacts Mail.app.
    """
    source = tmp_path / "builder.applescript"
    source.write_text(builder(tmp_path), encoding="utf-8")
    compiled = tmp_path / "builder.scpt"
    completed = subprocess.run(  # noqa: S603 - offline parse check, never contacts Mail
        [_OSACOMPILE, "-o", str(compiled), str(source)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, (completed.stderr or completed.stdout).strip()
