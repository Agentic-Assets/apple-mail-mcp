"""Pure reply AppleScript builders (object-model and native-window paths).

These compose f-string scripts only; ``reply_to_email`` in ``compose.py`` runs
them. Keeping them here removes the largest AppleScript blocks from the tool
module without touching any I/O or patched name.
"""

from dataclasses import dataclass

from apple_mail_mcp.core import inbox_mailbox_script
from apple_mail_mcp.tools.compose.lookup_scripts import _compose_signature_script


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
        "        end try"
    )


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
    TYPED System Events keystroke (never the clipboard, which clobbered the
    pasteboard and leaked bodies into the wrong thread).

    UI scripting is isolated to the focus guard + keystroke and is unavoidable
    here: the native rich format cannot be expressed through the Mail dictionary.
    The guard treats Mail's dictionary front-window name as authoritative and
    tolerates an empty System Events title (an AX quirk for compose windows); a
    different non-empty SE title aborts without typing so a partial/wrong-thread
    draft is never saved. Requires Accessibility permission for the host process;
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
    return f'''
set bodyTempPath to "{body_temp_path}"
set replySubject to ""
set replyMessage to missing value
set quotedNeedle to ""
set didType to false
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
            set replySubject to sourceSubject
        else
            set replySubject to "Re: " & sourceSubject
        end if
        set replyBodyText to do shell script "cat " & quoted form of bodyTempPath

        -- Native Mail reply: Mail builds its own rich quoted thread and inserts the
        -- account's default reply signature into the opened window. Content is never
        -- reassigned below, so that native formatting is preserved.
        set replyMessage to reply foundMessage {reply_options}
        delay 1.2
        activate
        delay 0.4

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
    -- window must be the reply; an empty System Events title is tolerated (AX quirk);
    -- a different non-empty SE title aborts. Retry to ride out transient focus.
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
            end tell
            if (guardMail is replySubject) and (guardSE is replySubject or guardSE is "" or guardSE is "(unset)") then
                tell application "System Events"
                    tell process "Mail"
                        keystroke replyBodyText
                    end tell
                end tell
                set didType to true
                exit repeat
            end if
            delay 0.5
        end repeat

        if didType is false then
            tell application "Mail"
                try
                    close (every window whose name is replySubject) saving no
                end try
                {cleanup_script}
            end tell
            return "GUARD_ABORT: could not focus reply window (mailFront=" & guardMail & " seFront=" & guardSE & ")"
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
        {cleanup_script}
    end try
    return "Error: " & errMsg & return & "Please check that the account name is correct and the email exists."
end try
'''
