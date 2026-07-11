"""Shared reply-state row annotation: has_draft correlation + draft_scan aggregation.

Generic helper consumed by the inbox surface (``list_inbox_emails``,
``get_inbox_overview``), ``analytics/dashboard.py`` (``inbox_dashboard``),
and the ``search/`` surface (``search_emails``, ``get_email_by_id``,
``get_email_by_ids``, ``get_email_thread``, via ``date_field="received_date"``
since parsed search records key their received-date under a different
name), implementing the contract in
``tasks/active/reply-state-annotation/plan-2026-07-10.md``. Wraps
``core.reply_state.fetch_drafts_snapshot`` / ``DraftsSnapshot.matches()`` so
no tool surface re-implements per-account snapshot fan-out or the
``draft_scan`` response shape.

Every row passed in must already carry a native ``was_replied_to`` bool (set
from ``core.reply_state.was_replied_fragment()`` in the caller's own
AppleScript loop); this module only adds ``has_draft``.

Public API:

- ``annotate_rows_with_reply_state(rows, runner=..., timeout=..., ...)``:
  mutates *rows* in place, adding ``has_draft`` (``True``/``False``/``None``)
  to every row. Returns the accumulated ``dict[str, DraftsSnapshot]`` cache
  so callers that process one account at a time (``get_inbox_overview``) can
  share it across calls and build one combined summary at the end.
- ``build_draft_scan_status(snapshots)``: turns that cache into the
  top-level ``draft_scan`` object: ``{"status", "scanned", "accounts", ...}``.
"""

from __future__ import annotations

from typing import Any

from apple_mail_mcp.core.applescript import AppleScriptRunner
from apple_mail_mcp.core.reply_state import DraftsSnapshot, fetch_drafts_snapshot, resolve_has_draft

# Multi-account fan-out never fetches more than this many Drafts snapshots
# per call, mirroring the plan's "capped at 5 accounts" contract.
MAX_DRAFT_SNAPSHOT_ACCOUNTS = 5


def build_draft_scan_status(snapshots: dict[str, DraftsSnapshot]) -> dict[str, Any]:
    """Aggregate per-account Drafts snapshots into a top-level ``draft_scan`` object.

    Returns ``{"status": "skipped", "scanned": 0, "accounts": []}`` when
    *snapshots* is empty (no account ever needed a Drafts scan: either
    ``include_draft_state=False`` or no row carried a resolvable account).
    Otherwise ``status`` is ``"ok"`` only when every scanned account came
    back ``"ok"``; any ``"error"`` account flips the overall status to
    ``"error"`` and its message is folded into a combined ``"error"`` key.
    """
    if not snapshots:
        return {"status": "skipped", "scanned": 0, "accounts": []}

    accounts_detail: list[dict[str, Any]] = []
    errors: list[str] = []
    scanned_total = 0
    total_drafts = 0
    any_truncated = False
    all_ok = True
    for account, snapshot in snapshots.items():
        accounts_detail.append(
            {
                "account": account,
                "status": snapshot.status,
                "scanned": snapshot.scanned,
                "total": snapshot.total,
                "truncated": snapshot.truncated,
            }
        )
        scanned_total += snapshot.scanned
        total_drafts += snapshot.total if snapshot.total is not None else snapshot.scanned
        any_truncated = any_truncated or snapshot.truncated
        if snapshot.status != "ok":
            all_ok = False
            if snapshot.error:
                errors.append(f"{account}: {snapshot.error}")

    result: dict[str, Any] = {
        "status": "ok" if all_ok else "error",
        "scanned": scanned_total,
        "total": total_drafts,
        "truncated": any_truncated,
        "accounts": accounts_detail,
    }
    if errors:
        result["error"] = "; ".join(errors)
    return result


def annotate_rows_with_reply_state(
    rows: list[dict[str, Any]],
    *,
    runner: AppleScriptRunner,
    timeout: int,
    include_draft_state: bool = True,
    account: str | None = None,
    account_field: str = "account",
    date_field: str = "date",
    max_accounts: int = MAX_DRAFT_SNAPSHOT_ACCOUNTS,
    snapshots: dict[str, DraftsSnapshot] | None = None,
) -> dict[str, DraftsSnapshot]:
    """Annotate *rows* in place with ``has_draft``; return the snapshot cache used.

    Every row gains a ``has_draft`` key: ``True``/``False`` when a Drafts
    snapshot for its account came back ``"ok"``, ``None`` when the scan was
    skipped (``include_draft_state=False``) or the account's own snapshot
    errored or was never fetched. ``has_draft`` never silently becomes
    ``False`` on a failed or skipped scan.

    When *account* is given, every row is treated as belonging to that one
    account (an explicit override for callers such as ``get_inbox_overview``
    that already process one account's rows at a time). Otherwise each
    row's own ``row[account_field]`` groups it, and at most *max_accounts*
    distinct accounts (lazily, in first-seen row order) get a Drafts
    snapshot fetched; rows whose account did not make the cap get
    ``has_draft=None``.

    *date_field* names the row key holding the candidate's received-date
    string used by the subject+recipient+date correlation rule. Callers
    whose rows come from ``search.records._parse_search_records`` pass
    ``date_field="received_date"``; every other row shape in this codebase
    uses the default ``"date"``.

    Pass a shared *snapshots* dict across repeated calls (e.g. once per
    account in a loop) to reuse already-fetched snapshots and build one
    combined ``draft_scan`` via ``build_draft_scan_status()`` afterwards.
    The cache used (created fresh when *snapshots* is omitted) is always
    returned so the caller can pass it back in on the next call.
    """
    cache: dict[str, DraftsSnapshot] = {} if snapshots is None else snapshots

    if not include_draft_state:
        for row in rows:
            row["has_draft"] = None
        return cache

    if account is not None:
        # Nothing to correlate against an empty row list; skip the live
        # Drafts scan entirely rather than paying for an unused snapshot.
        accounts_needed: list[str] = [account] if rows else []
    else:
        accounts_needed = []
        for row in rows:
            row_account = row.get(account_field)
            if row_account and row_account not in accounts_needed:
                accounts_needed.append(row_account)

    for candidate_account in accounts_needed:
        if candidate_account in cache or len(cache) >= max_accounts:
            continue
        cache[candidate_account] = fetch_drafts_snapshot(candidate_account, runner, timeout)

    for row in rows:
        row_account = account if account is not None else row.get(account_field)
        snapshot = cache.get(row_account) if row_account else None
        row["has_draft"] = resolve_has_draft(
            snapshot,
            subject=row.get("subject") or "",
            sender_email=row.get("sender") or "",
            internet_message_id=row.get("internet_message_id"),
            email_date=row.get(date_field),
        )

    return cache
