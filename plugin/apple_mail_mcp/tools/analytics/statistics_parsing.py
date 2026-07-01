"""Pure parsing/formatting helpers for ``get_statistics`` output (no AppleScript I/O)."""

import re
from collections import Counter
from typing import Any

from apple_mail_mcp.constants import SCAN_BOUNDS


def _statistics_recent_days_applied(days_back: int, scope: str) -> float:
    if scope == "mailbox_breakdown":
        return 0.0
    return float(days_back) if days_back > 0 else 0.0


def _parse_account_overview_statistics(text: str) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "total_emails": 0,
        "unread": 0,
        "read": 0,
        "flagged": 0,
        "with_attachments": 0,
        "top_senders": [],
        "mailbox_distribution": [],
    }

    total_match = re.search(r"Total Emails: (\d+)", text)
    if total_match:
        stats["total_emails"] = int(total_match.group(1))

    unread_match = re.search(r"Unread: (\d+)(?: \((\d+)%\))?", text)
    if unread_match:
        stats["unread"] = int(unread_match.group(1))
        if unread_match.group(2) is not None:
            stats["unread_percent"] = int(unread_match.group(2))

    read_match = re.search(r"Read: (\d+)(?: \((\d+)%\))?", text)
    if read_match:
        stats["read"] = int(read_match.group(1))
        if read_match.group(2) is not None:
            stats["read_percent"] = int(read_match.group(2))

    flagged_match = re.search(r"Flagged: (\d+)", text)
    if flagged_match:
        stats["flagged"] = int(flagged_match.group(1))

    attachments_match = re.search(r"With Attachments: (\d+)(?: \((\d+)%\))?", text)
    if attachments_match:
        stats["with_attachments"] = int(attachments_match.group(1))
        if attachments_match.group(2) is not None:
            stats["with_attachments_percent"] = int(attachments_match.group(2))

    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in {"👥 TOP SENDERS", "👥 SAMPLE SENDERS"}:
            section = "senders"
            continue
        if stripped == "📁 MAILBOX DISTRIBUTION":
            section = "mailboxes"
            continue
        if section == "senders" and stripped.endswith(" emails"):
            sender_match = re.match(r"(.+): (\d+) emails$", stripped)
            if sender_match:
                stats["top_senders"].append(
                    {
                        "sender": sender_match.group(1),
                        "count": int(sender_match.group(2)),
                    }
                )
        elif section == "mailboxes" and ":" in stripped and not stripped.startswith("━"):
            mailbox_match = re.match(r"(.+): (\d+)(?: \((\d+)%\))?$", stripped)
            if mailbox_match:
                entry = {
                    "mailbox": mailbox_match.group(1),
                    "count": int(mailbox_match.group(2)),
                }
                if mailbox_match.group(3) is not None:
                    entry["percent"] = int(mailbox_match.group(3))
                stats["mailbox_distribution"].append(entry)

    return stats


def _parse_sender_stats_statistics(text: str) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for key, pattern in (
        ("total_emails", r"Total emails: (\d+)"),
        ("unread", r"Unread: (\d+)"),
        ("with_attachments", r"With attachments: (\d+)"),
    ):
        match = re.search(pattern, text)
        if match:
            stats[key] = int(match.group(1))
    return stats


def _parse_mailbox_breakdown_statistics(text: str) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for key, pattern in (
        ("total_messages", r"Total messages: (\d+)"),
        ("unread", r"Unread: (\d+)"),
        ("read", r"Read: (\d+)"),
    ):
        match = re.search(pattern, text)
        if match:
            stats[key] = int(match.group(1))
    return stats


_STATISTICS_ERROR_PREFIX = "__APPLE_MAIL_MCP_ERROR__|||"


def _parse_statistics_errors(text: str) -> list[str]:
    errors: list[str] = []
    for line in text.splitlines():
        if not line.startswith(_STATISTICS_ERROR_PREFIX):
            continue
        parts = line.split("|||", 2)
        if len(parts) == 3:
            mailbox = parts[1].strip() or "unknown mailbox"
            message = parts[2].strip() or "unknown error"
            errors.append(f"{mailbox}: {message}")
    return errors


def _strip_statistics_error_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith(_STATISTICS_ERROR_PREFIX))


def _parse_statistics_text(scope: str, text: str) -> dict[str, Any]:
    text = _strip_statistics_error_lines(text)
    if scope == "account_overview":
        return _parse_account_overview_statistics(text)
    if scope == "sender_stats":
        return _parse_sender_stats_statistics(text)
    return _parse_mailbox_breakdown_statistics(text)


def _format_statistics_json(
    *,
    scope: str,
    account: str,
    days_back: int,
    statistics: dict[str, Any],
    sender: str | None = None,
    mailbox: str | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "account": account,
        "scope": scope,
        "days_back": days_back,
        "recent_days_applied": _statistics_recent_days_applied(days_back, scope),
        "statistics": statistics,
        "errors": errors or [],
    }
    if sender is not None:
        payload["sender"] = sender
    if scope == "mailbox_breakdown":
        payload["mailbox"] = mailbox or "INBOX"
    return payload


def _statistics_json_error(
    error: str,
    *,
    account: str | None = None,
    days_back: int | None = None,
    scope: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": error, "errors": []}
    if account is not None:
        payload["account"] = account
    if days_back is not None:
        payload["days_back"] = days_back
    if scope is not None:
        payload["scope"] = scope
    if message is not None:
        payload["message"] = message
    return payload


def _statistics_scan_caps(days_back: int) -> tuple[int, int]:
    """Return (max_mailboxes, max_messages_per_mailbox) for overview/sender scans.

    Short windows use ``INBOX_LONG`` per mailbox; longer windows use
    ``SEARCH_WINDOW_CAP``.
    """
    if days_back > 0 and days_back <= 7:
        return 10, SCAN_BOUNDS["INBOX_LONG"]
    return 20, SCAN_BOUNDS["SEARCH_WINDOW_CAP"]


def _build_account_overview_report(raw_overview: str, escaped_account: str) -> str:
    """Parse the MBOX/ROW rows emitted by the overview AppleScript into text.

    Pure transform of the script's raw output (plus the already-escaped account
    label) into the human-readable statistics report.
    """
    # Mailbox-wide totals come from MBOX|||name|||total|||unread rows
    # (Mail.app's own count APIs). Sample counts come from ROW lines.
    mbox_total_counts: dict[str, int] = {}
    mbox_unread_counts: dict[str, int] = {}
    sample_flagged = 0
    sample_with_attachments = 0
    sample_total = 0
    sender_counter: Counter[str] = Counter()
    scan_errors: list[str] = []

    for line in raw_overview.splitlines():
        if line.startswith("MBOX|||"):
            parts = line.split("|||", 3)
            if len(parts) < 4:
                continue
            _, mbox, total_str, unread_str = parts
            try:
                mbox_total_counts[mbox] = mbox_total_counts.get(mbox, 0) + int(total_str)
                mbox_unread_counts[mbox] = mbox_unread_counts.get(mbox, 0) + int(unread_str)
            except ValueError:
                continue
        elif line.startswith("ROW|||"):
            parts = line.split("|||", 4)
            if len(parts) < 5:
                continue
            _, _mbox, flag_str, attach_str, sender = parts
            sample_total += 1
            if flag_str == "1":
                sample_flagged += 1
            if attach_str == "1":
                sample_with_attachments += 1
            if sender:
                sender_counter[sender] += 1
        elif line.startswith(_STATISTICS_ERROR_PREFIX):
            scan_errors.append(line)

    # Prefer MBOX-derived mailbox-wide totals; fall back to ROW-derived
    # sample totals when the script (or a legacy mock) emits no MBOX rows.
    if mbox_total_counts:
        total_emails = sum(mbox_total_counts.values())
        total_unread = sum(mbox_unread_counts.values())
        mailbox_totals = dict(mbox_total_counts)
    else:
        total_emails = sample_total
        total_unread = 0  # legacy fallback can't compute true unread
        mailbox_totals = {}

    total_read = total_emails - total_unread
    header = (
        "╔══════════════════════════════════════════╗\n"
        f"║      EMAIL STATISTICS - {escaped_account}       ║\n"
        "╚══════════════════════════════════════════╝\n\n"
    )
    lines_out = [header]
    lines_out.append("📊 VOLUME METRICS\n")
    lines_out.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    lines_out.append(f"Total Emails: {total_emails}\n")
    if total_emails > 0:
        lines_out.append(f"Unread: {total_unread} ({round(total_unread / total_emails * 100)}%)\n")
        lines_out.append(f"Read: {total_read} ({round(total_read / total_emails * 100)}%)\n")
        lines_out.append(f"Flagged: {sample_flagged}\n")
        lines_out.append(
            f"With Attachments: {sample_with_attachments} ({round(sample_with_attachments / total_emails * 100)}%)\n"
        )
    else:
        lines_out.append("Unread: 0\nRead: 0\nFlagged: 0\nWith Attachments: 0\n")
    lines_out.append("\n")
    lines_out.append("👥 SAMPLE SENDERS\n")
    lines_out.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    for sender, cnt in sender_counter.most_common(5):
        lines_out.append(f"{sender}: {cnt} emails\n")
    lines_out.append("\n")
    lines_out.append("📁 MAILBOX DISTRIBUTION\n")
    lines_out.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    for i, (mbox, cnt) in enumerate(sorted(mailbox_totals.items(), key=lambda x: -x[1])):
        if i >= 5:
            break
        if total_emails > 0:
            pct = round(cnt / total_emails * 100)
            lines_out.append(f"{mbox}: {cnt} ({pct}%)\n")
        else:
            lines_out.append(f"{mbox}: {cnt}\n")
    if scan_errors:
        lines_out.append("\nMAILBOX SCAN ERRORS\n")
        for err in scan_errors:
            lines_out.append(err + "\n")

    return "".join(lines_out)
