"""Management tools: moving, status updates, trash, and attachments."""

import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error, target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS, build_whose_id_list
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    build_mailbox_ref,
    contains_any_condition,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
    list_mail_account_names,
    normalize_message_ids,
    normalize_search_terms,
    run_applescript,
    validate_account_name,
    validate_save_path,
)
from apple_mail_mcp.server import (
    DESTRUCTIVE_TOOL_ANNOTATIONS,
    IDEMPOTENT_WRITE_TOOL_ANNOTATIONS,
    WRITE_TOOL_ANNOTATIONS,
    mcp,
)
from apple_mail_mcp.tools.search import _search_mail_records_sync as _search_mail_records


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
    import json as _json

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
    return _json.dumps(err.to_dict(), indent=2)


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
    records = _search_mail_records(
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


def _move_email_by_message_ids(
    *,
    account: str,
    from_mailbox: str,
    to_mailbox: str,
    message_ids: list[str],
    max_moves: int,
    dry_run: bool,
    timeout: int,
    dest_ref: str,
) -> str:
    normalized_ids = normalize_message_ids(message_ids)
    if not normalized_ids:
        return "Error: 'message_ids' must contain one or more numeric Mail ids"
    cap_error = _check_message_ids_cap(normalized_ids, "move_email")
    if cap_error:
        return cap_error

    safe_account = escape_applescript(account)
    safe_from = escape_applescript(from_mailbox)
    safe_to = escape_applescript(to_mailbox)
    id_condition = build_whose_id_list(normalized_ids)
    mode_label = (
        f"DRY RUN - PREVIEW MOVE BY IDS: {safe_from} -> {safe_to}"
        if dry_run
        else f"MOVING EMAILS BY IDS: {safe_from} -> {safe_to}"
    )
    move_action = "" if dry_run else "move aMessage to destMailbox"
    result_prefix = "Would move" if dry_run else "Moved"
    dest_setup = (
        ""
        if dry_run
        else f"""
                set destMailbox to {dest_ref}"""
    )

    script = f'''
    tell application "Mail"
        with timeout of {timeout} seconds
            set outputText to "{mode_label}" & return & return
            set moveCount to 0

            try
                set targetAccount to account "{safe_account}"
                {build_mailbox_ref(from_mailbox, var_name="sourceMailbox")}
                {dest_setup}

                set matchingMessages to every message of sourceMailbox whose {id_condition}
                if (count of matchingMessages) > {max_moves} then
                    set matchingMessages to items 1 thru {max_moves} of matchingMessages
                end if

                repeat with aMessage in matchingMessages
                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage

                        {move_action}

                        set outputText to outputText & "{result_prefix}: " & messageSubject & return
                        set outputText to outputText & "   From: " & messageSender & return
                        set outputText to outputText & "   Date: " & (messageDate as string) & return & return

                        set moveCount to moveCount + 1
                    end try
                end repeat

                set outputText to outputText & "========================================" & return
                set outputText to outputText & "REQUESTED IDS: {len(normalized_ids)}" & return
                set outputText to outputText & "TOTAL: " & moveCount & " email(s) {result_prefix.lower()}" & return
                if moveCount >= {max_moves} then
                    set outputText to outputText & "(max_moves limit reached)" & return
                end if
                set outputText to outputText & "========================================" & return

            on error errMsg
                return "Error: " & errMsg & return & "Check that account and mailbox names are correct. For nested mailboxes, use '/' separator."
            end try

            return outputText
        end timeout
    end tell
    '''

    try:
        return run_applescript(script, timeout=timeout)
    except AppleScriptTimeout:
        return (
            f"Error: move_email timed out after {timeout}s on account "
            f"'{account}'. Retry with a larger timeout or tighter filters."
        )


# Characters that could break AppleScript strings or mailbox names
_INVALID_MAILBOX_CHARS = re.compile(r"[\\\"<>|?*:\x00-\x1f]")


@mcp.tool(annotations=WRITE_TOOL_ANNOTATIONS)
@inject_preferences
def move_email(
    account: str | None = None,
    to_mailbox: str = "",
    message_ids: list[str] | None = None,
    subject_keyword: str | None = None,
    from_mailbox: str = "INBOX",
    max_moves: int = 50,
    subject_keywords: list[str] | None = None,
    sender: str | None = None,
    older_than_days: int | None = None,
    dry_run: bool = False,
    only_read: bool = False,
    recent_days: float = 2.0,
    allow_filter_scan: bool = False,
    timeout: int | None = None,
) -> str:
    """
      Move email(s) by exact ``message_ids`` (fast) or, rarely, by filters.

    Preferred: pass ``message_ids`` from ``list_inbox_emails`` or ``search_emails``.
    Filter-based moves require ``allow_filter_scan=True`` (slow on large mailboxes).
    Use ``dry_run=True`` with ``message_ids`` for a fast preview without moving.

      When ``message_ids`` is provided, moves exact IDs and ignores keyword/sender
      filters. When ``account`` is None the configured ``DEFAULT_MAIL_ACCOUNT`` is used.

      Args:
          account: Account name (e.g., "Gmail", "Work"). Defaults to DEFAULT_MAIL_ACCOUNT.
          to_mailbox: Destination mailbox name. For nested mailboxes, use "/" separator (e.g., "Projects/Amplify Impact")
          message_ids: List of exact Mail message ids (preferred path)
          subject_keyword: Filter by subject (requires allow_filter_scan=True)
          from_mailbox: Source mailbox name (default: "INBOX")
          max_moves: Maximum number of emails to move (default: 50, safety limit)
          subject_keywords: Optional list of keywords to match in subjects; matches any keyword
          sender: Optional sender to filter emails by (requires allow_filter_scan=True)
          older_than_days: Optional age filter - only move emails older than N days
          dry_run: If True, preview without acting. Fast with message_ids; slow with filters.
          only_read: If True, only move emails that have been read (default: False)
          recent_days: Recent window when using filter scan (default: 2.0).
          allow_filter_scan: Opt in to slow subject/sender filter scans (default: False).
          timeout: Optional AppleScript timeout in seconds (default: 300s).

      Returns:
          Confirmation message with details of moved emails
    """
    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: account is required (and no DEFAULT_MAIL_ACCOUNT configured)."
    if not to_mailbox:
        return "Error: to_mailbox is required."
    deprecated_selectors = _deprecated_target_selectors(
        subject_keyword=subject_keyword,
        subject_keywords=subject_keywords,
        sender=sender,
    )
    if message_ids is None and deprecated_selectors:
        return target_selector_deprecated_error(
            "move_email",
            deprecated_selectors,
            preferred="Call search_emails(...) or list_inbox_emails(...) first, then pass message_ids=[...].",
            discovery="search_emails(..., recent_days=..., limit=...) or list_inbox_emails(...)",
            exact_selector="message_ids",
        )

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    safe_to = escape_applescript(to_mailbox)
    effective_timeout = timeout if timeout is not None else 300

    mailbox_parts = to_mailbox.split("/")
    if len(mailbox_parts) > 1:
        dest_ref = f'mailbox "{escape_applescript(mailbox_parts[-1])}" of '
        for i in range(len(mailbox_parts) - 2, -1, -1):
            dest_ref += f'mailbox "{escape_applescript(mailbox_parts[i])}" of '
        dest_ref += "targetAccount"
    else:
        dest_ref = f'mailbox "{safe_to}" of targetAccount'

    if message_ids is not None:
        return _move_email_by_message_ids(
            account=account,
            from_mailbox=from_mailbox,
            to_mailbox=to_mailbox,
            message_ids=message_ids,
            max_moves=max_moves,
            dry_run=dry_run,
            timeout=effective_timeout,
            dest_ref=dest_ref,
        )

    subject_terms = normalize_search_terms(subject_keyword, subject_keywords)
    if not subject_terms and not sender and not older_than_days:
        return (
            "Error: At least one filter is required (subject_keyword, sender, "
            "or older_than_days), or pass message_ids=[...]. "
            "This prevents accidentally moving everything."
        )

    if not allow_filter_scan:
        return _filter_scan_disabled_error("move_email")

    effective_recent_days = recent_days if older_than_days is None else 0

    # Refuse unbounded scans on destructive tools. The id-direct path (above)
    # is always safe. older_than_days provides a date_to bound so it is safe
    # even when recent_days=0; only refuse when BOTH are absent.
    if older_than_days is None and recent_days <= 0:
        tool_error = ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=(
                "move_email refuses to scan without a date window; "
                "pass recent_days=7 or older_than_days=30 or message_ids=[...]"
            ),
            remediation={
                "preferred": "Pass recent_days=7 or older_than_days=30 or message_ids=[...]",
                "fallback_tool": "full_inbox_export",
                "fallback_tool_args": {
                    "account": account,
                },
            },
        )
        return serialize_tool_error(tool_error)

    if dry_run:
        try:
            records = _search_mail_records(
                account=account,
                mailbox=from_mailbox,
                subject_terms=subject_terms or None,
                sender=sender,
                read_status="read" if only_read else "all",
                date_from=_date_from_for_recent_days(effective_recent_days),
                date_to=_date_to_for_older_than(older_than_days),
                include_content=False,
                offset=0,
                limit=max_moves + 1,
                timeout=timeout if timeout is not None else 45,
                recent_days=effective_recent_days,
            )
        except AppleScriptTimeout:
            return _with_filter_scan_warning(
                f"Error: move_email dry-run timed out on account '{account}'. "
                "Retry with message_ids=[...] or a larger timeout."
            )
        return _with_filter_scan_warning(
            _format_dry_run_records(
                f"DRY RUN - PREVIEW MOVE: {from_mailbox} -> {to_mailbox}",
                records,
                "Would move",
                max_moves,
            )
        )

    search_timeout = timeout if timeout is not None else min(effective_timeout, 120)
    try:
        resolved_ids = _search_message_ids(
            account=account,
            mailbox=from_mailbox,
            subject_terms=subject_terms or None,
            sender=sender,
            read_status="read" if only_read else "all",
            date_from=_date_from_for_recent_days(effective_recent_days),
            date_to=_date_to_for_older_than(older_than_days),
            limit=max_moves,
            timeout=search_timeout,
            recent_days=effective_recent_days,
        )
    except AppleScriptTimeout:
        return _with_filter_scan_warning(
            f"Error: move_email timed out on account '{account}'. "
            "Prefer message_ids=[...] or retry with a larger timeout."
        )

    if not resolved_ids:
        return _with_filter_scan_warning(f"No matching emails found in {from_mailbox} for account '{account}'.")

    return _with_filter_scan_warning(
        _move_email_by_message_ids(
            account=account,
            from_mailbox=from_mailbox,
            to_mailbox=to_mailbox,
            message_ids=resolved_ids,
            max_moves=max_moves,
            dry_run=False,
            timeout=effective_timeout,
            dest_ref=dest_ref,
        )
    )


@mcp.tool(annotations=WRITE_TOOL_ANNOTATIONS)
@inject_preferences
def save_email_attachment(
    account: str | None = None,
    subject_keyword: str = "",
    attachment_name: str = "",
    save_path: str = "",
    message_ids: list[str] | None = None,
    attachment_index: int | None = None,
    timeout: int | None = None,
    max_size_bytes: int = 100 * 1024 * 1024,
) -> str:
    """
    Save a specific attachment from an email to disk.

    When ``message_ids`` is provided, locates the message by exact ID and
    ignores ``subject_keyword``. Prefer ``attachment_index`` from
    ``list_email_attachments(output_format="json")`` for deterministic
    selection; ``attachment_name`` remains for compatibility and rejects
    ambiguous duplicate matches.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to DEFAULT_MAIL_ACCOUNT.
        subject_keyword: Keyword to search for in email subjects (omit when message_ids is set)
        attachment_name: Name of the attachment to save
        save_path: Full path where to save the attachment
        message_ids: Optional list of exact Mail message ids for precise targeting
        attachment_index: Optional 1-based attachment index from
            ``list_email_attachments(output_format="json")``. Requires exactly
            one ``message_id``.
        timeout: Optional AppleScript timeout in seconds (default: 120s).
        max_size_bytes: Maximum attachment size in bytes (default: 100 MB). Refuses to
            save attachments larger than this limit. Also checks that the target directory
            has at least ``max_size_bytes + 100 MB`` of free disk space before saving.
            Pass a larger value (e.g. ``max_size_bytes=500*1024*1024``) to raise the cap,
            or save manually via Mail UI for very large attachments.

    Returns:
        Confirmation message with save location
    """
    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: account is required (and no DEFAULT_MAIL_ACCOUNT configured)."
    if message_ids is None and subject_keyword:
        return target_selector_deprecated_error(
            "save_email_attachment",
            ("subject_keyword",),
            preferred="Call search_emails(..., has_attachments=True) first, then pass message_ids=[...].",
            discovery="search_emails(subject_keyword=..., has_attachments=True, recent_days=..., limit=...)",
            exact_selector="message_ids",
        )

    account_err = validate_account_name(account, timeout=30 if timeout is None else min(timeout, 30))
    if account_err:
        return account_err

    if attachment_index is not None and attachment_index < 1:
        return "Error: attachment_index must be a positive 1-based integer."
    if message_ids is None and (not subject_keyword or not (attachment_name or attachment_index) or not save_path):
        return "Error: subject_keyword, attachment_name or attachment_index, and save_path are required."
    if not (attachment_name or attachment_index) or not save_path:
        return "Error: attachment_name or attachment_index, and save_path are required."

    if message_ids is None:
        try:
            records = _search_mail_records(
                account=account,
                mailbox="INBOX",
                subject_terms=[subject_keyword],
                include_content=False,
                offset=0,
                limit=1,
                timeout=timeout if timeout is not None else 45,
            )
        except AppleScriptTimeout:
            return (
                f"Error: AppleScript timed out while locating attachment email "
                f"on account {account!r}. Try again with a tighter subject or "
                "larger `timeout`."
            )
        if not records:
            return (
                "⚠ Attachment not found\n"
                f"Email keyword: {escape_applescript(subject_keyword)}\n"
                f"Attachment name: {escape_applescript(attachment_name)}"
            )
        resolved_ids = [str(record.get("message_id")) for record in records if record.get("message_id") is not None]
        if not resolved_ids:
            return (
                "⚠ Attachment not found\n"
                f"Email keyword: {escape_applescript(subject_keyword)}\n"
                f"Attachment name: {escape_applescript(attachment_name)}"
            )
        message_ids = resolved_ids

    if message_ids is not None:
        normalized_ids = normalize_message_ids(message_ids)
        if not normalized_ids:
            return "Error: 'message_ids' must contain one or more numeric Mail ids"
        cap_error = _check_message_ids_cap(normalized_ids, "save_email_attachment")
        if cap_error:
            return cap_error
        if attachment_index is not None and len(normalized_ids) != 1:
            err = ToolError(
                code="AMBIGUOUS_ATTACHMENT_SELECTOR",
                message="attachment_index requires exactly one message_id because indexes are per message.",
                remediation={
                    "preferred": (
                        "Call list_email_attachments(message_ids=[...], output_format='json') and then "
                        "call save_email_attachment(message_ids=[one_id], attachment_index=N, ...)."
                    ),
                    "exact_selector": "message_ids + attachment_index",
                },
            )
            return serialize_tool_error(err)
        id_condition = build_whose_id_list(normalized_ids)
        message_filter_script = f"set inboxMessages to every message of inboxMailbox whose {id_condition}"
        not_found_detail = f"Message ids: {', '.join(normalized_ids)}"

    # Expand tilde in save_path (POSIX file in AppleScript does not expand ~)
    expanded_path = str(Path(save_path).expanduser())

    # Path validation: use shared helper for home-dir + sensitive-dir checks
    path_err = validate_save_path(
        expanded_path,
        path_label="Save path",
        sensitive_action="save attachments to",
    )
    if path_err:
        return path_err

    save_path_obj = Path(expanded_path).resolve()
    expanded_path = str(save_path_obj)
    save_dir = save_path_obj.parent

    # Escape for AppleScript
    escaped_account = escape_applescript(account)
    escaped_attachment = escape_applescript(attachment_name)
    escaped_path = escape_applescript(expanded_path)
    use_attachment_index = attachment_index is not None
    attachment_index_value = attachment_index if attachment_index is not None else 0
    attachment_selector_label = (
        f"Attachment index: {attachment_index}" if use_attachment_index else f"Attachment name: {escaped_attachment}"
    )

    # --- Attachment size probe ---
    # Run a cheap AppleScript to get the attachment file size before saving.
    # ``file size of anAttachment`` is available on macOS 10.15+. We wrap it in
    # a try block so that if the property is absent on an older OS the probe
    # returns -1 and we skip the cap (safe fail-open rather than blocking saves).
    _probe_normalized_ids = normalize_message_ids(message_ids) if message_ids else []
    if _probe_normalized_ids:
        id_condition = build_whose_id_list(_probe_normalized_ids)
        _probe_message_lookup = f"every message of inboxMailbox whose {id_condition}"
    else:
        _probe_message_lookup = "messages 1 thru 1 of inboxMailbox"
    _escaped_att_probe = escape_applescript(attachment_name)
    _attachment_index_probe = attachment_index_value
    _probe_script = f'''
    tell application "Mail"
        try
            set targetAccount to account "{escaped_account}"
            {inbox_mailbox_script("inboxMailbox", "targetAccount")}
            set probeMessages to {_probe_message_lookup}
            set matchCount to 0
            set firstSize to -1
            repeat with aMessage in probeMessages
                set msgAttachments to mail attachments of aMessage
                set attachmentCount to count of msgAttachments
                repeat with attachmentIndex from 1 to attachmentCount
                    set anAttachment to item attachmentIndex of msgAttachments
                    set attachmentMatches to false
                    if {_attachment_index_probe} > 0 then
                        if attachmentIndex is {_attachment_index_probe} then set attachmentMatches to true
                    else if (name of anAttachment) contains "{_escaped_att_probe}" then
                        set attachmentMatches to true
                    end if
                    if attachmentMatches then
                        set matchCount to matchCount + 1
                        try
                            if firstSize is -1 then set firstSize to file size of anAttachment as integer
                        on error
                            if firstSize is -1 then set firstSize to -1
                        end try
                    end if
                end repeat
            end repeat
            return (matchCount as string) & "|||" & (firstSize as string)
        on error
            return "0|||-1"
        end try
    end tell
    '''
    _probe_timeout = 30 if timeout is None else min(timeout, 30)
    try:
        _probe_raw = run_applescript(_probe_script, timeout=_probe_timeout).strip()
        if "|||" in _probe_raw:
            _match_count_text, _size_text = (_probe_raw.split("|||", 1) + ["-1"])[:2]
            _attachment_match_count = int(_match_count_text) if _match_count_text.isdigit() else 0
            _attachment_size = int(_size_text) if _size_text.lstrip("-").isdigit() else -1
        else:
            _attachment_size = int(_probe_raw) if _probe_raw.lstrip("-").isdigit() else -1
            _attachment_match_count = 1 if _attachment_size >= 0 else 0
    except (AppleScriptTimeout, ValueError, OSError):
        _attachment_size = -1
        _attachment_match_count = 0

    if not use_attachment_index and _attachment_match_count > 1:
        err = ToolError(
            code="AMBIGUOUS_ATTACHMENT_SELECTOR",
            message=(
                f"Attachment name '{attachment_name}' matched {_attachment_match_count} attachments. "
                "Filename substring selection is ambiguous."
            ),
            remediation={
                "preferred": (
                    "Call list_email_attachments(message_ids=[...], output_format='json') and retry with "
                    "attachment_index for the chosen row."
                ),
                "exact_selector": "message_ids + attachment_index",
                "matches": _attachment_match_count,
            },
        )
        return serialize_tool_error(err)

    if _attachment_size >= 0:
        if _attachment_size > max_size_bytes:
            err = ToolError(
                code="ATTACHMENT_TOO_LARGE",
                message=(
                    f"Attachment '{attachment_name or f'index {attachment_index}'}' is {_attachment_size:,} bytes "
                    f"({_attachment_size / (1024 * 1024):.1f} MB), which exceeds the "
                    f"cap of {max_size_bytes:,} bytes ({max_size_bytes / (1024 * 1024):.0f} MB)."
                ),
                remediation={
                    "preferred": (f"Pass max_size_bytes={_attachment_size + 1} to raise the cap for this attachment"),
                    "alternative": "Use Mail UI to save the attachment manually",
                    "actual_size_bytes": _attachment_size,
                    "cap_bytes": max_size_bytes,
                },
            )
            return serialize_tool_error(err)

        # Disk-space guard: require attachment_size + 100 MB buffer free
        _disk_buffer = 100 * 1024 * 1024
        _required_free = _attachment_size + _disk_buffer
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            _free_bytes = shutil.disk_usage(save_dir).free
        except OSError:
            _free_bytes = None

        if _free_bytes is not None and _free_bytes < _required_free:
            err = ToolError(
                code="ATTACHMENT_TOO_LARGE",
                message=(
                    f"Insufficient disk space in '{save_dir}': "
                    f"{_free_bytes:,} bytes free, need at least "
                    f"{_required_free:,} bytes "
                    f"(attachment {_attachment_size:,} bytes + 100 MB buffer)."
                ),
                remediation={
                    "preferred": "Free up disk space and retry",
                    "alternative": "Use Mail UI to save the attachment manually",
                    "free_bytes": _free_bytes,
                    "required_bytes": _required_free,
                },
            )
            return serialize_tool_error(err)

    # Cap candidate set for subject search only — ID lookup is exact.
    # Sourced from ``constants.SCAN_BOUNDS["TRASH_SCAN"]`` so the cap is tunable
    # in one place alongside other bounded-scan limits.
    scan_cap = SCAN_BOUNDS["TRASH_SCAN"]
    cap_script = ""
    if message_ids is None:
        cap_script = f"""
            if (count of inboxMessages) > {scan_cap} then
                set inboxMessages to items 1 thru {scan_cap} of inboxMessages
            end if"""

    script = f'''
    tell application "Mail"
        set outputText to ""

        try
            set targetAccount to account "{escaped_account}"
            {inbox_mailbox_script("inboxMailbox", "targetAccount")}
            {message_filter_script}
            {cap_script}
            set foundAttachment to false

            repeat with aMessage in inboxMessages
                try
                    set messageSubject to subject of aMessage
                    set msgAttachments to mail attachments of aMessage

                    set attachmentCount to count of msgAttachments
                    repeat with attachmentLoopIndex from 1 to attachmentCount
                        set anAttachment to item attachmentLoopIndex of msgAttachments
                        set attachmentFileName to name of anAttachment
                        set attachmentMatches to false
                        if {attachment_index_value} > 0 then
                            if attachmentLoopIndex is {attachment_index_value} then set attachmentMatches to true
                        else if attachmentFileName contains "{escaped_attachment}" then
                            set attachmentMatches to true
                        end if

                        if attachmentMatches then
                            -- Save the attachment
                            save anAttachment in POSIX file "{escaped_path}"

                            set outputText to "✓ Attachment saved successfully!" & return & return
                            set outputText to outputText & "Email: " & messageSubject & return
                            set outputText to outputText & "Attachment: " & attachmentFileName & return
                            set outputText to outputText & "Saved to: {escaped_path}" & return

                            set foundAttachment to true
                            exit repeat
                        end if
                    end repeat

                    if foundAttachment then exit repeat
                end try
            end repeat

            if not foundAttachment then
                set outputText to "⚠ Attachment not found" & return
                set outputText to outputText & "{not_found_detail}" & return
                set outputText to outputText & "{attachment_selector_label}" & return
            end if

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''

    try:
        result = run_applescript(script, timeout=timeout if timeout is not None else 120)
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out while saving attachment from account "
            f"{account!r}. Try again or pass a larger `timeout`."
        )
    return result


@mcp.tool(annotations=IDEMPOTENT_WRITE_TOOL_ANNOTATIONS)
@inject_preferences
def update_email_status(
    account: str | None = None,
    action: str = "mark_read",
    subject_keyword: str | None = None,
    subject_keywords: list[str] | None = None,
    sender: str | None = None,
    mailbox: str = "INBOX",
    max_updates: int = 10,
    apply_to_all: bool = False,
    message_ids: list[str] | None = None,
    older_than_days: int | None = None,
    recent_days: float = 2.0,
    allow_filter_scan: bool = False,
    timeout: int | None = None,
) -> str:
    """
    Update email status - mark as read/unread or flag/unflag emails.

    Preferred: pass ``message_ids`` from a prior list/search call.
    Filter-based updates require ``allow_filter_scan=True`` (slow on large mailboxes).

    When message_ids is provided, uses exact ID matching (ignores other filters).

    When ``account`` is None the configured ``DEFAULT_MAIL_ACCOUNT`` is used.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to DEFAULT_MAIL_ACCOUNT.
        action: Action to perform: "mark_read", "mark_unread", "flag", "unflag"
        subject_keyword: Filter by subject (requires allow_filter_scan=True)
        subject_keywords: Optional list of subject keywords; matches any keyword
        sender: Optional sender to filter emails by (requires allow_filter_scan=True)
        mailbox: Mailbox to search in (default: "INBOX")
        max_updates: Maximum number of emails to update (safety limit, default: 10)
        apply_to_all: Bulk update without filters (requires allow_filter_scan=True)
        message_ids: List of exact Mail message ids (preferred path)
        older_than_days: Optional age filter - only update emails older than N days
        recent_days: Recent window when using filter scan (default: 2.0).
        allow_filter_scan: Opt in to slow filter scans (default: False).
        timeout: Optional AppleScript timeout in seconds (default: 300s).

    Returns:
        Confirmation message with details of updated emails
    """
    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: account is required (and no DEFAULT_MAIL_ACCOUNT configured)."
    deprecated_selectors = _deprecated_target_selectors(
        subject_keyword=subject_keyword,
        subject_keywords=subject_keywords,
        sender=sender,
    )
    if message_ids is None and deprecated_selectors:
        return target_selector_deprecated_error(
            "update_email_status",
            deprecated_selectors,
            preferred="Call search_emails(...) or list_inbox_emails(...) first, then pass message_ids=[...].",
            discovery="search_emails(..., recent_days=..., limit=...) or list_inbox_emails(...)",
            exact_selector="message_ids",
        )

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    safe_account = escape_applescript(account)
    effective_timeout = timeout if timeout is not None else 300

    # Build action scripts
    if action == "mark_read":
        bulk_action_script = "set read status of targetMessages to true"
        single_action_script = "set read status of aMessage to true"
        action_label = "Marked as read"
    elif action == "mark_unread":
        bulk_action_script = "set read status of targetMessages to false"
        single_action_script = "set read status of aMessage to false"
        action_label = "Marked as unread"
    elif action == "flag":
        bulk_action_script = "set flagged status of targetMessages to true"
        single_action_script = "set flagged status of aMessage to true"
        action_label = "Flagged"
    elif action == "unflag":
        bulk_action_script = "set flagged status of targetMessages to false"
        single_action_script = "set flagged status of aMessage to false"
        action_label = "Unflagged"
    else:
        return f"Error: Invalid action '{action}'. Use: mark_read, mark_unread, flag, unflag"

    # --- ID-based path (fast, ignores other filters) ---
    if message_ids is not None:
        normalized_ids = normalize_message_ids(message_ids)
        if not normalized_ids:
            return "Error: 'message_ids' must contain one or more numeric Mail ids"
        cap_error = _check_message_ids_cap(normalized_ids, "update_email_status")
        if cap_error:
            return cap_error

        id_condition = build_whose_id_list(normalized_ids)

        script = f'''
        tell application "Mail"
            with timeout of {effective_timeout} seconds
                set outputText to "UPDATING EMAIL STATUS BY IDS: {action_label}" & return & return
                set updateCount to 0

                try
                    set targetAccount to account "{safe_account}"
                    {build_mailbox_ref(mailbox, var_name="targetMailbox")}

                    set targetMessages to every message of targetMailbox whose {id_condition}
                    set requestedCount to {len(normalized_ids)}

                    if (count of targetMessages) > 0 then
                        try
                            {bulk_action_script}
                        on error errMsg number errNum
                            set outputText to outputText & "BULKERR|errNum=" & errNum & " errMsg=" & errMsg & return
                            repeat with aMessage in targetMessages
                                {single_action_script}
                            end repeat
                        end try

                        repeat with aMessage in targetMessages
                            try
                                set messageSubject to subject of aMessage
                                set messageSender to sender of aMessage
                                set messageDate to date received of aMessage

                                set outputText to outputText & "- {action_label}: " & messageSubject & return
                                set outputText to outputText & "   From: " & messageSender & return
                                set outputText to outputText & "   Date: " & (messageDate as string) & return & return
                                set updateCount to updateCount + 1
                            end try
                        end repeat
                    end if

                    set outputText to outputText & "========================================" & return
                    set outputText to outputText & "REQUESTED IDS: " & requestedCount & return
                    set outputText to outputText & "TOTAL UPDATED: " & updateCount & " email(s)" & return
                    set outputText to outputText & "========================================" & return

                on error errMsg
                    return "Error: " & errMsg
                end try

                return outputText
            end timeout
        end tell
        '''

        try:
            return run_applescript(script, timeout=effective_timeout)
        except AppleScriptTimeout:
            return f"Error: update_email_status timed out after {effective_timeout}s on account '{account}'."

    # --- Filter-based path ---
    subject_terms = normalize_search_terms(subject_keyword, subject_keywords)

    # Safety check: require at least one filter or explicit apply_to_all
    has_filter = bool(subject_terms) or bool(sender) or (older_than_days is not None and older_than_days > 0)
    if not has_filter and not apply_to_all:
        return (
            "Error: No filter provided. Provide message_ids=[...], subject_keyword, sender, "
            "or older_than_days to filter emails, or set apply_to_all=True."
        )

    if not allow_filter_scan:
        return _filter_scan_disabled_error("update_email_status")

    effective_recent_days = recent_days if older_than_days is None else 0.0

    if action in {"mark_read", "mark_unread"}:
        search_read = "unread" if action == "mark_read" else "read"
        search_timeout = timeout if timeout is not None else min(effective_timeout, 120)
        try:
            resolved_ids = _search_message_ids(
                account=account,
                mailbox=mailbox,
                subject_terms=subject_terms or None,
                sender=sender,
                read_status=search_read,
                date_from=_date_from_for_recent_days(effective_recent_days),
                date_to=_date_to_for_older_than(older_than_days),
                limit=max_updates,
                timeout=search_timeout,
                recent_days=effective_recent_days,
            )
        except AppleScriptTimeout:
            return _with_filter_scan_warning(
                f"Error: update_email_status timed out after {effective_timeout}s on account '{account}'."
            )
        if not resolved_ids:
            return _with_filter_scan_warning(f"No matching emails found in {mailbox} for account '{account}'.")
        return _with_filter_scan_warning(
            update_email_status(
                account=account,
                action=action,
                mailbox=mailbox,
                message_ids=resolved_ids,
                max_updates=max_updates,
                timeout=timeout,
            )
        )

    scan_cap = min(500, max(max_updates * 10, 50))
    per_msg_checks: list[str] = []
    if action == "flag":
        per_msg_checks.append("flagged status of aMessage is false")
    else:
        per_msg_checks.append("flagged status of aMessage is true")
    if subject_terms:
        subject_checks = " or ".join(f'messageSubject contains "{escape_applescript(term)}"' for term in subject_terms)
        per_msg_checks.append(f"({subject_checks})")
    if sender:
        per_msg_checks.append(f'messageSender contains "{escape_applescript(sender)}"')
    date_setup = ""
    if older_than_days and older_than_days > 0:
        date_setup = f"set cutoffDate to (current date) - ({older_than_days} * days)"
        per_msg_checks.append("messageDate < cutoffDate")

    combined_condition = " and ".join(per_msg_checks)

    script = f'''
    tell application "Mail"
        with timeout of {effective_timeout} seconds
            set outputText to "UPDATING EMAIL STATUS: {action_label}" & return & return
            set updateCount to 0

            try
                set targetAccount to account "{safe_account}"
                {build_mailbox_ref(mailbox, var_name="targetMailbox")}
                {date_setup}

                set matchingMessages to {{}}
                set candidateMessages to {{}}
                set messageCount to count of messages of targetMailbox
                if messageCount > {scan_cap} then
                    set scanUpperBound to {scan_cap}
                else
                    set scanUpperBound to messageCount
                end if
                if scanUpperBound > 0 then
                    set candidateMessages to messages 1 thru scanUpperBound of targetMailbox
                end if

                repeat with aMessage in candidateMessages
                    if (count of matchingMessages) >= {max_updates} then exit repeat
                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage
                        if {combined_condition} then
                            set end of matchingMessages to aMessage
                        end if
                    end try
                end repeat

                repeat with aMessage in matchingMessages
                    try
                        {single_action_script}
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage

                        set outputText to outputText & "- {action_label}: " & messageSubject & return
                        set outputText to outputText & "   From: " & messageSender & return
                        set outputText to outputText & "   Date: " & (messageDate as string) & return & return
                        set updateCount to updateCount + 1
                    end try
                end repeat

                set outputText to outputText & "========================================" & return
                set outputText to outputText & "TOTAL UPDATED: " & updateCount & " email(s)" & return
                set outputText to outputText & "========================================" & return

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end timeout
    end tell
    '''

    try:
        return _with_filter_scan_warning(run_applescript(script, timeout=effective_timeout))
    except AppleScriptTimeout:
        return _with_filter_scan_warning(
            f"Error: update_email_status timed out after {effective_timeout}s on account '{account}'."
        )


@mcp.tool(annotations=DESTRUCTIVE_TOOL_ANNOTATIONS)
@inject_preferences
def manage_trash(
    account: str | None = None,
    action: str = "move_to_trash",
    message_ids: list[str] | None = None,
    subject_keyword: str | None = None,
    subject_keywords: list[str] | None = None,
    sender: str | None = None,
    mailbox: str = "INBOX",
    max_deletes: int = 5,
    confirm_empty: bool = False,
    apply_to_all: bool = False,
    older_than_days: int | None = None,
    dry_run: bool = True,
    recent_days: float = 2.0,
    allow_filter_scan: bool = False,
    timeout: int | None = None,
) -> str:
    """
    Manage trash operations - delete emails or empty trash.

    Preferred: pass ``message_ids`` from a prior list/search call.
    Filter-based trash ops require ``allow_filter_scan=True`` (slow on large mailboxes).
    When dry_run=True (default), previews without acting; fast with message_ids.

    When ``message_ids`` is provided for ``move_to_trash`` or ``delete_permanent``,
    targets exact IDs and ignores keyword/sender filters.

    When ``account`` is None the configured ``DEFAULT_MAIL_ACCOUNT`` is used.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to DEFAULT_MAIL_ACCOUNT.
        action: Action to perform: "move_to_trash", "delete_permanent", "empty_trash"
        message_ids: List of exact Mail message ids (preferred path)
        subject_keyword: Filter by subject (requires allow_filter_scan=True)
        subject_keywords: Optional list of subject keywords; matches any keyword
        sender: Optional sender to filter emails (requires allow_filter_scan=True)
        mailbox: Source mailbox (default: "INBOX", not used for empty_trash or delete_permanent)
        max_deletes: Maximum number of emails to delete (safety limit, default: 5)
        confirm_empty: Must be True to execute "empty_trash" action (safety confirmation)
        apply_to_all: Bulk trash without filters (requires allow_filter_scan=True)
        older_than_days: Optional age filter - only affect emails older than N days
        dry_run: If True (default), preview what would be affected without acting
        recent_days: Recent window when using filter scan (default: 2.0).
        allow_filter_scan: Opt in to slow filter scans (default: False).
        timeout: Optional AppleScript timeout in seconds (default: 300s).

    Returns:
        Confirmation message with details of deleted emails
    """
    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: account is required (and no DEFAULT_MAIL_ACCOUNT configured)."
    deprecated_selectors = _deprecated_target_selectors(
        subject_keyword=subject_keyword,
        subject_keywords=subject_keywords,
        sender=sender,
    )
    if message_ids is None and action != "empty_trash" and deprecated_selectors:
        return target_selector_deprecated_error(
            "manage_trash",
            deprecated_selectors,
            preferred="Call search_emails(...) or list_inbox_emails(...) first, then pass message_ids=[...].",
            discovery="search_emails(..., recent_days=..., limit=...) or list_inbox_emails(...)",
            exact_selector="message_ids",
        )

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    subject_terms = normalize_search_terms(subject_keyword, subject_keywords)
    effective_timeout = timeout if timeout is not None else 300
    effective_recent_days = recent_days if older_than_days is None else 0

    if message_ids is not None:
        if action == "empty_trash":
            return "Error: message_ids cannot be used with empty_trash"

        normalized_ids = normalize_message_ids(message_ids)
        if not normalized_ids:
            return "Error: 'message_ids' must contain one or more numeric Mail ids"
        cap_error = _check_message_ids_cap(normalized_ids, "manage_trash")
        if cap_error:
            return cap_error

        id_condition = build_whose_id_list(normalized_ids)

        if action == "move_to_trash":
            mode_label = "DRY RUN - PREVIEW TRASH BY IDS" if dry_run else "MOVING EMAILS TO TRASH BY IDS"
            move_script = "" if dry_run else "move aMessage to trashMailbox"
            result_verb = "Would trash" if dry_run else "Moved to trash"
            trash_setup = (
                ""
                if dry_run
                else """
                    set trashMailbox to mailbox "Trash" of targetAccount"""
            )
            mailbox_ref = build_mailbox_ref(mailbox, var_name="sourceMailbox")
        elif action == "delete_permanent":
            mode_label = (
                "DRY RUN - PREVIEW PERMANENT DELETE BY IDS" if dry_run else "PERMANENTLY DELETING EMAILS BY IDS"
            )
            move_script = "" if dry_run else "delete aMessage"
            result_verb = "Would permanently delete" if dry_run else "Permanently deleted"
            trash_setup = ""
            mailbox_ref = 'set sourceMailbox to mailbox "Trash" of targetAccount'
        else:
            return f"Error: Invalid action '{action}'. Use: move_to_trash, delete_permanent, empty_trash"

        script = f'''
        tell application "Mail"
            with timeout of {effective_timeout} seconds
                set outputText to "{mode_label}" & return & return
                set deleteCount to 0

                try
                    set targetAccount to account "{safe_account}"
                    {mailbox_ref}
                    {trash_setup}

                    set matchingMessages to every message of sourceMailbox whose {id_condition}
                    if (count of matchingMessages) > {max_deletes} then
                        set matchingMessages to items 1 thru {max_deletes} of matchingMessages
                    end if

                    repeat with aMessage in matchingMessages
                        try
                            set messageSubject to subject of aMessage
                            set messageSender to sender of aMessage
                            set messageDate to date received of aMessage

                            {move_script}

                            set outputText to outputText & "{result_verb}: " & messageSubject & return
                            set outputText to outputText & "   From: " & messageSender & return
                            set outputText to outputText & "   Date: " & (messageDate as string) & return & return
                            set deleteCount to deleteCount + 1
                        end try
                    end repeat

                    set outputText to outputText & "========================================" & return
                    set outputText to outputText & "REQUESTED IDS: {len(normalized_ids)}" & return
                    set outputText to outputText & "TOTAL: " & deleteCount & " email(s) {result_verb.lower()}" & return
                    set outputText to outputText & "========================================" & return

                on error errMsg
                    return "Error: " & errMsg
                end try

                return outputText
            end timeout
        end tell
        '''

        try:
            return run_applescript(script, timeout=effective_timeout)
        except AppleScriptTimeout:
            return f"Error: manage_trash timed out after {effective_timeout}s on account '{account}'."

    # Refuse unbounded scans on destructive filter paths (move_to_trash and
    # delete_permanent). The id-direct path and empty_trash are exempt.
    # older_than_days provides a date_to bound so it is safe even when
    # recent_days=0; only refuse when BOTH are absent.
    if action != "empty_trash" and older_than_days is None and recent_days <= 0:
        tool_error = ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=(
                "manage_trash refuses to scan without a date window; "
                "pass recent_days=7 or older_than_days=30 or message_ids=[...]"
            ),
            remediation={
                "preferred": "Pass recent_days=7 or older_than_days=30 or message_ids=[...]",
                "fallback_tool": "full_inbox_export",
                "fallback_tool_args": {
                    "account": account,
                },
            },
        )
        return serialize_tool_error(tool_error)

    if action == "empty_trash":
        if not confirm_empty:
            return (
                "Error: empty_trash permanently deletes ALL messages in the trash. Set confirm_empty=True to proceed."
            )
        script = f'''
        tell application "Mail"
            with timeout of {effective_timeout} seconds
                set outputText to "EMPTYING TRASH" & return & return

                try
                    set targetAccount to account "{safe_account}"
                    set trashMailbox to mailbox "Trash" of targetAccount
                    set messageCount to count of messages of trashMailbox
                    set deleteCount to 0

                    if messageCount > {max_deletes} then
                        set trashMessages to messages 1 thru {max_deletes} of trashMailbox
                    else
                        set trashMessages to messages of trashMailbox
                    end if

                    repeat with aMessage in trashMessages
                        delete aMessage
                        set deleteCount to deleteCount + 1
                    end repeat

                    set outputText to outputText & "✓ Emptied trash for account: {safe_account}" & return
                    set outputText to outputText & "   Deleted " & deleteCount & " of " & messageCount & " message(s)" & return
                    if deleteCount < messageCount then
                        set outputText to outputText & "   (limited by max_deletes=" & {max_deletes} & ")" & return
                    end if

                on error errMsg
                    return "Error: " & errMsg
                end try

                return outputText
            end timeout
        end tell
        '''
    elif action == "delete_permanent":
        # Safety check: require at least one filter or explicit apply_to_all
        if not subject_terms and not sender and not apply_to_all:
            return (
                "Error: No filter provided. Provide message_ids=[...], subject_keyword, sender, "
                "or set apply_to_all=True."
            )

        if not allow_filter_scan:
            return _filter_scan_disabled_error("manage_trash")

        # Build search condition with escaped inputs
        conditions = []
        if subject_terms:
            conditions.append(contains_any_condition("subject", subject_terms))
        if sender:
            conditions.append(f'sender contains "{escape_applescript(sender)}"')

        if conditions:
            search_timeout = timeout if timeout is not None else min(effective_timeout, 120)
            try:
                resolved_ids = _search_message_ids(
                    account=account,
                    mailbox="Trash",
                    subject_terms=subject_terms or None,
                    sender=sender,
                    limit=max_deletes,
                    timeout=search_timeout,
                    recent_days=effective_recent_days,
                )
            except AppleScriptTimeout:
                return _with_filter_scan_warning(
                    f"Error: manage_trash timed out after {effective_timeout}s on account '{account}'."
                )
            if not resolved_ids:
                return _with_filter_scan_warning(f"No matching emails found in Trash for account '{account}'.")
            return _with_filter_scan_warning(
                manage_trash(
                    account=account,
                    action="delete_permanent",
                    message_ids=resolved_ids,
                    max_deletes=max_deletes,
                    dry_run=dry_run,
                    timeout=timeout,
                )
            )

        matching_messages_script = (
            f"if (count of messages of trashMailbox) > {max_deletes} then\n"
            f"                        set matchingMessages to messages 1 thru {max_deletes} of trashMailbox\n"
            f"                    else\n"
            f"                        set matchingMessages to messages of trashMailbox\n"
            f"                    end if"
        )

        mode_label = "DRY RUN - PREVIEW PERMANENT DELETE" if dry_run else "PERMANENTLY DELETING EMAILS"
        delete_script = "" if dry_run else "delete aMessage"
        result_verb = "Would permanently delete" if dry_run else "Permanently deleted"
        total_label = "TOTAL WOULD DELETE" if dry_run else "TOTAL DELETED"

        script = f'''
        tell application "Mail"
            with timeout of {effective_timeout} seconds
                set outputText to "{mode_label}" & return & return
                set deleteCount to 0

                try
                    set targetAccount to account "{safe_account}"
                    set trashMailbox to mailbox "Trash" of targetAccount
                    {matching_messages_script}
                    set matchingCount to count of matchingMessages

                    if matchingCount is 0 then
                        set targetMessages to {{}}
                    else if matchingCount > {max_deletes} then
                        set targetMessages to items 1 thru {max_deletes} of matchingMessages
                    else
                        set targetMessages to matchingMessages
                    end if

                    repeat with aMessage in targetMessages
                        try
                            set messageSubject to subject of aMessage
                            set messageSender to sender of aMessage

                            set outputText to outputText & "✓ {result_verb}: " & messageSubject & return
                            set outputText to outputText & "   From: " & messageSender & return & return

                            {delete_script}
                            set deleteCount to deleteCount + 1
                        end try
                    end repeat

                    set outputText to outputText & "========================================" & return
                    set outputText to outputText & "{total_label}: " & deleteCount & " email(s)" & return
                    set outputText to outputText & "========================================" & return

                on error errMsg
                    return "Error: " & errMsg
                end try

                return outputText
            end timeout
        end tell
        '''
    else:  # move_to_trash
        # Safety check: require at least one filter or explicit apply_to_all
        has_filter = bool(subject_terms) or bool(sender) or (older_than_days is not None and older_than_days > 0)
        if not has_filter and not apply_to_all:
            return (
                "Error: No filter provided. Provide message_ids=[...], subject_keyword, sender, "
                "or older_than_days, or set apply_to_all=True."
            )

        if not allow_filter_scan:
            return _filter_scan_disabled_error("manage_trash")

        if dry_run:
            try:
                records = _search_mail_records(
                    account=account,
                    mailbox=mailbox,
                    subject_terms=subject_terms or None,
                    sender=sender,
                    date_from=_date_from_for_recent_days(effective_recent_days),
                    date_to=_date_to_for_older_than(older_than_days),
                    include_content=False,
                    offset=0,
                    limit=max_deletes + 1,
                    timeout=timeout if timeout is not None else 45,
                    recent_days=effective_recent_days,
                )
            except AppleScriptTimeout:
                return _with_filter_scan_warning(
                    f"Error: manage_trash dry-run timed out on account '{account}'. "
                    "Prefer message_ids=[...] or a larger timeout."
                )
            return _with_filter_scan_warning(
                _format_dry_run_records(
                    "DRY RUN - PREVIEW TRASH",
                    records,
                    "Would trash",
                    max_deletes,
                )
            )

        search_timeout = timeout if timeout is not None else min(effective_timeout, 120)
        try:
            resolved_ids = _search_message_ids(
                account=account,
                mailbox=mailbox,
                subject_terms=subject_terms or None,
                sender=sender,
                date_from=_date_from_for_recent_days(effective_recent_days),
                date_to=_date_to_for_older_than(older_than_days),
                limit=max_deletes,
                timeout=search_timeout,
                recent_days=effective_recent_days,
            )
        except AppleScriptTimeout:
            return _with_filter_scan_warning(
                f"Error: manage_trash timed out after {effective_timeout}s on account '{account}'."
            )
        if not resolved_ids:
            return _with_filter_scan_warning(f"No matching emails found in {mailbox} for account '{account}'.")
        return _with_filter_scan_warning(
            manage_trash(
                account=account,
                action="move_to_trash",
                message_ids=resolved_ids,
                mailbox=mailbox,
                max_deletes=max_deletes,
                dry_run=False,
                timeout=timeout,
            )
        )

    try:
        result = run_applescript(script, timeout=effective_timeout)
        if action == "empty_trash":
            return result
        return _with_filter_scan_warning(result)
    except AppleScriptTimeout:
        if action == "empty_trash":
            return f"Error: manage_trash timed out after {effective_timeout}s on account '{account}'."
        return _with_filter_scan_warning(
            f"Error: manage_trash timed out after {effective_timeout}s on account '{account}'."
        )


@mcp.tool(annotations=WRITE_TOOL_ANNOTATIONS)
@inject_preferences
def create_mailbox(
    account: str | None = None,
    name: str = "",
    parent_mailbox: str | None = None,
    timeout: int | None = None,
) -> str:
    """
    Create a new mailbox (folder) in the specified account.

    Supports nested paths via the parent_mailbox parameter (e.g.,
    parent_mailbox="Projects" + name="2024" creates Projects/2024).
    You can also pass a full slash-separated path as *name*
    (e.g., "Projects/2024/ClientName") and omit parent_mailbox.

    When ``account`` is None the configured ``DEFAULT_MAIL_ACCOUNT`` is used.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to DEFAULT_MAIL_ACCOUNT.
        name: Name for the new mailbox. May contain "/" to create a
              nested path in one call (each segment is created if needed).
        parent_mailbox: Optional existing parent folder for nesting.
        timeout: Optional AppleScript timeout in seconds (default: 120s).

    Returns:
        Confirmation with the new mailbox path.
    """
    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: account is required (and no DEFAULT_MAIL_ACCOUNT configured)."

    account_err = validate_account_name(account)
    if account_err:
        return account_err

    # Validate name
    if not name or not name.strip():
        return "Error: Mailbox name cannot be empty."

    # Split name into segments (support "A/B/C" shorthand)
    segments = [s.strip() for s in name.split("/") if s.strip()]
    if not segments:
        return "Error: Mailbox name cannot be empty."

    for seg in segments:
        if _INVALID_MAILBOX_CHARS.search(seg):
            return (
                f"Error: Invalid characters in mailbox name segment '{seg}'. "
                'Avoid \\ " < > | ? * : and control characters.'
            )

    safe_account = escape_applescript(account)

    # If parent_mailbox is given, prepend its segments
    if parent_mailbox:
        parent_segments = [s.strip() for s in parent_mailbox.split("/") if s.strip()]
        segments = parent_segments + segments

    # Build AppleScript to create each level one at a time
    create_blocks = ""
    for depth in range(len(segments)):
        seg = escape_applescript(segments[depth])
        if depth == 0:
            create_blocks += f'''
            try
                set parentRef to mailbox "{seg}" of targetAccount
            on error
                make new mailbox at targetAccount with properties {{name:"{seg}"}}
                set parentRef to mailbox "{seg}" of targetAccount
            end try
'''
        else:
            create_blocks += f'''
            try
                set parentRef to mailbox "{seg}" of parentRef
            on error
                make new mailbox at parentRef with properties {{name:"{seg}"}}
                set parentRef to mailbox "{seg}" of parentRef
            end try
'''

    full_path = "/".join(segments)
    safe_path = escape_applescript(full_path)

    script = f'''
    tell application "Mail"
        set outputText to "CREATING MAILBOX" & return & return

        try
            set targetAccount to account "{safe_account}"

            {create_blocks}

            set outputText to outputText & "OK Mailbox created successfully!" & return & return
            set outputText to outputText & "Account: {safe_account}" & return
            set outputText to outputText & "Path: {safe_path}" & return

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''

    return run_applescript(script, timeout=timeout if timeout is not None else 120)


@mcp.tool(annotations=IDEMPOTENT_WRITE_TOOL_ANNOTATIONS)
@inject_preferences
def synchronize_account(
    account: str | None = None,
    confirm_sync: bool = False,
    all_accounts: bool = False,
) -> str:
    """
    Force Mail.app to synchronize an account (or every account) with its
    IMAP / Exchange server right now. Equivalent to clicking the
    refresh button next to the account or selecting Mailbox → Synchronize.

    Use after `move_email`, `update_email_status`, or `manage_trash`
    when downstream clients (iPhone, web mail, etc.) need to see the
    change immediately. Mail.app's natural sync cadence is "automatic"
    which can be several minutes — this collapses that to one IMAP push.

    Implementation note:
    --------------------
    Uses the `synchronize with <account>` AppleScript verb (per Mail.sdef:
    "Command to trigger synchronizing of an IMAP account with the server")
    rather than `check for new mail`. The latter is receive-only — it
    pulls new messages but does NOT push pending IMAP commands like
    queued moves / archives / flag changes. With `check for new mail`,
    archives done via `move_email` could sit in Mail.app's local cache
    for several minutes before reaching the IMAP server, leaving iPhone
    Mail (which reads IMAP directly) showing already-archived messages
    still in INBOX. `synchronize with` is the bidirectional verb that
    drains pending IMAP commands AND fetches new mail.

    Mail.app's synchronize is potentially long-running. We wrap each
    invocation in `with timeout of N seconds` so the AppleScript returns
    promptly. When the timeout fires (error -1712) Mail.app keeps the
    sync running in the background — exactly the fire-and-forget
    semantics callers expect.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to
                 DEFAULT_MAIL_ACCOUNT when configured.
        confirm_sync: Required explicit opt-in. Synchronizing can make Mail.app
                 download a large backlog of messages, so agents and test
                 batteries must not trigger it implicitly.
        all_accounts: Required in addition to confirm_sync=True to sync every
                 configured account.

    Returns:
        Confirmation string with the account(s) synced or queued.
    """
    # 8 s is comfortably longer than a healthy IMAP sync but short enough
    # that a stuck account / network blip doesn't block the MCP call. On
    # timeout we report "queued" — the sync continues asynchronously.
    PER_ACCOUNT_TIMEOUT_S = 8

    if account is None and _server.DEFAULT_MAIL_ACCOUNT and not all_accounts:
        account = _server.DEFAULT_MAIL_ACCOUNT

    if account is None or not account.strip():
        if not all_accounts:
            return (
                "Error: account is required (and no DEFAULT_MAIL_ACCOUNT configured). "
                "Set all_accounts=True with confirm_sync=True to sync every account."
            )
        if not confirm_sync:
            return (
                "Error: synchronize_account all-account sync requires confirm_sync=True. "
                "Synchronizing can trigger Mail.app to download a large message backlog; "
                "do not call it from routine tests."
            )
        # Probe account count to compute a scaled outer timeout.
        # Without scaling, a 4-account setup needs ~32s but the outer
        # wrapper fires at 13s — SIGKILL mid-sync causes partial IMAP commits.
        try:
            account_names = list_mail_account_names(timeout=PER_ACCOUNT_TIMEOUT_S)
        except AppleScriptTimeout:
            account_names = []
        account_count = max(len(account_names), 1)
        outer_timeout = PER_ACCOUNT_TIMEOUT_S * account_count + 5

        script = f"""
        tell application "Mail"
            set acctNames to {{}}
            set queuedNames to {{}}
            repeat with a in accounts
                set acctName to name of a
                set end of acctNames to acctName
                try
                    with timeout of {PER_ACCOUNT_TIMEOUT_S} seconds
                        synchronize with a
                    end timeout
                on error errMsg number errNum
                    if errNum is -1712 then
                        set end of queuedNames to acctName
                    end if
                end try
            end repeat
            set AppleScript's text item delimiters to ", "
            if (count of queuedNames) > 0 then
                return "Synchronized all accounts: " & (acctNames as string) & " (queued: " & (queuedNames as string) & ")"
            else
                return "Synchronized all accounts: " & (acctNames as string)
            end if
        end tell
        """
        return run_applescript(script, timeout=outer_timeout)

    account = account.strip()
    account_err = validate_account_name(account, timeout=PER_ACCOUNT_TIMEOUT_S)
    if account_err:
        return account_err
    if not confirm_sync:
        return (
            f"Error: synchronize_account for '{account}' requires confirm_sync=True. "
            "Synchronizing can trigger Mail.app to download a large message backlog."
        )

    acct_escaped = escape_applescript(account)
    script = f'''
    tell application "Mail"
        try
            set targetAccount to first account whose name is "{acct_escaped}"
            try
                with timeout of {PER_ACCOUNT_TIMEOUT_S} seconds
                    synchronize with targetAccount
                end timeout
                return "Synchronized: {acct_escaped}"
            on error errMsg number errNum
                if errNum is -1712 then
                    return "Synchronizing: {acct_escaped} (queued — push in progress)"
                end if
                return "Error: " & errMsg
            end try
        on error errMsg
            return "Error: " & errMsg
        end try
    end tell
    '''
    return run_applescript(script, timeout=PER_ACCOUNT_TIMEOUT_S + 5)
