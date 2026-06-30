"""The ``search_emails`` ``@mcp.tool`` and its windowing/replied-detection logic.

``validate_account_name`` is routed through the ``search`` package facade so the
conftest autouse patch and ``test_phase_a_fixes`` patch keep firing. ``asyncio``
is imported plainly (the shared module honors ``...search.asyncio`` patches), and
``DEFAULT_MAIL_ACCOUNT`` is read lazily off the ``_server`` module so tests can
monkeypatch ``apple_mail_mcp.server.DEFAULT_MAIL_ACCOUNT`` after import.
"""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.core import inject_preferences, list_mail_account_names, normalize_search_terms
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import search
from apple_mail_mcp.tools.search.dispatch import _search_mail_records, fetch_replied_ids
from apple_mail_mcp.tools.search.records import _body_scan_disabled_error, _build_search_response


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
        account_err = search.validate_account_name(account, timeout=validation_timeout)
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
