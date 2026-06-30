"""Analytics tools: attachments, statistics, exports, and dashboard."""

import asyncio
import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.applescript_snippets import sanitize_field_handler
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, WRITE_TOOL_ANNOTATIONS, mcp

logger = logging.getLogger(__name__)

from apple_mail_mcp.backend.base import ToolError, target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS, build_whose_id_list, iter_id_chunks
from apple_mail_mcp.constants import SCAN_BOUNDS, SKIP_FOLDERS
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
    list_mail_account_names,
    normalize_message_ids,
    run_applescript,
    validate_account_name,
    validate_save_path,
)
from apple_mail_mcp.tools.search import _search_mail_records_sync as _search_mail_records


def _parse_attachment_listing_rows(text: str) -> list[dict[str, Any]]:
    """Parse JSON-mode attachment rows emitted by AppleScript."""
    items: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("|||")
        if len(parts) != 7:
            continue
        message_id, subject, sender, received_date, attachment_index, filename, size_text = parts
        try:
            index_value = int(attachment_index)
        except ValueError:
            continue
        try:
            size_bytes: int | None = int(size_text)
        except ValueError:
            size_bytes = None
        items.append(
            {
                "message_id": message_id,
                "subject": subject,
                "sender": sender,
                "received_date": received_date,
                "attachment_index": index_value,
                "filename": filename,
                "size_bytes": size_bytes,
            }
        )
    return items


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def list_email_attachments(
    account: str | None = None,
    subject_keyword: str = "",
    message_ids: list[str] | None = None,
    max_results: int = 50,
    timeout: int | None = None,
    output_format: str = "text",
) -> str:
    """
    List attachments for exact message ids.

    ``subject_keyword`` is a deprecated selector retained for v3.x schema
    compatibility. Use ``search_emails(..., has_attachments=True)`` to discover
    candidate ids, then pass ``message_ids``. JSON mode returns attachment
    metadata with ``message_id`` and per-message ``attachment_index`` so
    ``save_email_attachment`` can select deterministically.

    Args:
        account: Account name (e.g., "Gmail", "Work", "Personal"). Falls back
            to ``DEFAULT_MAIL_ACCOUNT`` when None.
        subject_keyword: Keyword to search for in email subjects (omit when
            message_ids is set)
        message_ids: Optional list of exact Mail message ids for precise targeting
        max_results: Maximum number of messages to inspect from the inbox
            (default: 50). The AppleScript only enumerates this many messages.
        timeout: Optional AppleScript timeout in seconds. Defaults to the
            ``run_applescript`` baseline (120s).
        output_format: ``"text"`` (default) or ``"json"``.

    Returns:
        List of attachments with names, sizes, and exact selectors
    """

    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: 'account' is required (no DEFAULT_MAIL_ACCOUNT configured)"

    if message_ids is None and not subject_keyword:
        return "Error: subject_keyword or message_ids is required"
    if message_ids is None and subject_keyword:
        return target_selector_deprecated_error(
            "list_email_attachments",
            ("subject_keyword",),
            preferred="Call search_emails(..., has_attachments=True) first, then pass message_ids=[...].",
            discovery="search_emails(subject_keyword=..., has_attachments=True, recent_days=..., limit=...)",
            exact_selector="message_ids",
        )

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    # Escape for AppleScript
    escaped_keyword = escape_applescript(subject_keyword)
    escaped_account = escape_applescript(account)
    sanitize_script = sanitize_field_handler()

    header_label = subject_keyword
    use_id_lookup = False
    id_filter_script = ""

    if message_ids is not None:
        normalized_ids = normalize_message_ids(message_ids)
        if not normalized_ids:
            return "Error: 'message_ids' must contain one or more numeric Mail ids"
        if len(normalized_ids) > MAX_WHOSE_IDS:
            chunk_outputs = [
                list_email_attachments(
                    account=account,
                    message_ids=chunk,
                    max_results=max_results,
                    timeout=timeout,
                    output_format=output_format,
                )
                for chunk in iter_id_chunks(normalized_ids)
            ]
            if output_format == "json":
                items: list[dict[str, Any]] = []
                for chunk_output in chunk_outputs:
                    try:
                        payload = json.loads(chunk_output)
                    except json.JSONDecodeError:
                        return chunk_output
                    if isinstance(payload, dict) and payload.get("code"):
                        return chunk_output
                    items.extend(payload.get("items", []))
                return json.dumps(
                    {
                        "items": items,
                        "returned": len(items),
                        "message_ids": normalized_ids,
                        "selector": "message_ids",
                        "chunk_size": MAX_WHOSE_IDS,
                    },
                    indent=2,
                )
            return "\n\n".join(chunk_outputs)
        id_condition = build_whose_id_list(normalized_ids)
        use_id_lookup = True
        header_label = f"message_ids: {', '.join(normalized_ids)}"
        id_filter_script = f"set inboxMessages to every message of inboxMailbox whose {id_condition}"
    else:
        # Fast no-hit path: use the optimized search helper first so no-match
        # attachment checks don't scan the inbox with a Python-side loop.
        try:
            preflight_records = _search_mail_records(
                account=account,
                mailbox="INBOX",
                subject_terms=[subject_keyword],
                has_attachments=True,
                include_content=False,
                offset=0,
                limit=max_results,
                timeout=timeout,
            )
        except AppleScriptTimeout:
            return f"Error: AppleScript timed out while listing attachments for '{account}'"
        if not preflight_records:
            return (
                f"ATTACHMENTS FOR: {subject_keyword}\n\n"
                "========================================\n"
                "FOUND: 0 matching email(s)\n"
                "========================================"
            )

    escaped_header = escape_applescript(header_label)
    message_lookup_script = (
        id_filter_script
        if use_id_lookup
        else f"""
            if (count of messages of inboxMailbox) > {max_results} then
                set inboxMessages to messages 1 thru {max_results} of inboxMailbox
            else
                set inboxMessages to messages of inboxMailbox
            end if"""
    )
    subject_match_block = (
        ""
        if use_id_lookup
        else f"""
                    -- Check if subject contains keyword
                    if messageSubject contains "{escaped_keyword}" then"""
    )
    subject_match_close = "" if use_id_lookup else "                    end if"

    if output_format == "json":
        script = f'''
    {sanitize_script}
    tell application "Mail"
        set outputText to ""

        try
            set targetAccount to account "{escaped_account}"
            {inbox_mailbox_script("inboxMailbox", "targetAccount")}
            {message_lookup_script}

            repeat with aMessage in inboxMessages
                if resultCount >= {max_results} then exit repeat

                try
                    set messageId to id of aMessage as string
                    set messageSubject to subject of aMessage
                    set messageSender to sender of aMessage
                    set messageDate to date received of aMessage
{subject_match_block}
                        set msgAttachments to mail attachments of aMessage
                        set attachmentCount to count of msgAttachments

                        repeat with attachmentIndex from 1 to attachmentCount
                            set anAttachment to item attachmentIndex of msgAttachments
                            set attachmentName to name of anAttachment
                            set attachmentSizeText to ""
                            try
                                set attachmentSizeText to (file size of anAttachment as integer) as string
                            end try

                            set outputText to outputText & messageId & "|||" & my sanitize_field(messageSubject) & "|||" & my sanitize_field(messageSender) & "|||" & my sanitize_field(messageDate) & "|||" & (attachmentIndex as string) & "|||" & my sanitize_field(attachmentName) & "|||" & attachmentSizeText & return
                        end repeat

                        set resultCount to resultCount + 1
{subject_match_close}
                end try
            end repeat

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''
    else:
        script = f'''
    tell application "Mail"
        set outputText to "ATTACHMENTS FOR: {escaped_header}" & return & return
        set resultCount to 0

        try
            set targetAccount to account "{escaped_account}"
            {inbox_mailbox_script("inboxMailbox", "targetAccount")}
            {message_lookup_script}

            repeat with aMessage in inboxMessages
                if resultCount >= {max_results} then exit repeat

                try
                    set messageSubject to subject of aMessage
{subject_match_block}
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage

                        set outputText to outputText & "✉ " & messageSubject & return
                        set outputText to outputText & "   From: " & messageSender & return
                        set outputText to outputText & "   Date: " & (messageDate as string) & return & return

                        -- Get attachments
                        set msgAttachments to mail attachments of aMessage
                        set attachmentCount to count of msgAttachments

                        if attachmentCount > 0 then
                            set outputText to outputText & "   Attachments (" & attachmentCount & "):" & return

                            repeat with anAttachment in msgAttachments
                                set attachmentName to name of anAttachment
                                try
                                    set attachmentSize to file size of anAttachment
                                    set sizeInKB to (attachmentSize / 1024) as integer
                                    set outputText to outputText & "   📎 " & attachmentName & " (" & sizeInKB & " KB)" & return
                                on error
                                    set outputText to outputText & "   📎 " & attachmentName & return
                                end try
                            end repeat
                        else
                            set outputText to outputText & "   No attachments" & return
                        end if

                        set outputText to outputText & return
                        set resultCount to resultCount + 1
{subject_match_close}
                end try
            end repeat

            set outputText to outputText & "========================================" & return
            set outputText to outputText & "FOUND: " & resultCount & " matching email(s)" & return
            set outputText to outputText & "========================================" & return

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''

    try:
        result = run_applescript(script, timeout=timeout if timeout is not None else 120)
    except AppleScriptTimeout:
        return f"Error: AppleScript timed out while listing attachments for '{account}'"
    if output_format == "json":
        if result.startswith("Error:"):
            return result
        items = _parse_attachment_listing_rows(result)
        return json.dumps(
            {
                "items": items,
                "returned": len(items),
                "message_ids": normalize_message_ids(message_ids or []),
                "selector": "message_ids",
            },
            indent=2,
        )
    return result


def _statistics_recent_days_applied(days_back: int, scope: str) -> float:
    if scope == "mailbox_breakdown":
        return 0.0
    return float(days_back) if days_back > 0 else 0.0


def _parse_account_overview_statistics(text: str) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "total_emails": 0,
        "unread": 0,
        "read": 0,
        "flagged": 0,
        "with_attachments": 0,
        "top_senders": [],
        "mailbox_distribution": [],
    }

    total_match = re.search(r"Total Emails: (\d+)", text)
    if total_match:
        stats["total_emails"] = int(total_match.group(1))

    unread_match = re.search(r"Unread: (\d+)(?: \((\d+)%\))?", text)
    if unread_match:
        stats["unread"] = int(unread_match.group(1))
        if unread_match.group(2) is not None:
            stats["unread_percent"] = int(unread_match.group(2))

    read_match = re.search(r"Read: (\d+)(?: \((\d+)%\))?", text)
    if read_match:
        stats["read"] = int(read_match.group(1))
        if read_match.group(2) is not None:
            stats["read_percent"] = int(read_match.group(2))

    flagged_match = re.search(r"Flagged: (\d+)", text)
    if flagged_match:
        stats["flagged"] = int(flagged_match.group(1))

    attachments_match = re.search(r"With Attachments: (\d+)(?: \((\d+)%\))?", text)
    if attachments_match:
        stats["with_attachments"] = int(attachments_match.group(1))
        if attachments_match.group(2) is not None:
            stats["with_attachments_percent"] = int(attachments_match.group(2))

    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in {"👥 TOP SENDERS", "👥 SAMPLE SENDERS"}:
            section = "senders"
            continue
        if stripped == "📁 MAILBOX DISTRIBUTION":
            section = "mailboxes"
            continue
        if section == "senders" and stripped.endswith(" emails"):
            sender_match = re.match(r"(.+): (\d+) emails$", stripped)
            if sender_match:
                stats["top_senders"].append(
                    {
                        "sender": sender_match.group(1),
                        "count": int(sender_match.group(2)),
                    }
                )
        elif section == "mailboxes" and ":" in stripped and not stripped.startswith("━"):
            mailbox_match = re.match(r"(.+): (\d+)(?: \((\d+)%\))?$", stripped)
            if mailbox_match:
                entry = {
                    "mailbox": mailbox_match.group(1),
                    "count": int(mailbox_match.group(2)),
                }
                if mailbox_match.group(3) is not None:
                    entry["percent"] = int(mailbox_match.group(3))
                stats["mailbox_distribution"].append(entry)

    return stats


def _parse_sender_stats_statistics(text: str) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for key, pattern in (
        ("total_emails", r"Total emails: (\d+)"),
        ("unread", r"Unread: (\d+)"),
        ("with_attachments", r"With attachments: (\d+)"),
    ):
        match = re.search(pattern, text)
        if match:
            stats[key] = int(match.group(1))
    return stats


def _parse_mailbox_breakdown_statistics(text: str) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for key, pattern in (
        ("total_messages", r"Total messages: (\d+)"),
        ("unread", r"Unread: (\d+)"),
        ("read", r"Read: (\d+)"),
    ):
        match = re.search(pattern, text)
        if match:
            stats[key] = int(match.group(1))
    return stats


_STATISTICS_ERROR_PREFIX = "__APPLE_MAIL_MCP_ERROR__|||"


def _parse_statistics_errors(text: str) -> list[str]:
    errors: list[str] = []
    for line in text.splitlines():
        if not line.startswith(_STATISTICS_ERROR_PREFIX):
            continue
        parts = line.split("|||", 2)
        if len(parts) == 3:
            mailbox = parts[1].strip() or "unknown mailbox"
            message = parts[2].strip() or "unknown error"
            errors.append(f"{mailbox}: {message}")
    return errors


def _strip_statistics_error_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith(_STATISTICS_ERROR_PREFIX))


def _parse_statistics_text(scope: str, text: str) -> dict[str, Any]:
    text = _strip_statistics_error_lines(text)
    if scope == "account_overview":
        return _parse_account_overview_statistics(text)
    if scope == "sender_stats":
        return _parse_sender_stats_statistics(text)
    return _parse_mailbox_breakdown_statistics(text)


def _format_statistics_json(
    *,
    scope: str,
    account: str,
    days_back: int,
    statistics: dict[str, Any],
    sender: str | None = None,
    mailbox: str | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "account": account,
        "scope": scope,
        "days_back": days_back,
        "recent_days_applied": _statistics_recent_days_applied(days_back, scope),
        "statistics": statistics,
        "errors": errors or [],
    }
    if sender is not None:
        payload["sender"] = sender
    if scope == "mailbox_breakdown":
        payload["mailbox"] = mailbox or "INBOX"
    return payload


def _statistics_json_error(
    error: str,
    *,
    account: str | None = None,
    days_back: int | None = None,
    scope: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": error, "errors": []}
    if account is not None:
        payload["account"] = account
    if days_back is not None:
        payload["days_back"] = days_back
    if scope is not None:
        payload["scope"] = scope
    if message is not None:
        payload["message"] = message
    return payload


def _statistics_scan_caps(days_back: int) -> tuple[int, int]:
    """Return (max_mailboxes, max_messages_per_mailbox) for overview/sender scans.

    Short windows use ``INBOX_LONG`` per mailbox; longer windows use
    ``SEARCH_WINDOW_CAP``.
    """
    if days_back > 0 and days_back <= 7:
        return 10, SCAN_BOUNDS["INBOX_LONG"]
    return 20, SCAN_BOUNDS["SEARCH_WINDOW_CAP"]


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
    mailboxes and newest messages (``days_back <= 7``: 10 mailboxes × 75
    messages; longer windows: 20 × 250) so AppleScript wall time stays
    predictable on Exchange / Gmail accounts with deep history.
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
            is no longer accepted — pass ``days_back=7`` or ``30`` and route
            unbounded sweeps through ``full_inbox_export``.
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
                    "fallback_tool": "full_inbox_export",
                    "fallback_tool_args": {
                        "account": account,
                        "scope": scope,
                    },
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
    account_err = validate_account_name(account, timeout=validation_timeout)
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
            raw_overview = run_applescript(script, timeout=timeout if timeout is not None else 120)
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

        # Mailbox-wide totals come from MBOX|||name|||total|||unread rows
        # (Mail.app's own count APIs). Sample counts come from ROW lines.
        mbox_total_counts: dict[str, int] = {}
        mbox_unread_counts: dict[str, int] = {}
        sample_flagged = 0
        sample_with_attachments = 0
        sample_total = 0
        sender_counter: Counter[str] = Counter()
        scan_errors: list[str] = []

        for line in raw_overview.splitlines():
            if line.startswith("MBOX|||"):
                parts = line.split("|||", 3)
                if len(parts) < 4:
                    continue
                _, mbox, total_str, unread_str = parts
                try:
                    mbox_total_counts[mbox] = mbox_total_counts.get(mbox, 0) + int(total_str)
                    mbox_unread_counts[mbox] = mbox_unread_counts.get(mbox, 0) + int(unread_str)
                except ValueError:
                    continue
            elif line.startswith("ROW|||"):
                parts = line.split("|||", 4)
                if len(parts) < 5:
                    continue
                _, _mbox, flag_str, attach_str, sender = parts
                sample_total += 1
                if flag_str == "1":
                    sample_flagged += 1
                if attach_str == "1":
                    sample_with_attachments += 1
                if sender:
                    sender_counter[sender] += 1
            elif line.startswith(_STATISTICS_ERROR_PREFIX):
                scan_errors.append(line)

        # Prefer MBOX-derived mailbox-wide totals; fall back to ROW-derived
        # sample totals when the script (or a legacy mock) emits no MBOX rows.
        if mbox_total_counts:
            total_emails = sum(mbox_total_counts.values())
            total_unread = sum(mbox_unread_counts.values())
            mailbox_totals = dict(mbox_total_counts)
        else:
            total_emails = sample_total
            total_unread = 0  # legacy fallback can't compute true unread
            mailbox_totals = {}

        total_read = total_emails - total_unread
        header = (
            "╔══════════════════════════════════════════╗\n"
            f"║      EMAIL STATISTICS - {escaped_account}       ║\n"
            "╚══════════════════════════════════════════╝\n\n"
        )
        lines_out = [header]
        lines_out.append("📊 VOLUME METRICS\n")
        lines_out.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        lines_out.append(f"Total Emails: {total_emails}\n")
        if total_emails > 0:
            lines_out.append(f"Unread: {total_unread} ({round(total_unread / total_emails * 100)}%)\n")
            lines_out.append(f"Read: {total_read} ({round(total_read / total_emails * 100)}%)\n")
            lines_out.append(f"Flagged: {sample_flagged}\n")
            lines_out.append(
                f"With Attachments: {sample_with_attachments} ({round(sample_with_attachments / total_emails * 100)}%)\n"
            )
        else:
            lines_out.append("Unread: 0\nRead: 0\nFlagged: 0\nWith Attachments: 0\n")
        lines_out.append("\n")
        lines_out.append("👥 SAMPLE SENDERS\n")
        lines_out.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        for sender, cnt in sender_counter.most_common(5):
            lines_out.append(f"{sender}: {cnt} emails\n")
        lines_out.append("\n")
        lines_out.append("📁 MAILBOX DISTRIBUTION\n")
        lines_out.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        for i, (mbox, cnt) in enumerate(sorted(mailbox_totals.items(), key=lambda x: -x[1])):
            if i >= 5:
                break
            if total_emails > 0:
                pct = round(cnt / total_emails * 100)
                lines_out.append(f"{mbox}: {cnt} ({pct}%)\n")
            else:
                lines_out.append(f"{mbox}: {cnt}\n")
        if scan_errors:
            lines_out.append("\nMAILBOX SCAN ERRORS\n")
            for err in scan_errors:
                lines_out.append(err + "\n")

        result = "".join(lines_out)

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
        result = run_applescript(script, timeout=timeout if timeout is not None else 120)
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


_EXPORT_ENTIRE_MAILBOX_DEFAULT = 100
_EXPORT_ENTIRE_MAILBOX_WARN_THRESHOLD = 500


@mcp.tool(annotations=WRITE_TOOL_ANNOTATIONS)
@inject_preferences
def export_emails(
    account: str | None = None,
    scope: str = "entire_mailbox",
    subject_keyword: str | None = None,
    message_id: str | None = None,
    mailbox: str = "INBOX",
    save_directory: str = "~/Desktop",
    format: str = "txt",
    max_emails: int | None = None,
    timeout: int | None = None,
) -> str:
    """
    Export emails to files for backup or analysis.

    For ``entire_mailbox`` exports, the AppleScript binds only the first
    ``max_emails`` messages (``items 1 thru max_emails``) so the full message
    list of a 24K-message Exchange mailbox is never materialized.

    **Exchange / Gmail cold-cache warning:** ``entire_mailbox`` reads
    ``content of aMessage`` for every exported message. On an Exchange account
    that has not recently synced, each body read can take 1–3 seconds — 100
    emails is already 2–5 minutes of wall time. For larger metadata-only walks
    use ``full_inbox_export`` instead, which skips body reads entirely.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Falls back to
            ``DEFAULT_MAIL_ACCOUNT`` when None.
        scope: Export scope: "single_email" (requires subject_keyword or message_id)
            or "entire_mailbox"
        subject_keyword: Keyword to find email (optional when message_id is set)
        message_id: Optional numeric Apple Mail message id for single_email scope
        mailbox: Mailbox to export from (default: "INBOX")
        save_directory: Directory to save exports (default: "~/Desktop")
        format: Export format: "txt", "html" (default: "txt")
        max_emails: Maximum number of emails to export for entire_mailbox.
            Defaults to 100 for entire_mailbox scope. Values above 500 are
            accepted but will emit a performance warning — each message
            requires a body read from Mail which is expensive on Exchange
            cold-cache accounts. For large exports prefer ``full_inbox_export``.
        timeout: Optional AppleScript timeout in seconds. Defaults to 120s.

    Returns:
        Confirmation message with export location
    """

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: 'account' is required (no DEFAULT_MAIL_ACCOUNT configured)"

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    path_err = validate_save_path(save_directory)
    if path_err:
        return path_err

    # Apply scope-specific max_emails default and emit a performance warning
    # when the caller requests an unusually large body-read export.
    export_warning: str | None = None
    if scope == "entire_mailbox":
        if max_emails is None:
            max_emails = _EXPORT_ENTIRE_MAILBOX_DEFAULT
        elif max_emails > _EXPORT_ENTIRE_MAILBOX_WARN_THRESHOLD:
            export_warning = (
                f"⚠ Performance warning: max_emails={max_emails} will read the body of "
                f"{max_emails} messages from Mail.app. On an Exchange or Gmail account "
                "with a cold cache each body read can take 1–3 seconds, making this "
                f"export potentially {max_emails * 2 // 60}–{max_emails * 3 // 60} minutes long. "
                "For metadata-only walks over large mailboxes, use full_inbox_export instead."
            )
    elif max_emails is None:
        max_emails = 1000  # legacy default for future scopes

    save_dir = str(Path(save_directory).expanduser().resolve())

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    safe_mailbox = escape_applescript(mailbox)
    safe_format = escape_applescript(format)
    safe_save_dir = escape_applescript(save_dir)

    if scope == "single_email":
        if not message_id and not subject_keyword:
            return "Error: 'message_id' or 'subject_keyword' required for single_email scope"
        if not message_id and subject_keyword:
            return target_selector_deprecated_error(
                "export_emails",
                ("subject_keyword",),
                preferred="Call search_emails(...) first, then pass message_id for scope='single_email'.",
                discovery="search_emails(subject_keyword=..., recent_days=..., limit=...)",
                exact_selector="message_id",
            )

        safe_subject_keyword = escape_applescript(subject_keyword or "")
        if message_id:
            normalized_ids = normalize_message_ids([message_id])
            if not normalized_ids:
                return "Error: message_id must be a numeric Apple Mail message id"
            target_message_id = normalized_ids[0]
        else:
            try:
                records = _search_mail_records(
                    account=account,
                    mailbox=mailbox,
                    subject_terms=[subject_keyword],
                    include_content=False,
                    offset=0,
                    limit=1,
                    timeout=timeout if timeout is not None else 45,
                )
            except AppleScriptTimeout:
                return f"Error: AppleScript timed out while locating email for '{account}'"
            if not records:
                return f"⚠ No email found matching: {safe_subject_keyword}"
            target_message_id = str(records[0].get("message_id", "")).strip()
            if not target_message_id.isdigit():
                return f"⚠ No email found matching: {safe_subject_keyword}"

        script = f'''
        tell application "Mail"
            set outputText to "EXPORTING EMAIL" & return & return

            try
                set targetAccount to account "{safe_account}"
                -- Try to get mailbox
                try
                    set targetMailbox to mailbox "{safe_mailbox}" of targetAccount
                on error
                    if "{safe_mailbox}" is "INBOX" then
                        set targetMailbox to mailbox "Inbox" of targetAccount
                    else
                        error "Mailbox not found: {safe_mailbox}"
                    end if
                end try

                -- Resolve the subject through the bounded Python search helper,
                -- then export by exact Mail id so this path does not broad-scan
                -- a large remote mailbox by subject.
                set matchedMessages to (every message of targetMailbox whose id is {target_message_id})
                set foundMessage to missing value
                if (count of matchedMessages) > 0 then
                    set foundMessage to item 1 of matchedMessages
                end if

                if foundMessage is not missing value then
                    set messageSubject to subject of foundMessage
                    set messageSender to sender of foundMessage
                    set messageDate to date received of foundMessage
                    set messageContent to content of foundMessage

                    -- Create safe filename
                    set safeSubject to messageSubject
                    set AppleScript's text item delimiters to "/"
                    set safeSubjectParts to text items of safeSubject
                    set AppleScript's text item delimiters to "-"
                    set safeSubject to safeSubjectParts as string
                    set AppleScript's text item delimiters to ""

                    set fileName to safeSubject & ".{safe_format}"
                    set filePath to "{safe_save_dir}/" & fileName

                    -- Prepare export content
                    if "{safe_format}" is "txt" then
                        set exportContent to "Subject: " & messageSubject & return
                        set exportContent to exportContent & "From: " & messageSender & return
                        set exportContent to exportContent & "Date: " & (messageDate as string) & return & return
                        set exportContent to exportContent & messageContent
                    else if "{safe_format}" is "html" then
                        set exportContent to "<html><body>"
                        set exportContent to exportContent & "<h2>" & messageSubject & "</h2>"
                        set exportContent to exportContent & "<p><strong>From:</strong> " & messageSender & "</p>"
                        set exportContent to exportContent & "<p><strong>Date:</strong> " & (messageDate as string) & "</p>"
                        set exportContent to exportContent & "<hr>" & messageContent
                        set exportContent to exportContent & "</body></html>"
                    end if

                    -- Write to file
                    set fileRef to open for access POSIX file filePath with write permission
                    set eof of fileRef to 0
                    write exportContent to fileRef as «class utf8»
                    close access fileRef

                    set outputText to outputText & "✓ Email exported successfully!" & return & return
                    set outputText to outputText & "Subject: " & messageSubject & return
                    set outputText to outputText & "Saved to: " & filePath & return

                else
                    set outputText to outputText & "⚠ No email found matching: {safe_subject_keyword}" & return
                end if

            on error errMsg
                try
                    close access file filePath
                end try
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif scope == "entire_mailbox":
        script = f'''
        tell application "Mail"
            set outputText to "EXPORTING MAILBOX" & return & return

            try
                set targetAccount to account "{safe_account}"
                -- Try to get mailbox
                try
                    set targetMailbox to mailbox "{safe_mailbox}" of targetAccount
                on error
                    if "{safe_mailbox}" is "INBOX" then
                        set targetMailbox to mailbox "Inbox" of targetAccount
                    else
                        error "Mailbox not found: {safe_mailbox}"
                    end if
                end try

                -- Use Mail's count API for the headline total, then bind
                -- only the first max_emails messages to avoid materializing
                -- the entire mailbox on large Exchange/Gmail accounts.
                set messageCount to count of messages of targetMailbox
                if messageCount > {max_emails} then
                    set mailboxMessages to messages 1 thru {max_emails} of targetMailbox
                else
                    set mailboxMessages to messages of targetMailbox
                end if
                set exportCount to 0

                -- Create export directory
                set exportDir to "{safe_save_dir}/{safe_mailbox}_export"
                do shell script "mkdir -p " & quoted form of exportDir

                repeat with aMessage in mailboxMessages
                    if exportCount >= {max_emails} then exit repeat

                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage
                        set messageContent to content of aMessage

                        -- Create safe filename with index
                        set exportCount to exportCount + 1
                        set fileName to exportCount & "_" & messageSubject & ".{safe_format}"

                        -- Remove unsafe characters
                        set AppleScript's text item delimiters to "/"
                        set fileNameParts to text items of fileName
                        set AppleScript's text item delimiters to "-"
                        set fileName to fileNameParts as string
                        set AppleScript's text item delimiters to ""

                        set filePath to exportDir & "/" & fileName

                        -- Prepare export content
                        if "{safe_format}" is "txt" then
                            set exportContent to "Subject: " & messageSubject & return
                            set exportContent to exportContent & "From: " & messageSender & return
                            set exportContent to exportContent & "Date: " & (messageDate as string) & return & return
                            set exportContent to exportContent & messageContent
                        else if "{safe_format}" is "html" then
                            set exportContent to "<html><body>"
                            set exportContent to exportContent & "<h2>" & messageSubject & "</h2>"
                            set exportContent to exportContent & "<p><strong>From:</strong> " & messageSender & "</p>"
                            set exportContent to exportContent & "<p><strong>Date:</strong> " & (messageDate as string) & "</p>"
                            set exportContent to exportContent & "<hr>" & messageContent
                            set exportContent to exportContent & "</body></html>"
                        end if

                        -- Write to file
                        set fileRef to open for access POSIX file filePath with write permission
                        set eof of fileRef to 0
                        write exportContent to fileRef as «class utf8»
                        close access fileRef

                    on error
                        -- Close file handle before continuing to avoid fd leak
                        try
                            close access fileRef
                        end try
                    end try
                end repeat

                set outputText to outputText & "✓ Mailbox exported successfully!" & return & return
                set outputText to outputText & "Mailbox: {safe_mailbox}" & return
                set outputText to outputText & "Total emails in mailbox: " & messageCount & return
                set outputText to outputText & "Exported: " & exportCount & return
                if exportCount < messageCount then
                    set outputText to outputText & "(capped at max_emails={max_emails})" & return
                end if
                set outputText to outputText & "Location: " & exportDir & return

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    else:
        return f"Error: Invalid scope '{scope}'. Use: single_email, entire_mailbox"

    try:
        result = run_applescript(script, timeout=timeout if timeout is not None else 120)
    except AppleScriptTimeout:
        return f"Error: AppleScript timed out while exporting emails for '{account}'"
    if export_warning:
        return export_warning + "\n\n" + result
    return result


_FULL_EXPORT_DEFAULT_FIELDS = (
    "subject",
    "sender",
    "date_received",
    "read_status",
    "message_id",
)
_FULL_EXPORT_ALLOWED_FIELDS = (
    "subject",
    "sender",
    "date_received",
    "date_sent",
    "read_status",
    "flagged_status",
    "message_id",
    "mailbox",
)
_FULL_EXPORT_FIELD_SEP = "__APPLE_MAIL_MCP_FIELD__"
_FULL_EXPORT_ROW_SEP = "__APPLE_MAIL_MCP_ROW__"
_FULL_EXPORT_ERROR_PREFIX = "__APPLE_MAIL_MCP_FULL_EXPORT_ERROR__|||"


_FULL_EXPORT_FIELD_EXPRS: dict[str, str] = {
    "subject": "(subject of aMessage)",
    "sender": "(sender of aMessage)",
    "date_received": "((date received of aMessage) as string)",
    "date_sent": "((date sent of aMessage) as string)",
    "read_status": "((read status of aMessage) as string)",
    "flagged_status": "((flagged status of aMessage) as string)",
    "message_id": "((id of aMessage) as string)",
    "mailbox": '"INBOX"',
}


def _full_export_field_script(field: str) -> str:
    """Return AppleScript expression that yields *field* for ``aMessage``."""
    try:
        return _FULL_EXPORT_FIELD_EXPRS[field]
    except KeyError:
        raise ValueError(f"Unsupported field: {field}") from None


def _normalize_full_export_fields(fields: Any | None) -> list[str]:
    """Normalize MCP/CLI field input to a list of field names.

    mcporter named flags pass ``--fields subject,sender`` as a string even
    though the MCP schema advertises a list. Accept both forms here so the
    tool remains usable through generated wrappers.
    """
    if fields is None:
        return list(_FULL_EXPORT_DEFAULT_FIELDS)
    if isinstance(fields, str):
        return [part.strip() for part in fields.split(",") if part.strip()]
    return [str(field).strip() for field in fields if str(field).strip()]


def _full_export_batch_script(
    *,
    account: str,
    mailbox: str,
    start_index: int,
    end_index: int,
    fields: list[str],
) -> str:
    """Build AppleScript that emits rows of ``fields`` for one batch.

    Each row is delimited by ``_FULL_EXPORT_ROW_SEP`` (RS, 0x1E); each field
    within a row by ``_FULL_EXPORT_FIELD_SEP`` (US, 0x1F). The script binds
    only ``messages start_index thru end_index`` of the mailbox — never the
    whole list — so a 24K-message inbox is walked in O(batch_size) AppleScript
    work per round-trip. Numeric indices are AppleScript-safe; only the
    user-supplied ``account`` / ``mailbox`` strings are escaped.
    """
    safe_account = escape_applescript(account)
    safe_mailbox = escape_applescript(mailbox)

    # AppleScript has no inline `try` expression. Build one assignment per
    # requested field, then concatenate the variables into the output row.
    field_assignments = []
    field_vars = []
    for idx, field in enumerate(fields):
        var_name = f"fieldValue{idx}"
        field_vars.append(var_name)
        field_assignments.append(
            f"""
                    set {var_name} to ""
                    try
                        set {var_name} to {_full_export_field_script(field)}
                    on error
                        set {var_name} to ""
                    end try
            """
        )
    row_expr = f' & "{_FULL_EXPORT_FIELD_SEP}" & '.join(field_vars) if field_vars else '""'
    field_assignment_script = "".join(field_assignments)

    return f'''
    tell application "Mail"
        set outputRows to {{}}
        try
            set targetAccount to account "{safe_account}"
            try
                set targetMailbox to mailbox "{safe_mailbox}" of targetAccount
            on error
                if "{safe_mailbox}" is "INBOX" then
                    {inbox_mailbox_script("targetMailbox", "targetAccount")}
                else
                    error "Mailbox not found: {safe_mailbox}"
                end if
            end try

            set totalMessages to count of messages of targetMailbox
            set startIndex to {start_index}
            set endIndex to {end_index}
            if startIndex > totalMessages then
                set AppleScript's text item delimiters to ""
                return ""
            end if
            if endIndex > totalMessages then
                set endIndex to totalMessages
            end if

            set batchMessages to messages startIndex thru endIndex of targetMailbox
            repeat with aMessage in batchMessages
                try
                    {field_assignment_script}
                    set rowText to {row_expr}
                    set end of outputRows to rowText
                end try
            end repeat
        on error errMsg
            return "{_FULL_EXPORT_ERROR_PREFIX}" & errMsg
        end try

        set AppleScript's text item delimiters to "{_FULL_EXPORT_ROW_SEP}"
        set outputText to outputRows as string
        set AppleScript's text item delimiters to ""
        return outputText
    end tell
    '''


def _full_export_parse_batch(raw: str, fields: list[str]) -> list[dict[str, Any]]:
    if not raw:
        return []
    records: list[dict[str, Any]] = []
    for row in raw.split(_FULL_EXPORT_ROW_SEP):
        row = row.strip("\n\r")
        if not row:
            continue
        parts = row.split(_FULL_EXPORT_FIELD_SEP)
        if len(parts) < len(fields):
            parts = parts + [""] * (len(fields) - len(parts))
        record: dict[str, Any] = {}
        for field, value in zip(fields, parts, strict=False):
            text = value.strip()
            if field in ("read_status", "flagged_status"):
                record[field] = text.lower() == "true"
            else:
                record[field] = text
        records.append(record)
    return records


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
async def full_inbox_export(
    account: str | None = None,
    mailbox: str = "INBOX",
    fields: list[str] | str | None = None,
    max_emails: int = 10_000,
    batch_size: int = 500,
    output_format: str = "json",
    timeout: int | None = None,
    ctx: Any | None = None,
) -> str:
    """
    Walk every message in the specified mailbox and return their metadata.

    EXPENSIVE: on a 24,000-message inbox this can take 2-5 minutes. Use this
    only when you really need the entire inbox — for normal queries use
    ``list_inbox_emails(max_emails=50)`` or ``search_emails(recent_days=7)``
    instead.

    Streams progress notifications in batches of ``batch_size``. Returns JSON
    (or NDJSON if ``output_format='ndjson'``) with message metadata only — no
    message bodies, no attachments. To fetch bodies, follow up with
    ``get_email_by_id``.

    Caps at ``max_emails=10000`` by default to prevent runaway. Set explicitly
    for larger inboxes.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Falls back to
            ``DEFAULT_MAIL_ACCOUNT`` when None.
        mailbox: Mailbox to walk (default: ``"INBOX"``).
        fields: Metadata fields to include for each message. Defaults to
            ``["subject", "sender", "date_received", "read_status",
            "message_id"]``. Allowed: ``subject``, ``sender``,
            ``date_received``, ``date_sent``, ``read_status``,
            ``flagged_status``, ``message_id``, ``mailbox``.
        max_emails: Hard upper bound on messages returned (default 10000).
        batch_size: Messages fetched per AppleScript round-trip (default 500).
        output_format: ``"json"`` (default) or ``"ndjson"``.
        timeout: Per-batch AppleScript timeout in seconds (default 120).

    Returns:
        JSON-encoded list of message dicts, or newline-delimited JSON if
        ``output_format="ndjson"``. Each dict contains the requested
        ``fields``.
    """

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: 'account' is required (no DEFAULT_MAIL_ACCOUNT configured)"

    if output_format not in {"json", "ndjson"}:
        return "Error: Invalid output_format. Use: json, ndjson"

    if batch_size <= 0:
        return "Error: batch_size must be a positive integer"

    if max_emails <= 0:
        return "Error: max_emails must be a positive integer"

    resolved_fields = _normalize_full_export_fields(fields)
    invalid = [f for f in resolved_fields if f not in _FULL_EXPORT_ALLOWED_FIELDS]
    if invalid:
        allowed = ", ".join(_FULL_EXPORT_ALLOWED_FIELDS)
        return f"Error: invalid field(s): {', '.join(invalid)}. Allowed: {allowed}"

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    per_batch_timeout = timeout if timeout is not None else 120

    collected: list[dict[str, Any]] = []
    start_index = 1
    while start_index <= max_emails:
        remaining = max_emails - len(collected)
        if remaining <= 0:
            break
        this_batch = min(batch_size, remaining)
        end_index = start_index + this_batch - 1

        script = _full_export_batch_script(
            account=account,
            mailbox=mailbox,
            start_index=start_index,
            end_index=end_index,
            fields=resolved_fields,
        )

        try:
            raw = await asyncio.to_thread(run_applescript, script, per_batch_timeout)
        except AppleScriptTimeout:
            return (
                f"Error: AppleScript timed out while exporting '{mailbox}' "
                f"for '{account}' at batch {start_index}-{end_index}"
            )

        if raw.startswith(_FULL_EXPORT_ERROR_PREFIX):
            err = raw[len(_FULL_EXPORT_ERROR_PREFIX) :] or "unknown error"
            return f"Error: {err}"

        batch = _full_export_parse_batch(raw, resolved_fields)
        if not batch:
            # End of mailbox: AppleScript returned nothing for this slice.
            break

        collected.extend(batch)

        # Stream progress to the MCP client when a Context is available.
        if ctx is not None:
            try:
                report = getattr(ctx, "report_progress", None)
                if report is not None:
                    result = report(
                        progress=float(len(collected)),
                        total=float(max_emails),
                        message=(f"Exported {len(collected)} messages (batch {start_index}-{end_index})"),
                    )
                    if asyncio.iscoroutine(result):
                        await result
            except Exception:  # pragma: no cover - progress is best-effort
                logger.debug(
                    "full_inbox_export progress notification failed",
                    exc_info=True,
                )

        if len(batch) < this_batch:
            # Mailbox exhausted mid-batch.
            break

        start_index = end_index + 1

    if output_format == "ndjson":
        return "\n".join(json.dumps(record, ensure_ascii=False) for record in collected)
    return json.dumps(collected, ensure_ascii=False)


def _build_recent_one_account_script(
    account: str,
    max_per_account: int,
    include_preview: bool,
) -> str:
    """Build AppleScript that returns recent inbox messages for one account."""
    escaped_account = escape_applescript(account)
    preview_block = ""
    preview_field = '""'
    if include_preview:
        preview_block = """
                        set messagePreview to ""
                        try
                            set msgContent to content of aMessage
                            if length of msgContent > 150 then
                                set messagePreview to text 1 thru 150 of msgContent
                            else
                                set messagePreview to msgContent
                            end if
                            set AppleScript's text item delimiters to {return, linefeed}
                            set contentParts to text items of messagePreview
                            set AppleScript's text item delimiters to " "
                            set messagePreview to contentParts as string
                            set AppleScript's text item delimiters to ""
                        end try
        """
        preview_field = "messagePreview"

    return f'''
    tell application "Mail"
        set resultLines to {{}}
        try
            set anAccount to account "{escaped_account}"
            set accountName to name of anAccount
            {inbox_mailbox_script("inboxMailbox", "anAccount")}

            if (count of messages of inboxMailbox) > {max_per_account} then
                set inboxMessages to messages 1 thru {max_per_account} of inboxMailbox
            else
                set inboxMessages to messages of inboxMailbox
            end if

            repeat with aMessage in inboxMessages
                try
                    set messageSubject to subject of aMessage
                    set messageSender to sender of aMessage
                    set messageDate to date received of aMessage
                    set messageRead to read status of aMessage
                    set messageAppId to (id of aMessage) as string
                    set messageInternetId to ""
                    try
                        set messageInternetId to message id of aMessage
                    end try
                    {preview_block}
                    set end of resultLines to messageSubject & "|||" & messageSender & "|||" & (messageDate as string) & "|||" & messageRead & "|||" & accountName & "|||INBOX|||" & messageAppId & "|||" & messageInternetId & "|||" & {preview_field}
                end try
            end repeat
        end try
        set AppleScript's text item delimiters to linefeed
        return resultLines as string
    end tell
    '''


def _parse_recent_email_lines(result: str) -> list[dict[str, Any]]:
    emails: list[dict[str, Any]] = []
    if not result:
        return emails
    for line in result.split("\n"):
        if "|||" not in line:
            continue
        parts = line.split("|||", 8)
        if len(parts) >= 5:
            legacy_preview = parts[5].strip() if len(parts) > 5 else ""
            emails.append(
                {
                    "subject": parts[0].strip(),
                    "sender": parts[1].strip(),
                    "date": parts[2].strip(),
                    "is_read": parts[3].strip().lower() == "true",
                    "account": parts[4].strip(),
                    "mailbox": parts[5].strip() if len(parts) > 6 else "INBOX",
                    "message_id": parts[6].strip() if len(parts) > 6 else "",
                    "internet_message_id": parts[7].strip() if len(parts) > 7 else "",
                    "preview": parts[8].strip() if len(parts) > 8 else legacy_preview,
                }
            )
    return emails


def _get_recent_emails_structured(
    account: str | None = None,
    max_total: int = 20,
    max_per_account: int = 10,
    include_preview: bool = False,
    timeout: int | None = None,
) -> list[dict[str, Any]]:
    """
    Internal helper to get recent emails from all accounts as structured data.
    Runs one AppleScript per account sequentially (use async variant for dashboard).
    """
    accounts = [account] if account else list_mail_account_names(timeout=30 if timeout is None else min(timeout, 30))
    emails: list[dict[str, Any]] = []
    for account in accounts:
        script = _build_recent_one_account_script(account, max_per_account, include_preview)
        try:
            result = run_applescript(script, timeout=timeout if timeout is not None else 60)
        except AppleScriptTimeout:
            continue
        emails.extend(_parse_recent_email_lines(result))
        if len(emails) >= max_total:
            break
    return emails[:max_total]


async def _get_recent_emails_structured_async(
    account: str | None = None,
    max_total: int = 20,
    max_per_account: int = 10,
    include_preview: bool = False,
    timeout: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent emails per account in parallel."""
    if account:
        accounts = [account]
    else:
        try:
            accounts = await asyncio.to_thread(list_mail_account_names, timeout)
        except AppleScriptTimeout:
            return []

    per_call_timeout = timeout if timeout is not None else 60

    async def run_one(account: str) -> list[dict[str, Any]]:
        script = _build_recent_one_account_script(account, max_per_account, include_preview)
        try:
            raw = await asyncio.to_thread(run_applescript, script, per_call_timeout)
            return _parse_recent_email_lines(raw)
        except AppleScriptTimeout:
            return []

    batches = await asyncio.gather(*(run_one(a) for a in accounts))
    combined: list[dict[str, Any]] = []
    for batch in batches:
        combined.extend(batch)
    return combined[:max_total]


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
async def inbox_dashboard(
    account: str | None = None,
    include_preview: bool = False,
    max_total: int = 20,
    max_per_account: int = 10,
    output_format: str = "ui",
    timeout: int | None = None,
) -> Any:
    """
    Get an interactive dashboard view of your email inbox.

    By default, returns an interactive UI dashboard resource that displays:
    - Unread email counts by account (visual cards with badges)
    - Recent emails for the selected/default account, or all accounts if no
      account/default is configured
    - Quick action buttons for common operations (Mark Read, Archive, Delete)
    - Search functionality to filter emails

    Set ``output_format="json"`` for structured dashboard metadata without
    requiring MCP Apps UI support.

    Args:
        account: Optional account name. Defaults to ``DEFAULT_MAIL_ACCOUNT``
            when configured. Omit both only for an explicit all-account view.
        include_preview: Include body previews for recent emails (slower; default False).
        max_total: Maximum recent emails across all accounts (default: 20).
        max_per_account: Maximum recent emails per account (default: 10).
        output_format: ``ui`` (default) or ``json``.
        timeout: Optional per-call AppleScript timeout in seconds (default: 60).

    Note: Requires mcp-ui-server package and a compatible MCP client.

    Returns:
        UIResource with uri "ui://apple-mail/inbox-dashboard" containing
        an interactive HTML dashboard, or a structured dict when
        ``output_format="json"``.
    """
    if output_format not in {"ui", "json"}:
        return "Error: Invalid output_format. Use: ui, json"

    from apple_mail_mcp.tools.inbox import get_mailbox_unread_counts

    per_call_timeout = timeout if timeout is not None else 60
    selected_account = account or _server.DEFAULT_MAIL_ACCOUNT

    unread_task = asyncio.to_thread(
        get_mailbox_unread_counts,
        account=selected_account,
        summary_only=True,
        timeout=per_call_timeout,
    )
    recent_task = _get_recent_emails_structured_async(
        account=selected_account,
        max_total=max_total,
        max_per_account=max_per_account,
        include_preview=include_preview,
        timeout=per_call_timeout,
    )
    accounts_data, recent_emails = await asyncio.gather(unread_task, recent_task)

    if output_format == "json":
        return {
            "account": selected_account,
            "include_preview": include_preview,
            "max_total": max_total,
            "max_per_account": max_per_account,
            "accounts": accounts_data,
            "recent_emails": recent_emails,
            "errors": [],
        }

    from apple_mail_mcp import UI_AVAILABLE

    if not UI_AVAILABLE:
        return "Error: UI module not available. Please install mcp-ui-server package."

    from ui import create_inbox_dashboard_ui

    return create_inbox_dashboard_ui(
        accounts_data=accounts_data,
        recent_emails=recent_emails,
    )
