"""Pure reply AppleScript builders (object-model and native-window paths).

These compose f-string scripts only; ``reply_to_email`` in ``compose.py`` runs
them. Keeping them here removes the largest AppleScript blocks from the tool
module without touching any I/O or patched name.
"""

from dataclasses import dataclass

from apple_mail_mcp.core import inbox_mailbox_script
from apple_mail_mcp.tools.compose.constants import TYPING_CHUNK_SIZE, TYPING_INTER_CHUNK_DELAY
from apple_mail_mcp.tools.compose.lookup_scripts import _compose_signature_script
from apple_mail_mcp.tools.compose.typing_scripts import build_chunked_typing_handler


@dataclass(frozen=True)
class _ReplyModePlan:
    header_text: str
    post_action: str
    success_text: str


def _reply_mode_plan(effective_mode: str) -> _ReplyModePlan:
    """Return mode-specific output and Mail action script for replies."""
    if effective_mode == "send":
        return _ReplyModePlan("SENDING REPLY", "send replyMessage", "Reply sent successfully!")
    if effective_mode == "open":
        return _ReplyModePlan(
            "OPENING REPLY FOR REVIEW",
            """
        save replyMessage
        delay 0.8
        activate
        """,
            "Reply opened in Mail for review. Edit and send when ready.",
        )
    return _ReplyModePlan(
        "SAVING REPLY AS DRAFT",
        """
        save replyMessage
        delay 1.0
        """,
        "Reply saved as draft!",
    )


def _reply_command_options(effective_mode: str, reply_to_all: bool) -> tuple[str, str]:
    """Return Mail `reply` command options and any required settle delay."""
    if effective_mode == "open":
        reply_options = "with opening window"
        if reply_to_all:
            reply_options += " and reply to all"
        return reply_options, "delay 0.6"
    if reply_to_all:
        return "with reply to all", ""
    return "", ""


def _reply_signature_script(
    resolved_signature_name: str | None,
    *,
    include_signature: bool,
) -> str:
    """Return reply-specific signature AppleScript."""
    if resolved_signature_name:
        return _compose_signature_script("replyMessage", resolved_signature_name)
    if not include_signature:
        return "set message signature of replyMessage to missing value"
    return ""


def _reply_extra_output_lines(
    *,
    safe_cc: str,
    safe_bcc: str,
    safe_attachment_info: str,
    has_cc: bool,
    has_bcc: bool,
    has_attachments: bool,
) -> str:
    """Build optional status lines appended to native reply output."""
    lines: list[str] = []
    if has_cc:
        lines.append(f'set outputText to outputText & "CC: {safe_cc}" & return')
    if has_bcc:
        lines.append(f'set outputText to outputText & "BCC: {safe_bcc}" & return')
    if has_attachments:
        lines.append(f'set outputText to outputText & "Attachments:" & return & "{safe_attachment_info}" & return')
    return "\n        ".join(lines)


def _build_reply_objectmodel_applescript(
    *,
    header_text: str,
    success_text: str,
    safe_account: str,
    lookup_script: str,
    not_found_message: str,
    body_temp_path: str,
    reply_options: str,
    reply_settle_delay: str,
    sender_script: str,
    signature_script: str,
    cc_script: str,
    bcc_script: str,
    attachment_script: str,
    post_action: str,
    cleanup_script: str,
    safe_cc: str,
    safe_bcc: str,
    safe_attachment_info: str,
    has_cc: bool,
    has_bcc: bool,
    has_attachments: bool,
) -> str:
    """Build the object-model reply script used when ``native_format=False``.

    This path assigns the reply ``content`` directly (reply_body + a plain-text
    quoted original) without opening a window. It is the headless/bulk-safe path:
    no GUI focus, no Accessibility permission. The trade-off is that Mail's native
    rich quote bar and logo signature are flattened to plain text. The windowed
    ``native_format=True`` path (``_build_reply_native_window_applescript``)
    preserves the native look; this is the quiet fallback.
    """
    extra_output_lines = _reply_extra_output_lines(
        safe_cc=safe_cc,
        safe_bcc=safe_bcc,
        safe_attachment_info=safe_attachment_info,
        has_cc=has_cc,
        has_bcc=has_bcc,
        has_attachments=has_attachments,
    )

    return f'''
tell application "Mail"
    set outputText to "{header_text}" & return & return

    try
        set targetAccount to account "{safe_account}"
        {inbox_mailbox_script("inboxMailbox", "targetAccount")}
        {lookup_script}

        if foundMessage is missing value then
            return "{not_found_message}"
        end if

        set sourceSubject to subject of foundMessage as string
        if sourceSubject starts with "Re:" or sourceSubject starts with "RE:" or sourceSubject starts with "re:" then
            set replySubject to sourceSubject
        else
            set replySubject to "Re: " & sourceSubject
        end if
        set sourceSender to sender of foundMessage as string
        set sourceDate to date received of foundMessage as string
        set sourceContent to content of foundMessage as string
        set replyBodyText to do shell script "cat " & quoted form of "{body_temp_path}"

        -- Native Mail reply: Mail creates an outgoing reply message from the
        -- source message, then this script assigns the intended plain-text body
        -- above the quoted original before the draft is saved.
        set replyMessage to reply foundMessage {reply_options}
        {reply_settle_delay}

        {sender_script}
        {signature_script}

        set quotedOriginalNeedle to ""
        if replyBodyText is not "" then
            set quotedOriginalNeedle to "On " & sourceDate & ", " & sourceSender & " wrote:"
            set quotedOriginalText to quotedOriginalNeedle & return & sourceContent
            set composedReplyContent to replyBodyText & return & return & quotedOriginalText
            set content of replyMessage to (composedReplyContent as rich text)
        end if

        -- Optional extra recipients, on top of Mail's native reply recipients.
        {cc_script}
        {bcc_script}

        -- Add attachments
        {attachment_script}

        {post_action}

        set replyDraftId to ""
        try
            set replyDraftId to id of replyMessage as string
        end try

        set outputText to outputText & "{success_text}" & return
        set outputText to outputText & "To: native reply recipients" & return
        set outputText to outputText & "Subject: " & replySubject & return
        if replyDraftId is not "" then set outputText to outputText & "Draft ID: " & replyDraftId & return
        if quotedOriginalNeedle is not "" then set outputText to outputText & "Quote Needle: " & quotedOriginalNeedle & return
        {extra_output_lines}

        -- Clean up temp file
        {cleanup_script}

        return outputText
    on error errMsg
        try
            {cleanup_script}
        end try
        return "Error: " & errMsg & return & "Please check that the account name is correct and the email exists."
    end try
    end tell
    '''


def _native_reply_post_action(mode: str) -> str:
    """Return the post-keystroke Mail action for the windowed native reply path.

    draft: save, then close the reply window quietly (one draft remains; the
    auto-created shell is reused by ``save``, so no dedupe is needed). open: save
    and leave the window up for review. send: send (the window closes itself).
    """
    if mode == "send":
        return "send replyMessage\n        delay 0.5"
    if mode == "open":
        return "save replyMessage\n        delay 0.8\n        activate"
    return (
        "save replyMessage\n"
        "        delay 1.0\n"
        "        try\n"
        "            close (every window whose name is replySubject) saving no\n"
        "        end try\n"
        "        try\n"
        "            if derivedReplySubject is not replySubject then "
        "close (every window whose name is derivedReplySubject) saving no\n"
        "        end try"
    )


def _native_reply_subject_helpers_applescript() -> str:
    """AppleScript handlers that collapse leading Re:/Fwd: prefixes for guard compares.

    Mail normalizes compose-window titles (e.g. ``RE:  Re: Foo`` → ``Re: Foo``).
    Exact string equality against the raw source subject is a false focus failure;
    compare subject cores instead, and prefer Mail's live front-window title.
    """
    return """
on stripLeadingSpaces(rawText)
    set t to rawText as string
    repeat while t starts with " "
        if (length of t) is 1 then return ""
        set t to text 2 thru -1 of t
    end repeat
    return t
end stripLeadingSpaces

on stripReplySubjectPrefixes(rawSubject)
    set t to my stripLeadingSpaces(rawSubject)
    repeat 10 times
        if t is "" then exit repeat
        set prefixLen to 0
        ignoring case
            if t starts with "re:" then
                set prefixLen to 3
            else if t starts with "fwd:" then
                set prefixLen to 4
            else if t starts with "fw:" then
                set prefixLen to 3
            end if
        end ignoring
        if prefixLen is 0 then exit repeat
        if (length of t) is less than or equal to prefixLen then
            set t to ""
            exit repeat
        end if
        set t to my stripLeadingSpaces(text (prefixLen + 1) thru -1 of t)
    end repeat
    return t
end stripReplySubjectPrefixes

on subjectCoresMatch(leftSubject, rightSubject)
    set leftCore to my stripReplySubjectPrefixes(leftSubject)
    set rightCore to my stripReplySubjectPrefixes(rightSubject)
    if leftCore is "" or rightCore is "" then return false
    ignoring case
        return (leftCore is rightCore)
    end ignoring
end subjectCoresMatch

on looksLikeReplyWindowTitle(windowTitle)
    set t to my stripLeadingSpaces(windowTitle)
    if t is "" then return false
    ignoring case
        if t starts with "re:" then return true
        if t starts with "fwd:" then return true
        if t starts with "fw:" then return true
    end ignoring
    return false
end looksLikeReplyWindowTitle
"""


def _build_reply_native_window_applescript(
    *,
    header_text: str,
    success_text: str,
    safe_account: str,
    lookup_script: str,
    not_found_message: str,
    body_temp_path: str,
    reply_options: str,
    sender_script: str,
    signature_script: str,
    cc_script: str,
    bcc_script: str,
    attachment_script: str,
    mode: str,
    cleanup_script: str,
    safe_cc: str,
    safe_bcc: str,
    safe_attachment_info: str,
    has_cc: bool,
    has_bcc: bool,
    has_attachments: bool,
) -> str:
    """Build the windowed native reply script used when ``native_format=True``.

    Mail's ``reply ... with opening window`` renders its own rich quoted thread
    (the colored quote bar) and inserts the account's default reply signature
    (with logo). Those exist only in the rendered compose window, never in the
    dictionary ``content``, so this path NEVER reassigns ``content`` (doing so
    flattens them — the prior bug). Instead the reply body is inserted with a
    TYPED System Events keystroke, in small focus-guarded chunks rather than one
    keystroke of the whole body (AGENTIC-1214: a single keystroke of the whole
    body drops its tail near 320-480 chars and can leak shift state into ALL
    CAPS output; see ``typing_scripts.build_chunked_typing_handler``). Never the
    clipboard, which clobbered the pasteboard and leaked bodies into the wrong
    thread in two prior live reverts.

    UI scripting is isolated to the focus guard + keystroke and is unavoidable
    here: the native rich format cannot be expressed through the Mail dictionary.
    After ``reply``, the guard may adopt Mail's live front-window title when its
    subject core matches the derived reply subject (Mail normalizes duplicate
    Re:/Fwd: prefixes). The keystroke itself still requires exact title equality
    against that adopted ``replySubject``. An empty System Events title is
    tolerated (AX quirk); a different non-empty SE title aborts without typing.
    The same exact-title-or-empty check runs again before EVERY chunk (not just
    once before the loop), so a focus loss mid-typing aborts immediately instead
    of leaking chunks into whatever now holds focus; the abort discards the
    partially typed compose window (``close ... saving no``) so no partial draft
    is ever left behind. Requires Accessibility permission for the host process;
    callers that cannot grant it must stop and report the blocker; the
    ``native_format=False`` path is gated behind ``allow_windowless_fallback``.
    """
    extra_output_lines = _reply_extra_output_lines(
        safe_cc=safe_cc,
        safe_bcc=safe_bcc,
        safe_attachment_info=safe_attachment_info,
        has_cc=has_cc,
        has_bcc=has_bcc,
        has_attachments=has_attachments,
    )
    post_action = _native_reply_post_action(mode)
    subject_helpers = _native_reply_subject_helpers_applescript()
    typing_handler = build_chunked_typing_handler(
        chunk_size=TYPING_CHUNK_SIZE,
        inter_chunk_delay=TYPING_INTER_CHUNK_DELAY,
    )
    return f'''
{subject_helpers}
{typing_handler}
set bodyTempPath to "{body_temp_path}"
set derivedReplySubject to ""
set replySubject to ""
set replyMessage to missing value
set quotedNeedle to ""
set didType to false
set typingInterruptedDetail to ""
set guardMail to "(unset)"
set guardSE to "(unset)"

try
    tell application "Mail"
        set targetAccount to account "{safe_account}"
        {inbox_mailbox_script("inboxMailbox", "targetAccount")}
        {lookup_script}

        if foundMessage is missing value then
            {cleanup_script}
            return "{not_found_message}"
        end if

        set sourceSubject to subject of foundMessage as string
        if sourceSubject starts with "Re:" or sourceSubject starts with "RE:" or sourceSubject starts with "re:" then
            set derivedReplySubject to sourceSubject
        else
            set derivedReplySubject to "Re: " & sourceSubject
        end if
        set replySubject to derivedReplySubject
        set replyBodyText to do shell script "cat " & quoted form of bodyTempPath

        -- Native Mail reply: Mail builds its own rich quoted thread and inserts the
        -- account's default reply signature into the opened window. Content is never
        -- reassigned below, so that native formatting is preserved.
        set replyMessage to reply foundMessage {reply_options}
        delay 1.2
        activate
        delay 0.4

        -- Prefer Mail's own outgoing reply subject when it is the same reply thread.
        -- Mail collapses duplicate Re:/Fwd: prefixes, so the derived source subject
        -- can disagree with the compose title; adopt normalized titles only after a
        -- subject-core match so an unrelated open compose window is never adopted.
        try
            set replyMessageSubject to subject of replyMessage as string
            if replyMessageSubject is not "" then
                if my subjectCoresMatch(replyMessageSubject, derivedReplySubject) then
                    set replySubject to replyMessageSubject
                end if
            end if
        end try
        -- Prefer Mail's live compose-window title when it is the same reply thread.
        try
            set mailWindowTitle to name of front window as string
            if mailWindowTitle is not "" then
                if my subjectCoresMatch(mailWindowTitle, derivedReplySubject) then
                    set replySubject to mailWindowTitle
                end if
            end if
        end try

        -- Best-effort identity tweaks on the already-good native window.
        try
            {sender_script}
        end try
        try
            {signature_script}
        end try
        {cc_script}
        {bcc_script}
        {attachment_script}
    end tell

    -- Insert the reply body with a TYPED keystroke. Guard: Mail's dictionary front
    -- window must exactly equal the adopted replySubject (live title after core-
    -- matched adoption, else derived). An empty System Events title is tolerated
    -- (AX quirk); a different non-empty SE title aborts. Subject-core matching is
    -- only used to adopt Mail's normalized title, never as the keystroke boundary.
    if replyBodyText is not "" then
        repeat with guardAttempt from 1 to 4
            set guardMail to "(unset)"
            set guardSE to "(unset)"
            tell application "Mail"
                activate
            end tell
            delay 0.3
            tell application "System Events"
                tell process "Mail"
                    set frontmost to true
                    delay 0.3
                    try
                        perform action "AXRaise" of (first window whose name is replySubject)
                    end try
                    try
                        if derivedReplySubject is not replySubject then
                            perform action "AXRaise" of (first window whose name is derivedReplySubject)
                        end if
                    end try
                    delay 0.3
                    try
                        set guardSE to name of front window
                    end try
                end tell
            end tell
            tell application "Mail"
                try
                    set guardMail to name of front window
                end try
                -- Late adoption: if focus landed on Mail's normalized reply title,
                -- adopt it before the exact-title keystroke check.
                if guardMail is not "(unset)" and guardMail is not "" then
                    if my subjectCoresMatch(guardMail, derivedReplySubject) then
                        set replySubject to guardMail
                    end if
                end if
            end tell
            set mailOk to (guardMail is replySubject)
            set seOk to (guardSE is replySubject or guardSE is "" or guardSE is "(unset)")
            if mailOk and seOk then
                set typeChunksResult to my typeReplyBodyChunks(replyBodyText, replySubject, derivedReplySubject)
                if typeChunksResult is "typed" then
                    set didType to true
                else
                    set typingInterruptedDetail to typeChunksResult
                end if
                exit repeat
            end if
            delay 0.5
        end repeat

        if didType is false then
            -- Distinguish a mid-typing focus loss (chunked keystroke aborted
            -- partway through) from a pre-typing focus failure: true focus loss
            -- (Inbox / other app window) vs. a reply window whose title still
            -- does not core-match the expected subject.
            set abortDetailText to "could not focus reply window"
            set abortCode to "GUARD_ABORT"
            if typingInterruptedDetail is not "" then
                set abortCode to "TYPING_INTERRUPTED"
                set abortDetailText to typingInterruptedDetail
            else if guardMail is not "(unset)" and guardMail is not "" then
                if (my subjectCoresMatch(guardMail, derivedReplySubject)) is false then
                    if my looksLikeReplyWindowTitle(guardMail) then
                        set abortCode to "GUARD_ABORT_SUBJECT"
                    end if
                end if
            end if
            tell application "Mail"
                try
                    close (every window whose name is replySubject) saving no
                end try
                try
                    if derivedReplySubject is not replySubject then
                        close (every window whose name is derivedReplySubject) saving no
                    end if
                end try
                {cleanup_script}
            end tell
            return abortCode & return & "Subject: " & replySubject & return & "DerivedSubject: " & derivedReplySubject & return & "Detail: " & abortDetailText & " (mailFront=" & guardMail & " seFront=" & guardSE & ")"
        end if
        set quotedNeedle to "wrote:"
    end if

    delay 0.4
    tell application "Mail"
        {post_action}

        set outputText to "{header_text}" & return & return
        set outputText to outputText & "{success_text}" & return
        set outputText to outputText & "To: native reply recipients" & return
        set outputText to outputText & "Subject: " & replySubject & return
        if quotedNeedle is not "" then set outputText to outputText & "Quote Needle: " & quotedNeedle & return
        {extra_output_lines}

        {cleanup_script}

        return outputText
    end tell
on error errMsg
    try
        tell application "Mail"
            close (every window whose name is replySubject) saving no
        end tell
    end try
    try
        tell application "Mail"
            if derivedReplySubject is not "" and derivedReplySubject is not replySubject then
                close (every window whose name is derivedReplySubject) saving no
            end if
        end tell
    end try
    try
        {cleanup_script}
    end try
    return "Error: " & errMsg & return & "Please check that the account name is correct and the email exists."
end try
'''
