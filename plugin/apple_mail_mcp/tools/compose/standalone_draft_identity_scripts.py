"""Persisted Drafts identity scripts for standalone attachment drafts."""

from apple_mail_mcp.core.reply_state import drafts_mailbox_block
from apple_mail_mcp.tools.compose.constants import DRAFT_LIST_CAP


def _standalone_draft_identity_handlers() -> str:
    """Return handlers that identify one newly persisted Drafts message.

    Mail's outgoing-message ``id`` is an in-memory object identifier on some
    providers, including iCloud. A complete pre-compose Drafts snapshot and a
    post-save diff prove the actual Drafts message instead. RFC Message-ID is
    preferred when available. iCloud can leave it blank on unsent drafts, so a
    numeric Drafts-ID diff is the only fallback: it is bounded, requires the
    mailbox count to grow by exactly one, and rejects every ambiguous result.
    """
    return """
using terms from application "Mail"
on isNumericStandaloneDraftId(candidateId)
    if candidateId is "" then return false
    repeat with candidateCharacter in characters of candidateId
        if "0123456789" does not contain (candidateCharacter as string) then return false
    end repeat
    return true
end isNumericStandaloneDraftId

on fullDraftRfcSnapshot(draftsMailbox, draftCap)
    try
        set draftCount to count of messages of draftsMailbox
        if draftCount > draftCap then return missing value
        if draftCount is 0 then return {0, {}, {}}
        set draftMessages to messages 1 thru draftCount of draftsMailbox
        set rfcMessageIds to {}
        set numericDraftIds to {}
        repeat with aDraft in draftMessages
            set candidateDraftId to ""
            try
                set candidateDraftId to id of aDraft as string
            end try
            set rfcMessageId to ""
            try
                set rfcMessageId to message id of aDraft as string
            end try
            set end of rfcMessageIds to rfcMessageId
            set end of numericDraftIds to candidateDraftId
        end repeat
        return {draftCount, rfcMessageIds, numericDraftIds}
    on error
        return missing value
    end try
end fullDraftRfcSnapshot

on persistedStandaloneDraftId(draftsMailbox, beforeSnapshot, draftCap)
    try
        if beforeSnapshot is missing value then return {"", ""}
        set beforeCount to item 1 of beforeSnapshot
        set beforeRfcMessageIds to item 2 of beforeSnapshot
        set beforeNumericDraftIds to item 3 of beforeSnapshot
        set afterCount to count of messages of draftsMailbox
        if afterCount is not (beforeCount + 1) or afterCount > draftCap then return {"", ""}
        set afterDrafts to messages 1 thru afterCount of draftsMailbox
        set rfcCandidateIds to {}
        set numericCandidateIds to {}
        repeat with aDraft in afterDrafts
            set candidateDraftId to ""
            try
                set candidateDraftId to id of aDraft as string
            end try
            set rfcMessageId to ""
            try
                set rfcMessageId to message id of aDraft as string
            end try
            if rfcMessageId is not "" and beforeRfcMessageIds does not contain rfcMessageId then
                set end of rfcCandidateIds to candidateDraftId
            end if
            if my isNumericStandaloneDraftId(candidateDraftId) and beforeNumericDraftIds does not contain candidateDraftId then
                set end of numericCandidateIds to candidateDraftId
            end if
        end repeat
        if (count of rfcCandidateIds) is 1 then return {(item 1 of rfcCandidateIds as string), "rfc_message_id"}
        if (count of numericCandidateIds) is 1 then return {(item 1 of numericCandidateIds as string), "numeric_snapshot"}
    on error
        return {"", ""}
    end try
    return {"", ""}
end persistedStandaloneDraftId
end using terms from
"""


def standalone_draft_identity_setup_script() -> str:
    """Snapshot the complete bounded Drafts mailbox before compose creation."""
    drafts_resolver = drafts_mailbox_block(var_name="draftsMailbox", account_var="targetAccount")
    return f"""
            set preSaveDraftSnapshot to missing value
            {drafts_resolver}
            try
                if draftsMailbox is not missing value then
                    set preSaveDraftSnapshot to my fullDraftRfcSnapshot(draftsMailbox, {DRAFT_LIST_CAP})
                end if
            end try
"""


def standalone_draft_identity_resolver_script() -> str:
    """Resolve one new persisted Drafts ID after save, or emit no ID safely."""
    return f"""
            set savedDraftId to ""
            set savedDraftIdSource to ""
            try
                if preSaveDraftSnapshot is not missing value and draftsMailbox is not missing value then
                    -- iCloud may index the saved Drafts row after ``save`` returns.
                    delay 0.8
                    repeat with identityAttempt from 1 to 3
                        set savedDraftIdentity to my persistedStandaloneDraftId(draftsMailbox, preSaveDraftSnapshot, {DRAFT_LIST_CAP})
                        set savedDraftId to item 1 of savedDraftIdentity as string
                        set savedDraftIdSource to item 2 of savedDraftIdentity as string
                        if savedDraftId is not "" then exit repeat
                        if identityAttempt is less than 3 then delay 0.5
                    end repeat
                end if
            on error
                set savedDraftId to ""
            end try
"""


def standalone_marker_draft_finalize_script(marker: str, final_subject: str, proof_script: str) -> str:
    """Resolve one saved marker draft, then set its requested visible subject.

    A pre/post numeric-ID diff is conservative but can miss an iCloud row when
    the provider reindexes around save. The random marker is unique to this
    operation and is looked up only under the bounded Drafts cap. It is never a
    broad subject fallback and is replaced before the strict saved-draft check.
    """
    from apple_mail_mcp.core import escape_applescript

    safe_marker = escape_applescript(marker)
    safe_subject = escape_applescript(final_subject)
    return f"""
            set savedDraftId to ""
            set savedDraftIdSource to ""
            set attachmentTransactionProof to "identity_unavailable"
            try
                if draftsMailbox is not missing value then
                    repeat with identityAttempt from 1 to 4
                        set draftCount to count of messages of draftsMailbox
                        if draftCount is greater than {DRAFT_LIST_CAP} then exit repeat
                        set markedDrafts to {{}}
                        if draftCount is greater than 0 then
                            set draftMessages to messages 1 thru draftCount of draftsMailbox
                            repeat with candidateDraft in draftMessages
                                try
                                    if (subject of candidateDraft as string) is "{safe_marker}" then
                                        set end of markedDrafts to candidateDraft
                                    end if
                                end try
                            end repeat
                        end if
                        if (count of markedDrafts) is 1 then
                            set markedDraft to item 1 of markedDrafts
                            set candidateDraftId to id of markedDraft as string
                            if my isNumericStandaloneDraftId(candidateDraftId) then
                                {proof_script}
                                if attachmentTransactionProof is not "verified" then error "DRAFT_ATTACHMENT_PROOF_FAILED: " & attachmentTransactionProof
                                set subject of newMsg to "{safe_subject}"
                                save newMsg
                                delay 0.3
                                if (subject of markedDraft as string) is not "{safe_subject}" then error "DRAFT_ATTACHMENT_FINALIZATION_FAILED"
                                set refreshedDraftId to id of markedDraft as string
                                if my isNumericStandaloneDraftId(refreshedDraftId) then
                                    set savedDraftId to refreshedDraftId
                                    set savedDraftIdSource to "operation_subject_marker"
                                end if
                                exit repeat
                            end if
                        end if
                        if identityAttempt is less than 4 then delay 0.5
                    end repeat
                end if
            on error errMsg
                set savedDraftId to ""
                set attachmentTransactionProof to "finalization_failed"
                error errMsg
            end try
"""
