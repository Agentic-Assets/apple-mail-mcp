"""HTML compose must restore the real subject and never re-stamp the marker."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from apple_mail_mcp.core.applescript import AppleScriptTimeout
from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools.compose.constants import DRAFT_LIST_CAP
from apple_mail_mcp.tools.compose.html_subject_scripts import html_compose_subject_followup_script
from apple_mail_mcp.tools.compose.lookup_scripts import _build_draft_lookup
from apple_mail_mcp.tools.compose.standalone_draft_identity_scripts import (
    standalone_exact_marker_draft_scan,
    standalone_exact_marker_restore_or_delete_script,
    standalone_marker_draft_finalize_script,
)

_OSACOMPILE = shutil.which("osacompile") is not None
_CAP_EXIT = f"if draftCount is greater than {DRAFT_LIST_CAP} then exit repeat"
_HEAD_CAP = f"if headEnd > {DRAFT_LIST_CAP} then set headEnd to {DRAFT_LIST_CAP}"


def _capture_html_script(*, mode: str = "draft", subject: str = "Referral agreement", **kwargs: object) -> str:
    captured: dict[str, str] = {}

    def fake_run(script: str, timeout: int = 120) -> str:
        captured["script"] = script
        if mode == "send":
            return "Email sent successfully (HTML)"
        if mode == "open":
            return "Email opened in Mail for review (HTML). Edit and send when ready."
        return "Email saved as draft (HTML)"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        compose_tools.compose_email(
            account="Work",
            to="reviewer@example.com",
            subject=subject,
            body="Hello",
            body_html="<p>Hello</p>",
            mode=mode,
            **kwargs,  # type: ignore[arg-type]
        )
    return captured["script"]


def _assert_exact_marker_scan(script: str) -> None:
    lookup = _build_draft_lookup("unused")
    assert _HEAD_CAP in lookup
    assert _HEAD_CAP in script
    assert f"if totalDrafts > {DRAFT_LIST_CAP} then" in script
    assert "set tailStart to totalDrafts -" in script
    assert _CAP_EXIT not in script
    assert "whose subject" not in script
    assert "(subject of candidateDraft as string) is " in script
    assert "set end of markedDrafts to contents of candidateDraft" in script
    assert "if (count of markedDrafts) is 1" in script or "markerMatchCount is 1" in script


def test_no_attachment_html_restores_subject_after_paste_before_save() -> None:
    script = _capture_html_script(mode="draft", subject="Referral agreement")
    paste = script.index('keystroke "v" using command down')
    restore = script.index('set subject of newMsg to "Referral agreement"')
    save = script.index("save newMsg")
    assert paste < restore < save
    assert "set markedDrafts to" in script
    assert restore < script.index("set markedDrafts to")
    assert "set subject of newMsg to temporarySubjectMarker" not in script


def test_no_attachment_html_error_handler_does_not_restamp_marker() -> None:
    script = _capture_html_script(mode="draft")
    assert "set subject of newMsg to temporarySubjectMarker" not in script
    assert "close (window of newMsg) saving no" in script
    _assert_exact_marker_scan(script)


def test_focus_failure_error_path_deletes_the_fixture_without_restoring_subject() -> None:
    """Pre-restore failures must delete newMsg so Gmail cannot persist an empty real-subject draft."""
    script = _capture_html_script(mode="draft", subject="LIVE-TEST restore subject")
    success, _, error_handler = script.partition("on error errMsg")
    focus_fail = error_handler.index('if errMsg contains "COMPOSE_BODY_FOCUS_FAILED"')
    first_delete = error_handler.index("delete newMsg")
    leftover_restore = error_handler.index("set leftoverOutgoingStatus")
    assert focus_fail < first_delete < leftover_restore
    assert 'set subject of newMsg to "LIVE-TEST restore subject"' in success
    assert 'set subject of newMsg to "LIVE-TEST restore subject"' not in error_handler
    assert error_handler.count("delete newMsg") >= 2
    assert 'if errSubject contains "' not in error_handler
    assert "if errSubject is " in error_handler


def test_html_send_restores_and_verifies_before_send() -> None:
    script = _capture_html_script(mode="send", subject="Ready to send")
    restore = script.index('set subject of newMsg to "Ready to send"')
    send = script.index("send newMsg")
    assert restore < send
    assert 'if restoredOutgoingSubject contains "' not in script
    assert (
        'if restoredOutgoingSubject is not "Ready to send" then error "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"' in script
    )
    assert 'if restoredOutgoingSubject is "' in script
    assert script.index("set restoredOutgoingSubject to subject of newMsg as string") < send


def test_attachment_html_restores_outgoing_subject_before_save(tmp_path: Path) -> None:
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    scripts: list[str] = []

    def fake_run(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "Email saved as draft (HTML)\nDraft ID: 84053\n"

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Report",
            body="Please review.",
            attachments=str(attachment),
            mode="draft",
        )

    script = scripts[0]
    restore_outgoing = script.index('set subject of newMsg to "Report"')
    first_save = script.index("save newMsg")
    lookup = script.index("set markedDrafts to")
    assert restore_outgoing < first_save < lookup
    assert "set subject of markedDraft to" not in script
    assert "set refreshedDraftId to (id of markedDraft) as string" in script
    assert '(subject of candidateDraft as string) is "Report"' in script
    assert _CAP_EXIT not in script
    _assert_exact_marker_scan(script)


def test_open_attachment_html_restores_subject_before_save_even_on_proof_path(tmp_path: Path) -> None:
    attachment = tmp_path / "note.md"
    attachment.write_text("fixture")
    scripts: list[str] = []

    def fake_run(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "Email opened in Mail for review (HTML). Edit and send when ready."

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Fixture HTML subject restore attach",
            body="Hi fixture,",
            body_html="<p>Hi fixture,</p>",
            attachments=str(attachment),
            mode="open",
        )

    script = next(item for item in scripts if "on focusComposeBody(theMarker)" in item)
    restore = script.index('set subject of newMsg to "Fixture HTML subject restore attach"')
    save = script.index("save newMsg")
    proof_fail = script.index('error "DRAFT_ATTACHMENT_PROOF_FAILED:')
    assert restore < save
    assert save < proof_fail
    assert "set subject of markedDraft to" not in script
    assert "set subject of newMsg to temporarySubjectMarker" not in script
    assert script.count('set subject of newMsg to "Fixture HTML subject restore attach"') == 1


def test_python_throw_runs_followup_and_returns_structured_error() -> None:
    calls: list[str] = []

    def fake_run(script: str, timeout: int = 120) -> str:
        calls.append(script)
        if "focusComposeBody" in script:
            raise RuntimeError("AppleScript error: COMPOSE_BODY_FOCUS_FAILED")
        return "restored"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        result = compose_tools.compose_email(
            account="Work",
            to="reviewer@example.com",
            subject="Referral agreement",
            body="Hello",
            body_html="<p>Hello</p>",
            mode="draft",
        )

    assert len(calls) == 2
    assert "focusComposeBody" in calls[0]
    assert "focusComposeBody" not in calls[1]
    assert "(subject of candidateDraft as string) is " in calls[1]
    payload = json.loads(result)
    assert payload["code"] == "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"
    assert payload["error"] is True
    assert "Email saved as draft (HTML)" not in result
    assert "COMPOSE_BODY_FOCUS_FAILED" in payload["message"]
    assert "restored" in payload["message"]


def test_python_timeout_runs_followup_and_returns_structured_error() -> None:
    calls: list[str] = []

    def fake_run(script: str, timeout: int = 120) -> str:
        calls.append(script)
        if "focusComposeBody" in script:
            raise AppleScriptTimeout("timed out")
        return "cleared"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        result = compose_tools.compose_email(
            account="Work",
            to="reviewer@example.com",
            subject="Referral agreement",
            body="Hello",
            body_html="<p>Hello</p>",
            mode="draft",
        )

    assert len(calls) == 2
    payload = json.loads(result)
    assert payload["code"] == "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"
    assert "timed out" in payload["message"]
    assert "cleared" in payload["message"]
    assert "Email saved as draft" not in result


def test_sweep_cleared_is_not_proof_of_a_real_subject_draft() -> None:
    """Marker absence is cleanup only; follow-up must not convert restore failure into success."""
    calls: list[str] = []

    def fake_run(script: str, timeout: int = 120) -> str:
        calls.append(script)
        if "focusComposeBody" in script:
            raise RuntimeError("AppleScript error: HTML_COMPOSE_SUBJECT_RESTORE_FAILED")
        return "cleared"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        result = compose_tools.compose_email(
            account="Work",
            to="reviewer@example.com",
            subject="Referral agreement",
            body="Hello",
            body_html="<p>Hello</p>",
            mode="draft",
        )

    assert len(calls) == 2
    payload = json.loads(result)
    assert payload["code"] == "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"
    assert "Email saved as draft (HTML)" not in result
    assert "cleared" in payload["message"]


def test_followup_fails_closed_when_outgoing_subject_still_has_marker() -> None:
    def fake_run(script: str, timeout: int = 120) -> str:
        if "focusComposeBody" in script:
            raise RuntimeError("AppleScript error: HTML_COMPOSE_SUBJECT_RESTORE_FAILED")
        return "failed"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        result = compose_tools.compose_email(
            account="Work",
            to="reviewer@example.com",
            subject="Referral agreement",
            body="Hello",
            body_html="<p>Hello</p>",
            mode="draft",
        )

    payload = json.loads(result)
    assert payload["code"] == "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"
    assert "Email saved as draft (HTML)" not in result


def test_proof_failure_after_restore_is_not_wrapped_as_subject_restore_failed() -> None:
    def fake_run(script: str, timeout: int = 120) -> str:
        if "focusComposeBody" in script:
            raise RuntimeError("AppleScript error: DRAFT_ATTACHMENT_PROOF_FAILED: unavailable")
        return "cleared"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        result = compose_tools.compose_email(
            account="Work",
            to="reviewer@example.com",
            subject="Referral agreement",
            body="Hello",
            body_html="<p>Hello</p>",
            mode="open",
        )

    payload = json.loads(result)
    assert payload["code"] == "DRAFT_ATTACHMENT_PROOF_FAILED"
    assert payload["code"] != "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"
    assert "unavailable" in payload["message"]


def test_outgoing_ok_followup_is_not_a_saved_draft() -> None:
    """Restoring a leftover outgoing window does not prove a Drafts row exists."""

    def fake_run(script: str, timeout: int = 120) -> str:
        if "focusComposeBody" in script:
            raise RuntimeError("AppleScript error: HTML_COMPOSE_SUBJECT_RESTORE_FAILED")
        return "outgoing_ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        result = compose_tools.compose_email(
            account="Work",
            to="reviewer@example.com",
            subject="Referral agreement",
            body="Hello",
            body_html="<p>Hello</p>",
            mode="draft",
        )

    payload = json.loads(result)
    assert payload["code"] == "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"
    assert "Email saved as draft (HTML)" not in result


def test_html_compose_default_timeout_is_120() -> None:
    captured: dict[str, int | None] = {}

    def fake_run(script: str, timeout: int | None = 120) -> str:
        captured["timeout"] = timeout
        return "Email saved as draft (HTML)"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        compose_tools.compose_email(
            account="Work",
            to="reviewer@example.com",
            subject="Referral agreement",
            body="Hello",
            body_html="<p>Hello</p>",
            mode="draft",
        )

    assert captured["timeout"] == 120


def test_restore_verification_matches_the_exact_marker_token_not_a_prefix() -> None:
    """A legitimate subject may contain the marker prefix; only the uuid token is a leak."""
    subject = "Notes about __apple_mail_mcp_ internals"
    script = _capture_html_script(mode="draft", subject=subject)
    assert f'if restoredOutgoingSubject is not "{subject}" then error "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"' in script
    assert 'if restoredOutgoingSubject contains "__apple_mail_mcp_"' not in script
    assert 'if errSubject contains "__apple_mail_mcp_"' not in script


def test_success_path_sweep_fails_closed_instead_of_deleting_a_marker_draft() -> None:
    """If IMAP persisted the marker after outgoing readback, do not delete-and-succeed."""
    script = _capture_html_script(mode="draft")
    success, _, error_handler = script.partition("on error errMsg")
    assert 'if markerSweepStatus is not "cleared" then error "HTML_COMPOSE_SUBJECT_RESTORE_FAILED"' in success
    assert "delete markedDraft" not in success
    assert "delete markedDraft" in error_handler


def test_persist_is_failure_sweep_does_not_delete() -> None:
    script = standalone_exact_marker_restore_or_delete_script(
        "__apple_mail_mcp_abc123__",
        "Real subject",
        persist_is_failure=True,
    )
    assert "delete markedDraft" not in script
    assert 'set markerSweepStatus to "failed"' in script
    assert 'if leftoverCheck is not "Real subject" then' in script


def test_restore_or_delete_uses_exact_is_and_fails_closed_on_ambiguous() -> None:
    script = standalone_exact_marker_restore_or_delete_script("__apple_mail_mcp_abc123__", "Real subject")
    assert "(subject of candidateDraft as string) is " in script
    assert "whose subject" not in script
    assert 'subject contains "' not in script
    assert 'if (subject of markedDraft as string) is "__apple_mail_mcp_abc123__" then' in script
    assert "if markerMatchCount is greater than 1 then" in script
    assert 'set markerSweepStatus to "ambiguous"' in script
    assert "delete markedDraft" in script
    assert "set subject of markedDraft to" not in script
    assert 'set subject of leftoverMsg to "Real subject"' in script
    assert 'if leftoverCheck is not "Real subject" then' in script
    _assert_exact_marker_scan(script)


def test_marker_scan_copies_lookup_head_and_tail_shape() -> None:
    scan = standalone_exact_marker_draft_scan("__apple_mail_mcp_abc123__")
    lookup = _build_draft_lookup("keyword")
    assert _HEAD_CAP in scan
    assert _HEAD_CAP in lookup
    assert "set candidateMessages to messages 1 thru headEnd of draftsMailbox" in scan
    assert "if tailStart > headEnd then" in scan
    assert _CAP_EXIT not in scan
    assert "(subject of candidateDraft as string) is " in scan
    assert "contains" not in scan.split("repeat with candidateDraft", 1)[1]


def test_finalize_inner_id_miss_does_not_revert_or_delete() -> None:
    script = standalone_marker_draft_finalize_script("Final subject", 'set attachmentTransactionProof to "verified"')
    id_try = script.index("set refreshedDraftId to (id of markedDraft) as string")
    assert "set subject of markedDraft to" not in script
    assert "delete markedDraft" not in script
    assert "set subject of markedDraft to temporarySubjectMarker" not in script
    assert "set subject of newMsg to temporarySubjectMarker" not in script
    assert "set subject of newMsg to" not in script
    assert 'set attachmentTransactionProof to "identity_unavailable"' in script
    assert 'set attachmentTransactionProof to "finalization_failed"' in script
    assert '(subject of candidateDraft as string) is "Final subject"' in script
    assert 'set attachmentTransactionProof to "verified"' in script[:id_try]


def test_followup_script_is_standalone_mail_tell() -> None:
    script = html_compose_subject_followup_script(
        account="Work",
        marker="__apple_mail_mcp_abc123__",
        final_subject="Real subject",
    )
    assert script.lstrip().startswith('tell application "Mail"')
    assert "whose subject" not in script
    assert _CAP_EXIT not in script
    _assert_exact_marker_scan(script)


@pytest.mark.skipif(not _OSACOMPILE, reason="osacompile not available (non-macOS CI)")
def test_composed_html_script_osacompiles() -> None:
    script = _capture_html_script(mode="draft")
    with tempfile.TemporaryDirectory(prefix="apple-mail-html-compile-") as directory:
        source = Path(directory) / "html-compose.applescript"
        compiled = Path(directory) / "html-compose.scpt"
        source.write_text(script, encoding="utf-8")
        result = subprocess.run(
            ["osacompile", "-o", str(compiled), str(source)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(not _OSACOMPILE, reason="osacompile not available (non-macOS CI)")
def test_followup_script_osacompiles() -> None:
    script = html_compose_subject_followup_script()
    with tempfile.TemporaryDirectory(prefix="apple-mail-html-followup-") as directory:
        source = Path(directory) / "followup.applescript"
        compiled = Path(directory) / "followup.scpt"
        source.write_text(script, encoding="utf-8")
        result = subprocess.run(
            ["osacompile", "-o", str(compiled), str(source)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(not _OSACOMPILE, reason="osacompile not available (non-macOS CI)")
def test_attachment_html_script_osacompiles(tmp_path: Path) -> None:
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    scripts: list[str] = []

    def fake_run(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "Email saved as draft (HTML)\nDraft ID: 84053\n"

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        patch("apple_mail_mcp.tools.compose._validate_attachment_paths", return_value=([str(attachment)], None)),
    ):
        compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Report",
            body="Please review.",
            attachments=str(attachment),
            mode="draft",
        )

    with tempfile.TemporaryDirectory(prefix="apple-mail-html-attach-") as directory:
        source = Path(directory) / "html-attach.applescript"
        compiled = Path(directory) / "html-attach.scpt"
        source.write_text(scripts[0], encoding="utf-8")
        result = subprocess.run(
            ["osacompile", "-o", str(compiled), str(source)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    assert result.returncode == 0, result.stderr or result.stdout
