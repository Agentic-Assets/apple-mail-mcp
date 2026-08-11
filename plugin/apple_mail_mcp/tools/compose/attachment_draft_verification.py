"""Transaction-scoped strict verification for standalone attachment drafts."""

import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path


def marker_draft_verification_handlers() -> str:
    """Return Mail handlers that verify the one UUID-marked persisted draft."""
    return """
using terms from application "Mail"
on markerRecipientSetMatches(actualRecipients, expectedAddresses)
    try
        if (count of actualRecipients) is not (count of expectedAddresses) then return false
        set remainingAddresses to {}
        repeat with actualRecipient in actualRecipients
            set end of remainingAddresses to (address of actualRecipient as string)
        end repeat
        repeat with expectedAddress in expectedAddresses
            set matchIndex to 0
            repeat with addressIndex from 1 to count of remainingAddresses
                ignoring case
                    if (item addressIndex of remainingAddresses as string) is (expectedAddress as string) then
                        set matchIndex to addressIndex
                        exit repeat
                    end if
                end ignoring
            end repeat
            if matchIndex is 0 then return false
            set item matchIndex of remainingAddresses to missing value
        end repeat
        return true
    on error
        return false
    end try
end markerRecipientSetMatches

on markerDraftProof(draftMessage, expectedTo, expectedCc, expectedBcc, expectedBody, expectedAttachmentNames)
    try
        if my markerRecipientSetMatches(to recipients of draftMessage, expectedTo) is false then return "recipient_mismatch"
        if my markerRecipientSetMatches(cc recipients of draftMessage, expectedCc) is false then return "cc_recipient_mismatch"
        if my markerRecipientSetMatches(bcc recipients of draftMessage, expectedBcc) is false then return "bcc_recipient_mismatch"
        if (content of draftMessage as string) does not contain expectedBody then return "body_mismatch"
        set savedAttachments to mail attachments of draftMessage
        if (count of savedAttachments) is not (count of expectedAttachmentNames) then return "attachment_mismatch"
        set remainingAttachmentNames to {}
        repeat with savedAttachment in savedAttachments
            set attachmentSize to file size of savedAttachment as integer
            if attachmentSize is less than or equal to 0 then return "attachment_unreadable"
            set end of remainingAttachmentNames to (name of savedAttachment as string)
        end repeat
        repeat with expectedAttachmentName in expectedAttachmentNames
            set matchIndex to 0
            repeat with attachmentIndex from 1 to count of remainingAttachmentNames
                if (item attachmentIndex of remainingAttachmentNames as string) is (expectedAttachmentName as string) then
                    set matchIndex to attachmentIndex
                    exit repeat
                end if
            end repeat
            if matchIndex is 0 then return "attachment_mismatch"
            set item matchIndex of remainingAttachmentNames to missing value
        end repeat
        return "verified"
    on error
        return "unavailable"
    end try
end markerDraftProof
end using terms from
"""


def marker_draft_proof_call(
    *,
    to_addresses: list[str],
    cc_addresses: list[str],
    bcc_addresses: list[str],
    body: str,
    attachment_names: list[str],
) -> str:
    """Build the strict persisted-marker proof call for ``markedDraft``."""
    from apple_mail_mcp.core import escape_applescript

    def address_list(addresses: list[str]) -> str:
        return ", ".join(f'"{escape_applescript(address)}"' for address in addresses)

    recipients = address_list(to_addresses)
    cc_recipients = address_list(cc_addresses)
    bcc_recipients = address_list(bcc_addresses)
    names = ", ".join(f'"{escape_applescript(name)}"' for name in attachment_names)
    return (
        "set attachmentTransactionProof to my markerDraftProof(markedDraft, "
        f'{{{recipients}}}, {{{cc_recipients}}}, {{{bcc_recipients}}}, "{escape_applescript(body)}", {{{names}}})'
    )


def _draft_locator(output: str) -> str:
    prefix = "Draft ID: "
    return next((line[len(prefix) :].strip() for line in output.splitlines() if line.startswith(prefix)), "")


def _recipient_counts(value: object) -> Counter[str] | None:
    """Normalize a verifier recipient field into an exact address multiset."""
    if not isinstance(value, str):
        return None
    return Counter(address.strip().casefold() for address in value.split(",") if address.strip())


def _strict_readback_matches(
    readback: object,
    draft_id: str,
    attachment_paths: list[str],
    *,
    cc: str,
    bcc: str,
) -> bool:
    if not isinstance(readback, dict):
        return False
    attachments = readback.get("attachments")
    checks = readback.get("checks")
    found_rows = attachments.get("found") if isinstance(attachments, dict) else None
    if not isinstance(found_rows, list):
        return False
    expected_counts = Counter(Path(path).name for path in attachment_paths)
    found_counts = Counter(row.get("filename") for row in found_rows if isinstance(row, dict))
    recipients = readback.get("recipients")
    cc_counts = _recipient_counts(recipients.get("cc")) if isinstance(recipients, dict) else None
    bcc_counts = _recipient_counts(recipients.get("bcc")) if isinstance(recipients, dict) else None
    return (
        readback.get("found") is True
        and str(readback.get("draft_id") or "") == draft_id
        and readback.get("subject_matches_expected") is True
        and readback.get("body_contains_expected") is True
        and isinstance(checks, dict)
        and checks.get("to_matches_expected") is True
        and checks.get("cc_matches_expected") is True
        and cc_counts == _recipient_counts(cc)
        and bcc_counts == _recipient_counts(bcc)
        and isinstance(attachments, dict)
        and attachments.get("status") == "verified"
        and found_counts == expected_counts
        and len(found_rows) == sum(expected_counts.values())
        and all(isinstance(row, dict) and isinstance(row.get("size"), int) and row["size"] > 0 for row in found_rows)
        and readback.get("warnings") == []
    )


def verify_standalone_attachment_readiness(
    *,
    output: str,
    account: str,
    to: str,
    cc: str,
    bcc: str,
    subject: str,
    body: str,
    attachment_paths: list[str],
    timeout: int | None,
    verify_draft: Callable[..., str],
) -> str:
    """Certify a draft only through its immediate transaction-scoped readback.

    Mail does not reliably expose attachment metadata on a newly created
    outgoing-message reference. The bounded pre/post Drafts snapshot provides
    one current locator; this function immediately verifies that exact row's
    recipient, subject, body, attachment-name multiset, positive sizes, and
    warnings. The numeric locator is not a durable identity and is never used
    for a later automatic mutation.
    """
    proof_prefix = "Attachment Transaction Proof: "
    proof = next(
        (line[len(proof_prefix) :].strip() for line in output.splitlines() if line.startswith(proof_prefix)), ""
    )
    if proof == "verified":
        return "\n".join(
            [
                "Attachment Verification Status: verified",
                "Attachment Proof Scope: same-operation marker-bound persisted Drafts row",
                "Draft Locator: unavailable after iCloud ID rewrite",
                "Draft Locator Stability: not a reusable identity",
                "Attachment-bearing draft is ready for human review.",
            ]
        )
    if proof:
        return (
            "Error: DRAFT_ATTACHMENT_READBACK_FAILED\n"
            f"Persisted marker-draft verification returned {proof!r}; the draft is not ready."
        )

    draft_id = _draft_locator(output)
    if not draft_id:
        return (
            "Error: DRAFT_ATTACHMENT_READBACK_ID_UNAVAILABLE\n"
            "Mail did not expose one current Drafts locator for immediate verification; the draft is not ready."
        )
    try:
        raw_readback = verify_draft(
            account=account,
            draft_id=draft_id,
            expected_to=to,
            expected_cc=cc,
            expected_subject=subject,
            expected_body_contains=body,
            expected_attachments=[Path(path).name for path in attachment_paths],
            expected_signature=None,
            timeout=timeout,
        )
        readback = json.loads(raw_readback)
    except (Exception, json.JSONDecodeError):
        return (
            "Error: DRAFT_ATTACHMENT_READBACK_FAILED\n"
            f"Draft Locator: {draft_id}\n"
            "Mail could not immediately verify the saved draft body and attachments; the draft is not ready."
        )
    if not _strict_readback_matches(readback, draft_id, attachment_paths, cc=cc, bcc=bcc):
        return (
            "Error: DRAFT_ATTACHMENT_READBACK_FAILED\n"
            f"Draft Locator: {draft_id}\n"
            "The immediate Drafts readback did not verify the authored body and every requested readable attachment; the draft is not ready."
        )
    return "\n".join(
        [
            "Attachment Verification Status: verified",
            "Attachment Proof Scope: immediate transaction-scoped Drafts readback",
            f"Draft Locator: {draft_id}",
            "Draft Locator Stability: best-effort; not identity proof",
            "Attachment-bearing draft is ready for human review.",
        ]
    )
