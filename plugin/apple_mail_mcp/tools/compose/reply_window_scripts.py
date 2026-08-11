"""AppleScript handlers for identifying and closing one native reply window."""


def native_reply_window_handlers_applescript() -> str:
    """Return id-based native-reply window handlers.

    A reply title is diagnostic only.  The caller snapshots Mail window ids before
    creating the reply, adopts exactly one new same-thread window afterward, and
    never closes a window unless that exact id still has an expected title.
    """
    return """
on mailWindowIdSnapshot()
    tell application "Mail"
        try
            set windowIds to {}
            repeat with aWindow in every window
                set end of windowIds to id of aWindow as string
            end repeat
            return windowIds
        on error
            return missing value
        end try
    end tell
end mailWindowIdSnapshot

on windowIdWasPresent(candidateId, priorWindowIds)
    repeat with priorId in priorWindowIds
        if (contents of priorId as string) is candidateId then return true
    end repeat
    return false
end windowIdWasPresent

on newlyOpenedReplyWindowId(priorWindowIds, derivedTitle)
    if priorWindowIds is missing value then return ""
    tell application "Mail"
        try
            set matchingIds to {}
            repeat with candidateWindow in every window
                set candidateId to id of candidateWindow as string
                if (my windowIdWasPresent(candidateId, priorWindowIds)) is false then
                    set candidateTitle to name of candidateWindow as string
                    if my subjectCoresMatch(candidateTitle, derivedTitle) then set end of matchingIds to candidateId
                end if
            end repeat
            if (count of matchingIds) is 1 then return item 1 of matchingIds as string
        end try
    end tell
    return ""
end newlyOpenedReplyWindowId

on raiseNativeReplyWindowSafely(replyWindowId, expectedTitle, derivedTitle)
    -- Never raise a title-matched window here.  A user can have multiple
    -- replies with the same subject open, so only the window adopted from the
    -- pre/post reply snapshot is eligible to become frontmost.
    if replyWindowId is "" then return false
    tell application "Mail"
        try
            set candidateWindow to first window whose id is replyWindowId
            set candidateTitle to name of candidateWindow as string
            if candidateTitle is expectedTitle or candidateTitle is derivedTitle then
                set index of candidateWindow to 1
                activate
                return true
            end if
        end try
    end tell
    return false
end raiseNativeReplyWindowSafely

on closeNativeReplyWindowSafely(replyWindowId, expectedTitle, derivedTitle)
    if replyWindowId is "" then return false
    tell application "Mail"
        try
            set candidateWindow to first window whose id is replyWindowId
            set candidateTitle to name of candidateWindow as string
            if candidateTitle is expectedTitle or candidateTitle is derivedTitle then
                close candidateWindow saving no
                return true
            end if
        end try
    end tell
    return false
end closeNativeReplyWindowSafely
"""
