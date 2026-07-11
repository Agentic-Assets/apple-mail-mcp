"""Focus-guarded chunked System Events keystroke handler for the native reply body.

New leaf module (AGENTIC-1214) so the AppleScript handler text lives outside
``reply_scripts.py``, which is close to the 600 LOC module budget. A single
``keystroke`` of an entire reply body silently drops its tail near 320-480
characters (Bug 1) and can leak shift state into ALL CAPS output (Bug 3).
Typing the body in small chunks, clearing modifier state before and after
every chunk, and re-checking BOTH Mail's own front window and System Events'
process-level focus before every chunk keeps each keystroke call within
Mail's WebKit compose editor throughput and aborts immediately (never
re-stealing focus) the moment another window or application takes system
focus mid-typing, so a chunk can never leak into the wrong place. Clipboard
paste stays banned for the reply body (two prior live reverts; see
``reply_scripts.py`` docstring).
"""


def build_chunked_typing_handler(*, chunk_size: int, inter_chunk_delay: float) -> str:
    """Return the AppleScript handler that types a reply body in focus-guarded chunks.

    Only the two numeric bounds are interpolated below; nothing user-derived
    reaches this text, so no AppleScript escaping is required. The returned
    handlers run at the top-level script scope (alongside the subject-helper
    handlers) and open their own ``Mail`` / ``System Events`` tells.
    """
    return f"""
on chunkFocusBlockedName(expectedTitle, derivedTitle)
    -- Returns "" when Mail's own front window AND System Events' process-level
    -- focus both still point at the expected reply window. Returns a diagnostic
    -- name otherwise so the caller can abort with the actual front window/app
    -- name instead of typing into whatever now holds focus.
    set mailFrontName to "(unset)"
    tell application "Mail"
        try
            set mailFrontName to name of front window
        end try
    end tell
    if mailFrontName is not expectedTitle and mailFrontName is not derivedTitle then
        return mailFrontName
    end if
    set seOk to false
    set seFrontName to "(unset)"
    tell application "System Events"
        tell process "Mail"
            try
                if frontmost is true then
                    set seFrontName to ""
                    try
                        set seFrontName to name of front window
                    end try
                    if seFrontName is expectedTitle or seFrontName is derivedTitle or seFrontName is "" then
                        set seOk to true
                    end if
                end if
            end try
        end tell
    end tell
    if seOk then return ""
    return "SystemEvents:" & seFrontName
end chunkFocusBlockedName

on typeReplyBodyChunks(bodyText, expectedTitle, derivedTitle)
    set bodyLength to count of characters of bodyText
    if bodyLength is 0 then return "typed"
    -- Clear any lingering modifier state before the first chunk. A truncated or
    -- interrupted prior keystroke pass is the suspected source of Bug 3's leaked
    -- shift state; releasing modifiers here resets it for this typing pass.
    tell application "System Events"
        key up shift
        key up option
        key up control
        key up command
    end tell
    set chunkStart to 1
    repeat while chunkStart is less than or equal to bodyLength
        set chunkEnd to chunkStart + {chunk_size} - 1
        if chunkEnd > bodyLength then set chunkEnd to bodyLength
        if chunkEnd < bodyLength then
            -- Prefer a newline boundary so line structure stays intact; fall
            -- back to a space boundary so words are not split across the
            -- inter-chunk pause; fall back to the hard size boundary when
            -- neither is found in the chunk window.
            set scanIndex to chunkEnd
            set boundaryFound to false
            repeat while scanIndex > chunkStart
                set scanChar to character scanIndex of bodyText
                if scanChar is return or scanChar is linefeed then
                    set chunkEnd to scanIndex
                    set boundaryFound to true
                    exit repeat
                end if
                set scanIndex to scanIndex - 1
            end repeat
            if boundaryFound is false then
                set scanIndex to chunkEnd
                repeat while scanIndex > chunkStart
                    if character scanIndex of bodyText is space then
                        set chunkEnd to scanIndex
                        exit repeat
                    end if
                    set scanIndex to scanIndex - 1
                end repeat
            end if
        end if
        set chunkText to text chunkStart thru chunkEnd of bodyText
        -- Re-verify focus before EACH chunk (not just once before the loop).
        -- A drift mid-typing means the user or another app took focus; abort
        -- so no partial body is ever left typed into a stray window.
        set blockedName to my chunkFocusBlockedName(expectedTitle, derivedTitle)
        if blockedName is not "" then
            return "interrupted:" & blockedName
        end if
        tell application "System Events"
            tell process "Mail"
                key up shift
                keystroke chunkText
                key up shift
            end tell
        end tell
        set chunkStart to chunkEnd + 1
        if chunkStart is less than or equal to bodyLength then delay {inter_chunk_delay}
    end repeat
    return "typed"
end typeReplyBodyChunks
"""
