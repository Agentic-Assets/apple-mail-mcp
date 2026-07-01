"""``list_mailboxes`` tool and its JSON variant.

``run_applescript`` and ``validate_account_name`` route through the ``inbox``
facade; ``account_not_found_json`` is imported directly (patched at source)."""

import json
from typing import Any

from apple_mail_mcp.core import (
    AppleScriptTimeout,
    account_not_found_json,
    escape_applescript,
    inject_preferences,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import inbox


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def list_mailboxes(
    account: str | None = None,
    include_counts: bool = False,
    output_format: str = "text",
    max_mailboxes: int | None = None,
    timeout: int | None = None,
) -> str:
    """
    List all mailboxes (folders) for a specific account or all accounts.

    Args:
        account: Optional account name to filter (e.g., "Gmail", "Work"). If None, shows all accounts.
        include_counts: Whether to include message counts for each mailbox (default: False).
            Counts are expensive on large accounts — pass True only for folder audits.
        output_format: "text" (default, human-readable) or "json" (structured list of mailbox dicts)
        max_mailboxes: Cap on mailboxes returned per account. Defaults to 100. When the cap
            fires, text mode appends a truncation banner and JSON mode includes
            ``total``, ``returned``, and ``truncated`` fields.
        timeout: Optional AppleScript timeout in seconds (default: 120s).

    Returns:
        Formatted list of mailboxes with optional message counts.
        For nested mailboxes, shows both indented format and path format (e.g., "Projects/Amplify Impact")
    """
    # Apply the default cap for both modes.
    effective_max_mailboxes = max_mailboxes if max_mailboxes is not None else 100

    if account:
        validation_timeout = 30 if timeout is None else min(timeout, 30)
        account_err = inbox.validate_account_name(account, timeout=validation_timeout)
        if account_err:
            if output_format == "json":
                return account_not_found_json(account, timeout=validation_timeout)
            return account_err

    if output_format == "json":
        return _list_mailboxes_json(
            account,
            include_counts,
            max_mailboxes=effective_max_mailboxes,
            timeout=timeout,
        )

    count_script = (
        """
        try
            set msgCount to count of messages of aMailbox
            set unreadCount to unread count of aMailbox
            set outputText to outputText & " (" & msgCount & " total, " & unreadCount & " unread)"
        on error
            set outputText to outputText & " (count unavailable)"
        end try
    """
        if include_counts
        else ""
    )

    # Escape user inputs for AppleScript
    escaped_account = escape_applescript(account) if account else None

    account_filter = (
        f'''
        if accountName is "{escaped_account}" then
    '''
        if account
        else ""
    )

    account_filter_end = "end if" if account else ""

    script = f"""
    tell application "Mail"
        set outputText to "MAILBOXES" & return & return
        set allAccounts to every account
        set wasCapped to false

        repeat with anAccount in allAccounts
            set accountName to name of anAccount

            {account_filter}
                set outputText to outputText & "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" & return
                set outputText to outputText & "📁 ACCOUNT: " & accountName & return
                set outputText to outputText & "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" & return & return

                try
                    set accountMailboxes to every mailbox of anAccount
                    set mailboxCount to 0

                    repeat with aMailbox in accountMailboxes
                        set mailboxCount to mailboxCount + 1
                        if mailboxCount > {effective_max_mailboxes} then
                            set wasCapped to true
                            exit repeat
                        end if
                        set mailboxName to name of aMailbox
                        set outputText to outputText & "  📂 " & mailboxName

                        {count_script}

                        set outputText to outputText & return

                        -- List sub-mailboxes with path notation
                        try
                            set subMailboxes to every mailbox of aMailbox
                            repeat with subBox in subMailboxes
                                set subName to name of subBox
                                set outputText to outputText & "    └─ " & subName & " [Path: " & mailboxName & "/" & subName & "]"

                                {count_script.replace("aMailbox", "subBox") if include_counts else ""}

                                set outputText to outputText & return
                            end repeat
                        end try
                    end repeat

                    set outputText to outputText & return
                on error errMsg
                    set outputText to outputText & "  ⚠ Error accessing mailboxes: " & errMsg & return & return
                end try
            {account_filter_end}
        end repeat

        if wasCapped then
            set outputText to outputText & "⚠ Truncated: list_mailboxes capped at {effective_max_mailboxes} mailboxes per account." & return
            set outputText to outputText & "  Pass max_mailboxes=N to adjust the cap." & return
        end if

        return outputText
    end tell
    """

    try:
        result = inbox.run_applescript(script, timeout=timeout if timeout is not None else 120)
    except AppleScriptTimeout:
        return (
            "Error: list_mailboxes timed out while enumerating mailboxes. "
            "Retry with a specific account, include_counts=False, or a larger `timeout`."
        )
    return result


def _list_mailboxes_json(
    account: str | None,
    include_counts: bool = True,
    *,
    max_mailboxes: int | None = None,
    timeout: int | None = None,
) -> str:
    """Return mailboxes as JSON."""
    escaped_account = escape_applescript(account) if account else None
    account_filter = f'if accountName is "{escaped_account}" then' if account else ""
    account_filter_end = "end if" if account else ""
    cap_check = ""
    if max_mailboxes is not None and max_mailboxes > 0:
        cap_check = f"""
            if mailboxIndex > {max_mailboxes} then exit repeat
        """

    def count_fields(var_name: str) -> str:
        if not include_counts:
            return """
        set msgCount to -1
        set unreadCount to -1
        """
        return f"""
        set msgCount to -1
        set unreadCount to -1
        try
            set msgCount to count of messages of {var_name}
            set unreadCount to unread count of {var_name}
        end try
        """

    script = f"""
    tell application "Mail"
        set resultLines to {{}}
        set allAccounts to every account
        repeat with anAccount in allAccounts
            set accountName to name of anAccount
            {account_filter}
            try
                set accountMailboxes to every mailbox of anAccount
                set mailboxIndex to 0
                repeat with currentMailbox in accountMailboxes
                    set mailboxIndex to mailboxIndex + 1
                    {cap_check}
                    try
                        set mailboxName to name of currentMailbox
                        {count_fields("currentMailbox")}
                        set end of resultLines to accountName & "|||" & mailboxName & "|||" & mailboxName & "|||" & msgCount & "|||" & unreadCount
                        {cap_check}
                        try
                            set childMailboxes to every mailbox of currentMailbox
                            repeat with childMailbox in childMailboxes
                                set mailboxIndex to mailboxIndex + 1
                                {cap_check}
                                set childName to name of childMailbox
                                {count_fields("childMailbox")}
                                set end of resultLines to accountName & "|||" & childName & "|||" & mailboxName & "/" & childName & "|||" & msgCount & "|||" & unreadCount
                            end repeat
                        end try
                    end try
                end repeat
            end try
            {account_filter_end}
        end repeat
        set AppleScript's text item delimiters to linefeed
        return resultLines as string
    end tell
    """
    try:
        raw = inbox.run_applescript(script, timeout=timeout if timeout is not None else 120)
    except AppleScriptTimeout:
        return json.dumps(
            {
                "error": "timed_out",
                "mailboxes": [],
                "message": (
                    "list_mailboxes timed out while enumerating mailboxes. "
                    "Retry with a specific account, include_counts=false, "
                    "or a larger timeout."
                ),
            },
            indent=2,
        )
    mailboxes = []
    for line in raw.splitlines():
        parts = line.split("|||")
        if len(parts) != 5:
            continue
        msg_count = int(parts[3]) if parts[3].lstrip("-").isdigit() else -1
        unread_count = int(parts[4]) if parts[4].lstrip("-").isdigit() else -1
        item: dict[str, Any] = {
            "account": parts[0],
            "name": parts[1],
            "path": parts[2],
        }
        if include_counts:
            item["message_count"] = msg_count
            item["unread_count"] = unread_count
        mailboxes.append(item)

    if max_mailboxes is None:
        return json.dumps(mailboxes, indent=2)

    total = len(mailboxes)
    # If the AppleScript emitted more rows than the cap (fence-post: parent row
    # appended before the post-parent cap_check fires) truncate here so the
    # returned contract (len <= max_mailboxes) is always satisfied.
    truncated = total >= max_mailboxes
    if total > max_mailboxes:
        mailboxes = mailboxes[:max_mailboxes]
    returned = len(mailboxes)
    payload = {
        "mailboxes": mailboxes,
        "total": total,
        "returned": returned,
        "truncated": truncated,
    }
    return json.dumps(payload, indent=2)
