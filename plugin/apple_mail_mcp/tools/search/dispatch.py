"""Account-list, single/multi-account fan-out, sync bridge, and replied-id probe.

Calls to ``run_applescript`` are routed through the ``search`` package facade so
existing ``patch('...tools.search.run_applescript')`` test seams keep firing.
``asyncio`` is imported plainly: tests patch ``...tools.search.asyncio.to_thread``
on the shared module object, which this module honors via the same import.
"""

import asyncio
from typing import Any

from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.core import fetch_replied_ids as _core_fetch_replied_ids
from apple_mail_mcp.tools import search
from apple_mail_mcp.tools.search.records import _parse_search_records, _search_error_detail
from apple_mail_mcp.tools.search.script import _build_search_script, _list_accounts_script


def fetch_replied_ids(account: str, sent_cap: int = 200, timeout: int | None = 60) -> set[str]:
    """Fetch replied Message-ID set using this module's ``run_applescript``.

    Wraps the core helper so tests that patch
    ``apple_mail_mcp.tools.search.run_applescript`` also cover the
    Sent-mailbox probe.
    """
    return _core_fetch_replied_ids(account, sent_cap=sent_cap, timeout=timeout, runner=search.run_applescript)


def _list_mail_accounts(timeout: int | None = 30) -> list[str]:
    """Return the list of Mail account names. Cheap (<1s) on any setup."""
    raw = search.run_applescript(_list_accounts_script(), timeout=timeout)
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
    result = search.run_applescript(script, timeout=timeout if timeout is not None else 180)
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
        # A per-account AppleScriptTimeout propagates unchanged to the caller.
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

    records, errors, error_details, _body_capped = asyncio.run(_search_mail_records(**kwargs))
    if errors and not records:
        non_timeout = [item for item in error_details if item.get("type") != "timeout"]
        if non_timeout:
            detail = "; ".join(f"{item['account']}: {item['type']}: {item['message']}" for item in non_timeout)
            raise RuntimeError(f"AppleScript failed for account(s): {detail}")
        raise AppleScriptTimeout(f"AppleScript timed out for account(s): {', '.join(errors)}")
    return records
