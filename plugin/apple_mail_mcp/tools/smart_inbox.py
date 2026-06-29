"""Smart inbox tools: follow-up tracking, actionable email detection, and sender analytics."""

from collections import Counter
from dataclasses import dataclass
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.constants import (
    NEWSLETTER_KEYWORD_PATTERNS,
    NEWSLETTER_PLATFORM_PATTERNS,
    SCAN_BOUNDS,
)
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    date_cutoff_script,
    escape_applescript,
    fetch_replied_ids,
    inbox_mailbox_script,
    inject_preferences,
    run_applescript,
    validate_account_name,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp


def _newsletter_filter_condition(sender_var: str = "messageSender") -> str:
    """Return AppleScript condition that evaluates to true if email is a newsletter.

    Must be evaluated inside an ``ignoring case`` block — uses raw sender
    text (no longer lowercased) so case-folding is the AppleScript engine's
    job, not a per-message shell-out.
    """
    platform_checks = " or ".join(
        f'{sender_var} contains "{escape_applescript(p)}"' for p in NEWSLETTER_PLATFORM_PATTERNS
    )
    keyword_checks = " or ".join(
        f'{sender_var} contains "{escape_applescript(k)}"' for k in NEWSLETTER_KEYWORD_PATTERNS
    )
    return f"({platform_checks} or {keyword_checks})"


def _is_noreply_recipient(address: str) -> bool:
    lower = address.casefold()
    return any(token in lower for token in ("noreply", "no-reply", "do-not-reply", "donotreply"))


def _sent_mailbox_script(var_name: str, account_var: str) -> str:
    return f"""
            set {var_name} to missing value
            try
                set {var_name} to mailbox "Sent Messages" of {account_var}
            on error
                try
                    set {var_name} to mailbox "Sent" of {account_var}
                on error
                    try
                        set {var_name} to mailbox "Sent Items" of {account_var}
                    on error
                        return "Error: Could not find Sent mailbox"
                    end try
                end try
            end try
    """


@dataclass(frozen=True)
class _AwaitingReplySentRow:
    mail_app_id: str
    internet_message_id: str
    subject: str
    recipient_address: str
    sent_at: str


def _parse_awaiting_reply_sent_rows(raw: str) -> list[_AwaitingReplySentRow]:
    rows: list[_AwaitingReplySentRow] = []
    for line in raw.splitlines():
        if not line.startswith("SENT|||"):
            continue
        parts = line.split("|||", 5)
        if len(parts) == 6:
            rows.append(
                _AwaitingReplySentRow(
                    mail_app_id=parts[1],
                    internet_message_id=parts[2],
                    subject=parts[3],
                    recipient_address=parts[4],
                    sent_at=parts[5],
                )
            )
    return rows


def _parse_inbox_replied_ids(raw: str) -> set[str]:
    """Build the set of Message-IDs referenced by inbox In-Reply-To / References headers."""
    replied: set[str] = set()
    for line in raw.splitlines():
        if not line.startswith("INBOXHDR|||"):
            continue
        parts = line.split("|||", 2)
        if len(parts) < 3:
            continue
        value = parts[2].strip()
        if not value:
            continue
        # Extract all <id@host> tokens from the header value.
        for token in value.split():
            token = token.strip()
            if token.startswith("<") and token.endswith(">") and "@" in token:
                replied.add(token)
            elif "@" in token and not token.startswith("<"):
                replied.add("<" + token + ">")
    return replied


def _normalize_message_id(raw_id: str) -> str:
    """Ensure a Message-ID has angle brackets."""
    raw_id = raw_id.strip()
    if not raw_id.startswith("<"):
        raw_id = "<" + raw_id
    if not raw_id.endswith(">"):
        raw_id = raw_id + ">"
    return raw_id


def _filter_awaiting_reply(
    *,
    sent_rows: list[_AwaitingReplySentRow],
    replied_to_ids: set[str],
    exclude_noreply: bool,
    max_results: int,
) -> list[_AwaitingReplySentRow]:
    """Apply noreply + already-replied filters; cap to *max_results*."""
    awaiting: list[_AwaitingReplySentRow] = []
    for sent_row in sent_rows:
        if len(awaiting) >= max_results:
            break
        if exclude_noreply and _is_noreply_recipient(sent_row.recipient_address):
            continue
        mid = _normalize_message_id(sent_row.internet_message_id) if sent_row.internet_message_id else ""
        if mid and mid in replied_to_ids:
            continue
        awaiting.append(sent_row)
    return awaiting


def _format_awaiting_reply_results(
    *,
    account: str,
    days_back: int,
    sent_rows: list[_AwaitingReplySentRow],
    replied_to_ids: set[str],
    exclude_noreply: bool,
    max_results: int,
) -> str:
    awaiting = _filter_awaiting_reply(
        sent_rows=sent_rows,
        replied_to_ids=replied_to_ids,
        exclude_noreply=exclude_noreply,
        max_results=max_results,
    )
    lines = [
        "EMAILS AWAITING REPLY",
        f"Account: {account} | Last {days_back} days",
        "========================================",
        "",
    ]
    for index, sent_row in enumerate(awaiting, start=1):
        lines.append(f"{index}. {sent_row.subject}")
        lines.append(f"   To: {sent_row.recipient_address}")
        lines.append(f"   Sent: {sent_row.sent_at}")
        lines.append("")

    lines.append("========================================")
    lines.append(f"Found {len(awaiting)} sent email(s) awaiting reply.")
    return "\n".join(lines)


def _build_awaiting_reply_json(
    *,
    account: str,
    days_back: int,
    max_results: int,
    sent_rows: list[_AwaitingReplySentRow],
    replied_to_ids: set[str],
    exclude_noreply: bool,
) -> dict[str, Any]:
    awaiting = _filter_awaiting_reply(
        sent_rows=sent_rows,
        replied_to_ids=replied_to_ids,
        exclude_noreply=exclude_noreply,
        max_results=max_results,
    )
    return {
        "account": account,
        "days_back": days_back,
        "max_results": max_results,
        "awaiting": [
            {
                "subject": row.subject,
                "recipient": row.recipient_address,
                "sent_at": row.sent_at,
                "message_id": row.internet_message_id,
                "mail_app_id": row.mail_app_id,
            }
            for row in awaiting
        ],
        "errors": [],
    }


def _build_awaiting_reply_inbox_script(
    *,
    escaped_account: str,
    inbox_cap: int,
    days_back: int,
) -> str:
    """Return AppleScript that emits In-Reply-To and References headers from inbox messages.

    Iterates ``headers of aMessage`` to read only the two header rows we need
    (no full RFC822 blob). ``header value of header named "..."`` is *not*
    valid Mail.app dictionary syntax and fails to parse with osascript -2740.
    """
    inbox_date_check = "if messageDate < cutoffDate then exit repeat" if days_back > 0 else ""
    return f'''
    tell application "Mail"
        try
            set targetAccount to account "{escaped_account}"
            {inbox_mailbox_script("inboxMailbox", "targetAccount")}
            {date_cutoff_script(days_back, "cutoffDate")}

            set inboxMessages to {{}}
            set inboxCount to count of messages of inboxMailbox
            if inboxCount > {inbox_cap} then
                set inboxUpperBound to {inbox_cap}
            else
                set inboxUpperBound to inboxCount
            end if
            if inboxUpperBound > 0 then
                set inboxMessages to messages 1 thru inboxUpperBound of inboxMailbox
            end if

            set outputLines to {{}}
            repeat with aMessage in inboxMessages
                try
                    set messageDate to date received of aMessage
                    {inbox_date_check}
                    set inReplyTo to ""
                    set refsHeader to ""
                    try
                        repeat with aHeader in (headers of aMessage)
                            set hName to name of aHeader
                            if hName is "In-Reply-To" then
                                set inReplyTo to content of aHeader
                            else if hName is "References" then
                                set refsHeader to content of aHeader
                            end if
                        end repeat
                    end try
                    if inReplyTo is not "" then
                        set end of outputLines to "INBOXHDR|||in-reply-to|||" & inReplyTo
                    end if
                    if refsHeader is not "" then
                        set end of outputLines to "INBOXHDR|||references|||" & refsHeader
                    end if
                end try
            end repeat

            set AppleScript's text item delimiters to linefeed
            return outputLines as string
        on error errMsg
            return "Error: " & errMsg
        end try
    end tell
    '''


def _build_awaiting_reply_sent_script(
    *,
    escaped_account: str,
    sent_cap: int,
    days_back: int,
) -> str:
    """Return AppleScript that emits sent message rows with internet_message_id.

    Shape: SENT|||mail_app_id|||internet_message_id|||subject|||recipient|||date_sent
    """
    sent_date_check = "if messageDate < cutoffDate then exit repeat" if days_back > 0 else ""
    return f'''
    tell application "Mail"
        try
            set targetAccount to account "{escaped_account}"
            {_sent_mailbox_script("sentMailbox", "targetAccount")}
            {date_cutoff_script(days_back, "cutoffDate")}

            set sentMessages to {{}}
            set sentCount to count of messages of sentMailbox
            if sentCount > {sent_cap} then
                set sentUpperBound to {sent_cap}
            else
                set sentUpperBound to sentCount
            end if
            if sentUpperBound > 0 then
                set sentMessages to messages 1 thru sentUpperBound of sentMailbox
            end if

            set outputLines to {{}}
            repeat with aMessage in sentMessages
                try
                    set messageDate to date sent of aMessage
                    {sent_date_check}
                    set mailAppId to ""
                    try
                        set mailAppId to id of aMessage as string
                    end try
                    set internetMsgId to ""
                    try
                        set internetMsgId to message id of aMessage as string
                    end try
                    set messageSubject to ""
                    try
                        set messageSubject to subject of aMessage
                    end try
                    set recipAddr to ""
                    try
                        set messageRecipients to every to recipient of aMessage
                        if (count of messageRecipients) > 0 then
                            set recipAddr to address of item 1 of messageRecipients
                        end if
                    end try
                    set end of outputLines to "SENT|||" & mailAppId & "|||" & internetMsgId & "|||" & messageSubject & "|||" & recipAddr & "|||" & (messageDate as string)
                end try
            end repeat

            set AppleScript's text item delimiters to linefeed
            return outputLines as string
        on error errMsg
            return "Error: " & errMsg
        end try
    end tell
    '''


def _awaiting_reply_error(message: str, *, output_format: str) -> str | dict[str, Any]:
    if output_format == "json":
        return {"error": message, "errors": [message], "awaiting": []}
    return f"Error: {message}"


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def get_awaiting_reply(
    account: str | None = None,
    days_back: int = 7,
    exclude_noreply: bool = True,
    max_results: int = 20,
    timeout: int | None = None,
    output_format: str = "text",
) -> str | dict[str, Any]:
    """Find sent emails that haven't received a reply yet.

    Scans the Sent mailbox for outgoing emails and cross-references with
    the Inbox using Message-ID matching (In-Reply-To / References headers)
    to determine whether a reply has arrived. No subject-substring matching
    is used. Useful for follow-up tracking.

    Args:
        account: Account name (e.g., "Gmail", "Work", "Personal").
            Falls back to ``DEFAULT_MAIL_ACCOUNT`` env-configured account when None.
        days_back: How many days back to check sent emails (default: 7)
        exclude_noreply: Skip emails sent to noreply/no-reply addresses (default: True)
        max_results: Maximum results to return (default: 20)
        timeout: Optional AppleScript timeout in seconds. Defaults to 120s.
        output_format: ``"text"`` (default, human-readable) or ``"json"``
            (returns a structured dict suitable for programmatic use).

    Returns:
        Either a formatted text block or a dict ``{"account", "days_back",
        "max_results", "awaiting": [...], "errors": []}`` depending on
        *output_format*.
    """
    if output_format not in {"text", "json"}:
        return _awaiting_reply_error(
            f"invalid output_format: {output_format!r} (expected 'text' or 'json')",
            output_format="text",
        )

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return _awaiting_reply_error(
            "No account specified and DEFAULT_MAIL_ACCOUNT is not set",
            output_format=output_format,
        )

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = validate_account_name(account, timeout=validation_timeout)
    if account_err:
        if output_format == "json":
            # ``validate_account_name`` returns either a serialized JSON
            # ``ToolError`` string or a plain "Error: ..." string. Surface
            # whichever shape it produced under the JSON ``error`` key so
            # callers don't have to dual-decode.
            return {"error": account_err, "errors": [account_err], "awaiting": []}
        return account_err

    escaped_account = escape_applescript(account)

    # ``INBOX_SHORT`` (30) caps the inbox slice; the sent slice stays at 20
    # to match the existing tighter follow-up window.
    sent_cap = min(max(max_results, 5), 20)
    inbox_cap = min(max(max_results * 2, 10), SCAN_BOUNDS["INBOX_SHORT"])
    inbox_script = _build_awaiting_reply_inbox_script(
        escaped_account=escaped_account,
        inbox_cap=inbox_cap,
        days_back=days_back,
    )
    sent_script = _build_awaiting_reply_sent_script(
        escaped_account=escaped_account,
        sent_cap=sent_cap,
        days_back=days_back,
    )
    effective_timeout = timeout if timeout is not None else 120
    # Split budget so the two sequential calls cannot exceed the total wall time.
    # Inbox scan gets 60 % (min 30 s); sent scan gets the remaining 40 % (min 30 s).
    inbox_timeout = max(30, int(effective_timeout * 0.6))
    sent_timeout = max(30, int(effective_timeout * 0.4))

    try:
        inbox_raw = run_applescript(inbox_script, timeout=inbox_timeout)
    except AppleScriptTimeout:
        return _awaiting_reply_error(
            (
                f"get_awaiting_reply timed out during inbox scan on account '{account}' "
                f"after {inbox_timeout}s — try increasing timeout or reducing days_back"
            ),
            output_format=output_format,
        )
    try:
        sent_raw = run_applescript(sent_script, timeout=sent_timeout)
    except AppleScriptTimeout:
        return _awaiting_reply_error(
            (
                f"get_awaiting_reply timed out during sent scan on account '{account}' "
                f"after {sent_timeout}s — try increasing timeout or reducing days_back"
            ),
            output_format=output_format,
        )

    for raw in (inbox_raw, sent_raw):
        if raw.startswith("Error:"):
            if output_format == "json":
                return {"error": raw, "errors": [raw], "awaiting": []}
            return raw

    sent_rows = _parse_awaiting_reply_sent_rows(sent_raw)
    replied_to_ids = _parse_inbox_replied_ids(inbox_raw)

    if output_format == "json":
        return _build_awaiting_reply_json(
            account=account,
            days_back=days_back,
            max_results=max_results,
            sent_rows=sent_rows,
            replied_to_ids=replied_to_ids,
            exclude_noreply=exclude_noreply,
        )

    return _format_awaiting_reply_results(
        account=account,
        days_back=days_back,
        sent_rows=sent_rows,
        replied_to_ids=replied_to_ids,
        exclude_noreply=exclude_noreply,
        max_results=max_results,
    )


@dataclass(frozen=True)
class _NeedsResponseRow:
    """Structured per-message candidate emitted by the inbox script."""

    mail_app_id: str
    internet_message_id: str
    subject: str
    sender: str
    date_str: str
    is_flagged: bool
    has_question: bool

    @property
    def message_id(self) -> str:
        """Backward-compatible alias for older internal tests/helpers."""
        return self.internet_message_id


def _parse_needs_response_inbox_rows(raw: str) -> list[_NeedsResponseRow]:
    """Parse ``MSG|||...`` lines into ``_NeedsResponseRow`` instances.

    Schema: MSG|||mail_app_id|||internet_message_id|||subject|||sender|||date_str|||is_flagged|||has_question
    Booleans are encoded as ``"true"`` / ``"false"``. Malformed rows are
    skipped silently so a single bad message can't poison the result.
    """
    rows: list[_NeedsResponseRow] = []
    for line in raw.splitlines():
        if not line.startswith("MSG|||"):
            continue
        parts = line.split("|||", 7)
        if len(parts) == 7:
            # Backwards-compatible parser for older tests/log captures that
            # emitted only the Internet Message-ID in the message_id slot.
            _, internet_message_id, subject, sender, date_str, is_flagged, has_question = parts
            mail_app_id = ""
        elif len(parts) == 8:
            _, mail_app_id, internet_message_id, subject, sender, date_str, is_flagged, has_question = parts
        else:
            continue
        rows.append(
            _NeedsResponseRow(
                mail_app_id=mail_app_id,
                internet_message_id=internet_message_id,
                subject=subject,
                sender=sender,
                date_str=date_str,
                is_flagged=is_flagged.strip().lower() == "true",
                has_question=has_question.strip().lower() == "true",
            )
        )
    return rows


def _priority_label(*, has_question: bool, is_flagged: bool, already_replied: bool) -> str:
    """Match the AppleScript priority labeling the legacy tool produced."""
    if has_question or is_flagged:
        if has_question and is_flagged:
            label = "HIGH (flagged + question)"
        elif is_flagged:
            label = "HIGH (flagged)"
        else:
            label = "MEDIUM (contains question)"
    else:
        label = "NORMAL"
    if already_replied:
        label = f"[ALREADY REPLIED] {label}"
    return label


def _classify_needs_response_rows(
    rows: list[_NeedsResponseRow],
    *,
    replied_ids: set[str],
    include_already_replied: bool,
    max_results: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Split candidates into (high, normal, skipped_replied_count).

    Each item dict matches the JSON output shape; the text formatter just
    re-renders the same dicts. The high/normal split mirrors the legacy
    AppleScript behavior: ``has_question or is_flagged`` -> high.
    """
    high: list[dict[str, Any]] = []
    normal: list[dict[str, Any]] = []
    skipped = 0

    for row in rows:
        if len(high) + len(normal) >= max_results:
            break
        already_replied = False
        if row.internet_message_id and replied_ids:
            already_replied = _normalize_message_id(row.internet_message_id) in replied_ids

        if already_replied and not include_already_replied:
            skipped += 1
            continue

        priority = _priority_label(
            has_question=row.has_question,
            is_flagged=row.is_flagged,
            already_replied=already_replied,
        )
        entry = {
            "subject": row.subject,
            "sender": row.sender,
            "date": row.date_str,
            "priority": priority,
            "already_replied": already_replied,
            "message_id": row.mail_app_id,
            "internet_message_id": row.internet_message_id,
        }
        if row.has_question or row.is_flagged:
            high.append(entry)
        else:
            normal.append(entry)

    return high, normal, skipped


def _format_needs_response_text(
    *,
    account: str,
    mailbox: str,
    days_back: int,
    high: list[dict[str, Any]],
    normal: list[dict[str, Any]],
    skipped_replied: int,
    include_already_replied: bool,
) -> str:
    """Render the human-readable output identical to the legacy AppleScript."""
    lines = [
        "EMAILS NEEDING RESPONSE",
        f"Account: {account} | Mailbox: {mailbox} | Last {days_back} days",
        "========================================",
        "",
    ]
    result_count = 0
    for entry in (*high, *normal):
        result_count += 1
        lines.append(f"{result_count}. [{entry['priority']}] {entry['subject']}")
        lines.append(f"   From: {entry['sender']}")
        lines.append(f"   Date: {entry['date']}")
        lines.append("")

    lines.append("========================================")
    lines.append(f"Found {result_count} email(s) needing response.")
    if not include_already_replied and skipped_replied > 0:
        lines.append(
            f"Filtered {skipped_replied} already-replied email(s). "
            "Re-run with include_already_replied=True to see them."
        )
    # Trailing newline + return-style separator matched the legacy output.
    return "\n".join(lines) + "\n"


def _build_needs_response_inbox_script(
    *,
    escaped_account: str,
    escaped_mailbox: str,
    days_back: int,
    inbox_cap: int,
    max_results: int,
    scan_body: bool,
) -> str:
    """Return AppleScript that emits one ``MSG|||...`` row per candidate email.

    Filters newsletters/automated senders inline (cheaper than fetching every
    sender into Python). Does NOT perform replied-detection — that runs as a
    separate script so the per-message loop is a flat O(N) walk.
    """
    newsletter_condition = _newsletter_filter_condition("messageSender")
    date_check = "if messageDate < cutoffDate then exit repeat" if days_back > 0 else ""
    body_scan_block = (
        """
                            try
                                set msgContent to content of aMessage
                                if length of msgContent > 500 then
                                    set msgContent to text 1 thru 500 of msgContent
                                end if
                                if msgContent contains "?" then set hasQuestion to true
                            end try
"""
        if scan_body
        else ""
    )
    return f'''
    tell application "Mail"
        try
            set targetAccount to account "{escaped_account}"

            try
                set targetMailbox to mailbox "{escaped_mailbox}" of targetAccount
            on error
                if "{escaped_mailbox}" is "INBOX" then
                    set targetMailbox to mailbox "Inbox" of targetAccount
                else
                    error "Mailbox not found: {escaped_mailbox}"
                end if
            end try

            {date_cutoff_script(days_back, "cutoffDate")}

            -- Bounded newest-first slice; no `whose` filter to avoid
            -- materializing deep remote mailboxes.
            set mailboxMessages to {{}}
            set mailboxCount to count of messages of targetMailbox
            if mailboxCount > {inbox_cap} then
                set mailboxUpperBound to {inbox_cap}
            else
                set mailboxUpperBound to mailboxCount
            end if
            if mailboxUpperBound > 0 then
                set mailboxMessages to messages 1 thru mailboxUpperBound of targetMailbox
            end if

            set outputLines to {{}}
            set emittedCount to 0

            repeat with aMessage in mailboxMessages
                if emittedCount >= {max_results} then exit repeat
                try
                    set messageDate to date received of aMessage
                    {date_check}

                    if not (read status of aMessage) then
                        set messageSender to sender of aMessage
                        set messageSubject to subject of aMessage

                        -- Newsletter/automated filter stays in AppleScript:
                        -- shipping every sender back to Python would defeat the
                        -- point of bounding the scan.
                        set isNewsletter to false
                        set isAutomated to false
                        ignoring case
                            set isNewsletter to {newsletter_condition}
                            set isAutomated to (messageSender contains "noreply" or messageSender contains "no-reply" or messageSender contains "donotreply" or messageSender contains "do-not-reply" or messageSender contains "notifications@" or messageSender contains "mailer-daemon" or messageSender contains "postmaster@")
                        end ignoring

                        if not isNewsletter and not isAutomated then
                            set hasQuestion to (messageSubject contains "?")
                            {body_scan_block}

                            set isFlagged to false
                            try
                                set isFlagged to flagged status of aMessage
                            end try

                            set mailAppMessageId to id of aMessage as string

                            -- Internet Message-ID may not be available on every
                            -- message; emit "" in that case so Python treats it
                            -- as never-replied.
                            set inboxInternetMessageId to ""
                            try
                                set rawMessageId to message id of aMessage
                                if rawMessageId is not missing value then
                                    set inboxInternetMessageId to rawMessageId as string
                                end if
                            end try

                            set flagText to "false"
                            if isFlagged then set flagText to "true"
                            set questionText to "false"
                            if hasQuestion then set questionText to "true"

                            set end of outputLines to "MSG|||" & mailAppMessageId & "|||" & inboxInternetMessageId & "|||" & messageSubject & "|||" & messageSender & "|||" & (messageDate as string) & "|||" & flagText & "|||" & questionText
                            set emittedCount to emittedCount + 1
                        end if
                    end if
                end try
            end repeat

            set AppleScript's text item delimiters to linefeed
            set outputText to outputLines as string
            set AppleScript's text item delimiters to ""
            return outputText
        on error errMsg
            return "Error: " & errMsg
        end try
    end tell
    '''


def _needs_response_error(message: str, *, output_format: str) -> str | dict[str, Any]:
    if output_format == "json":
        return {
            "error": message,
            "errors": [message],
            "high_priority": [],
            "normal_priority": [],
        }
    return f"Error: {message}"


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def get_needs_response(
    account: str | None = None,
    mailbox: str = "INBOX",
    days_back: int = 7,
    max_results: int = 20,
    scan_body: bool = False,
    include_already_replied: bool = False,
    check_already_replied: bool = False,
    timeout: int | None = None,
    output_format: str = "text",
) -> str | dict[str, Any]:
    """Identify unread emails that likely need a response from you.

    Filters out newsletters, automated emails, and noreply senders.
    Prioritises direct emails (To: you) with question marks as likely
    needing a reply.

    Replied-detection: scans the Sent mailbox for ``In-Reply-To:`` and
    ``References:`` headers to build a set of Message-IDs the user has
    replied to, then matches each candidate's Internet Message-ID against
    that set in Python (O(1) set lookup). Header-based detection only —
    no subject-substring matching.

    Args:
        account: Account name (e.g., "Gmail", "Work", "Personal").
            Falls back to ``DEFAULT_MAIL_ACCOUNT`` env-configured account when None.
        mailbox: Mailbox to scan (default: "INBOX")
        days_back: How many days back to look (default: 7)
        max_results: Maximum results to return (default: 20)
        scan_body: When True, scan message body for question marks (slower).
            Subject-only detection is usually enough for daily triage (default: False).
        include_already_replied: When False (default), emails the user has
            already replied to are filtered out — this is the safe default
            to prevent agents drafting duplicate replies. When True, those
            emails are kept but annotated with a ``[ALREADY REPLIED]``
            prefix in the priority label.
        check_already_replied: When True, scan the Sent mailbox to detect
            already-replied emails (duplicate-reply protection). Defaults to
            ``False`` because reading Sent headers on Exchange triggers per-message
            IMAP downloads and causes timeouts on large inboxes. Enable on
            smaller accounts or when duplicate-reply protection is required.
        timeout: Optional AppleScript timeout in seconds. Defaults to 120s.
        output_format: ``"text"`` (default, human-readable) or ``"json"``
            (returns a structured dict suitable for programmatic use).

    Returns:
        Ranked list of emails likely needing a response. Either a formatted
        text block or a dict ``{"account", "mailbox", "days_back",
        "max_results", "high_priority": [...], "normal_priority": [...],
        "skipped_replied_count", "errors"}`` depending on *output_format*.
    """
    if output_format not in {"text", "json"}:
        return _needs_response_error(
            f"invalid output_format: {output_format!r} (expected 'text' or 'json')",
            output_format="text",
        )

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return _needs_response_error(
            "No account specified and DEFAULT_MAIL_ACCOUNT is not set",
            output_format=output_format,
        )

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = validate_account_name(account, timeout=validation_timeout)
    if account_err:
        if output_format == "json":
            return {
                "error": account_err,
                "errors": [account_err],
                "high_priority": [],
                "normal_priority": [],
            }
        return account_err

    escaped_account = escape_applescript(account)
    escaped_mailbox = escape_applescript(mailbox)

    # Cap message collection. Tighter caps keep daily triage under agent
    # budgets; ``INBOX_SHORT`` (30) is the centralized ceiling.
    inbox_cap = min(max(max_results * 2, 20), SCAN_BOUNDS["INBOX_SHORT"])
    sent_cap = 20

    # Budget: when replied-detection is on, the inbox scan gets the bulk
    # of the wall-clock budget (the Sent scan is bounded and typically
    # 1–3 s). Mirrors the get_awaiting_reply 60/40 split, but skewed
    # further toward inbox because the Sent slice here is smaller.
    effective_timeout = timeout if timeout is not None else 120
    if check_already_replied:
        inbox_timeout = max(30, int(effective_timeout * 0.7))
        sent_timeout = max(30, int(effective_timeout * 0.3))
    else:
        inbox_timeout = effective_timeout
        sent_timeout = effective_timeout  # unused when check_already_replied=False

    inbox_script = _build_needs_response_inbox_script(
        escaped_account=escaped_account,
        escaped_mailbox=escaped_mailbox,
        days_back=days_back,
        inbox_cap=inbox_cap,
        max_results=max_results,
        scan_body=scan_body,
    )

    try:
        inbox_raw = run_applescript(inbox_script, timeout=inbox_timeout)
    except AppleScriptTimeout:
        return _needs_response_error(
            (
                f"get_needs_response timed out on account '{account}' after "
                f"{inbox_timeout}s — try increasing timeout or reducing days_back"
            ),
            output_format=output_format,
        )

    if inbox_raw.startswith("Error:"):
        if output_format == "json":
            return {
                "error": inbox_raw,
                "errors": [inbox_raw],
                "high_priority": [],
                "normal_priority": [],
            }
        return inbox_raw

    # Replied set: fetched via core helper which routes through this module's
    # ``run_applescript`` symbol via the injected runner so tests patching
    # ``apple_mail_mcp.tools.smart_inbox.run_applescript`` see the call.
    # When check_already_replied=False the set stays empty and the inbox
    # script is the only AppleScript invocation.
    if check_already_replied:
        replied_ids: set[str] = fetch_replied_ids(
            account,
            sent_cap=sent_cap,
            timeout=sent_timeout,
            runner=run_applescript,
        )
    else:
        replied_ids = set()

    rows = _parse_needs_response_inbox_rows(inbox_raw)
    high, normal, skipped_replied = _classify_needs_response_rows(
        rows,
        replied_ids=replied_ids,
        include_already_replied=include_already_replied,
        max_results=max_results,
    )

    if output_format == "json":
        return {
            "account": account,
            "mailbox": mailbox,
            "days_back": days_back,
            "max_results": max_results,
            "high_priority": high,
            "normal_priority": normal,
            "skipped_replied_count": skipped_replied,
            "errors": [],
        }

    return _format_needs_response_text(
        account=account,
        mailbox=mailbox,
        days_back=days_back,
        high=high,
        normal=normal,
        skipped_replied=skipped_replied,
        include_already_replied=include_already_replied,
    )


def _top_senders_error(
    message: str,
    *,
    output_format: str,
    account: str | None = None,
    mailbox: str | None = None,
    days_back: int | None = None,
    top_n: int | None = None,
    group_by_domain: bool | None = None,
    error_code: str = "error",
) -> str | dict[str, Any]:
    if output_format == "json":
        payload: dict[str, Any] = {
            "error": error_code,
            "errors": [message],
            "senders": [],
        }
        if account is not None:
            payload["account"] = account
        if mailbox is not None:
            payload["mailbox"] = mailbox
        if days_back is not None:
            payload["days_back"] = days_back
        if top_n is not None:
            payload["top_n"] = top_n
        if group_by_domain is not None:
            payload["group_by_domain"] = group_by_domain
        return payload
    return f"Error: {message}"


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def get_top_senders(
    account: str | None = None,
    mailbox: str = "INBOX",
    days_back: int = 30,
    top_n: int = 10,
    group_by_domain: bool = False,
    output_format: str = "text",
    timeout: int | None = None,
) -> str | dict[str, Any]:
    """Analyse a mailbox to find the most frequent senders.

    Useful for identifying key contacts, high-volume senders to filter,
    or newsletter sources to unsubscribe from.

    Args:
        account: Account name (e.g., "Gmail", "Work", "Personal").
            Falls back to ``DEFAULT_MAIL_ACCOUNT`` env-configured account when None.
        mailbox: Mailbox to analyse (default: "INBOX")
        days_back: How many days back to look (default: 30). ``0`` (all time)
            is no longer accepted — pass ``days_back=7`` or ``30`` and route
            unbounded sweeps through ``full_inbox_export``.
        top_n: Number of top senders to return (default: 10)
        group_by_domain: Group results by domain instead of individual sender (default: False)
        output_format: ``"text"`` (default, human-readable) or ``"json"``
            (structured dict with stable keys + ``errors[]``).
        timeout: Optional AppleScript timeout in seconds. Defaults to 120s.

    Returns:
        Ranked list of senders (or domains) with email counts. Text by
        default; a dict with keys ``{account, mailbox, days_back, top_n,
        group_by_domain, senders, total_analysed, mailbox_count,
        unique_senders, scan_cap, errors}`` when ``output_format='json'``.
    """
    if output_format not in {"text", "json"}:
        return _top_senders_error(
            f"invalid output_format: {output_format!r} (expected 'text' or 'json')",
            output_format="text",
        )

    if days_back <= 0:
        err = ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=("get_top_senders refuses to scan without days_back; pass days_back=7 or 30"),
            remediation={
                "preferred": "Pass days_back=7 or 30",
                "fallback_tool": "full_inbox_export",
                "fallback_tool_args": {
                    "account": account,
                    "mailbox": mailbox,
                },
            },
        )
        if output_format == "json":
            payload = err.to_dict()
            payload.setdefault("errors", [])
            payload["senders"] = []
            return payload
        return serialize_tool_error(err)

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return _top_senders_error(
            "No account specified and DEFAULT_MAIL_ACCOUNT is not set",
            output_format=output_format,
            mailbox=mailbox,
            days_back=days_back,
            top_n=top_n,
            group_by_domain=group_by_domain,
            error_code="account_required",
        )

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = validate_account_name(account, timeout=validation_timeout)
    if account_err:
        if output_format == "json":
            return {
                "error": "account_not_found",
                "errors": [account_err],
                "account": account,
                "mailbox": mailbox,
                "days_back": days_back,
                "top_n": top_n,
                "group_by_domain": group_by_domain,
                "senders": [],
            }
        return account_err

    escaped_account = escape_applescript(account)
    escaped_mailbox = escape_applescript(mailbox)

    date_cutoff = date_cutoff_script(days_back, "cutoffDate")
    date_check = "if messageDate < cutoffDate then exit repeat" if days_back > 0 else ""

    # Cap message scan. Prefer a bounded newest-first slice + Python aggregation
    # over `whose` filters that materialize huge inboxes. ``INBOX_LONG`` (100)
    # is the centralized ceiling for the longest-window read tools.
    scan_cap = min(SCAN_BOUNDS["INBOX_LONG"], max(top_n * 5, 15))
    if days_back > 14:
        scan_cap = min(scan_cap, 25)
    if days_back >= 30:
        scan_cap = min(scan_cap, 15)

    # Build the extraction key: either full sender or domain.
    if group_by_domain:
        # Extract domain from email address
        extract_key = """
                            -- Extract domain from sender address
                            set senderKey to ""
                            set atPos to 0
                            set senderLen to length of messageSender
                            repeat with i from 1 to senderLen
                                if character i of messageSender is "@" then
                                    set atPos to i
                                end if
                            end repeat
                            if atPos > 0 then
                                -- Find the closing > if present
                                set endPos to senderLen
                                repeat with i from atPos to senderLen
                                    if character i of messageSender is ">" then
                                        set endPos to i - 1
                                        exit repeat
                                    end if
                                end repeat
                                set senderKey to text (atPos + 1) thru endPos of messageSender
                            else
                                set senderKey to messageSender
                            end if
"""
        title_label = "TOP SENDER DOMAINS"
    else:
        extract_key = """
                            set senderKey to messageSender
"""
        title_label = "TOP SENDERS"

    # Return one ROW|||sender per message; Python aggregates with Counter.
    script = f'''
    tell application "Mail"
        try
            set targetAccount to account "{escaped_account}"

            -- Get target mailbox
            try
                set targetMailbox to mailbox "{escaped_mailbox}" of targetAccount
            on error
                if "{escaped_mailbox}" is "INBOX" then
                    set targetMailbox to mailbox "Inbox" of targetAccount
                else
                    error "Mailbox not found: {escaped_mailbox}"
                end if
            end try

            {date_cutoff}

            set mailboxMessages to {{}}
            set mailboxCount to count of messages of targetMailbox
            if mailboxCount > {scan_cap} then
                set mailboxUpperBound to {scan_cap}
            else
                set mailboxUpperBound to mailboxCount
            end if
            if mailboxUpperBound > 0 then
                set mailboxMessages to messages 1 thru mailboxUpperBound of targetMailbox
            end if

            set outputLines to {{}}
            set totalAnalysed to 0

            repeat with aMessage in mailboxMessages
                try
                    set messageDate to date received of aMessage
                    {date_check}

                    set messageSender to sender of aMessage
                    set totalAnalysed to totalAnalysed + 1

                    {extract_key}

                    set end of outputLines to "ROW|||" & senderKey
                end try
            end repeat

            set end of outputLines to "TOTAL|||" & (totalAnalysed as string)
            set end of outputLines to "MAILBOX_COUNT|||" & (mailboxCount as string)

            set AppleScript's text item delimiters to linefeed
            set outputText to outputLines as string
            set AppleScript's text item delimiters to ""
            return outputText

        on error errMsg
            return "ERROR|||" & errMsg
        end try
    end tell
    '''

    try:
        raw = run_applescript(script, timeout=timeout)
    except AppleScriptTimeout:
        wait_s = timeout if timeout is not None else 120
        return _top_senders_error(
            f"get_top_senders timed out on account '{account}' after "
            f"{wait_s}s — try increasing timeout or reducing days_back",
            output_format=output_format,
            account=account,
            mailbox=mailbox,
            days_back=days_back,
            top_n=top_n,
            group_by_domain=group_by_domain,
            error_code="timeout",
        )

    if raw.startswith("ERROR|||"):
        return _top_senders_error(
            raw.split("|||", 1)[1],
            output_format=output_format,
            account=account,
            mailbox=mailbox,
            days_back=days_back,
            top_n=top_n,
            group_by_domain=group_by_domain,
            error_code="applescript_error",
        )

    # Parse ROW lines and aggregate in Python (fast Counter vs AppleScript O(n^2)).
    total_analysed = 0
    mailbox_count = 0
    sender_counts: Counter[str] = Counter()
    for line in raw.splitlines():
        if line.startswith("TOTAL|||"):
            try:
                total_analysed = int(line.split("|||", 1)[1].strip())
            except ValueError:
                total_analysed = 0
        elif line.startswith("MAILBOX_COUNT|||"):
            try:
                mailbox_count = int(line.split("|||", 1)[1].strip())
            except ValueError:
                mailbox_count = 0
        elif line.startswith("ROW|||"):
            key = line.split("|||", 1)[1].strip()
            if key:
                sender_counts[key] += 1

    unique_count = len(sender_counts)
    top_entries = sender_counts.most_common(top_n)

    if output_format == "json":
        sender_records: list[dict[str, Any]] = []
        for key, cnt in top_entries:
            entry: dict[str, Any] = {
                "key": key,
                "count": cnt,
            }
            if total_analysed > 0:
                entry["percent"] = round((cnt / total_analysed) * 100)
            sender_records.append(entry)
        return {
            "account": account,
            "mailbox": mailbox,
            "days_back": days_back,
            "top_n": top_n,
            "group_by_domain": group_by_domain,
            "senders": sender_records,
            "total_analysed": total_analysed,
            "mailbox_count": mailbox_count,
            "unique_senders": unique_count,
            "scan_cap": scan_cap,
            "errors": [],
        }

    lines = [
        title_label,
        f"Account: {account} | Mailbox: {mailbox} | Last {days_back} days",
        "========================================",
        "",
    ]
    for i, (key, cnt) in enumerate(top_entries, start=1):
        if total_analysed > 0:
            pct = round((cnt / total_analysed) * 100)
            pct_text = f" ({pct}%)"
        else:
            pct_text = ""
        lines.append(f"{i}. {key}: {cnt} emails{pct_text}")

    lines.append("")
    lines.append("========================================")
    lines.append(f"Total emails analysed: {total_analysed}")
    if mailbox_count > 0 and scan_cap < mailbox_count:
        lines.append(
            f"Note: analysed {total_analysed} of {mailbox_count} messages (capped at {scan_cap} — "
            "increase days_back or use full_inbox_export for a complete count)"
        )
    lines.append(f"Unique senders: {unique_count}")

    return "\n".join(lines) + "\n"
