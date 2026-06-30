"""``get_mailbox_unread_counts`` tool (summary fast path + nested per-mailbox path).

``run_applescript`` and ``validate_account_name`` are routed through the ``inbox``
facade so the existing test patch seams keep firing."""

from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import inbox


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def get_mailbox_unread_counts(
    account: str | None = None,
    include_zero: bool = False,
    summary_only: bool = False,
    max_mailboxes: int = 100,
    timeout: int | None = None,
) -> dict[str, Any]:
    """
    Get unread counts per mailbox for one account or all accounts.

    When summary_only=True, returns only per-account inbox unread totals
    (replaces the former get_unread_count tool).

    Args:
        account: Optional account name filter
        include_zero: Whether to include mailboxes with zero unread messages
        summary_only: If True, return only per-account inbox unread totals
                      (flat dict of account name -> unread count)
        max_mailboxes: Maximum number of top-level mailboxes to enumerate per
            account (default: 100). When the cap fires, the account's result
            includes a ``truncated: true`` field. On Exchange accounts with
            deep nested folder trees, or Gmail accounts with 200+ labels,
            exceeding this cap can trigger the 120s timeout from sheer
            property-read volume.
        timeout: Optional AppleScript timeout in seconds (default: 120s).

    Returns:
        If summary_only=False: nested dict keyed by account name then mailbox path
        If summary_only=True: flat dict mapping account names to inbox unread counts
    """
    if account is None and _server.DEFAULT_MAIL_ACCOUNT:
        account = _server.DEFAULT_MAIL_ACCOUNT

    if account:
        account_err = inbox.validate_account_name(account, timeout=30 if timeout is None else min(timeout, 30))
        if account_err:
            return {"error": "account_not_found", "account": account}

    escaped_account = escape_applescript(account) if account else None
    effective_timeout = timeout if timeout is not None else 120

    # Fast path: summary_only returns just per-account inbox unread totals
    if summary_only:
        summary_account_filter = (
            f'''
                if accountName is not "{escaped_account}" then
                    set shouldIncludeAccount to false
                end if
        '''
            if account
            else ""
        )
        script = f"""
        tell application "Mail"
            set resultList to {{}}
            set allAccounts to every account

            repeat with anAccount in allAccounts
                set accountName to name of anAccount
                set shouldIncludeAccount to true
                {summary_account_filter}

                if shouldIncludeAccount then
                    try
                        {inbox_mailbox_script("inboxMailbox", "anAccount")}
                        set unreadCount to unread count of inboxMailbox
                        set end of resultList to accountName & ":" & unreadCount
                    on error
                        set end of resultList to accountName & ":ERROR"
                    end try
                end if
            end repeat

            set AppleScript's text item delimiters to "|"
            return resultList as string
        end tell
        """
        try:
            result = inbox.run_applescript(script, timeout=effective_timeout)
        except AppleScriptTimeout:
            return {
                "error": "timed_out",
                "message": (
                    "AppleScript timed out while fetching inbox unread counts. Try again or pass a larger `timeout`."
                ),
            }
        flat_counts: dict[str, int] = {}
        for item in result.split("|"):
            if ":" in item:
                acct_name, count_str = item.split(":", 1)
                if count_str != "ERROR":
                    flat_counts[acct_name] = int(count_str)
                else:
                    flat_counts[acct_name] = -1
        return flat_counts

    account_filter = (
        f'''
            if accountName is not "{escaped_account}" then
                set shouldIncludeAccount to false
            end if
    '''
        if account
        else ""
    )

    script = f"""
    tell application "Mail"
        set resultList to {{}}
        set allAccounts to every account

        repeat with anAccount in allAccounts
            set accountName to name of anAccount
            set shouldIncludeAccount to true
            {account_filter}

            if shouldIncludeAccount then
                try
                    set accountMailboxes to every mailbox of anAccount
                    set mailboxIndex to 0
                    set accountTruncated to false

                    repeat with aMailbox in accountMailboxes
                        set mailboxIndex to mailboxIndex + 1
                        if mailboxIndex > {max_mailboxes} then
                            set accountTruncated to true
                            exit repeat
                        end if
                        try
                            set mailboxName to name of aMailbox
                            -- Always emit the parent row with its own unread count
                            -- (bare name as key, NOT prefixed).  Exchange INBOX has
                            -- messages AND children — skipping the parent silently
                            -- drops its own unread count.
                            set unreadCount to unread count of aMailbox
                            if {str(include_zero).lower()} or unreadCount > 0 then
                                set end of resultList to accountName & "|||" & mailboxName & "|||" & unreadCount
                            end if
                            -- Also emit child mailboxes under parent/child paths so
                            -- each child's own count is visible without double-counting
                            -- the parent (different keys: "Inbox" vs "Inbox/Sub").
                            set subMailboxes to {{}}
                            try
                                set subMailboxes to every mailbox of aMailbox
                            end try
                            repeat with subBox in subMailboxes
                                set subName to name of subBox
                                set subUnread to unread count of subBox
                                if {str(include_zero).lower()} or subUnread > 0 then
                                    set end of resultList to accountName & "|||" & mailboxName & "/" & subName & "|||" & subUnread
                                end if
                            end repeat
                        end try
                    end repeat

                    if accountTruncated then
                        set end of resultList to accountName & "|||__TRUNCATED__|||{max_mailboxes}"
                    end if
                end try
            end if
        end repeat

        if (count of resultList) is 0 then
            return ""
        end if

        set AppleScript's text item delimiters to linefeed
        set outputText to resultList as string
        set AppleScript's text item delimiters to ""
        return outputText
    end tell
    """

    try:
        result = inbox.run_applescript(script, timeout=effective_timeout)
    except AppleScriptTimeout:
        return {
            "error": "timed_out",
            "message": (
                "AppleScript timed out while fetching mailbox unread counts. Try again or pass a larger `timeout`."
            ),
        }
    nested_counts: dict[str, dict[str, int | bool]] = {}
    truncated_accounts: set[str] = set()
    if not result:
        return nested_counts

    for line in result.splitlines():
        parts = line.split("|||", 2)
        if len(parts) != 3:
            continue
        account_name, mailbox_name, unread_value = parts
        if mailbox_name == "__TRUNCATED__":
            truncated_accounts.add(account_name)
            continue
        nested_counts.setdefault(account_name, {})[mailbox_name] = int(unread_value)

    # Attach truncation marker to offending account records.
    for acct in truncated_accounts:
        if acct not in nested_counts:
            nested_counts[acct] = {}
        nested_counts[acct]["__truncated__"] = True

    return nested_counts
