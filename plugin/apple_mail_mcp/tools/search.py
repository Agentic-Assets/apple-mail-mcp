"""Search tools: finding and filtering emails."""

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from apple_mail_mcp import server as _server
from apple_mail_mcp.applescript_snippets import recipient_addresses_block, sanitize_field_handler, thread_headers_block
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS, build_whose_id_list, compute_scan_upper_bound, iter_id_chunks
from apple_mail_mcp.constants import SCAN_BOUNDS, THREAD_PREFIXES
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    account_not_found_json,
    build_mailbox_ref,
    escape_applescript,
    inject_preferences,
    list_mail_account_names,
    normalize_message_ids,
    normalize_search_terms,
    run_applescript,
    validate_account_name,
)
from apple_mail_mcp.core import (
    fetch_replied_ids as _core_fetch_replied_ids,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp


def fetch_replied_ids(account: str, sent_cap: int = 200, timeout: int | None = 60) -> set[str]:
    """Fetch replied Message-ID set using this module's ``run_applescript``.

    Wraps the core helper so tests that patch
    ``apple_mail_mcp.tools.search.run_applescript`` also cover the
    Sent-mailbox probe.
    """
    return _core_fetch_replied_ids(account, sent_cap=sent_cap, timeout=timeout, runner=run_applescript)


MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _build_applescript_date(var_name: str, date_value: str | None, end_of_day: bool = False) -> str:
    """Build AppleScript to create a date from an ISO day string."""
    if not date_value:
        return ""

    try:
        parsed_date = datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid date '{date_value}'. Use YYYY-MM-DD") from exc

    month_name = MONTH_NAMES[parsed_date.month - 1]
    seconds = 86399 if end_of_day else 0
    return f"""
                set {var_name} to current date
                set year of {var_name} to {parsed_date.year}
                set month of {var_name} to {month_name}
                set day of {var_name} to {parsed_date.day}
                set time of {var_name} to {seconds}
    """


_ERROR_MAILBOX_PREFIX = "ERROR_MAILBOX|||"


def _parse_search_records(
    output: str,
) -> "tuple[list[dict[str, Any]], list[dict[str, str]]]":
    """Parse structured search output into (records, mailbox_errors).

    Each *mailbox_errors* entry is a dict with keys ``mailbox`` and ``message``
    for mailboxes that emitted an ``ERROR_MAILBOX|||`` marker line.
    """
    if not output:
        return [], []

    records = []
    mailbox_errors: list[dict[str, str]] = []
    for line in output.splitlines():
        if line.startswith(_ERROR_MAILBOX_PREFIX):
            tail = line[len(_ERROR_MAILBOX_PREFIX) :]
            mb, _, msg = tail.partition("|||")
            mailbox_errors.append({"mailbox": mb.strip(), "message": msg.strip()})
            continue
        parts = line.split("|||", 13)
        if len(parts) < 8:
            continue

        internet_message_id = parts[1].strip()
        record = {
            "message_id": parts[0].strip(),
            "internet_message_id": internet_message_id,
            "subject": parts[2].strip(),
            "sender": parts[3].strip(),
            "mailbox": parts[4].strip(),
            "account": parts[5].strip(),
            "is_read": parts[6].strip().lower() == "true",
            "received_date": parts[7].strip(),
        }
        if internet_message_id:
            # Apple Mail requires: message:// scheme, angle brackets (percent-encoded),
            # and raw @ in the Message-ID. Normalize ID in case angle brackets are
            # present or missing (AppleScript returns both forms).
            msg_id = internet_message_id.strip("<>")
            record["mail_link"] = f"message://%3C{quote(msg_id, safe='@')}%3E"
        # Optional trailing fields, set only when present and non-empty.
        optional_fields = (
            (8, "content_preview"),
            (9, "to"),
            (10, "cc"),
            (11, "in_reply_to"),
            (12, "references"),
            (13, "bcc"),
        )
        for idx, key in optional_fields:
            if len(parts) > idx and parts[idx].strip():
                record[key] = parts[idx].strip()
        records.append(record)

    return records, mailbox_errors


def _sort_search_records(records: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    """Sort records by received date."""
    reverse = sort == "date_desc"
    return sorted(records, key=lambda item: item.get("received_date", ""), reverse=reverse)


def _format_search_records_text(
    records: list[dict[str, Any]],
    subject_only: bool = False,
    errors: list[str] | None = None,
    error_details: list[dict[str, str]] | None = None,
    recent_days_applied: float | None = None,
) -> str:
    """Format search records as human-readable text."""
    lines = []

    if subject_only:
        lines.append("SUBJECT SEARCH RESULTS")
        lines.append("")
        for item in records:
            lines.append(f"- {item['subject']}")
    else:
        lines.append("SEARCH RESULTS")
        if recent_days_applied is not None:
            if recent_days_applied <= 0:
                lines.append("Window: full inbox")
            elif recent_days_applied == 2.0:
                lines.append("Window: last 48h")
            else:
                lines.append(f"Window: last {recent_days_applied}d")
        lines.append("")
        for item in records:
            indicator = "✓" if item["is_read"] else "✉"
            replied_prefix = "[REPLIED] " if item.get("already_replied") else ""
            lines.append(f"{indicator} {replied_prefix}{item['subject']}")
            lines.append(f"   From: {item['sender']}")
            lines.append(f"   Date: {item['received_date']}")
            lines.append(f"   Mailbox: {item['mailbox']}")
            if item.get("mail_link"):
                lines.append(f"   Link: {item['mail_link']}")
            if item.get("content_preview"):
                lines.append(f"   Content: {item['content_preview']}")
            lines.append("")

    lines.append("========================================")
    lines.append(f"FOUND: {len(records)} matching email(s)")
    if errors:
        if error_details:
            detail_text = "; ".join(f"{item['account']} ({item['type']}: {item['message']})" for item in error_details)
            lines.append(f"PARTIAL: {len(errors)} account issue(s): {detail_text}")
        else:
            lines.append(f"PARTIAL: {len(errors)} account issue(s): {', '.join(errors)}")
    lines.append("========================================")
    return "\n".join(lines)


SENDER_ONLY_SEARCH_HINT = (
    "sender-only search can be slow on large mailboxes; add subject_keyword, "
    "date_from, has_attachments, or body_text (with allow_body_scan=True) to narrow the scan"
)
CONTENT_PREVIEW_SEARCH_HINT = (
    "include_content=True adds body previews to results and can be slower or expose more message text; "
    "leave it false for discovery, then fetch exact messages by id"
)
BODY_TEXT_SEARCH_HINT = (
    "body_text scans message bodies and can be slow or broad; keep account, date, subject, and limit filters tight"
)


def _body_scan_disabled_error() -> str:
    """Structured error when body_text is set without allow_body_scan opt-in."""
    tool_error = ToolError(
        code="BODY_SCAN_DISABLED",
        message=(
            "search_emails refuses body_text scans without allow_body_scan=True; "
            "body scans are O(N × message-size) on large mailboxes"
        ),
        remediation={
            "preferred": ("Narrow with subject_keyword, sender, date_from, or has_attachments instead"),
            "escape_hatch": "allow_body_scan=True (slow; pair with tight date_from)",
        },
    )
    return serialize_tool_error(tool_error)


def _build_search_response(
    records: list[dict[str, Any]],
    offset: int,
    limit: int,
    sort: str,
    output_format: str,
    subject_only: bool = False,
    errors: list[str] | None = None,
    error_details: list[dict[str, str]] | None = None,
    recent_days_applied: float | None = None,
    searched_from: str | None = None,
    body_search_capped: bool = False,
    mailbox_count_capped: bool = False,
    mailboxes_truncated: bool = False,
    sender_only_hint: bool = False,
    include_content_hint: bool = False,
    body_text_hint: bool = False,
) -> str:
    """Return either JSON or text for search results."""
    sorted_records = _sort_search_records(records, sort)
    has_more = len(sorted_records) > limit
    items = sorted_records[:limit]
    next_offset = offset + len(items) if has_more else None

    _max_mb_all = SCAN_BOUNDS["MAX_MAILBOXES_PER_SEARCH_ALL"]
    if output_format == "json":
        payload: dict[str, Any] = {
            "items": items,
            "offset": offset,
            "limit": limit,
            "returned": len(items),
            "has_more": has_more,
            "next_offset": next_offset,
            "sort": sort,
            "recent_days_applied": recent_days_applied if recent_days_applied is not None else 0.0,
            "searched_from": searched_from,
        }
        if body_search_capped:
            payload["body_search_capped"] = True
            _body_cap = SCAN_BOUNDS["BODY_SEARCH_AUTO_CAP"]
            payload["body_search_cap_warning"] = (
                f"body_text scan was capped at {_body_cap} messages because no explicit date_from "
                "was supplied. Pass date_from='YYYY-MM-DD' to search a larger window."
            )
        if mailboxes_truncated:
            payload["mailboxes_truncated"] = True
        if mailbox_count_capped:
            payload.setdefault("warnings", []).append(
                f"mailbox='All' search was capped at {_max_mb_all} mailboxes per account "
                "(SCAN_BOUNDS['MAX_MAILBOXES_PER_SEARCH_ALL']). Accounts with more than "
                f"{_max_mb_all} labels/folders (e.g. Gmail with 200+ labels) may have "
                "incomplete results. Pass mailbox='INBOX' or a specific folder name "
                "for a complete search."
            )
        if sender_only_hint:
            payload.setdefault("warnings", []).append(SENDER_ONLY_SEARCH_HINT)
        if include_content_hint:
            payload.setdefault("warnings", []).append(CONTENT_PREVIEW_SEARCH_HINT)
        if body_text_hint:
            payload.setdefault("warnings", []).append(BODY_TEXT_SEARCH_HINT)
        if errors:
            payload["errors"] = errors
        if error_details:
            payload["error_details"] = error_details
        return json.dumps(payload)

    text_result = _format_search_records_text(
        items,
        subject_only=subject_only,
        errors=errors,
        error_details=error_details,
        recent_days_applied=recent_days_applied,
    )
    if body_search_capped:
        _body_cap = SCAN_BOUNDS["BODY_SEARCH_AUTO_CAP"]
        warning = (
            f"WARNING: body_text scan capped at {_body_cap} messages (no explicit date_from). "
            "Pass date_from='YYYY-MM-DD' to search a larger window.\n"
        )
        text_result = warning + text_result
    if mailbox_count_capped:
        mb_warning = (
            f"WARNING: mailbox='All' search capped at {_max_mb_all} mailboxes per account. "
            "Accounts with many labels (e.g. Gmail 200+ labels) may have incomplete results.\n"
        )
        text_result = mb_warning + text_result
    if sender_only_hint:
        text_result = f"WARNING: {SENDER_ONLY_SEARCH_HINT}\n" + text_result
    if include_content_hint:
        text_result = f"WARNING: {CONTENT_PREVIEW_SEARCH_HINT}\n" + text_result
    if body_text_hint:
        text_result = f"WARNING: {BODY_TEXT_SEARCH_HINT}\n" + text_result
    return text_result


def _search_error_detail(account: str, exc: Exception) -> dict[str, str]:
    if isinstance(exc, AppleScriptTimeout):
        return {"account": account, "type": "timeout", "message": str(exc)}
    return {
        "account": account,
        "type": exc.__class__.__name__,
        "message": str(exc),
    }


def _build_search_script(
    account: str,
    mailbox: str,
    subject_terms: list[str] | None,
    sender: str | None,
    has_attachments: bool | None,
    read_status: str,
    date_from: str | None,
    date_to: str | None,
    include_content: bool,
    content_length: int,
    offset: int,
    limit: int,
    body_text: str | None,
    sender_exact: str | None = None,
    sender_domain: str | None = None,
    internet_message_id: str | None = None,
    recent_days: float = 0.0,
    timeout: int | None = None,
    date_from_explicit: bool = False,
    mailboxes: list[str] | None = None,
) -> tuple[str, bool, bool]:
    """Build the AppleScript for a single account's search.

    The script caps message collection inside AppleScript via either a
    ``whose`` clause sliced down to ``items 1 thru collectLimit`` or a
    ``messages 1 thru collectLimit`` bound directly, so we never materialize
    the full message list of a large (10K+) mailbox.

    Scan-cap scales with ``recent_days`` so that narrow filters (sender,
    subject_terms) over a wider date window actually inspect a meaningful
    portion of that window — otherwise a 7-day query with default limit=20
    would only inspect the 21 newest messages and silently miss matches
    further back. Floor stays at ``collect_limit + offset`` and ceiling
    caps at ``SEARCH_WINDOW_CAP`` to keep Mail bounded on remote IMAP/Exchange mailboxes.

    Performance guidance — body_text:
        When ``body_text`` is set without an explicit ``date_from``, the
        scan_cap is further capped at ``BODY_SEARCH_AUTO_CAP`` to prevent hundreds of cold-cache
        IMAP body fetches (each can take ~1s on large Exchange inboxes).
        When the caller passes an explicit ``date_from`` (``date_from_explicit=True``),
        the cap is left as-is because the caller has explicitly bounded the window.
        A ``body_search_capped`` key is returned in structured responses when the
        cap fires to help callers understand why results may be incomplete.
    """
    escaped_sender = escape_applescript(sender) if sender else None
    escaped_sender_exact = escape_applescript(sender_exact.strip()) if sender_exact and sender_exact.strip() else None
    escaped_sender_domain = (
        escape_applescript(sender_domain.strip().lstrip("@")) if sender_domain and sender_domain.strip() else None
    )
    normalized_internet_message_id = ""
    if internet_message_id and internet_message_id.strip():
        normalized_internet_message_id = internet_message_id.strip()
        if not normalized_internet_message_id.startswith("<"):
            normalized_internet_message_id = "<" + normalized_internet_message_id
        if not normalized_internet_message_id.endswith(">"):
            normalized_internet_message_id = normalized_internet_message_id + ">"
    escaped_internet_message_id = escape_applescript(normalized_internet_message_id)
    use_body_search = body_text is not None

    collect_limit = limit + 1  # +1 for has_more probe; offset is decremented separately
    base_cap = collect_limit + offset
    # Window cap from the shared bounded-scan helper (Phase A of the
    # whose-elimination refactor — see plugin/apple_mail_mcp/bounded_scan.py).
    # We floor at base_cap so callers paginating past the helper's cap still
    # get a slice large enough to honor their offset+limit.
    if recent_days and recent_days > 0:
        window_cap = compute_scan_upper_bound(recent_days)
        # Narrow header searches are usually "needle" lookups. On large
        # Exchange accounts, binding hundreds of recent messages just to prove
        # a no-hit subject does not exist can exceed wrapper timeouts. Keep the
        # scan bounded by the caller's requested page, and only widen as the
        # requested recent window widens.
        if (
            subject_terms
            and not sender
            and not sender_exact
            and not sender_domain
            and not internet_message_id
            and body_text is None
            and has_attachments is None
            and read_status == "all"
        ):
            scan_cap = base_cap
        else:
            scan_cap = max(base_cap, window_cap)
    else:
        scan_cap = base_cap

    # Body-search cap: reading ``content of aMessage`` for every candidate is
    # O(N × message-size) and triggers cold-cache IMAP fetches on large remote
    # mailboxes. When the caller has not explicitly bounded the window with
    # ``date_from``, cap at 100 to keep wall time reasonable. When an explicit
    # ``date_from`` is passed the caller has intentionally bounded the scan, so
    # we leave the cap as-is.
    BODY_SEARCH_AUTO_CAP = SCAN_BOUNDS["BODY_SEARCH_AUTO_CAP"]
    body_search_capped = False
    if use_body_search and not date_from_explicit and scan_cap > BODY_SEARCH_AUTO_CAP:
        scan_cap = min(scan_cap, BODY_SEARCH_AUTO_CAP)
        body_search_capped = True

    # Track whether the mailbox-count cap is active (mailbox="All" path).
    # The AppleScript guard caps at MAX_MAILBOXES_PER_SEARCH; we surface this
    # to callers via a warnings field so they know results may be incomplete on
    # accounts with many labels (e.g. Gmail with 200+ labels).
    mailbox_count_capped = mailbox == "All"

    bounded_candidate_script = f"""
                            set matchingMessages to {{}}
                            set candidateMessages to {{}}
                            set scanUpperBound to {scan_cap}
                            try
                                set candidateMessages to messages 1 thru scanUpperBound of currentMailbox
                            on error
                                try
                                    set candidateMessages to messages of currentMailbox
                                end try
                            end try
    """

    _max_mailboxes_per_search = (
        SCAN_BOUNDS["MAX_MAILBOXES_PER_SEARCH_ALL"] if mailbox == "All" else SCAN_BOUNDS["MAX_MAILBOXES_PER_SEARCH"]
    )
    if mailboxes:
        # Explicit mailbox list: look up each named folder, degrade gracefully
        # if a name doesn't exist (emits ERROR_MAILBOX instead of hard failure).
        mailbox_lookups = "\n".join(
            f"""                try
                    set end of searchMailboxes to mailbox "{escape_applescript(mb)}" of targetAccount
                on error
                    set end of recordLines to "ERROR_MAILBOX|||{escape_applescript(mb)}|||mailbox not found"
                end try"""
            for mb in mailboxes
        )
        mailbox_script = f"""
                set searchMailboxes to {{}}
{mailbox_lookups}
        """
        skip_script = ""
    elif mailbox == "All":
        mailbox_script = f"""
                set searchMailboxes to every mailbox of targetAccount
                if (count of searchMailboxes) > {_max_mailboxes_per_search} then
                    set searchMailboxes to items 1 thru {_max_mailboxes_per_search} of searchMailboxes
                end if
        """
        skip_script = """
                        set skipFolders to {"Trash", "Junk", "Junk Email", "Deleted Items", "Sent", "Sent Items", "Sent Messages", "Drafts", "Spam", "Deleted Messages"}
                        repeat with skipFolder in skipFolders
                            if mailboxName is skipFolder then
                                set shouldSkip to true
                                exit repeat
                            end if
                        end repeat
        """
    else:
        _mailbox_resolve = build_mailbox_ref(mailbox, account_var="targetAccount", var_name="searchMailbox")
        mailbox_script = f"""
                {_mailbox_resolve}
                set searchMailboxes to {{searchMailbox}}
        """
        skip_script = ""

    date_setup = _build_applescript_date("fromDate", date_from)
    date_setup += _build_applescript_date("toDate", date_to, end_of_day=True)

    escaped_account = escape_applescript(account)
    account_setup = f'''
                set searchAccounts to {{account "{escaped_account}"}}
        '''

    # Build per-message filter block. Avoid broad `every message ... whose`
    # filters because Mail.app can materialize remote mailboxes before applying
    # them. We bind a bounded newest-first slice, then filter in that slice.
    #
    # Date lower bounds need a special fast path: Exchange inboxes can have
    # tens of thousands of messages, and even a bounded 300-message slice is
    # too slow if we read subject/sender/body for every no-hit query. Mail
    # returns mailbox messages newest-first, so once a message is older than
    # fromDate the rest of the slice is outside the requested window.
    early_date_break = "if messageDate < fromDate then exit repeat" if date_from and not date_to else ""
    escaped_body = escape_applescript(body_text) if body_text else ""
    per_msg_conditions: list[str] = []
    if subject_terms:
        subject_checks = " or ".join(f'messageSubject contains "{escape_applescript(t)}"' for t in subject_terms)
        per_msg_conditions.append(f"({subject_checks})")
        candidate_subject_checks = " or ".join(f'subject contains "{escape_applescript(t)}"' for t in subject_terms)
    else:
        candidate_subject_checks = ""
    if sender:
        per_msg_conditions.append(f'messageSender contains "{escaped_sender}"')
    if escaped_sender_exact:
        per_msg_conditions.append(
            f'(messageSender is "{escaped_sender_exact}" or messageSender contains "<{escaped_sender_exact}>")'
        )
    if escaped_sender_domain:
        per_msg_conditions.append(f'messageSender contains "@{escaped_sender_domain}"')
    if escaped_internet_message_id:
        per_msg_conditions.append(
            f'(internetMessageId is "{escaped_internet_message_id}" or internetMessageId is "{escaped_internet_message_id.strip("<>")}")'
        )
    if read_status == "read":
        per_msg_conditions.append("messageRead is true")
    elif read_status == "unread":
        per_msg_conditions.append("messageRead is false")
    if date_from:
        per_msg_conditions.append("messageDate >= fromDate")
    if date_to:
        per_msg_conditions.append("messageDate <= toDate")
    if has_attachments is True:
        per_msg_conditions.append("(count of mail attachments of aMessage) > 0")
    elif has_attachments is False:
        per_msg_conditions.append("(count of mail attachments of aMessage) = 0")
    if use_body_search:
        per_msg_conditions.append(f'msgContent contains "{escaped_body}"')

    if (
        subject_terms
        and not sender
        and not sender_exact
        and not sender_domain
        and not internet_message_id
        and has_attachments is None
        and read_status == "all"
        and not use_body_search
        and not date_to
    ):
        # Fast no-hit/needle path: filter the already-bounded newest slice
        # with the cheapest possible per-message reads. Avoid `whose` here:
        # AppleScript does not reliably apply it to a list of message objects
        # returned by `messages 1 thru N`. The slice is deliberately tiny for
        # default subject lookups, so a subject-only loop is fast and avoids
        # date/sender/read-status/body fetches on large Exchange inboxes.
        message_collection = f"""
                                {bounded_candidate_script}
                            ignoring case
                                repeat with aMessage in candidateMessages
                                    if (count of matchingMessages) >= {scan_cap} then exit repeat
                                    try
                                        set messageSubject to subject of aMessage
                                        if {candidate_subject_checks} then
                                            set end of matchingMessages to aMessage
                                        end if
                                    end try
                                end repeat
                            end ignoring
        """
    elif per_msg_conditions:
        combined_condition = " and ".join(per_msg_conditions)
        content_read_block = (
            """
                                        set msgContent to ""
                                        try
                                            set msgContent to content of aMessage
                                        end try
        """
            if use_body_search
            else ""
        )
        message_collection = f"""
                                {bounded_candidate_script}
                            ignoring case
                                repeat with aMessage in candidateMessages
                                    if (count of matchingMessages) >= {scan_cap} then exit repeat
                                    try
                                        set messageDate to date received of aMessage
                                        {early_date_break}
                                        set messageSubject to subject of aMessage
                                        set messageSender to sender of aMessage
                                        set internetMessageId to ""
                                        try
                                            set internetMessageId to message id of aMessage
                                        end try
                                        set messageRead to read status of aMessage
                                        {content_read_block}
                                        if {combined_condition} then
                                            set end of matchingMessages to aMessage
                                        end if
                                    end try
                                end repeat
                            end ignoring
        """
    else:
        message_collection = f"""
                                {bounded_candidate_script}
                            repeat with aMessage in candidateMessages
                                try
                                    set messageDate to date received of aMessage
                                    {early_date_break}
                                    set end of matchingMessages to aMessage
                                end try
                            end repeat
        """

    # Template the inner AppleScript timeout from the same value the outer
    # run_applescript wrapper will use, minus 10 s so the AS timeout fires
    # before SIGKILL and Mail.app can clean up gracefully. Floor at 30 s to
    # keep the script meaningful on very tight timeouts.
    inner_timeout = max(30, (timeout if timeout is not None else 180) - 10)

    script = f"""
    on sanitize_field(value)
        try
            set valueText to value as string
        on error
            set valueText to ""
        end try

        set AppleScript's text item delimiters to {{return, linefeed, tab}}
        set valueParts to text items of valueText
        set AppleScript's text item delimiters to " "
        set valueText to valueParts as string
        set AppleScript's text item delimiters to "|||"
        set valueParts to text items of valueText
        set AppleScript's text item delimiters to " | "
        set valueText to valueParts as string
        set AppleScript's text item delimiters to ""
        return valueText
    end sanitize_field

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
        with timeout of {inner_timeout} seconds
            try
                set recordLines to {{}}
                set offsetRemaining to {offset}
                set collectLimit to {collect_limit}
                {date_setup}
                {account_setup}

                repeat with targetAccount in searchAccounts
                    if collectLimit <= 0 then exit repeat
                    set accountName to my sanitize_field(name of targetAccount)
                    {mailbox_script}

                    repeat with currentMailbox in searchMailboxes
                        if collectLimit <= 0 then exit repeat

                        -- NB: do NOT wrap this per-mailbox scan in `with timeout`.
                        -- Materializing `messages 1 thru N` on a 24K+ Exchange
                        -- mailbox routinely exceeds a short timeout; the inner
                        -- candidate-fetch try/catch then swallows the timeout and
                        -- the mailbox silently returns 0 rows. Per-mailbox failures
                        -- are already isolated by the `on error -> ERROR_MAILBOX`
                        -- handler below, and the whole call is bounded by the
                        -- outer call-level timeout budget ({inner_timeout}s).
                        try
                            set mailboxName to my sanitize_field(name of currentMailbox)
                            set shouldSkip to false
                            {skip_script}

                                if not shouldSkip then
                                    {message_collection}
                                    set matchingCount to count of matchingMessages

                                    if offsetRemaining >= matchingCount then
                                        set offsetRemaining to offsetRemaining - matchingCount
                                    else
                                        set startIndex to offsetRemaining + 1
                                        set availableCount to matchingCount - offsetRemaining
                                        if availableCount > collectLimit then
                                            set endIndex to startIndex + collectLimit - 1
                                        else
                                            set endIndex to startIndex + availableCount - 1
                                        end if

                                        if endIndex >= startIndex then
                                            set targetMessages to items startIndex thru endIndex of matchingMessages

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
                                                    set contentPreview to ""
                                                    -- Recipients (to/cc) are intentionally NOT resolved here.
                                                    -- Per-message `to recipients`/`address of` can HANG (not error,
                                                    -- so `on error` cannot catch it) on large remote Exchange/Gmail
                                                    -- mailboxes, blocking the whole bulk scan until timeout. Fetch
                                                    -- recipients per message via get_email_by_id instead (single,
                                                    -- bounded, fast). Emit empty placeholders to keep field alignment.
                                                    set toRecips to ""
                                                    set ccRecips to ""

                                                    if {str(include_content).lower()} then
                                                        try
                                                            set msgContent to content of aMessage
                                                            set AppleScript's text item delimiters to {{return, linefeed, tab}}
                                                            set contentParts to text items of msgContent
                                                            set AppleScript's text item delimiters to " "
                                                            set cleanText to contentParts as string
                                                            set AppleScript's text item delimiters to ""
                                                            if {content_length} > 0 and length of cleanText > {content_length} then
                                                                set contentPreview to my sanitize_field(text 1 thru {content_length} of cleanText & "...")
                                                            else
                                                                set contentPreview to my sanitize_field(cleanText)
                                                            end if
                                                        on error
                                                            set contentPreview to ""
                                                        end try
                                                    end if

                                                    set readValue to "false"
                                                    if messageRead then
                                                        set readValue to "true"
                                                    end if

                                                    set recordLine to messageId & "|||" & internetMessageId & "|||" & messageSubject & "|||" & messageSender & "|||" & mailboxName & "|||" & accountName & "|||" & readValue & "|||" & receivedAt & "|||" & contentPreview & "|||" & toRecips & "|||" & ccRecips & "|||" & "" & "|||" & "" & "|||" & ""
                                                    set end of recordLines to recordLine
                                                    set collectLimit to collectLimit - 1
                                                    if collectLimit <= 0 then exit repeat
                                                end try
                                            end repeat
                                        end if

                                        set offsetRemaining to 0
                                    end if
                                end if
                        on error errMsg
                            -- Emit a structured marker so Python can surface it
                            -- in error_details instead of silently discarding it.
                            set end of recordLines to "ERROR_MAILBOX|||" & (name of currentMailbox) & "|||" & errMsg
                        end try
                    end repeat
                end repeat

                if (count of recordLines) is 0 then
                    return ""
                end if

                set AppleScript's text item delimiters to linefeed
                set outputText to recordLines as string
                set AppleScript's text item delimiters to ""
                return outputText
            on error errMsg
                return "ERROR|||" & errMsg
            end try
        end timeout
    end tell
    """

    return script, body_search_capped, mailbox_count_capped


def _list_accounts_script() -> str:
    """Tiny AppleScript that returns one account name per line."""
    return """
    tell application "Mail"
        set acctNames to {}
        repeat with anAccount in (every account)
            set end of acctNames to (name of anAccount)
        end repeat
        set AppleScript's text item delimiters to linefeed
        return acctNames as string
    end tell
    """


def _list_mail_accounts(timeout: int | None = 30) -> list[str]:
    """Return the list of Mail account names. Cheap (<1s) on any setup."""
    raw = run_applescript(_list_accounts_script(), timeout=timeout)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _search_one_account(
    account: str,
    mailbox: str,
    subject_terms: list[str] | None,
    sender: str | None,
    sender_exact: str | None,
    sender_domain: str | None,
    internet_message_id: str | None,
    has_attachments: bool | None,
    read_status: str,
    date_from: str | None,
    date_to: str | None,
    include_content: bool,
    content_length: int,
    offset: int,
    limit: int,
    body_text: str | None,
    timeout: int | None,
    recent_days: float = 0.0,
    date_from_explicit: bool = False,
    mailboxes: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], bool, bool]:
    """Run the search AppleScript for a single account synchronously.

    Returns (records, mailbox_errors, body_search_capped, mailbox_count_capped).
    *mailbox_errors* is a list of dicts with ``mailbox`` and ``message`` keys for
    Exchange mailboxes that could not be searched (e.g. restricted folders).
    Callers surface these via ``error_details`` so agents know which mailboxes were
    skipped.
    *body_search_capped* is True when the body-search auto-cap fired (100 messages
    when no explicit date_from was supplied).
    *mailbox_count_capped* is True when mailbox="All" and the AppleScript guard
    capped the search at MAX_MAILBOXES_PER_SEARCH mailboxes.
    """
    script, body_search_capped, mailbox_count_capped = _build_search_script(
        account=account,
        mailbox=mailbox,
        subject_terms=subject_terms,
        sender=sender,
        sender_exact=sender_exact,
        sender_domain=sender_domain,
        internet_message_id=internet_message_id,
        has_attachments=has_attachments,
        read_status=read_status,
        date_from=date_from,
        date_to=date_to,
        include_content=include_content,
        content_length=content_length,
        offset=offset,
        limit=limit,
        body_text=body_text,
        recent_days=recent_days,
        timeout=timeout,
        date_from_explicit=date_from_explicit,
        mailboxes=mailboxes,
    )
    result = run_applescript(script, timeout=timeout if timeout is not None else 180)
    if result.startswith("ERROR|||"):
        raise ValueError(result.split("|||", 1)[1])
    records, mailbox_errors = _parse_search_records(result)
    return records, mailbox_errors, body_search_capped, mailbox_count_capped


async def _search_mail_records(
    account: str | None = None,
    mailbox: str = "INBOX",
    subject_terms: list[str] | None = None,
    sender: str | None = None,
    sender_exact: str | None = None,
    sender_domain: str | None = None,
    internet_message_id: str | None = None,
    has_attachments: bool | None = None,
    read_status: str = "all",
    date_from: str | None = None,
    date_to: str | None = None,
    include_content: bool = False,
    content_length: int = 300,
    offset: int = 0,
    limit: int = 100,
    sort: str = "date_desc",
    body_text: str | None = None,
    timeout: int | None = None,
    recent_days: float = 0.0,
    date_from_explicit: bool = False,
    mailboxes: list[str] | None = None,
) -> "tuple[list[dict[str, Any]], list[str], list[dict[str, str]], bool]":
    """Return (records, error_account_names, error_details, body_search_capped) from Apple Mail.

    When account is None, dispatches one AppleScript per account in parallel
    via ``asyncio.to_thread`` so wall time is bounded by the slowest single
    account rather than the sum. A per-account ``AppleScriptTimeout`` becomes
    an entry in the returned errors list — the call still returns whatever
    other accounts produced.

    ``body_search_capped`` is True when the body-search auto-cap (100 messages)
    fired because no explicit ``date_from`` was passed.
    """
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if limit <= 0:
        return [], [], [], False
    if sort not in {"date_desc", "date_asc"}:
        raise ValueError("Invalid sort. Use: date_desc, date_asc")
    if read_status not in {"all", "read", "unread"}:
        raise ValueError("Invalid read_status. Use: all, read, unread")

    # Single-account: short-circuit, no gather overhead.
    if account:
        try:
            records, mb_errors, body_capped, mb_count_capped = await asyncio.to_thread(
                _search_one_account,
                account,
                mailbox,
                subject_terms,
                sender,
                sender_exact,
                sender_domain,
                internet_message_id,
                has_attachments,
                read_status,
                date_from,
                date_to,
                include_content,
                content_length,
                offset,
                limit,
                body_text,
                timeout,
                recent_days,
                date_from_explicit,
                mailboxes,
            )
            mb_error_details = [
                {"account": account, "mailbox": e["mailbox"], "type": "mailbox_error", "message": e["message"]}
                for e in mb_errors
            ]
            return records, [], mb_error_details, body_capped
        except AppleScriptTimeout as exc:
            return [], [account], [_search_error_detail(account, exc)], False

    # Multi-account: fetch account list cheaply, then dispatch in parallel.
    try:
        accounts = await asyncio.to_thread(_list_mail_accounts, timeout)
    except AppleScriptTimeout as exc:
        raise ValueError("Mail account listing timed out") from exc

    if not accounts:
        return [], [], [], False

    async def run_one(acct: str) -> tuple[str, Any]:
        try:
            recs, mb_errs, body_capped, mb_count_capped = await asyncio.to_thread(
                _search_one_account,
                acct,
                mailbox,
                subject_terms,
                sender,
                sender_exact,
                sender_domain,
                internet_message_id,
                has_attachments,
                read_status,
                date_from,
                date_to,
                include_content,
                content_length,
                offset,
                limit,
                body_text,
                timeout,
                recent_days,
                date_from_explicit,
                mailboxes,
            )
            return acct, (recs, mb_errs, body_capped, mb_count_capped)
        except AppleScriptTimeout:
            return acct, AppleScriptTimeout(acct)
        except Exception as exc:
            return acct, exc

    results = await asyncio.gather(*(run_one(acct) for acct in accounts))

    combined: list[dict[str, Any]] = []
    errors: list[str] = []
    error_details: list[dict[str, str]] = []
    any_body_capped = False
    for acct, outcome in results:
        if isinstance(outcome, Exception):
            errors.append(acct)
            error_details.append(_search_error_detail(acct, outcome))
        else:
            recs, mb_errs, body_capped, _mb_count_capped = outcome
            combined.extend(recs)
            if body_capped:
                any_body_capped = True
            for e in mb_errs:
                error_details.append(
                    {
                        "account": acct,
                        "mailbox": e["mailbox"],
                        "type": "mailbox_error",
                        "message": e["message"],
                    }
                )

    return combined, errors, error_details, any_body_capped


def _search_mail_records_sync(**kwargs: Any) -> list[dict[str, Any]]:
    """Synchronous bridge for sync tools (move_email, manage_trash,
    list_email_attachments) that need preflight records. Returns just the
    record list. When a per-account ``AppleScriptTimeout`` was caught
    inside the async helper, re-raise it here so sync callers can surface
    a structured "timed out" error rather than silently treating it as
    "no matches". Sync callers should pass an explicit ``account`` so this
    stays a single-account dispatch and avoids the multi-account gather
    path."""
    account = kwargs.get("account")
    if account:
        try:
            records, _mb_errors, _body_capped, _mb_count_capped = _search_one_account(
                account=account,
                mailbox=kwargs.get("mailbox", "INBOX"),
                subject_terms=kwargs.get("subject_terms"),
                sender=kwargs.get("sender"),
                sender_exact=kwargs.get("sender_exact"),
                sender_domain=kwargs.get("sender_domain"),
                internet_message_id=kwargs.get("internet_message_id"),
                has_attachments=kwargs.get("has_attachments"),
                read_status=kwargs.get("read_status", "all"),
                date_from=kwargs.get("date_from"),
                date_to=kwargs.get("date_to"),
                include_content=kwargs.get("include_content", False),
                content_length=kwargs.get("content_length", 300),
                offset=kwargs.get("offset", 0),
                limit=kwargs.get("limit", 100),
                body_text=kwargs.get("body_text"),
                timeout=kwargs.get("timeout"),
                recent_days=kwargs.get("recent_days", 0.0),
                date_from_explicit=kwargs.get("date_from_explicit", False),
                mailboxes=kwargs.get("mailboxes"),
            )
            return records
        except AppleScriptTimeout:
            raise

    records, errors, error_details, _body_capped = asyncio.run(_search_mail_records(**kwargs))
    if errors and not records:
        non_timeout = [item for item in error_details if item.get("type") != "timeout"]
        if non_timeout:
            detail = "; ".join(f"{item['account']}: {item['type']}: {item['message']}" for item in non_timeout)
            raise RuntimeError(f"AppleScript failed for account(s): {detail}")
        raise AppleScriptTimeout(f"AppleScript timed out for account(s): {', '.join(errors)}")
    return records


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
async def search_emails(
    account: str | None = None,
    all_accounts: bool = False,
    mailbox: str = "INBOX",
    subject_keyword: str | None = None,
    subject_keywords: list[str] | None = None,
    sender: str | None = None,
    sender_exact: str | None = None,
    sender_domain: str | None = None,
    internet_message_id: str | None = None,
    has_attachments: bool | None = None,
    read_status: str = "all",
    date_from: str | None = None,
    date_to: str | None = None,
    recent_days: float = 2.0,
    include_content: bool = False,
    max_content_length: int = 500,
    body_text: str | None = None,
    allow_body_scan: bool = False,
    max_results: int | None = 20,
    output_format: str = "text",
    offset: int = 0,
    limit: int | None = None,
    sort: str = "date_desc",
    exclude_replied: bool = False,
    flag_replied: bool = False,
    timeout: int | None = None,
    mailboxes: list[str] | None = None,
) -> str:
    """Defaults to the last 48 hours and the configured default account. Pass `recent_days=7` for the past week; ``recent_days=0`` without ``date_from`` is rejected — use ``full_inbox_export`` for audited full-mailbox sweeps.

    Unified search tool with JSON output, pagination, and real date filtering.

    Consolidates subject search, sender search, exact sender/domain discovery,
    body content search, exact Internet Message-ID lookup, and cross-account
    search into a single tool.

    Smart defaults:
        - When `date_from` is None and `recent_days > 0`, an effective window
          of `now - recent_days` days is applied. Unbounded scans
          (``recent_days=0`` without ``date_from``) are refused with an
          ``UNBOUNDED_SCAN_REQUIRED`` error — call ``full_inbox_export`` for
          the audited escape hatch. An explicit ``date_from`` always wins.
        - When `account` is None and `all_accounts` is False, the tool falls
          back to the ``DEFAULT_MAIL_ACCOUNT`` env-configured account if one
          is set. Pass `all_accounts=True` to opt back into multi-account
          dispatch even when a default is configured.
        - `recent_days` is applied BEFORE pagination, so `offset` counts
          within the windowed result set.

    Performance guidance (read before omitting filters on large mailboxes):
        - Multi-account search (account=None) on a 10K+ inbox can be slow.
          Prefer passing `account` plus `date_from` together when you know
          which mailbox the messages are in.
        - Setting `body_text` to any non-empty string scans message bodies
          (O(N × message-size)); pair with tight filters (account, date_from,
          subject_keyword) to keep wall time predictable on large mailboxes.
        - When account is None each account runs in parallel; one slow
          account no longer blocks the others, but its name will appear in
          the response's `errors` field (JSON) or partial banner (text).
          JSON also includes `error_details` when the failure reason is known.

    Args:
        account: Account name to search in (e.g., "Gmail", "Work").
            If None, searches ALL accounts in parallel (slower wall time on
            very large inboxes — prefer specifying account + date_from).
        mailbox: Mailbox to search (default: "INBOX", use "All" for all mailboxes, or specific folder name)
        subject_keyword: Optional keyword to search in subject
        subject_keywords: Optional list of subject keywords; matches any keyword
        sender: Optional fuzzy sender email or name to filter by
        sender_exact: Optional exact sender address discovery filter
        sender_domain: Optional exact sender domain discovery filter, with or without "@"
        internet_message_id: Optional exact Internet Message-ID discovery filter.
            Angle brackets are optional.
        has_attachments: Optional filter for emails with attachments (True/False/None)
        read_status: Filter by read status: "all", "read", "unread" (default: "all")
        date_from: Optional start date filter (format: "YYYY-MM-DD")
        date_to: Optional end date filter (format: "YYYY-MM-DD")
        include_content: Whether to include email content preview (slower)
        max_content_length: Maximum content length in characters when include_content=True (default: 500, 0 = unlimited)
        body_text: Optional[str] text to search for in email body content (case-insensitive).
            Setting `body_text` to any non-empty string scans message bodies
            (O(N × message-size)); pair with tight filters (account, date_from,
            subject_keyword) to keep wall time predictable on large mailboxes.
        allow_body_scan: Opt in to body_text scans (default False). When False,
            passing body_text returns a structured ``BODY_SCAN_DISABLED`` error.
        max_results: Backward-compatible alias for limit
        output_format: Output format: "text" or "json" (default: "text")
        offset: Number of matching results to skip before returning data
        limit: Maximum number of results to return per page
        sort: Result sort order: "date_desc" or "date_asc"
        exclude_replied: When True, filter out emails the user has already
            replied to (detected via Message-ID matching against Sent
            mailbox). Default False keeps backward-compatible behavior.
            When True, replied emails are removed before formatting, so
            ``flag_replied`` has no visible effect.
        flag_replied: When True (opt-in; default False) AND
            ``exclude_replied=False``, annotate already-replied emails —
            text mode prefixes the subject with ``[REPLIED] `` and JSON
            mode adds an ``already_replied: true`` field. Default False
            keeps the per-call cost low (no extra Sent-mailbox AppleScript
            probe); set True for safer agent workflows. Only matters when
            ``exclude_replied=False``.
        timeout: Optional per-account AppleScript timeout in seconds. Defaults
            to 180s. Raise this for known-slow accounts (e.g. large Exchange
            inboxes) when the default times out.
        mailboxes: Optional explicit list of folder names to search (e.g.
            ["Archive", "Sent"]). When provided and non-empty, overrides
            ``mailbox`` and searches only those named folders for the account.
            Missing folders emit a structured mailbox error and are skipped.

    Returns:
        Formatted list of matching emails or JSON payload with stable message
        metadata. When one or more accounts fail during a multi-account call,
        the response includes account names plus error details so the caller can
        retry timeout accounts or fix non-timeout failures.
    """
    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    if body_text and not allow_body_scan:
        return _body_scan_disabled_error()

    sender_only_hint = bool(
        sender
        and not sender_exact
        and not sender_domain
        and not subject_keyword
        and not subject_keywords
        and not internet_message_id
        and date_from is None
        and not body_text
        and has_attachments is None
    )

    if limit is None:
        limit = max_results if max_results is not None else 100

    effective_recent_days = float(recent_days) if recent_days else 0.0
    if date_from is None and effective_recent_days <= 0:
        tool_error = ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=(
                "search_emails refuses to scan without a date window or recent_days; pass recent_days=7 or date_from"
            ),
            remediation={
                "preferred": "Pass recent_days=7 or date_from='YYYY-MM-DD'",
                "fallback_tool": "full_inbox_export",
                "fallback_tool_args": {
                    "account": account or "<your account>",
                    "filter_subject": subject_keyword or (subject_keywords[0] if subject_keywords else None),
                },
            },
        )
        # Always emit the structured JSON envelope (with remediation) for
        # UNBOUNDED_SCAN_REQUIRED, even when output_format != "json".
        # Dropping the remediation would lose the caller's recovery path.
        return serialize_tool_error(tool_error)

    # Smart default: fall back to the configured default account when neither
    # `account` nor `all_accounts` is set. Lazy attribute read so tests can
    # monkeypatch `apple_mail_mcp.server.DEFAULT_MAIL_ACCOUNT` after import.
    if account is None and not all_accounts and _server.DEFAULT_MAIL_ACCOUNT:
        account = _server.DEFAULT_MAIL_ACCOUNT

    if account:
        validation_timeout = 30 if timeout is None else min(timeout, 30)
        account_err = validate_account_name(account, timeout=validation_timeout)
        if account_err:
            if output_format == "json":
                return json.dumps(
                    {
                        "results": [],
                        "total": 0,
                        "error": "account_not_found",
                        "account": account,
                        "available_accounts": list_mail_account_names(timeout=validation_timeout),
                    },
                    indent=2,
                )
            return account_err

    # Smart default: 48h window when no explicit start date was passed.
    searched_from: str | None = None
    _date_from_explicit = False
    if date_from is None and effective_recent_days > 0:
        cutoff = datetime.now() - timedelta(days=effective_recent_days)
        date_from = cutoff.strftime("%Y-%m-%d")
        searched_from = date_from
    elif date_from is not None:
        # Explicit caller override — effective window is 0 for reporting purposes.
        effective_recent_days = 0.0
        searched_from = date_from
        _date_from_explicit = True

    subject_terms = normalize_search_terms(subject_keyword, subject_keywords)

    try:
        records, errors, error_details, body_search_capped = await _search_mail_records(
            account=account,
            mailbox=mailbox,
            subject_terms=subject_terms,
            sender=sender,
            sender_exact=sender_exact,
            sender_domain=sender_domain,
            internet_message_id=internet_message_id,
            has_attachments=has_attachments,
            read_status=read_status,
            date_from=date_from,
            date_to=date_to,
            include_content=include_content,
            content_length=max_content_length,
            offset=offset,
            limit=limit,
            sort=sort,
            body_text=body_text,
            timeout=timeout,
            recent_days=effective_recent_days,
            date_from_explicit=_date_from_explicit,
            mailboxes=mailboxes if mailboxes else None,
        )

        # Replied-detection: build the replied-Message-ID set once and
        # apply it to records. Detection is best-effort per account; if
        # the Sent mailbox is unreachable we get an empty set and no
        # records are flagged or filtered.
        if exclude_replied or flag_replied:
            replied_set: set[str] = set()
            if account:
                replied_set = await asyncio.to_thread(fetch_replied_ids, account, 200, timeout)
            else:
                # Multi-account: union per-account replied sets so a record
                # is flagged when ANY account's Sent mailbox shows a reply
                # for its Message-ID.
                accounts_seen = sorted({r.get("account", "") for r in records if r.get("account")})
                if accounts_seen:
                    sets = await asyncio.gather(
                        *(asyncio.to_thread(fetch_replied_ids, acct, 200, timeout) for acct in accounts_seen)
                    )
                    for s in sets:
                        replied_set |= s

            def _is_replied(rec: dict[str, Any]) -> bool:
                raw_id = rec.get("internet_message_id", "")
                if not raw_id:
                    return False
                token = raw_id.strip()
                if not token.startswith("<"):
                    token = "<" + token
                if not token.endswith(">"):
                    token = token + ">"
                return token in replied_set

            if exclude_replied:
                records = [r for r in records if not _is_replied(r)]
            elif flag_replied:
                for rec in records:
                    if _is_replied(rec):
                        rec["already_replied"] = True

        _mailbox_all = mailbox == "All"
        return _build_search_response(
            records,
            offset=offset,
            limit=limit,
            sort=sort,
            output_format=output_format,
            subject_only=False,
            errors=errors or None,
            error_details=error_details or None,
            recent_days_applied=effective_recent_days,
            searched_from=searched_from,
            body_search_capped=body_search_capped,
            mailbox_count_capped=_mailbox_all,
            mailboxes_truncated=_mailbox_all,
            sender_only_hint=sender_only_hint,
            include_content_hint=include_content,
            body_text_hint=bool(body_text),
        )
    except ValueError as exc:
        return f"Error: {exc}"


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

                {to_recipients_script}

                {cc_recipients_script}

                {thread_headers_script}

                {bcc_recipients_script}

                return messageId & "|||" & internetMessageId & "|||" & messageSubject & "|||" & messageSender & "|||" & mailboxName & "|||" & accountName & "|||" & readValue & "|||" & receivedAt & "|||" & contentPreview & "|||" & toRecips & "|||" & ccRecips & "|||" & inReplyTo & "|||" & refsValue & "|||" & bccRecips
            on error errMsg
                return "ERROR|||" & errMsg
            end try
        end timeout
    end tell
    '''

    result = run_applescript(script, timeout=effective_timeout)
    if result.startswith("ERROR|||"):
        raise ValueError(result.split("|||", 1)[1])

    records, _mb_errors = _parse_search_records(result)
    item = records[0] if records else None
    if item is not None and include_content:
        preview = item.get("content_preview", "") or ""
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

                            {to_recipients_script}

                            {cc_recipients_script}

                            {thread_headers_script}

                            {bcc_recipients_script}

                            set end of recordLines to messageId & "|||" & internetMessageId & "|||" & messageSubject & "|||" & messageSender & "|||" & mailboxName & "|||" & accountName & "|||" & readValue & "|||" & receivedAt & "|||" & contentPreview & "|||" & toRecips & "|||" & ccRecips & "|||" & inReplyTo & "|||" & refsValue & "|||" & bccRecips
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

        result = run_applescript(script, timeout=effective_timeout)
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
) -> str:
    """
    Fetch one email by its exact Apple Mail message id.

    Use this after `search_emails` returns a `message_id` when you need the
    full message body or stable metadata without running another broad subject
    search.

    Returned fields include ``to``, ``cc``, ``bcc`` (recipient addresses),
    ``in_reply_to`` and ``references`` (thread-linking headers parsed from the
    raw ``all headers`` of the message), and ``has_quoted_original`` (True when
    the content contains a quoted prior message). When ``in_reply_to`` or
    ``references`` are present, the message is confirmed to be part of a thread
    — useful for verifying that a draft reply is correctly threaded.

    Args:
        account: Account name to search in (e.g., "Gmail", "Work").
        message_id: Exact numeric Apple Mail message id returned by search tools.
        mailbox: Mailbox to search in (default: "INBOX").
        include_content: Whether to include email content (default: True).
        max_content_length: Maximum content characters to return when include_content=True.
        output_format: Output format: "text" or "json" (default: "text").
        timeout: Optional AppleScript timeout in seconds (default: 120s).

    Returns:
        One matching email as text, or JSON with {"item": ...}. If no message is
        found, JSON returns {"item": null}. JSON items include ``to``, ``cc``,
        ``bcc``, ``in_reply_to``, ``references``, and ``has_quoted_original``
        when available.
    """
    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = validate_account_name(account, timeout=validation_timeout)
    if account_err:
        if output_format == "json":
            return account_not_found_json(account, timeout=validation_timeout)
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

    if output_format == "json":
        return json.dumps({"item": item})

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

    Returns:
        JSON with requested_ids, items in requested order, missing_ids, invalid_ids,
        returned count, and chunk_size. Text mode formats found items and lists
        missing or invalid ids.
    """
    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = validate_account_name(account, timeout=validation_timeout)
    if account_err:
        if output_format == "json":
            return account_not_found_json(account, timeout=validation_timeout)
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
      (``recent_days=0``) are refused — use ``full_inbox_export`` for the
      audited full-mailbox escape hatch. Subject matching is case-insensitive.

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
    account_err = validate_account_name(account, timeout=validation_timeout)
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
                "fallback_tool": "full_inbox_export",
                "fallback_tool_args": {
                    "account": account,
                    "filter_subject": subject_keyword,
                },
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
        result = run_applescript(script, timeout=effective_timeout)
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
