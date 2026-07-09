"""Pure AppleScript builders for ``list_inbox_emails`` (collection block, text, JSON)."""

from apple_mail_mcp.bounded_scan import (
    build_bounded_filtered_scan,
    build_bounded_message_scan,
)
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import (
    content_preview_script,
    escape_applescript,
    inbox_mailbox_script,
    sanitize_pipe_delimited_field,
)
from apple_mail_mcp.tools.inbox.parsing import _read_filter_condition


def _build_inbox_collection_block(max_emails: int, read_filter: str) -> str:
    """Build the AppleScript that sets ``inboxMessages`` to a bounded slice.

    For the filtered modes (``read_filter`` ∈ ``"read"`` / ``"unread"``)
    bind a bounded newest-first window via ``messages 1 thru N``, then
    iterate in AppleScript with an ``if`` predicate via
    ``build_bounded_filtered_scan``. The historical ``whose read status
    is false`` over the bound slice crashed on Gmail (the slice's message
    refs point at ``[Gmail]/All Mail``, which ``whose`` can't resolve as
    a list query) — the in-loop pattern is the only safe form.
    """
    condition = _read_filter_condition(read_filter)
    if condition is not None:
        scan_cap = min(
            max(max_emails * 10, SCAN_BOUNDS["INBOX_DEFAULT_CAP"] // 2),
            SCAN_BOUNDS["INBOX_MAX_CAP"],
        )
        # Hard ceiling: never bind more than INBOX_HARD_CEILING messages via
        # `messages 1 thru scan_cap`, regardless of how max_emails*10 scaled.
        scan_cap = min(scan_cap, SCAN_BOUNDS["INBOX_HARD_CEILING"])
        return build_bounded_filtered_scan(
            mailbox_var="inboxMailbox",
            scan_cap=scan_cap,
            target_max=max_emails,
            condition_expr=condition,
            output_var="inboxMessages",
        )
    scan_cap = min(max_emails, SCAN_BOUNDS["INBOX_HARD_CEILING"])
    bounded = build_bounded_message_scan("inboxMailbox", scan_cap)
    return f"{bounded}\n            set inboxMessages to candidateMessages"


def _build_list_inbox_text_script(
    account: str,
    max_emails: int,
    read_filter: str,
    include_content: bool,
    include_message_id: bool = False,
) -> str:
    """Build a text-format inbox script for one account.

    *read_filter* selects ``"all"`` (no filter), ``"unread"``, or
    ``"read"``. The filtered modes bind a bounded newest-first window
    (``scan_cap = min(max(max_emails*10, 100), 1000)``) and apply the
    predicate via an in-loop ``if`` (``build_bounded_filtered_scan``) —
    safe on Gmail and on 24K-message Exchange inboxes alike.
    """
    assert max_emails > 0, "caller must enforce bounded slice (max_emails > 0)"
    escaped_account = escape_applescript(account)
    message_id_text_block = ""
    if include_message_id:
        message_id_text_block = (
            'set internetMessageId to ""\n'
            "                        try\n"
            "                            set internetMessageId to message id of aMessage\n"
            "                        end try\n"
            '                        set outputText to outputText & "__MSG_ID__|||" & internetMessageId & return'
        )

    collection = _build_inbox_collection_block(max_emails, read_filter)

    return f"""
    tell application "Mail"
        set outputText to ""
        try
            set anAccount to account "{escaped_account}"
            set accountName to name of anAccount
            {inbox_mailbox_script("inboxMailbox", "anAccount")}
            {collection}
            set messageCount to count of inboxMessages

            if messageCount > 0 then
                set outputText to outputText & "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" & return
                set outputText to outputText & "📧 ACCOUNT: " & accountName & " (" & messageCount & " messages)" & return
                set outputText to outputText & "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" & return & return

                set currentIndex to 0
                set sentCount to 0
                repeat with aMessage in inboxMessages
                    set currentIndex to currentIndex + 1
                    if currentIndex > {max_emails} then exit repeat

                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage
                        set messageRead to read status of aMessage
                        -- Strip ||| and embedded newlines from subject/sender so
                        -- text-format parser markers (__MSG_ID__|||, __COUNT__|||)
                        -- and the replied-detection line walk stay aligned.
                        {sanitize_pipe_delimited_field("messageSubject")}
                        {sanitize_pipe_delimited_field("messageSender")}
                        {message_id_text_block}

                        if messageRead then
                            set readIndicator to "✓"
                        else
                            set readIndicator to "✉"
                        end if

                        set outputText to outputText & readIndicator & " " & messageSubject & return
                        set outputText to outputText & "   From: " & messageSender & return
                        set outputText to outputText & "   Date: " & (messageDate as string) & return

                        {content_preview_script(200) if include_content else ""}

                        set outputText to outputText & return
                        set sentCount to sentCount + 1
                    end try
                end repeat
                set outputText to outputText & "__COUNT__|||" & sentCount & return
            end if
        on error errMsg
            set outputText to outputText & "⚠ Error accessing inbox for account {escaped_account}" & return & "   " & errMsg & return & return
        end try

        return outputText
    end tell
    """


def _build_list_inbox_json_script(
    account: str,
    max_emails: int,
    read_filter: str,
    include_content: bool = False,
    include_message_id: bool = False,
) -> str:
    """Build a JSON-format inbox script for one account.

    Each emitted line always includes the integer Mail.app ``id`` of the
    message (field index 5, exposed as ``"message_id"`` by the parser) so
    callers can pass it directly to ``get_email_by_id``.

    When *include_message_id* is True, an extra
    ``|||<internet-message-id>`` field (RFC 2822 Message-ID) is appended
    after the integer id for replied-detection.

    *read_filter* selects ``"all"`` / ``"read"`` / ``"unread"``; the
    filtered modes use the in-loop ``if`` pattern from
    ``build_bounded_filtered_scan`` (safe on Gmail and large Exchange).
    """
    assert max_emails > 0, "caller must enforce bounded slice (max_emails > 0)"
    escaped_account = escape_applescript(account)

    collection = _build_inbox_collection_block(max_emails, read_filter)

    if include_content:
        content_field = (
            'set contentPreview to ""\n'
            "                    try\n"
            "                        set msgContent to content of aMessage\n"
            "                        set AppleScript's text item delimiters to {return, linefeed, tab}\n"
            "                        set contentParts to text items of msgContent\n"
            '                        set AppleScript\'s text item delimiters to " "\n'
            "                        set contentPreview to contentParts as string\n"
            '                        set AppleScript\'s text item delimiters to ""\n'
            "                        if length of contentPreview > 200 then\n"
            "                            set contentPreview to text 1 thru 200 of contentPreview\n"
            "                        end if\n"
            "                    end try"
        )
        content_suffix = ' & "|||" & contentPreview'
    else:
        content_field = ""
        content_suffix = ""

    if include_message_id:
        message_id_field = (
            'set internetMessageId to ""\n'
            "                    try\n"
            "                        set internetMessageId to message id of aMessage\n"
            "                    end try"
        )
        message_id_suffix = ' & "|||" & internetMessageId'
    else:
        message_id_field = ""
        message_id_suffix = ""

    return f"""
    tell application "Mail"
        set resultLines to {{}}
        try
            set anAccount to account "{escaped_account}"
            set accountName to name of anAccount
            {inbox_mailbox_script("inboxMailbox", "anAccount")}
            {collection}
            set currentIndex to 0
            repeat with aMessage in inboxMessages
                set currentIndex to currentIndex + 1
                if currentIndex > {max_emails} then exit repeat
                try
                    set messageSubject to subject of aMessage
                    set messageSender to sender of aMessage
                    set messageDate to date received of aMessage
                    set messageRead to read status of aMessage
                    -- Wrap id read in its own try so a transient failure during
                    -- sync doesn't drop the entire row (matches search_emails
                    -- sanitize_field fallback behaviour).
                    set mailAppId to ""
                    try
                        set mailAppId to id of aMessage
                    end try
                    -- Strip ||| and embedded newlines from subject/sender so the
                    -- Python parser can't be confused into mapping the wrong
                    -- message_id onto an email (would lose data on delete).
                    {sanitize_pipe_delimited_field("messageSubject")}
                    {sanitize_pipe_delimited_field("messageSender")}
                    {content_field}
                    {message_id_field}
                    set end of resultLines to messageSubject & "|||" & messageSender & "|||" & (messageDate as string) & "|||" & messageRead & "|||" & accountName & "|||" & mailAppId{message_id_suffix}{content_suffix}
                end try
            end repeat
        end try
        set AppleScript's text item delimiters to linefeed
        return resultLines as string
    end tell
    """
