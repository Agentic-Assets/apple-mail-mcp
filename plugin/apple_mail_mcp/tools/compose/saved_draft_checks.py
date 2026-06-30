"""Saved reply/forward draft verification probes used by the reply and forward tools (and by tests directly).

``run_applescript`` is reached through the ``compose`` facade to preserve the existing patch seam."""

from apple_mail_mcp.applescript_snippets import sanitize_field_handler, text_offset_handler
from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.constants import DRAFT_LIST_CAP
from apple_mail_mcp.tools.compose.verification import (
    _first_non_empty_line,
    _format_forward_verification_lines,
    _reply_verification_from_output,
    _ReplyDraftVerification,
)


def _verify_saved_forward_draft(
    account: str,
    *,
    draft_id: str | None,
    to: str,
    subject: str | None,
    lead_message: str | None,
    expected_signature: bool | None,
    timeout: int | None = None,
) -> str:
    """Verify a saved forward draft by exact Drafts id when Mail exposes it."""
    if not draft_id:
        return "Verification Status: unavailable\nWarning: Mail did not expose a saved forward Draft ID\n"

    raw_verification = compose.verify_draft(
        account=account,
        draft_id=draft_id,
        expected_to=to,
        expected_subject=subject,
        expected_body_contains=_first_non_empty_line(lead_message or "") or None,
        expected_signature=expected_signature,
        timeout=timeout,
    )
    return _format_forward_verification_lines(raw_verification, draft_id)


def _verify_saved_reply_draft(
    account: str,
    reply_subject: str,
    reply_body: str,
    *,
    draft_id: str | None = None,
    quoted_needle: str | None = None,
    expected_attachment_count: int | None = None,
    expected_attachment_names: list[str] | None = None,
    signature_requested: bool | None = None,
    expected_signature_name: str | None = None,
    timeout: int | None = None,
) -> _ReplyDraftVerification:
    """Confirm a native reply draft appears in a bounded newest Drafts window."""
    safe_account = escape_applescript(account)
    safe_reply_subject = escape_applescript(reply_subject)
    safe_body_needle = escape_applescript(_first_non_empty_line(reply_body))
    safe_draft_id = escape_applescript(draft_id or "")
    safe_quoted_needle = escape_applescript(_first_non_empty_line(quoted_needle or ""))
    expected_attachment_names = expected_attachment_names or []
    expected_attachment_names_script = (
        "{" + ", ".join(f'"{escape_applescript(name)}"' for name in expected_attachment_names if name) + "}"
    )
    expected_attachment_count_value = -1 if expected_attachment_count is None else max(0, expected_attachment_count)
    signature_requested_flag = (
        "missing value" if signature_requested is None else ("true" if signature_requested else "false")
    )
    safe_expected_signature_name = escape_applescript(expected_signature_name or "")
    verification_timeout = 60 if timeout is None else max(30, min(timeout, 120))
    sanitize_script = sanitize_field_handler(include_attachment_row_delimiter=True)
    text_offset_script = text_offset_handler()
    script = f'''
    {sanitize_script}

    {text_offset_script}

    on stripLineBreaks(theText)
        -- Mail's compose window soft-wraps long lines, and `content as string`
        -- renders those wraps as line breaks (sometimes mid-word), which would
        -- defeat a contiguous-substring match for a typed reply body. Removing
        -- CR/LF rejoins the text so the body needle is found regardless of wrap.
        set previousDelimiters to AppleScript's text item delimiters
        set AppleScript's text item delimiters to {{return, linefeed}}
        set lineParts to text items of theText
        set AppleScript's text item delimiters to ""
        set joinedText to lineParts as string
        set AppleScript's text item delimiters to previousDelimiters
        return joinedText
    end stripLineBreaks

    on replyBodyIsBeforeQuote(draftContent, replyBodyNeedle, quotedNeedle)
        set flatContent to my stripLineBreaks(draftContent)
        set bodyOffset to my textOffset(flatContent, my stripLineBreaks(replyBodyNeedle))
        if bodyOffset is 0 then return "missing"
        if quotedNeedle is "" then return "found"
        set quoteOffset to my textOffset(flatContent, my stripLineBreaks(quotedNeedle))
        if quoteOffset is 0 then return "found"
        if bodyOffset < quoteOffset then return "found"
        return "after_quote"
    end replyBodyIsBeforeQuote

    using terms from application "Mail"

    on attachmentStatus(draftMessage, expectedAttachmentCount, expectedAttachmentNames)
        if expectedAttachmentCount < 0 then return "not_requested"
        try
            set draftAttachments to mail attachments of draftMessage
            set actualAttachmentCount to count of draftAttachments
            if actualAttachmentCount < expectedAttachmentCount then return "missing"
            if (count of expectedAttachmentNames) is 0 then return "verified"

            set draftAttachmentNames to {{}}
            repeat with anAttachment in draftAttachments
                set end of draftAttachmentNames to (name of anAttachment as string)
            end repeat
            repeat with expectedAttachmentName in expectedAttachmentNames
                set matchIndex to 0
                repeat with nameIndex from 1 to count of draftAttachmentNames
                    if item nameIndex of draftAttachmentNames is (expectedAttachmentName as string) then
                        set matchIndex to nameIndex
                        exit repeat
                    end if
                end repeat
                if matchIndex is 0 then return "missing"
                set item matchIndex of draftAttachmentNames to missing value
            end repeat
            return "verified"
        on error
            return "unsupported"
        end try
    end attachmentStatus

    on attachmentCount(draftMessage)
        try
            return count of mail attachments of draftMessage
        on error
            return ""
        end try
    end attachmentCount

    on attachmentRows(draftMessage)
        set attachmentRowsText to ""
        try
            repeat with anAttachment in mail attachments of draftMessage
                set attachmentName to my sanitize_field(name of anAttachment)
                set attachmentSize to ""
                try
                    set attachmentSize to file size of anAttachment as string
                end try
                set attachmentRowsText to attachmentRowsText & attachmentName & "::" & attachmentSize & ";;"
            end repeat
        end try
        return attachmentRowsText
    end attachmentRows

    on signatureStatus(draftContent, replyBodyNeedle, quotedNeedle, signatureWasRequested, expectedSignatureName)
        if signatureWasRequested is missing value then return "not_requested"
        if signatureWasRequested is false then return "not_requested"
        set newBodyText to draftContent
        if quotedNeedle is not "" then
            set quoteOffset to my textOffset(draftContent, quotedNeedle)
            if quoteOffset > 1 then set newBodyText to text 1 thru (quoteOffset - 1) of draftContent
        end if
        try
            if expectedSignatureName is not "" then
                repeat with sig in signatures
                    if (name of sig as string) is expectedSignatureName then
                        set expectedSigText to content of sig as string
                        if expectedSigText is not "" and newBodyText contains expectedSigText then return "detected"
                        return "missing"
                    end if
                end repeat
                return "missing"
            end if
            repeat with sig in signatures
                set sigText to content of sig as string
                if sigText is not "" and newBodyText contains sigText then return "detected"
            end repeat
        end try
        return "missing"
    end signatureStatus

    on verifyReplyDraft(draftMessage, replyBodyNeedle, quotedNeedle, expectedAttachmentCount, expectedAttachmentNames, signatureWasRequested, expectedSignatureName)
        set draftId to id of draftMessage as string
        set draftContent to content of draftMessage as string
        set draftAttachmentStatus to my attachmentStatus(draftMessage, expectedAttachmentCount, expectedAttachmentNames)
        set draftAttachmentCount to my attachmentCount(draftMessage)
        set draftAttachmentRows to my attachmentRows(draftMessage)
        set draftSignatureStatus to my signatureStatus(draftContent, replyBodyNeedle, quotedNeedle, signatureWasRequested, expectedSignatureName)
        if replyBodyNeedle is "" then return "FOUND|" & draftId & "|" & draftAttachmentStatus & "|" & draftSignatureStatus & "|" & draftAttachmentCount & "|" & draftAttachmentRows
        set bodyStatus to my replyBodyIsBeforeQuote(draftContent, replyBodyNeedle, quotedNeedle)
        if bodyStatus is "found" then return "FOUND|" & draftId & "|" & draftAttachmentStatus & "|" & draftSignatureStatus & "|" & draftAttachmentCount & "|" & draftAttachmentRows
        if bodyStatus is "after_quote" then return "BODY_AFTER_QUOTE|" & draftId
        return "BODY_MISSING|" & draftId
    end verifyReplyDraft

    end using terms from

    tell application "Mail"
        set targetAccount to account "{safe_account}"
        set targetDraftIdText to "{safe_draft_id}"
        set replyBodyNeedle to "{safe_body_needle}"
        set quotedNeedle to "{safe_quoted_needle}"
        set expectedAttachmentCount to {expected_attachment_count_value}
        set expectedAttachmentNames to {expected_attachment_names_script}
        set signatureWasRequested to {signature_requested_flag}
        set expectedSignatureName to "{safe_expected_signature_name}"
        set replyDraftVerified to false
        set bodyMissingDraftId to ""
        set bodyAfterQuoteDraftId to ""
        set foundDraftId to ""

        repeat with verifyAttempt from 1 to 20
            try
                set draftsMailbox to mailbox "Drafts" of targetAccount
                if targetDraftIdText is not "" then
                    try
                        set targetDraftId to targetDraftIdText as integer
                        set targetDrafts to every message of draftsMailbox whose id is targetDraftId
                        if (count of targetDrafts) > 0 then
                            set exactDraft to item 1 of targetDrafts
                            set exactResult to my verifyReplyDraft(exactDraft, replyBodyNeedle, quotedNeedle, expectedAttachmentCount, expectedAttachmentNames, signatureWasRequested, expectedSignatureName)
                            return exactResult
                        end if
                    end try
                end if

                set totalDrafts to count of messages of draftsMailbox
                set headEnd to totalDrafts
                if headEnd > {DRAFT_LIST_CAP} then set headEnd to {DRAFT_LIST_CAP}
                if headEnd > 0 then
                    set candidateDrafts to messages 1 thru headEnd of draftsMailbox
                    repeat with draftMessage in candidateDrafts
                        try
                            set draftMatched to false
                            set draftSubject to subject of draftMessage as string
                            if "{safe_reply_subject}" is "" or draftSubject is "{safe_reply_subject}" then
                                set draftResult to my verifyReplyDraft(draftMessage, replyBodyNeedle, quotedNeedle, expectedAttachmentCount, expectedAttachmentNames, signatureWasRequested, expectedSignatureName)
                                if draftResult starts with "FOUND|" then
                                    set draftMatched to true
                                    set foundDraftId to draftResult
                                else if draftResult starts with "BODY_AFTER_QUOTE|" then
                                    if bodyAfterQuoteDraftId is "" then set bodyAfterQuoteDraftId to text 18 thru -1 of draftResult
                                else if draftResult starts with "BODY_MISSING|" then
                                    if bodyMissingDraftId is "" then set bodyMissingDraftId to text 14 thru -1 of draftResult
                                end if
                            end if

                            if draftMatched then
                                set replyDraftVerified to true
                                exit repeat
                            end if
                        end try
                    end repeat
                end if
            end try
            if replyDraftVerified then exit repeat
            delay 1
        end repeat

        if replyDraftVerified then
            return foundDraftId
        end if
        if bodyAfterQuoteDraftId is not "" then
            return "BODY_AFTER_QUOTE|" & bodyAfterQuoteDraftId
        end if
        if bodyMissingDraftId is not "" then
            return "BODY_MISSING|" & bodyMissingDraftId
        end if
        return "NOT_FOUND"
    end tell
    '''
    try:
        output = compose.run_applescript(script, timeout=verification_timeout).strip()
    except AppleScriptTimeout:
        return _ReplyDraftVerification(ok=False, status="verification_timeout", error_artifact_id=draft_id)
    except Exception:  # noqa: BLE001 - caller converts verification failure into a safe error
        return _ReplyDraftVerification(ok=False, status="applescript_error", error_artifact_id=draft_id)
    return _reply_verification_from_output(output)
