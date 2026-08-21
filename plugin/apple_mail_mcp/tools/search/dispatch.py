"""Account-list, single/multi-account fan-out, and sync bridge.

Calls to ``run_applescript`` are routed through the ``search`` package facade so
existing ``patch('...tools.search.run_applescript')`` test seams keep firing.
``asyncio`` is imported plainly: tests patch ``...tools.search.asyncio.to_thread``
on the shared module object, which this module honors via the same import.

The legacy Sent-mailbox ``fetch_replied_ids`` probe that used to live here was
removed once ``search_emails`` switched to Mail's native ``was_replied_to``
property (read in the same per-message pass, no second AppleScript round
trip; see ``core.reply_state.was_replied_fragment`` and
``tasks/active/reply-state-annotation/plan-2026-07-10.md``); it had no other
callers in this package. ``smart_inbox.get_needs_response`` and
``inbox.list_inbox_emails`` keep their own copies for the opt-in Sent-scan
paths those tools still support.
"""

import asyncio
from typing import Any

from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import search
from apple_mail_mcp.tools.search.records import _parse_search_records, _search_error_detail
from apple_mail_mcp.tools.search.script import _build_search_script, _list_accounts_script

_VALID_SORTS = frozenset({"date_desc", "date_asc"})
_VALID_READ_STATUSES = frozenset({"all", "read", "unread"})


def _validate_dispatch_args(*, offset: int, limit: int, sort: str, read_status: str) -> None:
    """Reject page bounds and enums that must never reach ``script``.

    Both dispatch entry points cross this seam — ``_search_mail_records``
    (async) and ``_search_mail_records_sync`` — because only the async one used
    to check, and the sync bridge called ``_search_one_account`` directly.
    ``script._build_search_script`` derives its scan bound as
    ``base_cap = limit + 1 + offset``, so an unchecked non-positive ``limit`` or
    negative ``offset`` produced a slice that a read-only live probe across four
    Mail backends showed is silently wrong rather than an error:

    * ``messages 1 thru 1`` (``limit=0``) binds the newest message and returns
      it, so a page the caller sized at zero yields one real message.
    * ``messages 1 thru 0`` (``limit=-1``) does **not** raise on a non-empty
      mailbox. AppleScript clamps index 0 up to 1 and hands back exactly ONE
      message, verified by id to be the first. There is nothing to catch.
    * ``messages 1 thru -1`` (``limit=-2``, or an ``offset`` negative enough to
      cancel the limit) is end-relative and spans the ENTIRE mailbox.

    The sync bridge feeds ``move_email`` and ``manage_trash``, so a bound
    accepted here can select a message the caller never chose. Enforcing
    ``offset >= 0`` and ``limit >= 1`` is what makes ``base_cap >= 2`` an
    invariant of every script this package builds.

    Raising is deliberate. The async path used to answer ``limit <= 0`` with
    ``return [], [], [], False``, an authoritative empty a caller cannot
    distinguish from "no matches" — the AGENTIC-2344 shape. ``ValueError``
    matches the ``offset`` guard that path already had, and
    ``analytics.export`` already catches it on this bridge
    (``except ValueError as exc: return f"Error: {exc}"``). Tools that want a
    structured refusal validate ahead of this seam, as ``search_emails`` does
    with ``UNBOUNDED_SCAN_REQUIRED``.
    """
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if limit <= 0:
        # Deliberately does not quote the resulting slice: that would restate
        # `script`'s `base_cap` arithmetic in a second place and go stale the
        # next time the builder's cap math is touched.
        raise ValueError(
            f"limit must be >= 1 (got {limit}); a non-positive page size builds a scan "
            "slice Mail resolves to real messages instead of none"
        )
    if sort not in _VALID_SORTS:
        raise ValueError("Invalid sort. Use: date_desc, date_asc")
    if read_status not in _VALID_READ_STATUSES:
        raise ValueError("Invalid read_status. Use: all, read, unread")


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

    When account is None, dispatches one AppleScript per account sequentially
    (each call still runs off the event loop via ``asyncio.to_thread``, since
    Mail.app AppleScript is now serialized behind a single-flight lock and
    concurrent dispatch only adds thread churn), so wall time is the sum
    across accounts rather than the slowest single account. A per-account
    ``AppleScriptTimeout`` becomes an entry in the returned errors list — the
    call still returns whatever other accounts produced.

    ``body_search_capped`` is True when the body-search auto-cap (100 messages)
    fired because no explicit ``date_from`` was passed.

    Raises ``ValueError`` for a bad page bound or enum (see
    ``_validate_dispatch_args``); a non-positive ``limit`` used to return an
    empty tuple here, which read as an authoritative zero.
    """
    _validate_dispatch_args(offset=offset, limit=limit, sort=sort, read_status=read_status)

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
                {
                    "account": account,
                    "mailbox": e["mailbox"],
                    "type": e.get("type", "mailbox_error"),
                    "message": e["message"],
                }
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
                        "type": e.get("type", "mailbox_error"),
                        "message": e["message"],
                    }
                )

    return combined, errors, error_details, any_body_capped


def _search_mail_records_sync(**kwargs: Any) -> list[dict[str, Any]]:
    """Synchronous bridge for sync tools that need preflight records.

    Callers are ``move_email`` and ``manage_trash`` (through the ``manage``
    facade alias ``_search_mail_records``) and ``export_emails``' filtered scope
    (direct import). Returns just the record list. When a per-account
    ``AppleScriptTimeout`` was caught inside the async helper, re-raise it here
    so sync callers can surface a structured "timed out" error rather than
    silently treating it as "no matches". Sync callers should pass an explicit
    ``account`` so this stays a single-account dispatch and avoids the
    multi-account gather path.

    Bounds are validated here rather than only inside ``_search_mail_records``:
    the ``account`` branch below calls ``_search_one_account`` directly and so
    skipped that guard entirely, handing a non-positive ``limit`` or negative
    ``offset`` straight to the script builder. Raises ``ValueError`` for those
    (see ``_validate_dispatch_args``). Two of the three callers are mutations,
    so this bridge fails loudly instead of returning a plausible record set.
    """
    offset = kwargs.get("offset", 0)
    limit = kwargs.get("limit", 100)
    read_status = kwargs.get("read_status", "all")
    _validate_dispatch_args(
        offset=offset,
        limit=limit,
        sort=kwargs.get("sort", "date_desc"),
        read_status=read_status,
    )

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
            read_status=read_status,
            date_from=kwargs.get("date_from"),
            date_to=kwargs.get("date_to"),
            include_content=kwargs.get("include_content", False),
            content_length=kwargs.get("content_length", 300),
            offset=offset,
            limit=limit,
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
