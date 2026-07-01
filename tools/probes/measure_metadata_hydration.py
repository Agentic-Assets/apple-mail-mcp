#!/usr/bin/env python3
"""Measure read-only metadata hydration costs for exact Apple Mail ids.

This helper is intentionally not part of the MCP tool surface. It requires an
explicit live-read confirmation flag and reports only timings plus aggregate
counts. It never prints message contents, headers, subjects, senders, recipient
addresses, attachment names, or raw message ids.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from dataclasses import dataclass
from math import ceil
from typing import Any

CASE_HEADERS = "headers_by_exact_id"
CASE_ATTACHMENTS = "attachment_count_by_exact_id"
CASE_COMBINED = "combined_metadata_hydration_by_exact_id"
CASES = (CASE_HEADERS, CASE_ATTACHMENTS, CASE_COMBINED)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True)
class MeasurementCase:
    """One read-only metadata hydration measurement case."""

    name: str
    measure_headers: bool
    measure_attachments: bool


def _percentile(values: list[float], percentile: int) -> float:
    """Return a simple nearest-rank percentile for a non-empty sample."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, ceil((percentile / 100.0) * len(ordered)) - 1)
    return round(ordered[index], 3)


def _parse_csv_ids(raw_value: str) -> list[str]:
    """Return normalized numeric ids from a comma-separated argument."""
    ids: list[str] = []
    seen: set[str] = set()
    for item in raw_value.split(","):
        candidate = item.strip()
        if candidate.isdecimal() and candidate not in seen:
            seen.add(candidate)
            ids.append(candidate)
    return ids


def _escape_applescript(value: str) -> str:
    """Escape a string for safe AppleScript double-quoted literals."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\u2028", "\\n")
        .replace("\u2029", "\\n")
    )


def run_applescript(script: str, timeout: int | None = 120) -> str:
    """Run AppleScript through osascript and return sanitized stdout."""
    effective_timeout = 120 if timeout is None else timeout
    result = subprocess.run(
        ["osascript", "-"],
        input=script.encode("utf-8"),
        capture_output=True,
        timeout=effective_timeout,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"AppleScript error: {stderr or result.returncode}")
    output = result.stdout.decode("utf-8", errors="replace").strip()
    return _CONTROL_CHARS_RE.sub("", output.replace("\r\n", "\n").replace("\r", "\n"))


def _build_measurement_script(
    *,
    account: str,
    mailbox: str,
    message_ids: list[str],
    measure_headers: bool,
    measure_attachments: bool,
    timeout: int,
) -> str:
    """Build an exact-id AppleScript that returns aggregate counts only."""
    safe_account = _escape_applescript(account)
    safe_mailbox = _escape_applescript(mailbox)
    requested_ids = ", ".join(message_ids)
    header_block = ""
    attachment_block = ""
    if measure_headers:
        header_block = """
                    try
                        set headerText to all headers of aMessage as string
                        set totalHeaderChars to totalHeaderChars + (length of headerText)
                    end try
        """
    if measure_attachments:
        attachment_block = """
                    try
                        set totalAttachmentCount to totalAttachmentCount + (count of mail attachments of aMessage)
                    end try
        """

    return f'''
tell application "Mail"
    with timeout of {timeout} seconds
        set requestedIds to {{{requested_ids}}}
        set foundCount to 0
        set missingCount to 0
        set totalHeaderChars to 0
        set totalAttachmentCount to 0

        set targetAccount to account "{safe_account}"
        try
            set targetMailbox to mailbox "{safe_mailbox}" of targetAccount
        on error
            if "{safe_mailbox}" is "INBOX" then
                set targetMailbox to mailbox "Inbox" of targetAccount
            else
                error "Mailbox not found: {safe_mailbox}"
            end if
        end try

        repeat with requestedId in requestedIds
            set matchedMessages to every message of targetMailbox whose id is requestedId
            if (count of matchedMessages) is 0 then
                set missingCount to missingCount + 1
            else
                set foundCount to foundCount + 1
                set aMessage to item 1 of matchedMessages
                {header_block}
                {attachment_block}
            end if
        end repeat

        return "FOUND|||" & foundCount & "|||MISSING|||" & missingCount & "|||HEADER_CHARS|||" & totalHeaderChars & "|||ATTACHMENTS|||" & totalAttachmentCount
    end timeout
end tell
'''


def _parse_measurement_output(raw_output: str) -> dict[str, int]:
    """Parse the aggregate AppleScript output."""
    parts = raw_output.strip().split("|||")
    parsed: dict[str, int] = {}
    for index in range(0, len(parts) - 1, 2):
        key = parts[index].strip().lower()
        value = parts[index + 1].strip()
        if key:
            parsed[key] = int(value)
    return {
        "found_count": parsed.get("found", 0),
        "missing_count": parsed.get("missing", 0),
        "header_chars_total": parsed.get("header_chars", 0),
        "attachment_count_total": parsed.get("attachments", 0),
    }


def _run_case(
    case: MeasurementCase,
    *,
    account: str,
    mailbox: str,
    message_ids: list[str],
    repeats: int,
    timeout: int,
) -> dict[str, Any]:
    """Run one measurement case and return aggregate timing statistics."""
    samples: list[float] = []
    last_counts: dict[str, int] = {}
    script = _build_measurement_script(
        account=account,
        mailbox=mailbox,
        message_ids=message_ids,
        measure_headers=case.measure_headers,
        measure_attachments=case.measure_attachments,
        timeout=timeout,
    )

    for _ in range(repeats):
        start = time.perf_counter()
        raw_output = run_applescript(script, timeout=timeout)
        samples.append(round((time.perf_counter() - start) * 1000.0, 3))
        last_counts = _parse_measurement_output(raw_output)

    return {
        "name": case.name,
        "sample_count": len(samples),
        "p50_ms": _percentile(samples, 50),
        "p95_ms": _percentile(samples, 95),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "message_id_count": len(message_ids),
        **last_counts,
    }


def measure_metadata_hydration(
    *,
    account: str,
    mailbox: str,
    message_ids: list[str],
    repeats: int = 3,
    timeout: int = 120,
) -> dict[str, Any]:
    """Measure exact-id header and attachment-count hydration without exposing content."""
    if not message_ids:
        raise ValueError("message_ids must contain at least one numeric Mail message id")
    if repeats <= 0:
        raise ValueError("repeats must be positive")

    cases = (
        MeasurementCase(CASE_HEADERS, measure_headers=True, measure_attachments=False),
        MeasurementCase(CASE_ATTACHMENTS, measure_headers=False, measure_attachments=True),
        MeasurementCase(CASE_COMBINED, measure_headers=True, measure_attachments=True),
    )
    return {
        "ok": True,
        "live_mail": True,
        "privacy": {
            "exact_ids_only": True,
            "prints_private_content": False,
            "prints_raw_message_ids": False,
        },
        "account": "(redacted)",
        "mailbox": mailbox,
        "message_id_count": len(message_ids),
        "repeats": repeats,
        "cases": [
            _run_case(case, account=account, mailbox=mailbox, message_ids=message_ids, repeats=repeats, timeout=timeout)
            for case in cases
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure read-only exact-id metadata hydration costs without printing Mail content.",
    )
    parser.add_argument("--account", required=True, help="Exact Apple Mail account name")
    parser.add_argument("--mailbox", default="INBOX", help="Mailbox path, default INBOX")
    parser.add_argument("--message-ids", required=True, help="Comma-separated numeric Mail message ids")
    parser.add_argument("--repeats", type=int, default=3, help="Measurement repeats per case")
    parser.add_argument("--timeout", type=int, default=120, help="AppleScript timeout in seconds")
    parser.add_argument(
        "--confirm-read-only-live-mail",
        action="store_true",
        help="Required confirmation. Reads aggregate metadata from Mail but sends nothing and creates no drafts.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the measurement CLI."""
    args = _build_parser().parse_args(argv)
    if not args.confirm_read_only_live_mail:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "confirm_read_only_live_mail_required",
                    "message": "Pass --confirm-read-only-live-mail to run exact-id read-only Mail measurements.",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    message_ids = _parse_csv_ids(args.message_ids)
    try:
        payload = measure_metadata_hydration(
            account=args.account,
            mailbox=args.mailbox,
            message_ids=message_ids,
            repeats=args.repeats,
            timeout=args.timeout,
        )
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
