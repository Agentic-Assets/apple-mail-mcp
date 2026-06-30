"""Pure parsing and formatting of saved reply/forward draft verifier output.

No I/O lives here; the live verifiers in ``compose.py`` feed their raw
AppleScript output into these helpers.
"""

import json
from dataclasses import dataclass
from typing import Any

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.tools.draft_verification import _normalize_attachment_rows


def _extract_output_field(output: str, field_name: str) -> str | None:
    """Return a `Field: value` line from a tool status string."""
    prefix = f"{field_name}: "
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def _first_non_empty_line(value: str, *, max_chars: int = 500) -> str:
    """Return a bounded content needle for saved-draft verification."""
    for line in value.splitlines():
        candidate = line.strip()
        if candidate:
            return candidate[:max_chars]
    return ""


@dataclass(frozen=True)
class _ReplyDraftVerification:
    ok: bool
    status: str = "not_found"
    body_missing_artifact_id: str | None = None
    error_artifact_id: str | None = None
    matched_artifact_id: str | None = None
    attachment_status: str | None = None
    attachment_count: int | None = None
    attachments_applied: list[dict[str, Any]] | None = None
    signature_status: str | None = None


def _reply_verification_from_output(output: str) -> _ReplyDraftVerification:
    """Parse the saved-reply verifier AppleScript response."""
    parts = output.strip().split("|", 5)
    status = parts[0] if parts else ""
    artifact_id = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    attachment_status = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    signature_status = parts[3].strip() if len(parts) > 3 and parts[3].strip() else None
    attachment_count_text = parts[4].strip() if len(parts) > 4 and parts[4].strip() else None
    try:
        attachment_count = int(attachment_count_text) if attachment_count_text is not None else None
    except ValueError:
        attachment_count = None
    attachments_applied = _normalize_attachment_rows(parts[5]) if len(parts) > 5 and parts[5].strip() else None
    if status == "FOUND":
        return _ReplyDraftVerification(
            ok=True,
            status="found",
            matched_artifact_id=artifact_id,
            attachment_status=attachment_status,
            attachment_count=attachment_count,
            attachments_applied=attachments_applied,
            signature_status=signature_status,
        )
    if status == "BODY_MISSING":
        return _ReplyDraftVerification(
            ok=False,
            status="body_missing",
            body_missing_artifact_id=artifact_id,
        )
    if status == "BODY_AFTER_QUOTE":
        return _ReplyDraftVerification(
            ok=False,
            status="body_after_quote",
            body_missing_artifact_id=artifact_id,
        )
    return _ReplyDraftVerification(ok=False, status="not_found")


def _reply_exact_id_verified(verification: _ReplyDraftVerification, draft_id: str | None) -> bool:
    """Return whether verification proved the exact saved Drafts artifact."""
    return bool(verification.ok and draft_id and verification.matched_artifact_id == draft_id)


def _reply_attachment_details_requested(verification: _ReplyDraftVerification) -> bool:
    """Return whether attachment details describe a requested attachment check."""
    return bool(verification.attachment_status and verification.attachment_status != "not_requested")


def _format_reply_verification_lines(verification: _ReplyDraftVerification, fallback_draft_id: str | None) -> str:
    """Return stable success metadata lines for a verified reply draft."""
    verified_id = verification.matched_artifact_id or fallback_draft_id or ""
    lines = [
        f"Verification Status: {verification.status}",
    ]
    if verified_id:
        lines.append(f"Verified Draft ID: {verified_id}")
    if verification.ok and fallback_draft_id and verified_id and verified_id != fallback_draft_id:
        lines.append(
            "Warning: saved draft was verified by bounded Drafts fallback, not by the exact Draft ID returned by Mail"
        )
    if verification.attachment_status:
        lines.append(f"Attachment Verification Status: {verification.attachment_status}")
        if _reply_attachment_details_requested(verification) and verification.attachment_count is not None:
            lines.append(f"Attachments Applied Count: {verification.attachment_count}")
        if _reply_attachment_details_requested(verification) and verification.attachments_applied:
            lines.append("Attachments Applied:")
            for attachment in verification.attachments_applied:
                filename = attachment.get("filename") or ""
                size = attachment.get("size")
                size_text = f" ({size} bytes)" if size is not None else ""
                lines.append(f"  {filename}{size_text}")
        if verification.attachment_status in {"missing", "unsupported"}:
            lines.append("Warning: requested attachments could not be verified on the saved draft")
    if verification.signature_status:
        lines.append(f"Signature Verification Status: {verification.signature_status}")
        if verification.signature_status == "missing":
            lines.append("Warning: requested Mail signature was not detected above the quoted original")
    return "\n".join(lines) + "\n"


def _reply_success_payload(
    *,
    mode: str,
    reply_subject: str | None,
    draft_id: str | None,
    verification: _ReplyDraftVerification,
) -> dict[str, Any]:
    """Return the machine-readable success contract for verified reply drafts."""
    verified_id = verification.matched_artifact_id or draft_id
    return {
        "mode": mode,
        "sent": False,
        "subject": reply_subject or "",
        "draft_id": draft_id,
        "verified_draft_id": verified_id,
        "verification_status": verification.status,
        "exact_id_verified": _reply_exact_id_verified(verification, draft_id),
        "body_present": verification.status == "found",
        "attachment_status": verification.attachment_status,
        "attachment_count": verification.attachment_count,
        "attachments_applied": verification.attachments_applied or [],
        "signature_status": verification.signature_status,
        "mailbox": "Drafts",
    }


def _format_forward_verification_lines(
    raw_verification: str,
    fallback_draft_id: str,
) -> str:
    """Return stable success metadata lines for a verified forward draft."""
    try:
        payload = json.loads(raw_verification)
    except json.JSONDecodeError:
        return "Verification Status: error\nWarning: saved forward draft verification returned invalid JSON\n"

    warnings = payload.get("warnings") or []
    found = payload.get("found") is True
    if found and warnings:
        status = "found_with_warnings"
    elif found:
        status = "found"
    else:
        status = "not_found"
    verified_id = str(payload.get("draft_id") or fallback_draft_id)
    lines = [f"Verification Status: {status}"]
    if verified_id:
        lines.append(f"Verified Draft ID: {verified_id}")
    if payload.get("error"):
        lines.append(f"Verification Error: {payload['error']}")
    if warnings:
        lines.append("Verification Warnings: " + ", ".join(str(item) for item in warnings))
    return "\n".join(lines) + "\n"


def _reply_draft_verification_error(
    verification: _ReplyDraftVerification,
    *,
    mode_text: str,
    reply_body: str,
) -> str:
    """Serialize a structured draft-verification failure when an artifact id is known."""
    artifact_id = verification.body_missing_artifact_id or verification.error_artifact_id
    if not artifact_id:
        return (
            f"Error: Reply draft was {mode_text}, but Mail did not verify it in the newest Drafts "
            "window. No email was sent. Please check Mail Drafts and retry after Mail finishes saving."
        )

    if verification.status == "body_after_quote":
        code = "REPLY_DRAFT_BODY_AFTER_QUOTE"
        detail = "contains the inserted reply body after the quoted original instead of above it"
    elif verification.status == "body_missing":
        code = "REPLY_DRAFT_BODY_MISSING"
        detail = "does not contain the inserted reply body"
    elif verification.status == "verification_timeout":
        code = "REPLY_DRAFT_VERIFICATION_TIMEOUT"
        detail = "could not be verified before the verifier timed out"
    else:
        code = "REPLY_DRAFT_VERIFICATION_ERROR"
        detail = "could not be verified because Mail returned a verifier error"

    return serialize_tool_error(
        ToolError(
            code=code,
            message=(
                f"Reply draft was {mode_text}, but saved Drafts artifact {artifact_id} {detail}. No email was sent."
            ),
            remediation={
                "artifact_message_id": artifact_id,
                "draft_id": artifact_id,
                "mailbox": "Drafts",
                "verification_status": verification.status,
                "expected_body_needle": _first_non_empty_line(reply_body),
                "preferred": (
                    "Inspect or delete the artifact by exact Drafts message_id, then retry after Mail finishes saving."
                ),
            },
        )
    )
