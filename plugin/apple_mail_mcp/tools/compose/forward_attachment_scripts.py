"""Transaction-scoped AppleScript for attachment-bearing forward drafts."""

from pathlib import Path

from apple_mail_mcp.core import escape_applescript
from apple_mail_mcp.tools.compose.constants import DRAFT_LIST_CAP


def forward_marker_draft_verification_handlers() -> str:
    """Verify a marked forward while allowing only Mail's Outlook PNG asset."""
    return """
using terms from application "Mail"
on forwardMarkerInlineSignatureAsset(attachmentName)
    ignoring case
        return attachmentName starts with "Outlook-" and attachmentName ends with ".png"
    end ignoring
end forwardMarkerInlineSignatureAsset

on forwardMarkerDraftProof(draftMessage, expectedTo, expectedCc, expectedBcc, expectedBody, expectedAttachmentNames)
    try
        if my markerRecipientSetMatches(to recipients of draftMessage, expectedTo) is false then return "recipient_mismatch"
        if my markerRecipientSetMatches(cc recipients of draftMessage, expectedCc) is false then return "cc_recipient_mismatch"
        if my markerRecipientSetMatches(bcc recipients of draftMessage, expectedBcc) is false then return "bcc_recipient_mismatch"
        if (content of draftMessage as string) does not contain expectedBody then return "body_mismatch"
        set savedAttachments to mail attachments of draftMessage
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
        repeat with remainingAttachmentName in remainingAttachmentNames
            if remainingAttachmentName is not missing value then
                if my forwardMarkerInlineSignatureAsset(remainingAttachmentName as string) is false then return "attachment_mismatch"
            end if
        end repeat
        return "verified"
    on error
        return "unavailable"
    end try
end forwardMarkerDraftProof
end using terms from
"""


def forward_marker_draft_proof_call(
    *,
    to_addresses: list[str],
    cc_addresses: list[str],
    bcc_addresses: list[str],
    body: str,
    attachment_paths: list[str],
) -> str:
    """Build the strict proof call for the single marked forward Drafts row."""

    def address_list(addresses: list[str]) -> str:
        return ", ".join(f'"{escape_applescript(address)}"' for address in addresses)

    attachment_names = ", ".join(f'"{escape_applescript(Path(path).name)}"' for path in attachment_paths)
    return (
        "set forwardAttachmentProof to my forwardMarkerDraftProof(markedForwardDraft, "
        f"{{{address_list(to_addresses)}}}, {{{address_list(cc_addresses)}}}, "
        f'{{{address_list(bcc_addresses)}}}, "{escape_applescript(body)}", {{{attachment_names}}})'
    )


def forward_marker_finalize_script(marker: str, proof_script: str) -> str:
    """Verify then finalize one marked draft, deleting this operation on failure."""
    safe_marker = escape_applescript(marker)
    return f"""
        set forwardAttachmentProof to "identity_unavailable"
        try
            if draftsMailbox is not missing value then
                repeat with markerAttempt from 1 to 4
                    set draftCount to count of messages of draftsMailbox
                    if draftCount is greater than {DRAFT_LIST_CAP} then exit repeat
                    set markedForwardDrafts to {{}}
                    if draftCount is greater than 0 then
                        set draftMessages to messages 1 thru draftCount of draftsMailbox
                        repeat with candidateDraft in draftMessages
                            try
                                if (subject of candidateDraft as string) is "{safe_marker}" then
                                    set end of markedForwardDrafts to candidateDraft
                                end if
                            end try
                        end repeat
                    end if
                    if (count of markedForwardDrafts) is 1 then
                        set markedForwardDraft to item 1 of markedForwardDrafts
                        {proof_script}
                        if forwardAttachmentProof is not "verified" then error "FORWARD_ATTACHMENT_PROOF_FAILED: " & forwardAttachmentProof
                        set subject of markedForwardDraft to fwdSubject
                        save markedForwardDraft
                        delay 0.3
                        if (subject of markedForwardDraft as string) is not fwdSubject then error "FORWARD_ATTACHMENT_FINALIZATION_FAILED"
                        set refreshedDraftId to id of markedForwardDraft as string
                        if my isNumericStandaloneDraftId(refreshedDraftId) then
                            set savedDraftId to refreshedDraftId
                            set savedDraftIdSource to "operation_subject_marker"
                        end if
                        exit repeat
                    end if
                    if markerAttempt is less than 4 then delay 0.5
                end repeat
            end if
            if forwardAttachmentProof is not "verified" then error "FORWARD_ATTACHMENT_PROOF_FAILED: " & forwardAttachmentProof
        on error errMsg
            try
                delete markedForwardDraft
            on error
                try
                    delete forwardMessage
                end try
            end try
            set savedDraftId to ""
            set forwardAttachmentProof to "finalization_failed"
            error errMsg
        end try
"""
