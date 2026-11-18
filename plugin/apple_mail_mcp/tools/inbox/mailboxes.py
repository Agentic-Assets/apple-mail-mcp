"""``list_mailboxes`` tool and its JSON variant.

``run_applescript`` and ``validate_account_name`` route through the ``inbox``
facade; ``account_not_found_json`` is imported directly (patched at source)."""

import json
from collections import Counter
from typing import Any
from urllib.parse import unquote

from apple_mail_mcp.core import (
    AppleScriptTimeout,
    account_not_found_json,
    escape_applescript,
    inject_preferences,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import inbox
from apple_mail_mcp.tools.unread_provenance import (
    UNREAD_SOURCE_CACHED,
    unread_count_disclosure,
    unread_count_is_suspect,
    unread_count_text_footer,
    unread_count_text_label,
)

_MAILBOX_ERROR = "ERROR_MAILBOX"
_MAILBOX_CAP_REACHED = "MAILBOX_CAP_REACHED"

# Preserve selector names byte-for-byte through the row protocol. Sanitizing a
# mailbox name would produce a path that a later Mail operation cannot resolve.
_MAILBOX_WIRE_HANDLERS = f"""
on encode_mailbox_field(fieldValue)
    set fieldText to fieldValue as text
    set savedDelimiters to AppleScript's text item delimiters
    repeat with replacement in {{{{"%", "%25"}}, {{"|", "%7C"}}, {{return, "%0D"}}, {{linefeed, "%0A"}}}}
        set AppleScript's text item delimiters to item 1 of replacement
        set fieldParts to text items of fieldText
        set AppleScript's text item delimiters to item 2 of replacement
        set fieldText to fieldParts as text
    end repeat
    set AppleScript's text item delimiters to savedDelimiters
    return fieldText
end encode_mailbox_field

on mailbox_error_row(accountName, detail)
    return "{_MAILBOX_ERROR}|||" & my encode_mailbox_field(accountName) & "|||" & my encode_mailbox_field(detail)
end mailbox_error_row
"""


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS, title="Mailbox Folders")
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

    ``include_counts=True`` unread numbers are Mail.app's **cached**
    ``unread count`` aggregate, not measured counts. They drift low: measured
    2026-08-17 on a 25,012-message Exchange Inbox, Mail reported 3,236 unread
    where per-message truth was 10,016 (a 68% under-report). Text mode marks
    each one ``[Mail cached, unverified]``; JSON mode sets
    ``unread_count_source`` on every row. Because this tool also reads
    ``count of messages``, it flags any mailbox whose cached unread count
    exceeds its message count as ``unread_count_suspect``.

    Args:
        account: Optional account name to filter (e.g., "Gmail", "Work"). If None, shows all accounts.
        include_counts: Whether to include message counts for each mailbox (default: False).
            Counts are expensive on large accounts — pass True only for folder audits.
            ``message_count`` comes from Mail's ``count of messages`` and is
            reliable; ``unread_count`` is the cached aggregate described above.
        output_format: "text" (default, human-readable) or "json" (structured list of mailbox dicts)
        max_mailboxes: Cap on mailboxes returned per account. Defaults to 100. When the cap
            prevents reading further folders, text mode appends a truncation banner and
            JSON mode sets ``truncated``. JSON ``total`` counts valid rows observed in the
            bounded scan. If any folders could not be read, JSON includes ``errors``.
        timeout: Optional AppleScript timeout in seconds (default: 120s).

    Returns:
        Formatted list of mailboxes with optional message counts.
        For nested mailboxes, shows both indented format and path format (e.g., "Projects/Amplify Impact")

        With ``include_counts=True``, JSON rows carry ``unread_count_source``
        (``"mail_cached_aggregate"``) and, when the cached count exceeds the
        message count, ``unread_count_suspect``; the capped payload also carries
        envelope-level ``unread_count_measured`` / ``unread_count_note`` and the
        suspect fields. Text mode appends ``[Mail cached, unverified]`` or
        ``[Mail cached, SUSPECT]`` to each unread number plus a closing note.
    """
    # Apply the default cap for both modes.
    effective_max_mailboxes = max_mailboxes if max_mailboxes is not None else 100
    if effective_max_mailboxes <= 0:
        message = "max_mailboxes must be greater than zero."
        if output_format == "json":
            return json.dumps({"error": "invalid_max_mailboxes", "mailboxes": [], "message": message})
        return f"Error: {message}"

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

    # `unread count` is Mail's cached aggregate, so every emitted number is
    # labelled inline. A cached count above the mailbox's own message count is
    # impossible and is marked SUSPECT — free here because we already read both.
    count_script = (
        f"""
        try
            set msgCount to count of messages of aMailbox
            set unreadCount to unread count of aMailbox
            if unreadCount > msgCount then
                set unreadLabel to "{unread_count_text_label(suspect=True)}"
            else
                set unreadLabel to "{unread_count_text_label()}"
            end if
            set outputText to outputText & " (" & msgCount & " total, " & unreadCount & " unread" & unreadLabel & ")"
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
                                set mailboxCount to mailboxCount + 1
                                if mailboxCount > {effective_max_mailboxes} then exit repeat
                                set subName to name of subBox
                                set outputText to outputText & "    └─ " & subName & " [Path: " & mailboxName & "/" & subName & "]"

                                {count_script.replace("aMailbox", "subBox") if include_counts else ""}

                                set outputText to outputText & return
                            end repeat
                        on error childErr
                            set outputText to outputText & "    ⚠ Error accessing child mailboxes: " & childErr & return
                        end try
                        if mailboxCount > {effective_max_mailboxes} then
                            set wasCapped to true
                            exit repeat
                        end if
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
    if include_counts:
        return "\n".join([result, "", *unread_count_text_footer()])
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
    cap_report = ""
    if max_mailboxes is not None and max_mailboxes > 0:
        cap_check = f"""
            if mailboxIndex > {max_mailboxes} then exit repeat
        """
        cap_report = f"""
            if mailboxIndex > {max_mailboxes} then set end of resultLines to "{_MAILBOX_CAP_REACHED}"
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
        on error countErr
            set end of resultLines to my mailbox_error_row(accountName, "Counts unavailable: " & countErr)
        end try
        """

    script = f"""{_MAILBOX_WIRE_HANDLERS}
    tell application "Mail"
        set resultLines to {{}}
        set allAccounts to every account
        repeat with anAccount in allAccounts
            try
                set accountName to name of anAccount
            on error accountErr
                set end of resultLines to my mailbox_error_row("", "Account name unavailable: " & accountErr)
                set accountName to missing value
            end try
            if accountName is not missing value then
            {account_filter}
            try
                set accountMailboxes to every mailbox of anAccount
                set wireAccount to my encode_mailbox_field(accountName)
                set mailboxIndex to 0
                repeat with currentMailbox in accountMailboxes
                    set mailboxIndex to mailboxIndex + 1
                    {cap_check}
                    try
                        set mailboxName to name of currentMailbox
                        set wireName to my encode_mailbox_field(mailboxName)
                        {count_fields("currentMailbox")}
                        set end of resultLines to wireAccount & "|||" & wireName & "|||" & wireName & "|||" & msgCount & "|||" & unreadCount
                        try
                            set childMailboxes to every mailbox of currentMailbox
                            repeat with childMailbox in childMailboxes
                                set mailboxIndex to mailboxIndex + 1
                                {cap_check}
                                try
                                    set childName to my encode_mailbox_field(name of childMailbox)
                                    {count_fields("childMailbox")}
                                    set end of resultLines to wireAccount & "|||" & childName & "|||" & wireName & "/" & childName & "|||" & msgCount & "|||" & unreadCount
                                on error childRowErr
                                    set end of resultLines to my mailbox_error_row(accountName, "Child mailbox row unavailable: " & childRowErr)
                                end try
                            end repeat
                        on error childListErr
                            set end of resultLines to my mailbox_error_row(accountName, "Child mailboxes of " & mailboxName & " unavailable: " & childListErr)
                        end try
                    on error mailboxErr
                        set end of resultLines to my mailbox_error_row(accountName, "Mailbox row unavailable: " & mailboxErr)
                    end try
                    {cap_check}
                end repeat
                {cap_report}
            on error accountListErr
                set end of resultLines to my mailbox_error_row(accountName, accountListErr)
            end try
            {account_filter_end}
            end if
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
    mailboxes: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    malformed_rows = 0
    truncated = False
    for line in raw.split("\n"):
        if not line:
            continue
        if line == _MAILBOX_CAP_REACHED:
            truncated = True
            continue
        parts = line.split("|||")
        if len(parts) == 3 and parts[0] == _MAILBOX_ERROR:
            errors.append({"account": unquote(parts[1]), "message": unquote(parts[2])})
            continue
        if len(parts) != 5:
            malformed_rows += 1
            continue
        item: dict[str, Any] = {
            "account": unquote(parts[0]),
            "name": unquote(parts[1]),
            "path": unquote(parts[2]),
        }
        if include_counts:
            for key, raw_count in zip(("message_count", "unread_count"), parts[3:], strict=True):
                try:
                    item[key] = int(raw_count)
                except ValueError:
                    item[key] = -1
                    errors.append({"account": item["account"], "message": f"Invalid {key} for {item['path']}"})
            # Label every cached number, and flag the impossible ones. The rule
            # (including -1, the "count unavailable" sentinel rather than a real
            # count) lives in `unread_provenance` so this row flag and the
            # envelope verdict below cannot disagree.
            item["unread_count_source"] = UNREAD_SOURCE_CACHED
            if unread_count_is_suspect(cached_unread=item["unread_count"], total_messages=item["message_count"]):
                item["unread_count_suspect"] = True
        mailboxes.append(item)

    if malformed_rows:
        errors.append({"account": "", "message": f"{malformed_rows} malformed mailbox row(s) discarded"})

    if max_mailboxes is None and not errors and not truncated:
        # Bare-list shape is part of this branch's contract, so there is no
        # envelope to hang provenance on; per-row `unread_count_source` carries
        # it here.
        return json.dumps(mailboxes, indent=2)

    # Envelope-level provenance, seeded from the first impossible row (found
    # before the cap trims the list) so the detail names real numbers instead of
    # a generic warning; per-row `unread_count_suspect` flags all of them.
    disclosure: dict[str, Any] = {}
    if include_counts:
        offender = next((row for row in mailboxes if row.get("unread_count_suspect")), None)
        disclosure = unread_count_disclosure(
            cached_unread=offender["unread_count"] if offender else None,
            total_messages=offender["message_count"] if offender else None,
        )

    total = len(mailboxes)
    if max_mailboxes is not None:
        # Defend against older or oversized script responses without letting an
        # early account consume the quota of every subsequent account.
        seen: Counter[str] = Counter()
        bounded_mailboxes = []
        for row in mailboxes:
            acct = row["account"]
            seen[acct] += 1
            if seen[acct] <= max_mailboxes:
                bounded_mailboxes.append(row)
            else:
                truncated = True
        mailboxes = bounded_mailboxes
    payload = {
        "mailboxes": mailboxes,
        "total": total,
        "returned": len(mailboxes),
        "truncated": truncated,
        **disclosure,
    }
    if errors:
        payload["errors"] = errors
    return json.dumps(payload, indent=2)
