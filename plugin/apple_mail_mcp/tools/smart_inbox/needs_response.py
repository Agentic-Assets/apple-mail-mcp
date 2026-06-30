"""``get_needs_response`` tool: unread-emails-likely-needing-reply heuristic.

Filters newsletters/automated senders, labels priority, and joins against the
replied-detection set. Patched names (``run_applescript``, ``validate_account_name``)
are referenced via the ``smart_inbox`` package facade so the test seams keep firing;
``fetch_replied_ids`` is invoked with ``runner=smart_inbox.run_applescript``.
"""

from dataclasses import dataclass
from typing import Any

from apple_mail_mcp import server as _server
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
    inject_preferences,
    sanitize_pipe_delimited_field,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import smart_inbox
from apple_mail_mcp.tools.smart_inbox.helpers import _normalize_message_id


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
                            {sanitize_pipe_delimited_field("messageSubject")}
                            {sanitize_pipe_delimited_field("messageSender")}

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
    account_err = smart_inbox.validate_account_name(account, timeout=validation_timeout)
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
        inbox_raw = smart_inbox.run_applescript(inbox_script, timeout=inbox_timeout)
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
            runner=smart_inbox.run_applescript,
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
