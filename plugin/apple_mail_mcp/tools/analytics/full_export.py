"""``full_inbox_export`` tool plus its batched metadata-walk script builder and parser."""

import asyncio
import json
import logging
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import analytics

logger = logging.getLogger(__name__)

_FULL_EXPORT_DEFAULT_FIELDS = (
    "subject",
    "sender",
    "date_received",
    "read_status",
    "message_id",
)
_FULL_EXPORT_ALLOWED_FIELDS = (
    "subject",
    "sender",
    "date_received",
    "date_sent",
    "read_status",
    "flagged_status",
    "message_id",
    "mailbox",
)
_FULL_EXPORT_FIELD_SEP = "__APPLE_MAIL_MCP_FIELD__"
_FULL_EXPORT_ROW_SEP = "__APPLE_MAIL_MCP_ROW__"
_FULL_EXPORT_ERROR_PREFIX = "__APPLE_MAIL_MCP_FULL_EXPORT_ERROR__|||"


_FULL_EXPORT_FIELD_EXPRS: dict[str, str] = {
    "subject": "(subject of aMessage)",
    "sender": "(sender of aMessage)",
    "date_received": "((date received of aMessage) as string)",
    "date_sent": "((date sent of aMessage) as string)",
    "read_status": "((read status of aMessage) as string)",
    "flagged_status": "((flagged status of aMessage) as string)",
    "message_id": "((id of aMessage) as string)",
    "mailbox": '"INBOX"',
}


def _full_export_field_script(field: str) -> str:
    """Return AppleScript expression that yields *field* for ``aMessage``."""
    try:
        return _FULL_EXPORT_FIELD_EXPRS[field]
    except KeyError:
        raise ValueError(f"Unsupported field: {field}") from None


def _normalize_full_export_fields(fields: Any | None) -> list[str]:
    """Normalize MCP/CLI field input to a list of field names.

    mcporter named flags pass ``--fields subject,sender`` as a string even
    though the MCP schema advertises a list. Accept both forms here so the
    tool remains usable through generated wrappers.
    """
    if fields is None:
        return list(_FULL_EXPORT_DEFAULT_FIELDS)
    if isinstance(fields, str):
        return [part.strip() for part in fields.split(",") if part.strip()]
    return [str(field).strip() for field in fields if str(field).strip()]


def _full_export_batch_script(
    *,
    account: str,
    mailbox: str,
    start_index: int,
    end_index: int,
    fields: list[str],
) -> str:
    """Build AppleScript that emits rows of ``fields`` for one batch.

    Each row is delimited by ``_FULL_EXPORT_ROW_SEP`` (RS, 0x1E); each field
    within a row by ``_FULL_EXPORT_FIELD_SEP`` (US, 0x1F). The script binds
    only ``messages start_index thru end_index`` of the mailbox — never the
    whole list — so a 24K-message inbox is walked in O(batch_size) AppleScript
    work per round-trip. Numeric indices are AppleScript-safe; only the
    user-supplied ``account`` / ``mailbox`` strings are escaped.
    """
    safe_account = escape_applescript(account)
    safe_mailbox = escape_applescript(mailbox)

    # AppleScript has no inline `try` expression. Build one assignment per
    # requested field, then concatenate the variables into the output row.
    field_assignments = []
    field_vars = []
    for idx, field in enumerate(fields):
        var_name = f"fieldValue{idx}"
        field_vars.append(var_name)
        field_assignments.append(
            f"""
                    set {var_name} to ""
                    try
                        set {var_name} to {_full_export_field_script(field)}
                    on error
                        set {var_name} to ""
                    end try
            """
        )
    row_expr = f' & "{_FULL_EXPORT_FIELD_SEP}" & '.join(field_vars) if field_vars else '""'
    field_assignment_script = "".join(field_assignments)

    return f'''
    tell application "Mail"
        set outputRows to {{}}
        try
            set targetAccount to account "{safe_account}"
            try
                set targetMailbox to mailbox "{safe_mailbox}" of targetAccount
            on error
                if "{safe_mailbox}" is "INBOX" then
                    {inbox_mailbox_script("targetMailbox", "targetAccount")}
                else
                    error "Mailbox not found: {safe_mailbox}"
                end if
            end try

            set totalMessages to count of messages of targetMailbox
            set startIndex to {start_index}
            set endIndex to {end_index}
            if startIndex > totalMessages then
                set AppleScript's text item delimiters to ""
                return ""
            end if
            if endIndex > totalMessages then
                set endIndex to totalMessages
            end if

            set batchMessages to messages startIndex thru endIndex of targetMailbox
            repeat with aMessage in batchMessages
                try
                    {field_assignment_script}
                    set rowText to {row_expr}
                    set end of outputRows to rowText
                end try
            end repeat
        on error errMsg
            return "{_FULL_EXPORT_ERROR_PREFIX}" & errMsg
        end try

        set AppleScript's text item delimiters to "{_FULL_EXPORT_ROW_SEP}"
        set outputText to outputRows as string
        set AppleScript's text item delimiters to ""
        return outputText
    end tell
    '''


def _full_export_parse_batch(raw: str, fields: list[str]) -> list[dict[str, Any]]:
    if not raw:
        return []
    records: list[dict[str, Any]] = []
    for row in raw.split(_FULL_EXPORT_ROW_SEP):
        row = row.strip("\n\r")
        if not row:
            continue
        parts = row.split(_FULL_EXPORT_FIELD_SEP)
        if len(parts) < len(fields):
            parts = parts + [""] * (len(fields) - len(parts))
        record: dict[str, Any] = {}
        for field, value in zip(fields, parts, strict=False):
            text = value.strip()
            if field in ("read_status", "flagged_status"):
                record[field] = text.lower() == "true"
            else:
                record[field] = text
        records.append(record)
    return records


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
async def full_inbox_export(
    account: str | None = None,
    mailbox: str = "INBOX",
    fields: list[str] | str | None = None,
    max_emails: int = 10_000,
    batch_size: int = 500,
    output_format: str = "json",
    timeout: int | None = None,
    ctx: Any | None = None,
) -> str:
    """
    Walk every message in the specified mailbox and return their metadata.

    EXPENSIVE: on a 24,000-message inbox this can take 2-5 minutes. Use this
    only when you really need the entire inbox — for normal queries use
    ``list_inbox_emails(max_emails=50)`` or ``search_emails(recent_days=7)``
    instead.

    Streams progress notifications in batches of ``batch_size``. Returns JSON
    (or NDJSON if ``output_format='ndjson'``) with message metadata only — no
    message bodies, no attachments. To fetch bodies, follow up with
    ``get_email_by_id``.

    Caps at ``max_emails=10000`` by default to prevent runaway. Set explicitly
    for larger inboxes.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Falls back to
            ``DEFAULT_MAIL_ACCOUNT`` when None.
        mailbox: Mailbox to walk (default: ``"INBOX"``).
        fields: Metadata fields to include for each message. Defaults to
            ``["subject", "sender", "date_received", "read_status",
            "message_id"]``. Allowed: ``subject``, ``sender``,
            ``date_received``, ``date_sent``, ``read_status``,
            ``flagged_status``, ``message_id``, ``mailbox``.
        max_emails: Hard upper bound on messages returned (default 10000).
        batch_size: Messages fetched per AppleScript round-trip (default 500).
        output_format: ``"json"`` (default) or ``"ndjson"``.
        timeout: Per-batch AppleScript timeout in seconds (default 120).

    Returns:
        JSON-encoded list of message dicts, or newline-delimited JSON if
        ``output_format="ndjson"``. Each dict contains the requested
        ``fields``.
    """

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: 'account' is required (no DEFAULT_MAIL_ACCOUNT configured)"

    if output_format not in {"json", "ndjson"}:
        return "Error: Invalid output_format. Use: json, ndjson"

    if batch_size <= 0:
        return "Error: batch_size must be a positive integer"

    if max_emails <= 0:
        return "Error: max_emails must be a positive integer"

    resolved_fields = _normalize_full_export_fields(fields)
    invalid = [f for f in resolved_fields if f not in _FULL_EXPORT_ALLOWED_FIELDS]
    if invalid:
        allowed = ", ".join(_FULL_EXPORT_ALLOWED_FIELDS)
        return f"Error: invalid field(s): {', '.join(invalid)}. Allowed: {allowed}"

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = analytics.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    per_batch_timeout = timeout if timeout is not None else 120

    collected: list[dict[str, Any]] = []
    start_index = 1
    while start_index <= max_emails:
        remaining = max_emails - len(collected)
        if remaining <= 0:
            break
        this_batch = min(batch_size, remaining)
        end_index = start_index + this_batch - 1

        script = _full_export_batch_script(
            account=account,
            mailbox=mailbox,
            start_index=start_index,
            end_index=end_index,
            fields=resolved_fields,
        )

        try:
            raw = await asyncio.to_thread(analytics.run_applescript, script, per_batch_timeout)
        except AppleScriptTimeout:
            return (
                f"Error: AppleScript timed out while exporting '{mailbox}' "
                f"for '{account}' at batch {start_index}-{end_index}"
            )

        if raw.startswith(_FULL_EXPORT_ERROR_PREFIX):
            err = raw[len(_FULL_EXPORT_ERROR_PREFIX) :] or "unknown error"
            return f"Error: {err}"

        batch = _full_export_parse_batch(raw, resolved_fields)
        if not batch:
            # End of mailbox: AppleScript returned nothing for this slice.
            break

        collected.extend(batch)

        # Stream progress to the MCP client when a Context is available.
        if ctx is not None:
            try:
                report = getattr(ctx, "report_progress", None)
                if report is not None:
                    result = report(
                        progress=float(len(collected)),
                        total=float(max_emails),
                        message=(f"Exported {len(collected)} messages (batch {start_index}-{end_index})"),
                    )
                    if asyncio.iscoroutine(result):
                        await result
            except Exception:  # pragma: no cover - progress is best-effort
                logger.debug(
                    "full_inbox_export progress notification failed",
                    exc_info=True,
                )

        if len(batch) < this_batch:
            # Mailbox exhausted mid-batch.
            break

        start_index = end_index + 1

    if output_format == "ndjson":
        return "\n".join(json.dumps(record, ensure_ascii=False) for record in collected)
    return json.dumps(collected, ensure_ascii=False)
