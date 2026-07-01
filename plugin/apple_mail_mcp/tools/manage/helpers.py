"""Shared management helpers: message-id caps, date cutoffs, filter-scan gating, and id resolution.

The patched name ``_search_mail_records`` is reached via the ``manage`` facade so the
``patch('...tools.manage._search_mail_records')`` test seams keep firing."""

from datetime import datetime, timedelta
from typing import Any

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS
from apple_mail_mcp.tools import manage


def _check_message_ids_cap(normalized_ids: list[str], tool_name: str) -> str | None:
    """Return a structured error string if *normalized_ids* exceeds the whose-id cap.

    Mail's AppleScript parser rejects or hangs on `id is X or id is Y or ...`
    predicates beyond ~200-500 terms (varies by macOS). We cap at
    ``MAX_WHOSE_IDS`` (50) and surface a structured ``WHOSE_ID_LIST_TOO_LARGE``
    so the caller knows to chunk the ids in Python and call the tool
    once per batch. Returns ``None`` when the list is within the cap.
    """
    if len(normalized_ids) <= MAX_WHOSE_IDS:
        return None
    err = ToolError(
        code="WHOSE_ID_LIST_TOO_LARGE",
        message=(
            f"{tool_name} received {len(normalized_ids)} message_ids; "
            f"hard cap is {MAX_WHOSE_IDS} per call. Mail's AppleScript "
            "parser rejects or hangs on very long `id is X or id is Y ...` "
            "predicates."
        ),
        remediation={
            "preferred": (
                f"Split message_ids into batches of {MAX_WHOSE_IDS} or fewer and call {tool_name} once per batch"
            ),
            "helper": "apple_mail_mcp.bounded_scan.iter_id_chunks",
        },
    )
    return serialize_tool_error(err)


def _date_to_for_older_than(days: int | None) -> str | None:
    """Return YYYY-MM-DD cutoff date for older-than filters."""
    if days is None or days <= 0:
        return None
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def _date_from_for_recent_days(days: float | None) -> str | None:
    """Return YYYY-MM-DD cutoff date for recent-window filters."""
    if days is None or days <= 0:
        return None
    return (datetime.now() - timedelta(days=float(days))).strftime("%Y-%m-%d")


FILTER_SCAN_WARNING = (
    "WARNING: filter scan enabled — slow and timeout-prone on large mailboxes (24k+). "
    "Prefer message_ids from list_inbox_emails or search_emails."
)


def _filter_scan_disabled_error(tool_name: str) -> str:
    """Structured error when filter-based mutation is attempted without opt-in."""
    return serialize_tool_error(
        ToolError(
            code="FILTER_SCAN_DISABLED",
            message=(f"{tool_name} requires message_ids by default. Filter scans are slow on large mailboxes."),
            remediation={
                "preferred": (
                    "Use search_emails(...) or list_inbox_emails(...) to collect "
                    f"message_id, then call {tool_name}(message_ids=[...])"
                ),
                "escape_hatch": (
                    "allow_filter_scan=True (slow; timeout-prone on 24k+ inboxes; use only for approved bulk campaigns)"
                ),
            },
        )
    )


def _with_filter_scan_warning(text: str) -> str:
    """Prefix filter-scan escape-hatch responses with an explicit warning."""
    return f"{FILTER_SCAN_WARNING}\n\n{text}"


def _deprecated_target_selectors(
    *,
    subject_keyword: str | None = None,
    subject_keywords: list[str] | None = None,
    sender: str | None = None,
) -> tuple[str, ...]:
    """Return legacy target selectors present in a call."""
    selectors: list[str] = []
    if subject_keyword:
        selectors.append("subject_keyword")
    if subject_keywords:
        selectors.append("subject_keywords")
    if sender:
        selectors.append("sender")
    return tuple(selectors)


def _format_dry_run_records(
    title: str,
    records: list[dict[str, Any]],
    result_prefix: str,
    limit: int,
) -> str:
    """Format structured search records as existing dry-run text."""
    lines = [title, ""]
    for record in records[:limit]:
        lines.append(f"{result_prefix}: {record.get('subject', '')}")
        lines.append(f"   From: {record.get('sender', '')}")
        lines.append(f"   Date: {record.get('received_date', '')}")
        lines.append("")
    lines.append("========================================")
    lines.append(f"TOTAL: {min(len(records), limit)} email(s) {result_prefix.lower()}")
    if len(records) > limit:
        lines.append("(limit reached)")
    lines.append("========================================")
    return "\n".join(lines)


def _search_message_ids(
    *,
    account: str,
    mailbox: str,
    subject_terms: list[str] | None = None,
    sender: str | None = None,
    read_status: str = "all",
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int,
    timeout: int | None = None,
    recent_days: float = 0.0,
) -> list[str]:
    """Resolve message IDs through the bounded search helper."""
    records = manage._search_mail_records(
        account=account,
        mailbox=mailbox,
        subject_terms=subject_terms or None,
        sender=sender,
        read_status=read_status,
        date_from=date_from,
        date_to=date_to,
        include_content=False,
        offset=0,
        limit=limit,
        timeout=timeout,
        recent_days=recent_days,
    )
    ids: list[str] = []
    for record in records:
        message_id = record.get("message_id")
        if message_id is None:
            continue
        ids.append(str(message_id))
        if len(ids) >= limit:
            break
    return ids
