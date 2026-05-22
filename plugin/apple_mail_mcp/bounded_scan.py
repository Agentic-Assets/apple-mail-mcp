"""Bounded scan helpers — the only sanctioned producers of ScanWindow tokens.

Phase A of the ``whose``-elimination refactor. See
``tasks/whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md`` and
``05-codebase-whose-map.md`` § 7 for the helper signatures and the
bounded-slice-before-``whose`` pattern these helpers encode.
"""

from __future__ import annotations

from apple_mail_mcp.backend.base import ScanWindow, ToolError
from apple_mail_mcp.core import normalize_message_ids


MAX_SCAN_DAYS = 365
MAX_SCAN_LIMIT = 10_000

_ISSUER = "core.bounded_inbox_scan"


def _unbounded_remediation(mailbox: str) -> dict:
    return {
        "preferred": "Pass recent_days=7 or limit=200",
        "fallback_tool": "full_inbox_export",
        "fallback_tool_args": {"mailbox": mailbox},
    }


def bounded_inbox_scan(
    *,
    mailbox: str,
    recent_days: float | None = None,
    limit: int | None = None,
    since: float | None = None,
) -> ScanWindow:
    """Return a validated ``ScanWindow`` capability token.

    At least one of ``recent_days``, ``limit``, or ``since`` must be set
    AND fall inside its module-level cap. Otherwise ``ToolError`` is
    raised with remediation pointing at ``full_inbox_export`` — the
    explicit, audited escape hatch for callers that truly need an
    unbounded pass.
    """
    if not mailbox or not str(mailbox).strip():
        raise ToolError(
            code="INVALID_SCAN_WINDOW",
            message="bounded_inbox_scan requires a non-empty mailbox name.",
        )

    bounded = False

    if recent_days is not None:
        if recent_days <= 0 or recent_days > MAX_SCAN_DAYS:
            raise ToolError(
                code="UNBOUNDED_SCAN_REQUIRED",
                message=(
                    f"recent_days must be in (0, {MAX_SCAN_DAYS}]; got "
                    f"{recent_days!r}."
                ),
                remediation=_unbounded_remediation(mailbox),
            )
        bounded = True

    if limit is not None:
        if limit <= 0 or limit > MAX_SCAN_LIMIT:
            raise ToolError(
                code="UNBOUNDED_SCAN_REQUIRED",
                message=(
                    f"limit must be in (0, {MAX_SCAN_LIMIT}]; got {limit!r}."
                ),
                remediation=_unbounded_remediation(mailbox),
            )
        bounded = True

    if since is not None:
        if since <= 0:
            raise ToolError(
                code="UNBOUNDED_SCAN_REQUIRED",
                message=f"since must be a positive epoch timestamp; got {since!r}.",
                remediation=_unbounded_remediation(mailbox),
            )
        bounded = True

    if not bounded:
        raise ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=(
                "bounded_inbox_scan requires at least one of recent_days, "
                "limit, or since."
            ),
            remediation=_unbounded_remediation(mailbox),
        )

    return ScanWindow(
        mailbox=mailbox,
        recent_days=recent_days,
        limit=limit,
        since=since,
        _issued_by=_ISSUER,
    )


def build_bounded_message_scan(
    mailbox_var: str,
    limit: int,
    whose_condition: str | None = None,
) -> str:
    """Return an AppleScript snippet that binds ``candidateMessages``.

    Mirrors the safe pattern used in ``tools/inbox.py:128-146``: slice a
    bounded newest-first window FIRST, then optionally apply a ``whose``
    filter against that small in-memory list. Mail.app must never be
    asked to materialize an entire remote mailbox just to evaluate a
    ``whose`` clause.
    """
    if not isinstance(limit, int) or limit <= 0:
        raise ToolError(
            code="INVALID_SCAN_WINDOW",
            message=f"build_bounded_message_scan requires limit > 0; got {limit!r}.",
        )

    snippet = (
        f"set _mbCount to count of messages of {mailbox_var}\n"
        f"            if _mbCount > {limit} then\n"
        f"                set candidateMessages to messages 1 thru {limit} of {mailbox_var}\n"
        f"            else\n"
        f"                set candidateMessages to messages of {mailbox_var}\n"
        f"            end if"
    )

    if whose_condition:
        snippet += (
            f"\n            set candidateMessages to "
            f"(candidateMessages whose {whose_condition})"
        )

    return snippet


def compute_scan_upper_bound(
    recent_days: float,
    base_cap: int = 200,
    window_cap: int = 500,
) -> int:
    """Derive a bounded slice size from a ``recent_days`` window.

    Mirrors the existing logic at ``tools/search.py:268-283``: tools that
    need to look back further than the default window scale the cap up,
    but never beyond ``window_cap``. ``base_cap`` applies for the
    smallest windows.
    """
    if recent_days is None or recent_days <= 0:
        return base_cap
    scaled = int(base_cap + (recent_days * 50))
    if scaled < base_cap:
        return base_cap
    if scaled > window_cap:
        return window_cap
    return scaled


def build_whose_id_list(message_ids: list[str]) -> str:
    """Return an AppleScript ``id is X or id is Y`` snippet for targeted ops.

    Input is validated through ``core.normalize_message_ids`` so only
    numeric Mail message ids ever reach AppleScript — this is the safe
    write-path use of ``whose`` (small, in-process id list, no remote
    materialization).
    """
    clean = normalize_message_ids(message_ids)
    if not clean:
        raise ToolError(
            code="INVALID_SCAN_WINDOW",
            message="build_whose_id_list requires at least one numeric message id.",
        )
    return " or ".join(f"id is {mid}" for mid in clean)


__all__ = [
    "MAX_SCAN_DAYS",
    "MAX_SCAN_LIMIT",
    "bounded_inbox_scan",
    "build_bounded_message_scan",
    "compute_scan_upper_bound",
    "build_whose_id_list",
]
