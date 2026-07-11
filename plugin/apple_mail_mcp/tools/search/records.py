"""Pure parse/format/response helpers and shared constants for search.

No AppleScript I/O lives here, so nothing in this module is patched as a test
seam — imports stay direct (core/constants/backend.base only).
"""

import json
from datetime import datetime
from typing import Any
from urllib.parse import quote

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.core.reply_state import reply_state_tags

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

    Rows carry a 15th field (index 14, ``wasRepliedToken`` from
    ``core.reply_state.was_replied_fragment``) that becomes
    ``was_replied_to`` (bool, always present in the returned record: Mail's
    native read-only ``was replied to`` property, no parameter gates it). A
    14-field row (no 15th field, e.g. an older-shaped mocked payload) is
    tolerated defensively: ``was_replied_to`` simply defaults to ``False``
    rather than raising.
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
        parts = line.split("|||", 14)
        if len(parts) < 8:
            continue

        internet_message_id = parts[1].strip()
        record: dict[str, Any] = {
            "message_id": parts[0].strip(),
            "internet_message_id": internet_message_id,
            "subject": parts[2].strip(),
            "sender": parts[3].strip(),
            "mailbox": parts[4].strip(),
            "account": parts[5].strip(),
            "is_read": parts[6].strip().lower() == "true",
            "received_date": parts[7].strip(),
            "was_replied_to": len(parts) > 14 and parts[14].strip().lower() == "true",
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
            was_replied = bool(item.get("was_replied_to") or item.get("already_replied"))
            tags = reply_state_tags(was_replied, item.get("has_draft"))
            tag_prefix = "".join(f"{tag} " for tag in tags)
            lines.append(f"{indicator} {tag_prefix}{item['subject']}")
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
    draft_scan: dict[str, Any] | None = None,
) -> str:
    """Return either JSON or text for search results.

    *draft_scan* (from ``tools.reply_state_wiring.build_draft_scan_status``,
    via ``annotate_rows_with_reply_state``) is surfaced as a top-level
    ``draft_scan`` key only in JSON output
    (``{"status": "ok" | "error" | "skipped", "scanned": N, "accounts": [...],
    "error"?: "..."}``); text output instead relies on the ``[REPLIED]`` /
    ``[HAS DRAFT]`` row prefixes already applied by ``_format_search_records_text``.
    """
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
        if draft_scan is not None:
            payload["draft_scan"] = draft_scan
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
