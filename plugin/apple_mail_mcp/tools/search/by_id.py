"""Exact-id fetch helpers and their two ``@mcp.tool`` tools.

``run_applescript``, ``validate_account_name``, and ``account_not_found_json``
are routed through the ``search`` package facade so the corresponding test patch
seams keep firing.
"""

import json
import re
from typing import Any

from apple_mail_mcp.applescript_snippets import (
    recipient_addresses_block,
    sanitize_field_handler,
    thread_headers_block,
)
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS, build_whose_id_list, iter_id_chunks
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    build_mailbox_ref,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
)
from apple_mail_mcp.core.reply_state import was_replied_fragment
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import search
from apple_mail_mcp.tools.reply_state_wiring import annotate_rows_with_reply_state, build_draft_scan_status
from apple_mail_mcp.tools.search.records import _format_search_records_text, _parse_search_records


def _fetch_email_record_by_id(
    account: str,
    message_id: str,
    mailbox: str = "INBOX",
    include_content: bool = True,
    max_content_length: int = 5000,
    timeout: int | None = None,
) -> dict[str, Any] | None:
    """Fetch one message record by numeric Mail id. Returns None when not found."""
    normalized_ids = normalize_message_ids([message_id])
    if not normalized_ids:
        return None

    if max_content_length < 0:
        raise ValueError("max_content_length must be >= 0")

    safe_account = escape_applescript(account)
    numeric_id = normalized_ids[0]
    effective_timeout = timeout if timeout is not None else 120
    sanitize_script = sanitize_field_handler()
    to_recipients_script = recipient_addresses_block(
        message_var="aMessage",
        recipient_kind="to",
        output_var="toRecips",
        include_on_error=True,
    )
    cc_recipients_script = recipient_addresses_block(
        message_var="aMessage",
        recipient_kind="cc",
        output_var="ccRecips",
        include_on_error=True,
    )
    bcc_recipients_script = recipient_addresses_block(
        message_var="aMessage",
        recipient_kind="bcc",
        output_var="bccRecips",
        include_on_error=True,
    )
    thread_headers_script = thread_headers_block(
        message_var="aMessage",
        in_reply_to_var="inReplyTo",
        references_var="refsValue",
        include_on_error=True,
    )

    script = f'''
    {sanitize_script}

    on pad2(numberValue)
        if numberValue < 10 then
            return "0" & (numberValue as string)
        end if
        return numberValue as string
    end pad2

    on month_number(monthValue)
        set monthValues to {{January, February, March, April, May, June, July, August, September, October, November, December}}
        repeat with monthIndex from 1 to 12
            if item monthIndex of monthValues is monthValue then
                return monthIndex
            end if
        end repeat
        return 0
    end month_number

    on iso_datetime(dateValue)
        set yearValue to year of dateValue as integer
        set monthValue to my month_number(month of dateValue)
        set dayValue to day of dateValue as integer
        set hourValue to hours of dateValue
        set minuteValue to minutes of dateValue
        set secondValue to seconds of dateValue
        return (yearValue as string) & "-" & my pad2(monthValue) & "-" & my pad2(dayValue) & "T" & my pad2(hourValue) & ":" & my pad2(minuteValue) & ":" & my pad2(secondValue)
    end iso_datetime

    tell application "Mail"
        with timeout of {effective_timeout} seconds
            try
                set targetAccount to account "{safe_account}"
                {build_mailbox_ref(mailbox, var_name="targetMailbox")}
                set targetMessages to every message of targetMailbox whose id is {numeric_id}

                if (count of targetMessages) is 0 then
                    return ""
                end if

                set aMessage to item 1 of targetMessages
                set messageId to my sanitize_field(id of aMessage)
                set internetMessageId to ""
                try
                    set internetMessageId to my sanitize_field(message id of aMessage)
                end try
                set messageSubject to my sanitize_field(subject of aMessage)
                set messageSender to my sanitize_field(sender of aMessage)
                set messageRead to read status of aMessage
                set messageDate to date received of aMessage
                set receivedAt to my iso_datetime(messageDate)
                set mailboxName to my sanitize_field(name of targetMailbox)
                set accountName to my sanitize_field(name of targetAccount)
                set contentPreview to ""

                if {str(include_content).lower()} then
                    try
                        set msgContent to content of aMessage
                        set AppleScript's text item delimiters to {{return, linefeed, tab}}
                        set contentParts to text items of msgContent
                        set AppleScript's text item delimiters to " "
                        set cleanText to contentParts as string
                        set AppleScript's text item delimiters to ""
                        if {max_content_length} > 0 and length of cleanText > {max_content_length} then
                            set contentPreview to my sanitize_field(text 1 thru {max_content_length} of cleanText & "...")
                        else
                            set contentPreview to my sanitize_field(cleanText)
                        end if
                    end try
                end if

                set readValue to "false"
                if messageRead then
                    set readValue to "true"
                end if
                {was_replied_fragment(var="aMessage")}

                {to_recipients_script}

                {cc_recipients_script}

                {thread_headers_script}

                {bcc_recipients_script}

                return messageId & "|||" & internetMessageId & "|||" & messageSubject & "|||" & messageSender & "|||" & mailboxName & "|||" & accountName & "|||" & readValue & "|||" & receivedAt & "|||" & contentPreview & "|||" & toRecips & "|||" & ccRecips & "|||" & inReplyTo & "|||" & refsValue & "|||" & bccRecips & "|||" & wasRepliedToken
            on error errMsg
                return "ERROR|||" & errMsg
            end try
        end timeout
    end tell
    '''

    result = search.run_applescript(script, timeout=effective_timeout)
    if result.startswith("ERROR|||"):
        raise ValueError(result.split("|||", 1)[1])

    records, _mb_errors = _parse_search_records(result)
    item = records[0] if records else None
    if item is not None and include_content:
        preview = item.get("content_preview", "") or ""
        content_truncated = bool(
            max_content_length > 0 and preview.endswith("...") and len(preview) >= max_content_length + 3
        )
        item["content"] = preview
        item["content_available"] = bool(preview)
        item["content_truncated"] = content_truncated
        item["content_status"] = (
            "truncated" if content_truncated else "available" if preview else "empty_or_unavailable"
        )
        has_quoted = bool(
            re.search(r"On .+wrote:", preview, re.DOTALL)
            or re.search(r"(?m)^>", preview)
            or "-----Original Message-----" in preview
        )
        item["has_quoted_original"] = has_quoted
    return item


def _fetch_email_records_by_ids(
    account: str,
    message_ids: list[str],
    mailbox: str = "INBOX",
    include_content: bool = True,
    max_content_length: int = 5000,
    timeout: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch exact message records by numeric Mail ids, chunked for AppleScript safety."""
    normalized_ids = normalize_message_ids(message_ids)
    if not normalized_ids:
        return []

    if max_content_length < 0:
        raise ValueError("max_content_length must be >= 0")

    safe_account = escape_applescript(account)
    effective_timeout = timeout if timeout is not None else 120
    sanitize_script = sanitize_field_handler()
    to_recipients_script = recipient_addresses_block(
        message_var="aMessage",
        recipient_kind="to",
        output_var="toRecips",
        include_on_error=True,
    )
    cc_recipients_script = recipient_addresses_block(
        message_var="aMessage",
        recipient_kind="cc",
        output_var="ccRecips",
        include_on_error=True,
    )
    bcc_recipients_script = recipient_addresses_block(
        message_var="aMessage",
        recipient_kind="bcc",
        output_var="bccRecips",
        include_on_error=True,
    )
    thread_headers_script = thread_headers_block(
        message_var="aMessage",
        in_reply_to_var="inReplyTo",
        references_var="refsValue",
        include_on_error=True,
    )
    content_preview_script = ""
    if include_content:
        content_preview_script = f"""
                            try
                                set msgContent to content of aMessage
                                set AppleScript's text item delimiters to {{return, linefeed, tab}}
                                set contentParts to text items of msgContent
                                set AppleScript's text item delimiters to " "
                                set cleanText to contentParts as string
                                set AppleScript's text item delimiters to ""
                                if {max_content_length} > 0 and length of cleanText > {max_content_length} then
                                    set contentPreview to my sanitize_field(text 1 thru {max_content_length} of cleanText & "...")
                                else
                                    set contentPreview to my sanitize_field(cleanText)
                                end if
                            end try
"""

    rows: list[str] = []
    for chunk in iter_id_chunks(normalized_ids):
        id_condition = build_whose_id_list(chunk)
        script = f'''
        {sanitize_script}

        on pad2(numberValue)
            if numberValue < 10 then
                return "0" & (numberValue as string)
            end if
            return numberValue as string
        end pad2

        on month_number(monthValue)
            set monthValues to {{January, February, March, April, May, June, July, August, September, October, November, December}}
            repeat with monthIndex from 1 to 12
                if item monthIndex of monthValues is monthValue then
                    return monthIndex
                end if
            end repeat
            return 0
        end month_number

        on iso_datetime(dateValue)
            set yearValue to year of dateValue as integer
            set monthValue to my month_number(month of dateValue)
            set dayValue to day of dateValue as integer
            set hourValue to hours of dateValue
            set minuteValue to minutes of dateValue
            set secondValue to seconds of dateValue
            return (yearValue as string) & "-" & my pad2(monthValue) & "-" & my pad2(dayValue) & "T" & my pad2(hourValue) & ":" & my pad2(minuteValue) & ":" & my pad2(secondValue)
        end iso_datetime

        tell application "Mail"
            with timeout of {effective_timeout} seconds
                try
                    set recordLines to {{}}
                    set targetAccount to account "{safe_account}"
                    {build_mailbox_ref(mailbox, var_name="targetMailbox")}
                    set targetMessages to every message of targetMailbox whose {id_condition}

                    repeat with aMessage in targetMessages
                        try
                            set messageId to my sanitize_field(id of aMessage)
                            set internetMessageId to ""
                            try
                                set internetMessageId to my sanitize_field(message id of aMessage)
                            end try
                            set messageSubject to my sanitize_field(subject of aMessage)
                            set messageSender to my sanitize_field(sender of aMessage)
                            set messageRead to read status of aMessage
                            set messageDate to date received of aMessage
                            set receivedAt to my iso_datetime(messageDate)
                            set mailboxName to my sanitize_field(name of targetMailbox)
                            set accountName to my sanitize_field(name of targetAccount)
                            set contentPreview to ""
{content_preview_script}

                            set readValue to "false"
                            if messageRead then
                                set readValue to "true"
                            end if
                            {was_replied_fragment(var="aMessage")}

                            {to_recipients_script}

                            {cc_recipients_script}

                            {thread_headers_script}

                            {bcc_recipients_script}

                            set end of recordLines to messageId & "|||" & internetMessageId & "|||" & messageSubject & "|||" & messageSender & "|||" & mailboxName & "|||" & accountName & "|||" & readValue & "|||" & receivedAt & "|||" & contentPreview & "|||" & toRecips & "|||" & ccRecips & "|||" & inReplyTo & "|||" & refsValue & "|||" & bccRecips & "|||" & wasRepliedToken
                        end try
                    end repeat

                    set AppleScript's text item delimiters to linefeed
                    set outputText to recordLines as string
                    set AppleScript's text item delimiters to ""
                    return outputText
                on error errMsg
                    return "ERROR|||" & errMsg
                end try
            end timeout
        end tell
        '''

        result = search.run_applescript(script, timeout=effective_timeout)
        if result.startswith("ERROR|||"):
            raise ValueError(result.split("|||", 1)[1])
        if result:
            rows.extend(result.splitlines())

    records, _mb_errors = _parse_search_records("\n".join(rows))
    if include_content:
        for item in records:
            preview = item.get("content_preview", "") or ""
            item["has_quoted_original"] = bool(
                re.search(r"On .+wrote:", preview, re.DOTALL)
                or re.search(r"(?m)^>", preview)
                or "-----Original Message-----" in preview
            )
    return records


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def get_email_by_id(
    account: str,
    message_id: str,
    mailbox: str = "INBOX",
    include_content: bool = True,
    max_content_length: int = 5000,
    output_format: str = "text",
    timeout: int | None = None,
    include_draft_state: bool = True,
) -> str:
    """
    Fetch one email by its exact Apple Mail message id.

    Use this after `search_emails` returns a `message_id` when you need the
    full message body or stable metadata without running another broad subject
    search.

    Returned fields include ``to``, ``cc``, ``bcc`` (recipient addresses),
    ``in_reply_to`` and ``references`` (thread-linking headers parsed from the
    raw ``all headers`` of the message), ``has_quoted_original`` (True when
    the content contains a quoted prior message), ``was_replied_to`` (bool,
    always present: Mail's native read-only "was replied to" property), and
    ``has_draft`` (true/false/null; see ``include_draft_state``). When
    ``in_reply_to`` or ``references`` are present, the message is confirmed to
    be part of a thread, useful for verifying that a draft reply is
    correctly threaded.

    Args:
        account: Account name to search in (e.g., "Gmail", "Work").
        message_id: Exact numeric Apple Mail message id returned by search tools.
        mailbox: Mailbox to search in (default: "INBOX").
        include_content: Whether to include email content (default: True).
        max_content_length: Maximum content characters to return when include_content=True.
        output_format: Output format: "text" or "json" (default: "text").
        timeout: Optional AppleScript timeout in seconds (default: 120s).
        include_draft_state: When True (default), fetch a bounded Drafts
            snapshot for the message's account and set ``has_draft`` on the
            item (true/false when the scan reached that account, null when it
            was skipped or errored). Set False to skip the Drafts scan
            entirely (zero extra AppleScript calls); ``has_draft`` is then
            always null and ``draft_scan.status`` is "skipped".

    Returns:
        One matching email as text (prefixed with `[REPLIED]` / `[HAS DRAFT]`
        when applicable), or JSON with {"item": ..., "draft_scan": {...}}. If
        no message is found, JSON returns {"item": null}. JSON items include
        ``content``, ``content_available``, ``content_truncated``,
        ``content_status``, ``to``, ``cc``, ``bcc``, ``in_reply_to``,
        ``references``, ``has_quoted_original``, ``was_replied_to``, and
        ``has_draft`` when available. ``draft_scan`` is
        ``{"status": "ok" | "error" | "skipped", "scanned": N, "accounts": [...],
        "error"?: "..."}``.
    """
    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = search.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        if output_format == "json":
            return search.account_not_found_json(account, timeout=validation_timeout)
        return account_err

    normalized_ids = normalize_message_ids([message_id])
    if not normalized_ids:
        return "Error: message_id must be a numeric Apple Mail message id"

    if max_content_length < 0:
        return "Error: max_content_length must be >= 0"

    numeric_id = normalized_ids[0]
    effective_timeout = timeout if timeout is not None else 120

    try:
        item = _fetch_email_record_by_id(
            account=account,
            message_id=message_id,
            mailbox=mailbox,
            include_content=include_content,
            max_content_length=max_content_length,
            timeout=effective_timeout,
        )
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out while fetching message_id={numeric_id} "
            f"on account {account!r}. Try again or pass a larger `timeout`."
        )
    except ValueError as exc:
        return f"Error: {exc}"

    snapshots = annotate_rows_with_reply_state(
        [item] if item is not None else [],
        runner=search.run_applescript,
        timeout=effective_timeout,
        include_draft_state=include_draft_state,
        date_field="received_date",
    )
    draft_scan = build_draft_scan_status(snapshots)

    if output_format == "json":
        return json.dumps({"item": item, "draft_scan": draft_scan})

    if item is None:
        return f"Error: No email found for message_id={numeric_id} in {mailbox}"
    return _format_search_records_text([item])


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def get_email_by_ids(
    account: str,
    message_ids: list[str],
    mailbox: str = "INBOX",
    include_content: bool = False,
    max_content_length: int = 1000,
    output_format: str = "json",
    timeout: int | None = None,
    include_draft_state: bool = True,
) -> str:
    """
    Fetch multiple emails by exact Apple Mail message ids.

    Use this after `search_emails`, `list_inbox_emails`, or `get_email_thread`
    returns reviewed numeric ids. The implementation chunks internally using
    the repository's 50-id AppleScript predicate cap, preserves the input id
    order, and returns per-id not-found information without running broad
    keyword or sender searches.

    Args:
        account: Account name to search in (e.g., "Gmail", "Work").
        message_ids: Exact numeric Apple Mail message ids returned by discovery tools.
        mailbox: Mailbox to search in (default: "INBOX").
        include_content: Whether to include email content previews (default: False).
        max_content_length: Maximum content characters to return when include_content=True.
        output_format: Output format: "json" or "text" (default: "json").
        timeout: Optional per-chunk AppleScript timeout in seconds (default: 120s).
        include_draft_state: When True (default), fetch one bounded Drafts
            snapshot per account appearing in the returned items (lazily,
            capped at 5 accounts) and set `has_draft` on every item
            (true/false when scanned, null when the scan was skipped or
            errored, never silently False). Set False to skip the Drafts
            scan entirely (zero extra AppleScript calls).

    Returns:
        JSON with requested_ids, items in requested order, missing_ids, invalid_ids,
        returned count, chunk_size, and draft_scan (`{"status": "ok" | "error" |
        "skipped", "scanned": N, "accounts": [...], "error"?: "..."}`). Every
        item also carries `was_replied_to` (bool, always present) and
        `has_draft` (true/false/null). Text mode formats found items
        (prefixed with `[REPLIED]` / `[HAS DRAFT]` when applicable) and lists
        missing or invalid ids.
    """
    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = search.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        if output_format == "json":
            return search.account_not_found_json(account, timeout=validation_timeout)
        return account_err

    raw_ids = [str(value).strip() for value in (message_ids or []) if str(value).strip()]
    normalized_ids = normalize_message_ids(raw_ids)
    invalid_ids = [value for value in raw_ids if not value.isdigit()]
    if not normalized_ids:
        return "Error: message_ids must contain one or more numeric Apple Mail message ids"

    if max_content_length < 0:
        return "Error: max_content_length must be >= 0"

    effective_timeout = timeout if timeout is not None else 120

    try:
        records = _fetch_email_records_by_ids(
            account=account,
            message_ids=normalized_ids,
            mailbox=mailbox,
            include_content=include_content,
            max_content_length=max_content_length,
            timeout=effective_timeout,
        )
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out while fetching {len(normalized_ids)} message_ids "
            f"on account {account!r}. Try fewer ids or pass a larger `timeout`."
        )
    except ValueError as exc:
        return f"Error: {exc}"

    records_by_id = {str(item.get("message_id", "")): item for item in records}
    ordered_items = [records_by_id[mid] for mid in normalized_ids if mid in records_by_id]
    missing_ids = [mid for mid in normalized_ids if mid not in records_by_id]

    snapshots = annotate_rows_with_reply_state(
        ordered_items,
        runner=search.run_applescript,
        timeout=effective_timeout,
        include_draft_state=include_draft_state,
        date_field="received_date",
    )
    draft_scan = build_draft_scan_status(snapshots)

    if output_format == "json":
        return json.dumps(
            {
                "requested_ids": normalized_ids,
                "items": ordered_items,
                "returned": len(ordered_items),
                "missing_ids": missing_ids,
                "invalid_ids": invalid_ids,
                "account": account,
                "mailbox": mailbox,
                "include_content": include_content,
                "chunk_size": MAX_WHOSE_IDS,
                "draft_scan": draft_scan,
            }
        )

    lines: list[str] = []
    if ordered_items:
        lines.append(_format_search_records_text(ordered_items))
    else:
        lines.append("No emails found for requested message_ids.")
    if missing_ids:
        lines.append(f"Missing message_ids: {', '.join(missing_ids)}")
    if invalid_ids:
        lines.append(f"Ignored invalid message_ids: {', '.join(invalid_ids)}")
    return "\n".join(lines)
