"""Regressions for native reply quote and attachment preservation."""

import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from apple_mail_mcp.tools import compose as compose_tools


def _saved_native_reply_output(*, draft_id: str = "84053") -> str:
    return "\n".join(
        [
            "SAVING REPLY AS DRAFT",
            "",
            "Reply saved as draft!",
            "To: native reply recipients",
            "Subject: Re: Test",
            f"Draft ID: {draft_id}",
            (f"Draft Identity: {draft_id}|||<draft-{draft_id}@example.com>|||<source@example.com>"),
            "Quote Needle: On Today, Sender <sender@example.com> wrote:",
            "",
        ]
    )


def _saved_native_reply_transaction_output(*, draft_id: str = "84053") -> str:
    """Return a native reply result with an iCloud-safe transaction identity."""
    return "\n".join(
        [
            "SAVING REPLY AS DRAFT",
            "",
            "Reply saved as draft!",
            "To: native reply recipients",
            "Subject: Re: Test",
            f"Draft ID: {draft_id}",
            "Draft Identity: " + "|||".join((draft_id, "", "", "transaction")),
            "Quote Needle: On Today, Sender <sender@example.com> wrote:",
            "",
        ]
    )


def _native_reply_script(scripts: list[str]) -> str:
    matches = [script for script in scripts if "reply foundMessage" in script]
    assert len(matches) == 1
    return matches[0]


@pytest.fixture
def home_temp_dir() -> Iterator[Path]:
    """Yield an automatically cleaned directory accepted by attachment safety checks."""
    with tempfile.TemporaryDirectory(prefix="apple-mail-reply-test-", dir=Path.home()) as directory:
        yield Path(directory)


def test_native_reply_fails_when_saved_body_has_no_quoted_original() -> None:
    """A body match alone must not verify a native reply whose quote vanished."""

    def fake_mail(script: str, timeout: int = 120) -> str:
        if "reply foundMessage" in script:
            return _saved_native_reply_output()
        if 'set targetDraftIdText to "84053"' in script:
            # Mail-side verification found the authored body but no quote after it.
            return "QUOTE_MISSING|84053|not_requested|not_requested|0|"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            output_format="json",
        )

    assert "REPLY_QUOTED_ORIGINAL_MISSING" in result


def test_saved_reply_verifier_emits_quote_missing_when_body_has_no_following_quote() -> None:
    """The generated Mail verifier must distinguish quote loss from body success."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "QUOTE_MISSING|84053|not_requested|not_requested|0|"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            "Reply body",
            draft_id="84053",
            quoted_needle="On Today, Sender <sender@example.com> wrote:",
        )

    assert len(scripts) == 1
    verifier_script = scripts[0]
    assert 'if bodyEndOffset > (count of characters of flatDraft) then return "quote_missing"' in verifier_script
    assert 'if bodyStatus is "quote_missing" then return "QUOTE_MISSING|" & draftId' in verifier_script


def test_native_reply_uses_source_content_as_its_quote_proof() -> None:
    """A sender-only attribution must not certify a lost native quote."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        if "reply foundMessage" in script:
            return _saved_native_reply_output()
        if 'set targetDraftIdText to "84053"' in script:
            return "FOUND|84053|not_requested|not_requested|0|"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            output_format="json",
        )

    assert json.loads(result)["verification_status"] == "found"
    native_script = _native_reply_script(scripts)
    assert "set sourceSender to sender of foundMessage as string" in native_script
    assert "set sourceContent to content of foundMessage as string" in native_script
    assert 'set quotedNeedle to sourceSender & " wrote:" & return & sourceQuoteAnchor' in native_script
    assert 'return "QUOTE_PROOF_UNAVAILABLE"' in native_script
    assert 'set quotedNeedle to sourceSender & " wrote:"\n' not in native_script
    assert 'set quotedNeedle to "wrote:"' not in native_script


def test_native_reply_never_raises_a_same_subject_window_to_type() -> None:
    """The native guard must activate the exact adopted window, not a title match."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return _saved_native_reply_output()

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
        )

    native_script = _native_reply_script(scripts)
    assert "on raiseNativeReplyWindowSafely(replyWindowId, expectedTitle, derivedTitle)" in native_script
    assert (
        "set replyWindowRaised to my raiseNativeReplyWindowSafely(replyWindowId, replySubject, derivedReplySubject)"
        in native_script
    )
    assert 'perform action "AXRaise" of (first window whose name is replySubject)' not in native_script


def test_saved_reply_verifier_does_not_fallback_after_exact_attachment_failure() -> None:
    """A known Drafts id with a missing attachment must fail closed, never certify an older match."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ATTACHMENT_MISSING|84053|missing|not_requested|0|"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        verification = compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            "Reply body",
            draft_id="84053",
            quoted_needle="Sender <sender@example.com> wrote:",
            expected_attachment_count=1,
            expected_attachment_names=["support.pdf"],
        )

    assert verification.status == "attachment_verification_failed"
    verifier_script = scripts[0]
    attachment_failure_return = 'if attachmentFailureResult is not "" then return attachmentFailureResult'
    fallback_scan = "if (requireNativeIdentity is false) and (requireExactAttachmentIdentity is false) then"
    assert attachment_failure_return in verifier_script
    assert verifier_script.index(attachment_failure_return) < verifier_script.index(fallback_scan)


def test_attachment_reply_refuses_a_fallback_match_without_persisted_identity() -> None:
    """An attachment-bearing reply cannot trust a same-subject Drafts fallback."""

    with patch(
        "apple_mail_mcp.tools.compose.run_applescript",
        return_value="FOUND|99999|verified|not_requested|1|support.pdf::9;;",
    ):
        verification = compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            "Reply body",
            expected_attachment_count=1,
            expected_attachment_names=["support.pdf"],
        )

    assert verification.ok is False
    assert verification.status == "identity_unavailable"


def test_attachment_reply_accepts_only_a_single_new_iCloud_transaction_draft(home_temp_dir: Path) -> None:
    """A blank iCloud Message-ID is safe only with one bounded newly saved Drafts row."""
    attachment = home_temp_dir / "support.pdf"
    attachment.write_bytes(b"%PDF-test")

    def fake_mail(script: str, timeout: int = 120) -> str:
        if "reply foundMessage" in script:
            return _saved_native_reply_transaction_output()
        if 'set targetDraftIdText to "84053"' in script:
            return "FOUND|84053|verified|not_requested|1|support.pdf::9;;"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            attachments=str(attachment),
            include_signature=False,
            output_format="json",
        )

    payload = json.loads(result)
    assert payload["verification_status"] == "found"
    assert payload["draft_id"] == "84053"
    assert payload["exact_id_verified"] is True
    assert payload["draft_id_source"] == "transaction_scoped_numeric_identity"


def test_iCloud_transaction_resolver_rejects_ambiguous_or_rfc_mismatched_drafts() -> None:
    """The no-RFC path must not turn an ambiguous or contradictory post-save set into identity."""
    scripts: list[str] = []

    with patch(
        "apple_mail_mcp.tools.compose.run_applescript",
        side_effect=lambda script, timeout=120: scripts.append(script) or "NOT_FOUND",
    ):
        compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            "Reply body",
            draft_id="84053",
            quoted_needle="Sender <sender@example.com> wrote:",
            expected_attachment_count=1,
            expected_attachment_names=["support.pdf"],
        )

    resolver_script = scripts[0]
    assert 'if (count of newDraftIdentities) is not 1 then return ""' in resolver_script
    assert 'if candidateRfcMessageId is "" then return {candidateDraftId, "", "", "transaction"}' in resolver_script
    assert "if my headerHasExactRfcToken(item 2 of inReplyToResult, sourceMessageId) then" in resolver_script
    assert 'return {candidateDraftId, candidateRfcMessageId, sourceMessageId, "rfc"}' in resolver_script


def test_reply_attachment_verifier_rejects_unreadable_attachment_metadata() -> None:
    """A zero or unreadable attachment must not be certified as materialized."""

    verification = compose_tools._reply_verification_from_output(
        "FOUND|84053|verified|not_requested|1|support.pdf::0;;"
    )

    assert verification.ok is False
    assert verification.status == "attachment_verification_failed"
    assert verification.attachment_status == "unreadable"


def test_saved_reply_verifier_emits_unreadable_for_nonpositive_attachment_size() -> None:
    """Mail-side attachment checks must reject a file that has no readable bytes."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ATTACHMENT_UNREADABLE|84053|unreadable|not_requested|1|support.pdf::0;;"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        verification = compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            "Reply body",
            draft_id="84053",
            expected_attachment_count=1,
            expected_attachment_names=["support.pdf"],
        )

    assert verification.ok is False
    assert verification.status == "attachment_verification_failed"
    assert verification.attachment_status == "unreadable"
    assert 'if attachmentSize is less than or equal to 0 then return "unreadable"' in scripts[0]
    assert 'if draftAttachmentStatus is "unreadable" then return "ATTACHMENT_UNREADABLE|"' in scripts[0]


@pytest.mark.parametrize("attachment_status", ["missing", "unsupported"])
def test_native_reply_fails_closed_when_requested_attachment_is_not_verified(
    home_temp_dir: Path,
    attachment_status: str,
) -> None:
    """A requested attachment must be required for reply verification success."""
    attachment = home_temp_dir / "support.pdf"
    attachment.write_bytes(b"%PDF-test")

    def fake_mail(script: str, timeout: int = 120) -> str:
        if "reply foundMessage" in script:
            return _saved_native_reply_output()
        if 'set targetDraftIdText to "84053"' in script:
            return f"FOUND|84053|{attachment_status}|not_requested|0|"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            attachments=str(attachment),
            include_signature=False,
            output_format="json",
        )

    payload = json.loads(result)
    assert payload.get("code") == "REPLY_DRAFT_ATTACHMENT_VERIFICATION_FAILED"
    assert payload["remediation"]["attachment_status"] == attachment_status
    assert payload["remediation"]["draft_id"] == "84053"


def test_native_reply_inserts_attachment_only_after_body_typing(home_temp_dir: Path) -> None:
    """The native quote must settle through typing before Mail adds attachments."""
    attachment = home_temp_dir / "support.pdf"
    attachment.write_bytes(b"%PDF-test")
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        if "reply foundMessage" in script:
            return _saved_native_reply_output()
        if 'set targetDraftIdText to "84053"' in script:
            return "FOUND|84053|verified|not_requested|1|support.pdf::9;;"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            attachments=str(attachment),
            include_signature=False,
            output_format="json",
        )

    assert json.loads(result)["verification_status"] == "found"
    script = _native_reply_script(scripts)
    body_typed = script.index(
        "set typeChunksResult to my typeReplyBodyChunks(replyBodyText, replySubject, derivedReplySubject, replyWindowId)"
    )
    attachment_inserted = script.index(
        "make new attachment with properties {file name:theFile} at after the last paragraph of content"
    )
    assert body_typed < attachment_inserted


def test_native_reply_focuses_a_guarded_editor_before_typing() -> None:
    """Native typing must target a verified Mail body editor, not just a title."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
        )

    script = _native_reply_script(scripts)
    assert "on focusReplyBodyEditor(expectedTitle, derivedTitle, expectedWindowId)" in script
    assert 'candidateRole is "AXWebArea"' in script
    assert 'candidateRole is "AXTextArea"' in script
    assert (
        "set editorFocusResult to my focusReplyBodyEditor(replySubject, derivedReplySubject, replyWindowId)" in script
    )
    assert script.index("set editorFocusResult to my focusReplyBodyEditor") < script.index(
        "set typeChunksResult to my typeReplyBodyChunks"
    )


def test_native_reply_editor_selector_uses_a_runtime_safe_role_loop() -> None:
    """System Events cannot filter ``entire contents`` by role with ``whose``."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
        )

    script = _native_reply_script(scripts)
    assert 'every UI element of entire contents of targetWindow whose role is "AXWebArea"' not in script
    assert 'every UI element of entire contents of targetWindow whose role is "AXTextArea"' not in script
    assert "set allElements to entire contents of targetWindow" in script
    assert "repeat with candidateElement in allElements" in script
    assert 'set candidateRole to value of attribute "AXRole" of candidateElement as string' in script


def test_native_reply_prefers_text_editor_and_requires_confirmed_focus() -> None:
    """Mail's visible web area is not necessarily the actionable text editor."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
        )

    script = _native_reply_script(scripts)
    assert "set webAreaFallback to missing value" in script
    assert 'if candidateRole is "AXTextArea" then' in script
    assert 'else if candidateRole is "AXWebArea" and webAreaFallback is missing value then' in script
    assert script.index('if candidateRole is "AXTextArea" then') < script.index('else if candidateRole is "AXWebArea"')
    assert "set replyEditor to webAreaFallback" in script
    assert "click replyEditor" in script
    assert 'set focusedUIElement to value of attribute "AXFocusedUIElement" of targetWindow' in script
    assert 'if editorIsFocused or focusedElementMatches then return "focused"' in script


def test_native_reply_aborts_before_attachment_when_editor_focus_is_not_verified(home_temp_dir: Path) -> None:
    """An attachment must not be created if the native reply body editor cannot be focused."""
    attachment = home_temp_dir / "support.pdf"
    attachment.write_bytes(b"%PDF-test")
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="",
            attachments=str(attachment),
            include_signature=False,
        )

    script = _native_reply_script(scripts)
    assert "set composeFocusVerified to false" in script
    assert "if composeFocusVerified is false then" in script
    assert script.index("if composeFocusVerified is false then") < script.index(
        "make new attachment with properties {file name:theFile}"
    )


def test_native_reply_cleanup_targets_only_the_opened_window() -> None:
    """A failed reply must never close another user draft sharing its subject."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
        )

    script = _native_reply_script(scripts)
    assert "set preReplyWindowIds to my mailWindowIdSnapshot()" in script
    assert "set replyWindowId to my newlyOpenedReplyWindowId(preReplyWindowIds, derivedReplySubject)" in script
    assert "set replyWindowId to id of front window as string" not in script
    assert "on closeNativeReplyWindowSafely(replyWindowId, expectedTitle, derivedTitle)" in script
    assert "close candidateWindow saving no" in script
    assert "close (every window whose name is" not in script


def test_native_reply_with_attachment_rejects_direct_send_before_mail_mutation(home_temp_dir: Path) -> None:
    """Attachments must go through save-and-verify before any send is allowed."""
    attachment = home_temp_dir / "support.pdf"
    attachment.write_bytes(b"%PDF-test")
    with (
        patch("apple_mail_mcp.tools.compose.reply._resolve_account", return_value=("Work", None)),
        patch("apple_mail_mcp.tools.compose.reply._validate_from_address", return_value=(None, None)),
        patch("apple_mail_mcp.tools.compose.reply._validate_signature_name", return_value=None),
        patch.object(compose_tools._server, "READ_ONLY", False),
        patch.object(compose_tools._server, "DRAFT_SAFE", False),
        patch("apple_mail_mcp.tools.compose.run_applescript") as mock_mail,
    ):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            attachments=str(attachment),
            mode="send",
            output_format="text",
        )

    mock_mail.assert_not_called()
    payload = json.loads(result)
    assert payload["code"] == "REPLY_SEND_REQUIRES_VERIFIED_DRAFT"
    assert payload["remediation"]["preferred_mode"] == "draft"


def test_saved_reply_verifier_retries_transient_attachment_miss() -> None:
    """A just-saved Exchange attachment may materialize after the draft body."""
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "ATTACHMENT_MISSING|84053|missing|not_requested|0|"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        verification = compose_tools._verify_saved_reply_draft(
            "Work",
            "Re: Test",
            "Reply body",
            draft_id="84053",
            quoted_needle="wrote:",
            expected_attachment_count=1,
            expected_attachment_names=["support.pdf"],
        )

    assert verification.ok is False
    assert verification.status == "attachment_verification_failed"
    verifier_script = scripts[0]
    assert (
        'if exactResult starts with "ATTACHMENT_" then\n'
        "                                set attachmentFailureResult to exactResult\n"
        "                            else\n"
        "                                return exactResult"
    ) in verifier_script


def test_native_attachment_only_reply_still_requires_quoted_original(home_temp_dir: Path) -> None:
    """An empty authored body must not exempt a native attachment reply from quote verification."""
    attachment = home_temp_dir / "support.pdf"
    attachment.write_bytes(b"%PDF-test")
    scripts: list[str] = []

    def fake_mail(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        if "reply foundMessage" in script:
            return _saved_native_reply_output()
        if 'set targetDraftIdText to "84053"' in script:
            return "QUOTE_MISSING|84053|verified|not_requested|1|support.pdf::9;;"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="",
            attachments=str(attachment),
            include_signature=False,
            output_format="json",
        )

    assert json.loads(result)["code"] == "REPLY_QUOTED_ORIGINAL_MISSING"
    script = _native_reply_script(scripts)
    assert 'set quotedNeedle to sourceSender & " wrote:"' in script
    verifier_script = next(script for script in scripts if 'set targetDraftIdText to "84053"' in script)
    assert 'if flatBody is "" then' in verifier_script
    assert 'if quoteOffsetWithoutBody > 0 then return "found"' in verifier_script
    assert 'return "quote_missing"' in verifier_script


def test_fallback_quote_failure_marks_artifact_as_suspect() -> None:
    """A same-subject fallback match is diagnostic and must not authorize deletion."""

    def fake_mail(script: str, timeout: int = 120) -> str:
        if "reply foundMessage" in script:
            return "\n".join(
                [
                    "SAVING REPLY AS DRAFT",
                    "Reply saved as draft!",
                    "To: native reply recipients",
                    "Subject: Re: Test",
                    "Quote Needle: wrote:",
                ]
            )
        if 'set targetDraftIdText to ""' in script:
            return "QUOTE_MISSING|99999|not_requested|not_requested|0|"
        return "ok"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_mail):
        result = compose_tools.reply_to_email(
            account="Work",
            message_id="12345",
            reply_body="Reply body",
            output_format="json",
        )

    payload = json.loads(result)
    remediation = payload["remediation"]
    assert payload["code"] == "REPLY_QUOTED_ORIGINAL_MISSING"
    assert remediation["artifact_identity_verified"] is False
    assert remediation["suspect_artifact_message_id"] == "99999"
    assert "draft_id" not in remediation
    assert "do not delete" in remediation["preferred"].lower()
