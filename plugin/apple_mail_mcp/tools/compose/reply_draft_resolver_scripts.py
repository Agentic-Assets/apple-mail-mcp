"""Safe native-reply Drafts-ID resolver AppleScript fragments.

Mail's immediate ``outgoing message`` identifier is not reliably the same as
the persisted identifier in an account's Drafts mailbox, particularly on
Exchange. Native replies therefore snapshot the complete Drafts mailbox only
when it fits inside a bounded cap, then emit an identity capsule only when one
new persisted RFC Message-ID has an ``In-Reply-To`` RFC token matching source.
"""

from apple_mail_mcp.tools.compose.constants import DRAFT_LIST_CAP


def _native_reply_draft_resolver_handlers_applescript() -> str:
    """Return Mail-aware handlers for a conservative persisted-Drafts lookup."""
    return """
using terms from application "Mail"
on fullDraftRfcSnapshot(draftsMailbox, draftCap)
    try
        set totalDrafts to count of messages of draftsMailbox
        if totalDrafts > draftCap then return missing value
        if totalDrafts is 0 then return {0, {}}
        set draftMessages to messages 1 thru totalDrafts of draftsMailbox
        set draftRfcMessageIds to {}
        repeat with aDraft in draftMessages
            set rfcMessageId to message id of aDraft as string
            if rfcMessageId is "" then return missing value
            set end of draftRfcMessageIds to rfcMessageId
        end repeat
        return {totalDrafts, draftRfcMessageIds}
    on error
        return missing value
    end try
end fullDraftRfcSnapshot

on sourceRfcMessageIdFor(sourceMessage)
    try
        set sourceMessageId to message id of sourceMessage as string
        if sourceMessageId is not "" then return sourceMessageId
    end try
    return ""
end sourceRfcMessageIdFor

on rfcMessageIdWasPresent(rfcMessageId, priorRfcMessageIds)
    repeat with priorRfcMessageId in priorRfcMessageIds
        if (contents of priorRfcMessageId as string) is rfcMessageId then return true
    end repeat
    return false
end rfcMessageIdWasPresent

on draftInReplyTo(draftMessage)
    try
        repeat with aHeader in (headers of draftMessage)
            if (name of aHeader as string) is "In-Reply-To" then return {true, content of aHeader as string}
        end repeat
        return {true, ""}
    on error
        return {false, ""}
    end try
end draftInReplyTo

on headerHasExactRfcToken(headerText, expectedRfcMessageId)
    if headerText is "" or expectedRfcMessageId is "" then return false
    set savedDelimiters to AppleScript's text item delimiters
    try
        set AppleScript's text item delimiters to ">"
        set headerParts to text items of headerText
        set AppleScript's text item delimiters to savedDelimiters
        repeat with headerPart in headerParts
            set partText to contents of headerPart as string
            if partText contains "<" then
                set AppleScript's text item delimiters to "<"
                set idParts to text items of partText
                set AppleScript's text item delimiters to savedDelimiters
                if (count of idParts) > 1 then
                    set candidateRfcMessageId to "<" & (item -1 of idParts as string) & ">"
                    if candidateRfcMessageId is expectedRfcMessageId then return true
                end if
            end if
        end repeat
    on error
        set AppleScript's text item delimiters to savedDelimiters
        return false
    end try
    set AppleScript's text item delimiters to savedDelimiters
    return false
end headerHasExactRfcToken

on persistedReplyDraftIdentity(draftsMailbox, preSaveDraftSnapshot, sourceMessageId, draftCap)
    try
        if sourceMessageId is "" then return ""
        if preSaveDraftSnapshot is missing value then return ""
        set preSaveDraftCount to item 1 of preSaveDraftSnapshot
        set preSaveDraftRfcMessageIds to item 2 of preSaveDraftSnapshot
        set postSaveDraftCount to count of messages of draftsMailbox
        if postSaveDraftCount > draftCap then return ""
        if postSaveDraftCount is not (preSaveDraftCount + 1) then return ""
        set postSaveDrafts to messages 1 thru postSaveDraftCount of draftsMailbox
        set matchingDraftIdentities to {}
        repeat with aDraft in postSaveDrafts
            set candidateDraftId to id of aDraft as string
            set candidateRfcMessageId to message id of aDraft as string
            if candidateRfcMessageId is "" then return ""
            if (my rfcMessageIdWasPresent(candidateRfcMessageId, preSaveDraftRfcMessageIds)) is false then
                set inReplyToResult to my draftInReplyTo(aDraft)
                if item 1 of inReplyToResult is false then return ""
                if my headerHasExactRfcToken(item 2 of inReplyToResult, sourceMessageId) then
                    set end of matchingDraftIdentities to {candidateDraftId, candidateRfcMessageId}
                end if
            end if
        end repeat
        if (count of matchingDraftIdentities) is 1 then
            set matchingIdentity to item 1 of matchingDraftIdentities
            return {item 1 of matchingIdentity as string, item 2 of matchingIdentity as string, sourceMessageId}
        end if
    end try
    return ""
end persistedReplyDraftIdentity
end using terms from
"""


def _native_reply_draft_resolver_setup_script() -> str:
    """Return the bounded pre-save snapshot and source RFC Message-ID lookup."""
    return f"""
        set sourceRfcMessageId to ""
        set preSaveDraftSnapshot to missing value
        try
            set sourceRfcMessageId to my sourceRfcMessageIdFor(foundMessage)
            set draftsMailbox to mailbox "Drafts" of targetAccount
            set preSaveDraftSnapshot to my fullDraftRfcSnapshot(draftsMailbox, {DRAFT_LIST_CAP})
        end try
    """


def _native_reply_draft_resolver_script() -> str:
    """Return a no-ID-on-ambiguity persisted-Drafts resolver after save."""
    return f"""
        set replyDraftId to ""
        set replyDraftRfcMessageId to ""
        try
            if preSaveDraftSnapshot is not missing value and sourceRfcMessageId is not "" then
                repeat with identityAttempt from 1 to 3
                    set replyDraftIdentity to my persistedReplyDraftIdentity(draftsMailbox, preSaveDraftSnapshot, sourceRfcMessageId, {DRAFT_LIST_CAP})
                    if replyDraftIdentity is not "" then
                        set replyDraftId to item 1 of replyDraftIdentity as string
                        set replyDraftRfcMessageId to item 2 of replyDraftIdentity as string
                        exit repeat
                    end if
                    if identityAttempt is less than 3 then delay 0.5
                end repeat
            end if
        on error
            set replyDraftId to ""
            set replyDraftRfcMessageId to ""
        end try
    """
