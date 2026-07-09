"""Thread reconstruction tool plus its pure header/subject/mailbox script helpers.

``run_applescript`` and ``validate_account_name`` are routed through the
``search`` package facade so the corresponding test patch seams keep firing.
"""

import json
import re
from datetime import datetime, timedelta
from typing import Any

from apple_mail_mcp.applescript_snippets import sanitize_field_handler, thread_headers_block
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.constants import THREAD_PREFIXES
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import search
from apple_mail_mcp.tools.search.by_id import _fetch_email_record_by_id
from apple_mail_mcp.tools.search.records import _build_applescript_date, _parse_search_records


def _thread_strip_prefixes_handler() -> str:
    """AppleScript handler to strip Re:/Fwd:/etc. prefixes from subjects."""
    prefix_checks = ""
    for prefix in THREAD_PREFIXES:
        escaped = escape_applescript(prefix)
        prefix_checks += f'''
                ignoring case
                    if baseSubj starts with "{escaped}" then
                        set baseSubj to text {len(prefix) + 1} thru -1 of baseSubj
                        repeat while baseSubj starts with " "
                            set baseSubj to text 2 thru -1 of baseSubj
                        end repeat
                        set didStrip to true
                    end if
                end ignoring
'''
    return f"""
    on stripThreadPrefixes(subj)
        set baseSubj to subj
        set didStrip to true
        repeat while didStrip
            set didStrip to false
            {prefix_checks}
        end repeat
        return baseSubj
    end stripThreadPrefixes
"""


_HEADER_MESSAGE_ID_RE = re.compile(r"<([^<>]+)>|([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+)")


def _normalize_thread_header_id(value: str) -> str:
    """Normalize a Message-ID-like token for thread graph comparisons."""
    return value.strip().strip("<>").strip().lower()


def _extract_thread_header_tokens(*values: str | None) -> list[str]:
    """Return normalized header Message-ID tokens from Message-ID/References fields."""
    tokens: set[str] = set()
    for value in values:
        if not value:
            continue
        for bracketed, bare in _HEADER_MESSAGE_ID_RE.findall(value):
            token = _normalize_thread_header_id(bracketed or bare)
            if token:
                tokens.add(token)
    return sorted(tokens)


def _applescript_string_list(values: list[str]) -> str:
    """Render a Python string list as an AppleScript list literal."""
    return "{" + ", ".join(f'"{escape_applescript(value)}"' for value in values) + "}"


def _thread_mailbox_script(mailbox: str, mailboxes: list[str] | None) -> str:
    """Build bounded mailbox selection setup for get_email_thread."""
    if mailboxes:
        mailbox_lines = [
            """
            set searchMailboxes to {}
            set useAllMailboxes to false
            """
        ]
        for mb in mailboxes:
            escaped_mb = escape_applescript(mb)
            if mb.lower() == "inbox":
                mailbox_lines.append(
                    """
            try
                set resolvedMailbox to mailbox "INBOX" of targetAccount
            on error
                set resolvedMailbox to mailbox "Inbox" of targetAccount
            end try
            set end of searchMailboxes to resolvedMailbox
                    """
                )
            else:
                mailbox_lines.append(
                    f"""
            set end of searchMailboxes to mailbox "{escaped_mb}" of targetAccount
                    """
                )
        return "\n".join(mailbox_lines)

    escaped_mailbox = escape_applescript(mailbox)
    return f'''
        try
            set searchMailbox to mailbox "{escaped_mailbox}" of targetAccount
        on error
            if "{escaped_mailbox}" is "INBOX" then
                set searchMailbox to mailbox "Inbox" of targetAccount
            else if "{escaped_mailbox}" is "All" then
                set searchMailboxes to every mailbox of targetAccount
                set useAllMailboxes to true
            else
                error "Mailbox not found: {escaped_mailbox}"
            end if
        end try

        if "{escaped_mailbox}" is not "All" then
            set searchMailboxes to {{searchMailbox}}
            set useAllMailboxes to false
        end if
    '''


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def get_email_thread(
    account: str,
    subject_keyword: str | None = None,
    message_id: str | None = None,
    mailbox: str = "INBOX",
    mailboxes: list[str] | None = None,
    max_messages: int = 50,
    recent_days: float = 2.0,
    include_preview: bool = True,
    output_format: str = "text",
    timeout: int | None = None,
) -> str:
    """
      Get an email conversation thread - all messages with the same or similar subject.

      Defaults to the last 48 hours. Unbounded thread scans
      (``recent_days=0``) are refused; full-mailbox scans are disabled, so
      pass a bounded ``recent_days`` window instead. Subject matching is
      case-insensitive.

    Preferred: pass ``message_id`` from ``search_emails`` or ``list_inbox_emails``
    to fetch the anchor message by id and match related messages by
    Internet Message-ID, In-Reply-To, and References headers before falling
    back to subject matching.

      Args:
          account: Account name (e.g., "Gmail", "Work")
          subject_keyword: Keyword to identify the thread (e.g., "Re: Project Update").
              Optional when ``message_id`` is provided.
          message_id: Optional numeric Apple Mail message id. When set, fetches the
              anchor message first and derives the thread subject from it.
          mailbox: Mailbox to search in (default: "INBOX", use "All" for all mailboxes).
              Ignored when ``mailboxes`` is provided.
          mailboxes: Explicit mailbox list to search. Prefer this over ``mailbox="All"``.
          max_messages: Maximum number of thread messages to return (default: 50)
          recent_days: Only scan messages received within this many days (default: 2.0
              = 48h). ``recent_days=0`` is rejected with ``UNBOUNDED_SCAN_REQUIRED``.
          include_preview: Include content previews in output. Set false to avoid
              reading message bodies during thread discovery.
          output_format: Output format: "text" or "json" (default: "text").
          timeout: Optional AppleScript timeout in seconds (default: 120).

      Returns:
          Formatted thread view, or JSON with items, ids, headers, anchor, and strategy.
    """
    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = search.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    if not message_id and not subject_keyword:
        return "Error: Provide either message_id or subject_keyword"

    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    if max_messages <= 0:
        return "Error: max_messages must be > 0"

    if mailboxes is not None:
        mailboxes = [mb.strip() for mb in mailboxes if mb and mb.strip()]
        if not mailboxes:
            return "Error: mailboxes must contain at least one mailbox name"
        if any(mb.lower() == "all" for mb in mailboxes):
            return 'Error: mailboxes does not accept "All"; use mailbox="All" only as a degraded fallback'

    effective_recent_days = float(recent_days) if recent_days else 0.0
    if effective_recent_days <= 0:
        tool_error = ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=("get_email_thread refuses to scan without a date window; pass recent_days=7 or smaller"),
            remediation={
                "preferred": "Pass recent_days=7",
                "note": "Full-mailbox scans are disabled; bound this call.",
            },
        )
        return serialize_tool_error(tool_error)
    effective_timeout = timeout if timeout is not None else 120

    resolved_mailbox = mailbox
    resolved_subject = subject_keyword or ""
    anchor: dict[str, Any] | None = None

    if message_id:
        normalized_ids = normalize_message_ids([message_id])
        if not normalized_ids:
            return "Error: message_id must be a numeric Apple Mail message id"
        lookup_mailboxes = mailboxes or [mailbox]
        for lookup_mailbox in lookup_mailboxes:
            try:
                anchor = _fetch_email_record_by_id(
                    account=account,
                    message_id=message_id,
                    mailbox=lookup_mailbox,
                    include_content=False,
                    max_content_length=0,
                    timeout=effective_timeout,
                )
            except AppleScriptTimeout:
                return (
                    f"Error: AppleScript timed out while fetching message_id={normalized_ids[0]} "
                    f"on account {account!r}. Try again or pass a larger `timeout`."
                )
            except ValueError as exc:
                return f"Error: {exc}"
            if anchor is not None:
                break
        if anchor is None:
            searched = ", ".join(lookup_mailboxes)
            return f"Error: No email found for message_id={normalized_ids[0]} in {searched}"
        resolved_subject = anchor.get("subject", "") or resolved_subject
        resolved_mailbox = anchor.get("mailbox") or mailbox

    escaped_account = escape_applescript(account)

    cleaned_keyword = resolved_subject
    for prefix in THREAD_PREFIXES:
        cleaned_keyword = cleaned_keyword.replace(prefix, "").strip()
    if not cleaned_keyword:
        cleaned_keyword = resolved_subject
    escaped_keyword = escape_applescript(cleaned_keyword)
    header_tokens = _extract_thread_header_tokens(
        anchor.get("internet_message_id") if anchor else None,
        anchor.get("in_reply_to") if anchor else None,
        anchor.get("references") if anchor else None,
    )
    header_matching_enabled = bool(message_id and header_tokens)
    thread_strategy = "header_first" if header_matching_enabled else "subject"
    header_tokens_literal = _applescript_string_list(header_tokens)

    date_setup = ""
    if effective_recent_days > 0:
        cutoff = datetime.now() - timedelta(days=effective_recent_days)
        date_setup = _build_applescript_date("cutoffDate", cutoff.strftime("%Y-%m-%d"))

    if effective_recent_days <= 0:
        window_line = "Window: full inbox"
    elif effective_recent_days == 2.0:
        window_line = "Window: last 48h"
    else:
        window_line = f"Window: last {effective_recent_days}d"

    scan_cap = max_messages
    date_check = "if messageDate < cutoffDate then exit repeat" if effective_recent_days > 0 else ""
    sanitize_script = sanitize_field_handler()
    thread_headers_script = thread_headers_block(
        message_var="aMessage",
        in_reply_to_var="inReplyTo",
        references_var="refsValue",
        include_on_error=True,
    )
    candidate_collection = f"""
                                set candidateMessages to {{}}
                                set messageCount to count of messages of currentMailbox
                                if messageCount > {scan_cap} then
                                    set scanUpperBound to {scan_cap}
                                else
                                    set scanUpperBound to messageCount
                                end if
                                if scanUpperBound > 0 then
                                    set candidateMessages to messages 1 thru scanUpperBound of currentMailbox
                                end if
    """
    mailbox_script = _thread_mailbox_script(resolved_mailbox, mailboxes)
    preview_collect_block = ""
    preview_text_block = ""
    if include_preview:
        preview_collect_block = """
                        -- Get content preview
                        try
                            set msgContent to content of aMessage
                            set AppleScript's text item delimiters to {return, linefeed}
                            set contentParts to text items of msgContent
                            set AppleScript's text item delimiters to " "
                            set cleanText to contentParts as string
                            set AppleScript's text item delimiters to ""

                            if length of cleanText > 150 then
                                set contentPreview to my sanitize_field(text 1 thru 150 of cleanText & "...")
                            else
                                set contentPreview to my sanitize_field(cleanText)
                            end if
                        end try
        """
        preview_text_block = """
                    if contentPreview is not "" then
                        set outputText to outputText & "   Preview: " & contentPreview & return
                    end if
        """

    script = f'''
    {sanitize_script}
    {_thread_strip_prefixes_handler()}

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
        set outputText to "EMAIL THREAD VIEW" & return & return
        set outputText to outputText & "Thread topic: {escaped_keyword}" & return
        set outputText to outputText & "Account: {escaped_account}" & return
        set outputText to outputText & "{window_line}" & return & return
        set recordRows to {{}}
        set headerThreadMessages to {{}}
        set subjectFallbackMessages to {{}}
        set threadMessages to {{}}
        set threadHeaderTokens to {header_tokens_literal}
        set selectedStrategy to "subject"
        {date_setup}

        try
            set targetAccount to account "{escaped_account}"
            {mailbox_script}

            -- Collect matching messages from mailboxes with date filter + cap
            repeat with currentMailbox in searchMailboxes
                if (count of headerThreadMessages) >= {max_messages} then exit repeat
                if (not {str(header_matching_enabled).lower()}) and (count of subjectFallbackMessages) >= {max_messages} then exit repeat

                try
                    {candidate_collection}

                    ignoring case
                        repeat with aMessage in candidateMessages
                            if (count of headerThreadMessages) >= {max_messages} then exit repeat
                            if (not {str(header_matching_enabled).lower()}) and (count of subjectFallbackMessages) >= {max_messages} then exit repeat

                            try
                                set messageSubject to subject of aMessage
                                set messageDate to date received of aMessage
                                {date_check}
                                set cleanSubject to my stripThreadPrefixes(messageSubject)
                                set subjectMatched to false
                                if cleanSubject contains "{escaped_keyword}" or messageSubject contains "{escaped_keyword}" then
                                    set subjectMatched to true
                                end if

                                set headerMatched to false
                                if {str(header_matching_enabled).lower()} then
                                    set internetMessageIdForMatch to ""
                                    try
                                        set internetMessageIdForMatch to message id of aMessage
                                    end try
                                    {thread_headers_script}
                                    set candidateHeaderText to internetMessageIdForMatch & " " & inReplyTo & " " & refsValue
                                    ignoring case
                                        repeat with threadToken in threadHeaderTokens
                                            if candidateHeaderText contains (threadToken as string) then
                                                set headerMatched to true
                                                exit repeat
                                            end if
                                        end repeat
                                    end ignoring
                                end if

                                if headerMatched then
                                    set end of headerThreadMessages to aMessage
                                else if subjectMatched and (count of subjectFallbackMessages) < {max_messages} then
                                    set end of subjectFallbackMessages to aMessage
                                end if
                            end try
                        end repeat
                    end ignoring
                end try
            end repeat

            if {str(header_matching_enabled).lower()} and (count of headerThreadMessages) > 0 then
                set threadMessages to headerThreadMessages
                set selectedStrategy to "header"
            else if {str(header_matching_enabled).lower()} then
                set selectedStrategy to "subject_fallback"
                set threadMessages to subjectFallbackMessages
            else
                set threadMessages to subjectFallbackMessages
            end if

            -- Display thread messages
            set messageCount to count of threadMessages
            set outputText to outputText & "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" & return
            set outputText to outputText & "FOUND " & messageCount & " MESSAGE(S) IN THREAD" & return
            set outputText to outputText & "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" & return & return

            repeat with aMessage in threadMessages
                try
                    set messageSubject to subject of aMessage
                    set messageSender to sender of aMessage
                    set messageDate to date received of aMessage
                    set messageRead to read status of aMessage
                    set messageId to my sanitize_field(id of aMessage)
                    set internetMessageId to ""
                    try
                        set internetMessageId to my sanitize_field(message id of aMessage)
                    end try
                    set mailboxName to my sanitize_field(name of mailbox of aMessage)
                    set accountName to my sanitize_field(name of account of mailbox of aMessage)
                    set receivedAt to my iso_datetime(messageDate)
                    {thread_headers_script}
                    set contentPreview to ""
                    {preview_collect_block}

                    if messageRead then
                        set readIndicator to "✓"
                        set readValue to "true"
                    else
                        set readIndicator to "✉"
                        set readValue to "false"
                    end if

                    set end of recordRows to messageId & "|||" & internetMessageId & "|||" & my sanitize_field(messageSubject) & "|||" & my sanitize_field(messageSender) & "|||" & mailboxName & "|||" & accountName & "|||" & readValue & "|||" & receivedAt & "|||" & contentPreview & "|||||||||" & inReplyTo & "|||" & refsValue & "|||"

                    set outputText to outputText & readIndicator & " " & messageSubject & return
                    set outputText to outputText & "   From: " & messageSender & return
                    set outputText to outputText & "   Date: " & (messageDate as string) & return
                    {preview_text_block}

                    set outputText to outputText & return
                end try
            end repeat

        on error errMsg
            return "Error: " & errMsg
        end try

        if "{output_format}" is "json" then
            set AppleScript's text item delimiters to return
            set outputRows to recordRows as string
            set AppleScript's text item delimiters to ""
            return "THREAD_STRATEGY|||" & selectedStrategy & return & outputRows
        end if

        return outputText
    end tell
    '''

    try:
        result = search.run_applescript(script, timeout=effective_timeout)
    except AppleScriptTimeout:
        return (
            f"Error: get_email_thread timed out on account '{account}' after "
            f"{effective_timeout}s. Retry with a larger timeout or tighter filters."
        )
    if output_format == "json":
        selection_strategy = thread_strategy
        parse_result = result
        if result.startswith("THREAD_STRATEGY|||"):
            first_line, _, remaining = result.partition("\n")
            selection_strategy = first_line.split("|||", 1)[1].strip() or selection_strategy
            parse_result = remaining
        records, _mailbox_errors = _parse_search_records(parse_result)
        payload: dict[str, Any] = {
            "items": records,
            "returned": len(records),
            "account": account,
            "mailbox": resolved_mailbox,
            "mailboxes": mailboxes or [resolved_mailbox],
            "subject_keyword": cleaned_keyword,
            "strategy": thread_strategy,
            "selection_strategy": selection_strategy,
            "subject_fallback_used": selection_strategy == "subject_fallback",
            "include_preview": include_preview,
            "recent_days_applied": effective_recent_days,
            "max_messages": max_messages,
        }
        if anchor is not None:
            payload["anchor"] = {
                "message_id": anchor.get("message_id", ""),
                "internet_message_id": anchor.get("internet_message_id", ""),
                "subject": anchor.get("subject", ""),
                "mailbox": anchor.get("mailbox", resolved_mailbox),
                "in_reply_to": anchor.get("in_reply_to", ""),
                "references": anchor.get("references", ""),
            }
        if message_id and not header_tokens:
            payload["warnings"] = [
                "message_id anchor had no thread headers; subject fallback was used",
            ]
        return json.dumps(payload)
    return result
