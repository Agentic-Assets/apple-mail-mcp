"""``inbox_dashboard`` tool plus recent-email helpers (sync + async)."""

import asyncio
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import analytics


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
    accounts = (
        [account] if account else analytics.list_mail_account_names(timeout=30 if timeout is None else min(timeout, 30))
    )
    emails: list[dict[str, Any]] = []
    for account in accounts:
        script = _build_recent_one_account_script(account, max_per_account, include_preview)
        try:
            result = analytics.run_applescript(script, timeout=timeout if timeout is not None else 60)
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
    """Fetch recent emails per account sequentially, off the event loop."""
    if account:
        accounts = [account]
    else:
        try:
            accounts = await asyncio.to_thread(analytics.list_mail_account_names, timeout)
        except AppleScriptTimeout:
            return []

    per_call_timeout = timeout if timeout is not None else 60

    async def run_one(account: str) -> list[dict[str, Any]]:
        script = _build_recent_one_account_script(account, max_per_account, include_preview)
        try:
            raw = await asyncio.to_thread(analytics.run_applescript, script, per_call_timeout)
            return _parse_recent_email_lines(raw)
        except AppleScriptTimeout:
            return []

    combined: list[dict[str, Any]] = []
    for account_name in accounts:
        combined.extend(await run_one(account_name))
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

    # Sequenced (not gathered): Mail AppleScript is serialized behind a
    # single-flight lock, so running these two probes concurrently would
    # only make them queue behind each other rather than overlap.
    accounts_data = await asyncio.to_thread(
        get_mailbox_unread_counts,
        account=selected_account,
        summary_only=True,
        timeout=per_call_timeout,
    )
    recent_emails = await analytics._get_recent_emails_structured_async(
        account=selected_account,
        max_total=max_total,
        max_per_account=max_per_account,
        include_preview=include_preview,
        timeout=per_call_timeout,
    )

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
