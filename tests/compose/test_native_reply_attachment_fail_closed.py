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
        "set typeChunksResult to my typeReplyBodyChunks(replyBodyText, replySubject, derivedReplySubject)"
    )
    attachment_inserted = script.index(
        "make new attachment with properties {file name:theFile} at after the last paragraph of content"
    )
    assert body_typed < attachment_inserted


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
    assert 'set quotedNeedle to "wrote:"' in script
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
