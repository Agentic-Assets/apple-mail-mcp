"""``get_statistics`` tool: account overview, sender, and mailbox-breakdown scopes."""

import json
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.constants import SKIP_FOLDERS
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inject_preferences,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import analytics
from apple_mail_mcp.tools.analytics.statistics_parsing import (
    _STATISTICS_ERROR_PREFIX,
    _build_account_overview_report,
    _format_statistics_json,
    _parse_statistics_errors,
    _parse_statistics_text,
    _statistics_json_error,
    _statistics_scan_caps,
)


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def get_statistics(
    account: str | None = None,
    scope: str = "account_overview",
    sender: str | None = None,
    mailbox: str | None = None,
    days_back: int = 30,
    output_format: str = "text",
    timeout: int | None = None,
) -> str | dict[str, Any]:
    """
    Get comprehensive email statistics and analytics.

    For ``account_overview`` and ``sender_stats``, scans a bounded slice of
    mailboxes and newest messages (``days_back <= 7``: 10 mailboxes; longer
    windows: 20 mailboxes; each capped at 50 messages) so AppleScript wall
    time stays predictable on Exchange / Gmail accounts with deep history.
    ``mailbox_breakdown`` is bounded by Mail.app's own count APIs and is not
    capped.

    ``account_overview`` reports **mailbox-wide** ``total_emails``/``unread``/
    ``read`` counts via Mail.app's ``count of messages`` / ``unread count of
    aMailbox`` APIs (not date-windowed). The ``days_back`` window still bounds
    the per-message sample used for ``top_senders``, ``flagged``,
    ``with_attachments``, and ``mailbox_distribution`` — those remain
    sample-based because Mail.app exposes no mailbox-wide count for them.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Falls back to
            ``DEFAULT_MAIL_ACCOUNT`` when None.
        scope: Analysis scope: "account_overview", "sender_stats", "mailbox_breakdown"
        sender: Specific sender for "sender_stats" scope
        mailbox: Specific mailbox for "mailbox_breakdown" scope
        days_back: Number of days to analyze (default: 30). ``0`` (all time)
            is no longer accepted; pass ``days_back=7`` or ``30``. Full-mailbox
            scans are disabled, so narrow the window instead.
        output_format: ``text`` (default, human-readable) or ``json`` (structured dict).
        timeout: Optional AppleScript timeout in seconds. Defaults to 120s.

    Returns:
        Formatted statistics report with metrics and insights, or a structured
        dict when ``output_format="json"``.
    """

    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    if days_back <= 0:
        return json.dumps(
            ToolError(
                code="UNBOUNDED_SCAN_REQUIRED",
                message=("get_statistics refuses to scan without days_back; pass days_back=7 or 30"),
                remediation={
                    "preferred": "Pass days_back=7 or 30",
                    "note": "Full-mailbox scans are disabled; bound this call.",
                },
            ).to_dict(),
            indent=2,
        )

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        if output_format == "json":
            return _statistics_json_error(
                "account_required",
                days_back=days_back,
                scope=scope,
                message="account is required (no DEFAULT_MAIL_ACCOUNT configured)",
            )
        return "Error: 'account' is required (no DEFAULT_MAIL_ACCOUNT configured)"

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = analytics.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        if output_format == "json":
            return _statistics_json_error(
                "account_not_found",
                account=account,
                days_back=days_back,
                scope=scope,
            )
        return account_err

    # Escape user inputs for AppleScript
    escaped_account = escape_applescript(account)
    escaped_sender = escape_applescript(sender) if sender else None
    escaped_mailbox = escape_applescript(mailbox) if mailbox else None

    max_mailboxes, max_messages_per_mailbox = _statistics_scan_caps(days_back)

    # Calculate date threshold if days_back > 0
    date_filter = ""
    if days_back > 0:
        date_filter = f"""
            set targetDate to (current date) - ({days_back} * days)
        """

    # Build skip folders condition from constants
    skip_folder_checks = " and ".join(f'mailboxName is not "{f}"' for f in SKIP_FOLDERS)

    if scope == "account_overview":
        # Emit structured rows; aggregate senders with Python Counter (O(N) vs
        # the former O(N×senders) in-AppleScript list scan).
        script = f'''
        tell application "Mail"
            set outputLines to {{}}

            {date_filter}

            try
                set targetAccount to account "{escaped_account}"
                set allMailboxes to every mailbox of targetAccount
                -- Always include INBOX first so it isn't sliced out on Exchange
                -- accounts where alphabetical ordering pushes it past the cap.
                -- Try several common name forms before falling back to a
                -- case-insensitive scan (handles "Inbox", localized names, etc.).
                set inboxMailboxRef to missing value
                try
                    set inboxMailboxRef to mailbox "INBOX" of targetAccount
                end try
                if inboxMailboxRef is missing value then
                    try
                        set inboxMailboxRef to mailbox "Inbox" of targetAccount
                    end try
                end if
                if inboxMailboxRef is missing value then
                    try
                        set inboxMailboxRef to mailbox "inbox" of targetAccount
                    end try
                end if
                if inboxMailboxRef is missing value then
                    repeat with mb in allMailboxes
                        set mbNameLower to name of mb
                        ignoring case
                            if mbNameLower is "inbox" then
                                set inboxMailboxRef to mb
                                exit repeat
                            end if
                        end ignoring
                    end repeat
                end if
                if (count of allMailboxes) > {max_mailboxes} then
                    set allMailboxes to items 1 thru {max_mailboxes} of allMailboxes
                end if
                if inboxMailboxRef is not missing value then
                    set hasInbox to false
                    set inboxRefName to name of inboxMailboxRef
                    repeat with mb in allMailboxes
                        ignoring case
                            if name of mb is inboxRefName then
                                set hasInbox to true
                                exit repeat
                            end if
                        end ignoring
                    end repeat
                    if not hasInbox then
                        set allMailboxes to {{inboxMailboxRef}} & allMailboxes
                    end if
                end if

                -- Analyze all mailboxes. For each mailbox emit:
                --   MBOX|||name|||totalCount|||unreadCount   (mailbox-wide via Mail's count APIs)
                --   ROW|||name|||flagged|||hasAttach|||sender   (per message in the bounded sample)
                -- Python aggregates senders with Counter (O(N)) and derives
                -- true totals from the MBOX rows; ROW lines drive the sample-
                -- based flagged/attachment/sender/mailbox-distribution stats.
                repeat with aMailbox in allMailboxes
                    try
                        set mailboxName to name of aMailbox

                        -- Skip system folders
                        if {skip_folder_checks} then

                            -- Mailbox-wide totals via Mail's count APIs. No
                            -- per-message work — cheap even on 24K-message
                            -- Exchange mailboxes.
                            set mailboxMessageCount to count of messages of aMailbox
                            set mailboxUnreadCount to 0
                            try
                                set mailboxUnreadCount to unread count of aMailbox
                            end try
                            set end of outputLines to "MBOX|||" & mailboxName & "|||" & mailboxMessageCount & "|||" & mailboxUnreadCount

                            -- Bind a bounded newest-first slice for the sample
                            -- scan. Avoid broad `every message ... whose date
                            -- ...` filters: Mail.app may materialize remote
                            -- mailboxes before filtering and trigger large
                            -- downloads.
                            set mailboxMessages to {{}}
                            if mailboxMessageCount > {max_messages_per_mailbox} then
                                set mailboxUpperBound to {max_messages_per_mailbox}
                            else
                                set mailboxUpperBound to mailboxMessageCount
                            end if
                            if mailboxUpperBound > 0 then
                                set mailboxMessages to messages 1 thru mailboxUpperBound of aMailbox
                            end if

                            set mboxErrorCount to 0
                            repeat with aMessage in mailboxMessages
                                try
                                    if {days_back} > 0 then
                                        set messageDate to date received of aMessage
                                        if messageDate < targetDate then exit repeat
                                    end if

                                    set isFlagged to false
                                    try
                                        set isFlagged to flagged status of aMessage
                                    end try
                                    set attachCount to count of mail attachments of aMessage
                                    set messageSender to sender of aMessage

                                    -- ROW|||mailbox|||flagged|||hasAttach|||sender
                                    if isFlagged then
                                        set flagStr to "1"
                                    else
                                        set flagStr to "0"
                                    end if
                                    if attachCount > 0 then
                                        set attachStr to "1"
                                    else
                                        set attachStr to "0"
                                    end if
                                    set end of outputLines to "ROW|||" & mailboxName & "|||" & flagStr & "|||" & attachStr & "|||" & messageSender
                                on error
                                    -- Single-message read failure: count and
                                    -- continue so a poisoned message doesn't
                                    -- silently shrink the sample.
                                    set mboxErrorCount to mboxErrorCount + 1
                                end try
                            end repeat

                            if mboxErrorCount > 0 then
                                set end of outputLines to "{_STATISTICS_ERROR_PREFIX}" & mailboxName & "|||" & mboxErrorCount & " message(s) skipped due to read errors"
                            end if

                        end if
                    on error errMsg
                        -- Surface per-mailbox failures instead of silently losing coverage.
                        try
                            set end of outputLines to "{_STATISTICS_ERROR_PREFIX}" & mailboxName & "|||" & errMsg
                        on error
                            set end of outputLines to "{_STATISTICS_ERROR_PREFIX}unknown mailbox|||unknown error"
                        end try
                    end try
                end repeat

            on error errMsg
                return "Error: " & errMsg
            end try

            set AppleScript's text item delimiters to linefeed
            set rawOut to outputLines as string
            set AppleScript's text item delimiters to ""
            return rawOut
        end tell
        '''

        # Parse emitted rows in Python and format output
        try:
            raw_overview = analytics.run_applescript(script, timeout=timeout if timeout is not None else 120)
        except AppleScriptTimeout:
            timeout_msg = f"Error: AppleScript timed out while computing statistics for '{account}'"
            if output_format == "json":
                return _statistics_json_error(
                    "timeout",
                    account=account,
                    days_back=days_back,
                    scope=scope,
                    message=timeout_msg,
                )
            return timeout_msg

        if raw_overview.startswith("Error:"):
            if output_format == "json":
                return _statistics_json_error(
                    "applescript_error",
                    account=account,
                    days_back=days_back,
                    scope=scope,
                    message=raw_overview,
                )
            return raw_overview

        result = _build_account_overview_report(raw_overview, escaped_account)

        if output_format == "json":
            statistics = _parse_statistics_text(scope, result)
            return _format_statistics_json(
                scope=scope,
                account=account,
                days_back=days_back,
                statistics=statistics,
                sender=sender,
                mailbox=mailbox,
                errors=_parse_statistics_errors(result),
            )
        return result

    if scope == "sender_stats":
        if not sender:
            if output_format == "json":
                return _statistics_json_error(
                    "sender_required",
                    account=account,
                    days_back=days_back,
                    scope=scope,
                    message="'sender' parameter required for sender_stats scope",
                )
            return "Error: 'sender' parameter required for sender_stats scope"

        script = f'''
        tell application "Mail"
            set outputText to "SENDER STATISTICS" & return & return
            set outputText to outputText & "Sender: {escaped_sender}" & return
            set outputText to outputText & "Account: {escaped_account}" & return & return

            {date_filter}

            try
                set targetAccount to account "{escaped_account}"
                set allMailboxes to every mailbox of targetAccount
                -- Cap mailbox scan to the first {max_mailboxes} mailboxes
                if (count of allMailboxes) > {max_mailboxes} then
                    set allMailboxes to items 1 thru {max_mailboxes} of allMailboxes
                end if

                set totalFromSender to 0
                set unreadFromSender to 0
                set withAttachments to 0
                set scanErrors to {{}}

                repeat with aMailbox in allMailboxes
                    try
                        set mailboxName to name of aMailbox

                        -- Skip system folders
                        if {skip_folder_checks} then

                            set matchedMessages to {{}}
                            set mailboxMessageCount to count of messages of aMailbox
                            if mailboxMessageCount > {max_messages_per_mailbox} then
                                set mailboxUpperBound to {max_messages_per_mailbox}
                            else
                                set mailboxUpperBound to mailboxMessageCount
                            end if
                            if mailboxUpperBound > 0 then
                                set matchedMessages to messages 1 thru mailboxUpperBound of aMailbox
                            end if

                            repeat with aMessage in matchedMessages
                                try
                                    if {days_back} > 0 then
                                        set messageDate to date received of aMessage
                                        if messageDate < targetDate then exit repeat
                                    end if

                                    set messageSender to sender of aMessage
                                    set senderMatches to false
                                    ignoring case
                                        if messageSender contains "{escaped_sender}" then set senderMatches to true
                                    end ignoring

                                    if senderMatches then
                                        set totalFromSender to totalFromSender + 1

                                        if not (read status of aMessage) then
                                            set unreadFromSender to unreadFromSender + 1
                                        end if

                                        if (count of mail attachments of aMessage) > 0 then
                                            set withAttachments to withAttachments + 1
                                        end if
                                    end if
                                end try
                            end repeat

                        end if
                    on error errMsg
                        -- Surface per-mailbox failures instead of silently losing coverage.
                        try
                            set end of scanErrors to "{_STATISTICS_ERROR_PREFIX}" & mailboxName & "|||" & errMsg
                        on error
                            set end of scanErrors to "{_STATISTICS_ERROR_PREFIX}unknown mailbox|||" & errMsg
                        end try
                    end try
                end repeat

                set outputText to outputText & "Total emails: " & totalFromSender & return
                set outputText to outputText & "Unread: " & unreadFromSender & return
                set outputText to outputText & "With attachments: " & withAttachments & return
                if (count of scanErrors) > 0 then
                    set outputText to outputText & return & "MAILBOX SCAN ERRORS" & return
                    repeat with scanError in scanErrors
                        set outputText to outputText & scanError & return
                    end repeat
                end if

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif scope == "mailbox_breakdown":
        mailbox_param = escaped_mailbox if mailbox else "INBOX"

        script = f'''
        tell application "Mail"
            set outputText to "MAILBOX STATISTICS" & return & return
            set outputText to outputText & "Mailbox: {mailbox_param}" & return
            set outputText to outputText & "Account: {escaped_account}" & return & return

            try
                set targetAccount to account "{escaped_account}"
                try
                    set targetMailbox to mailbox "{mailbox_param}" of targetAccount
                on error
                    if "{mailbox_param}" is "INBOX" then
                        set targetMailbox to mailbox "Inbox" of targetAccount
                    else
                        error "Mailbox not found"
                    end if
                end try

                -- Use Mail's own count APIs to avoid materializing the full
                -- message list on large mailboxes.
                set totalMessages to count of messages of targetMailbox
                set unreadMessages to unread count of targetMailbox

                set outputText to outputText & "Total messages: " & totalMessages & return
                set outputText to outputText & "Unread: " & unreadMessages & return
                set outputText to outputText & "Read: " & (totalMessages - unreadMessages) & return

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    else:
        if output_format == "json":
            return _statistics_json_error(
                "invalid_scope",
                account=account,
                days_back=days_back,
                scope=scope,
                message=(f"Invalid scope '{scope}'. Use: account_overview, sender_stats, mailbox_breakdown"),
            )
        return f"Error: Invalid scope '{scope}'. Use: account_overview, sender_stats, mailbox_breakdown"

    try:
        result = analytics.run_applescript(script, timeout=timeout if timeout is not None else 120)
    except AppleScriptTimeout:
        timeout_msg = f"Error: AppleScript timed out while computing statistics for '{account}'"
        if output_format == "json":
            return _statistics_json_error(
                "timeout",
                account=account,
                days_back=days_back,
                scope=scope,
                message=timeout_msg,
            )
        return timeout_msg

    if output_format == "json":
        if result.startswith("Error:"):
            return _statistics_json_error(
                "applescript_error",
                account=account,
                days_back=days_back,
                scope=scope,
                message=result,
            )
        statistics = _parse_statistics_text(scope, result)
        return _format_statistics_json(
            scope=scope,
            account=account,
            days_back=days_back,
            statistics=statistics,
            sender=sender,
            mailbox=mailbox,
            errors=_parse_statistics_errors(result),
        )

    return result
