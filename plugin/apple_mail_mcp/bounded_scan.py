"""Bounded scan helpers — the only sanctioned producers of ScanWindow tokens.

Phase A of the ``whose``-elimination refactor. See
``tasks/whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md`` and
``05-codebase-whose-map.md`` § 7 for the helper signatures and the
bounded-slice-then-loop-filter pattern these helpers encode.

FORBIDDEN PATTERNS — lint-enforced by ``tests/test_no_unbounded_whose.py``:

1.  ``<sliceVar> whose <predicate>`` where ``<sliceVar>`` is bound via
    ``messages 1 thru N of MB``. AppleScript's ``whose`` over a list
    re-resolves the predicate against each ref's underlying physical
    folder; on Gmail that folder is ``[Gmail]/All Mail`` and Mail
    rejects the call with ``Can't get {message id N of mailbox
    "[Gmail]/All Mail" ...} whose ...``. This was the v3.4.x Gmail
    crash. The slice-binding variable names the lint watches are listed
    in ``tests/test_no_unbounded_whose.SLICE_BIND_VARS``.

2.  ``build_bounded_message_scan(..., whose_condition=...)`` — the
    helper raises ``ToolError(code="UNSAFE_WHOSE_ON_LIST")`` at
    construction time so the bug is unrepresentable, not just
    discouraged.

3.  ``every message of MB whose <non-id-predicate>`` without a
    downstream bounded slice. Use ``build_bounded_message_scan`` plus
    in-loop filtering.

USE INSTEAD: ``build_bounded_filtered_scan(mailbox_var, scan_cap,
target_max, condition_expr)`` — emits the safe bounded-slice + in-loop
``repeat ... if`` pattern by construction.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from apple_mail_mcp.backend.base import ScanWindow, ToolError
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import normalize_message_ids

MAX_SCAN_DAYS = 365
MAX_SCAN_LIMIT = 10_000

# Mail.app's AppleScript parser rejects `id is X or id is Y or ...` predicates
# beyond ~200-500 OR-terms (it varies by macOS version and is undocumented).
# Cap conservatively so a caller passing a runaway message_ids list gets a
# clear ToolError instead of a Mail crash or hang. Callers that need to act
# on >50 messages at once must chunk in Python (`iter_id_chunks` helper).
MAX_WHOSE_IDS = 50

_ISSUER = "core.bounded_inbox_scan"


def _unbounded_remediation(mailbox: str) -> dict[str, Any]:
    return {
        "preferred": f"Pass recent_days=7 or limit={SCAN_BOUNDS['SEARCH_WINDOW_CAP']}",
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
                message=(f"recent_days must be in (0, {MAX_SCAN_DAYS}]; got {recent_days!r}."),
                remediation=_unbounded_remediation(mailbox),
            )
        bounded = True

    if limit is not None:
        if limit <= 0 or limit > MAX_SCAN_LIMIT:
            raise ToolError(
                code="UNBOUNDED_SCAN_REQUIRED",
                message=(f"limit must be in (0, {MAX_SCAN_LIMIT}]; got {limit!r}."),
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
            message=("bounded_inbox_scan requires at least one of recent_days, limit, or since."),
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

    Slices a bounded newest-first window via ``messages 1 thru N``.
    Filtering this slice with a property predicate must go through
    ``build_bounded_filtered_scan`` — `whose` over the resulting list
    crashes on remote IMAP accounts where the underlying message refs
    span multiple physical folders (e.g. Gmail's ``[Gmail]/All Mail``).
    """
    if not isinstance(limit, int) or limit <= 0:
        raise ToolError(
            code="INVALID_SCAN_WINDOW",
            message=f"build_bounded_message_scan requires limit > 0; got {limit!r}.",
        )

    if whose_condition is not None:
        raise ToolError(
            code="UNSAFE_WHOSE_ON_LIST",
            message=(
                "build_bounded_message_scan no longer accepts whose_condition: "
                "AppleScript's `whose` clause is unreliable on a list of message "
                "references bound by `messages 1 thru N` (it crashes on Gmail "
                "where the refs point at [Gmail]/All Mail). Use "
                "build_bounded_filtered_scan(...) which emits an in-loop `if` "
                "filter — the only safe pattern."
            ),
        )

    return (
        f"set _mbCount to count of messages of {mailbox_var}\n"
        f"            if _mbCount > {limit} then\n"
        f"                set candidateMessages to messages 1 thru {limit} of {mailbox_var}\n"
        f"            else\n"
        f"                set candidateMessages to messages of {mailbox_var}\n"
        f"            end if"
    )


def build_bounded_filtered_scan(
    mailbox_var: str,
    scan_cap: int,
    target_max: int,
    condition_expr: str,
    *,
    output_var: str = "inboxMessages",
    candidate_var: str = "candidateMessages",
) -> str:
    """Return an AppleScript snippet that filters a bounded slice in-loop.

    Emits the only safe filter-by-property pattern for Mail.app: bind a
    bounded newest-first slice via ``messages 1 thru scan_cap``, then
    iterate in AppleScript and append messages that satisfy
    ``condition_expr`` to ``output_var``, stopping once ``target_max``
    matches are collected.

    ``condition_expr`` is an AppleScript expression evaluated per-message;
    use ``aMessage`` as the loop variable, e.g. ``read status of aMessage
    is false`` or ``(count of mail attachments of aMessage) > 0``. The
    expression is interpolated verbatim — callers MUST NOT pass
    user-controlled input.

    This replaces the historical "bind slice then `whose`" pattern which
    crashes on remote IMAP accounts (Gmail) because Mail evaluates the
    `whose` against the message refs' underlying folder
    (``[Gmail]/All Mail``) rather than the bound list.
    """
    if not isinstance(scan_cap, int) or scan_cap <= 0:
        raise ToolError(
            code="INVALID_SCAN_WINDOW",
            message=f"build_bounded_filtered_scan requires scan_cap > 0; got {scan_cap!r}.",
        )
    if not isinstance(target_max, int) or target_max <= 0:
        raise ToolError(
            code="INVALID_SCAN_WINDOW",
            message=f"build_bounded_filtered_scan requires target_max > 0; got {target_max!r}.",
        )
    if not condition_expr or not condition_expr.strip():
        raise ToolError(
            code="INVALID_SCAN_WINDOW",
            message="build_bounded_filtered_scan requires a non-empty condition_expr.",
        )

    bounded = build_bounded_message_scan(mailbox_var, scan_cap)
    return (
        f"{bounded}\n"
        f"            set {output_var} to {{}}\n"
        f"            repeat with aMessage in {candidate_var}\n"
        f"                try\n"
        f"                    if {condition_expr} then\n"
        f"                        set end of {output_var} to aMessage\n"
        f"                        if (count of {output_var}) ≥ {target_max} then exit repeat\n"
        f"                    end if\n"
        f"                end try\n"
        f"            end repeat"
    )


def compute_scan_upper_bound(
    recent_days: float,
    base_cap: int | None = None,
    window_cap: int | None = None,
    days_scale: int | None = None,
) -> int:
    """Derive a bounded slice size from a ``recent_days`` window.

    Defaults come from ``constants.SCAN_BOUNDS`` so one edit retunes every
    tool. Scales as ``base_cap + recent_days * days_scale``, clamped to
    ``window_cap``.
    """
    base = base_cap if base_cap is not None else SCAN_BOUNDS["SEARCH_BASE_CAP"]
    window = window_cap if window_cap is not None else SCAN_BOUNDS["SEARCH_WINDOW_CAP"]
    scale = days_scale if days_scale is not None else SCAN_BOUNDS["SEARCH_DAYS_SCALE"]
    if recent_days is None or recent_days <= 0:
        return base
    scaled = int(base + (recent_days * scale))
    if scaled < base:
        return base
    if scaled > window:
        return window
    return scaled


def build_whose_id_list(message_ids: list[str]) -> str:
    """Return an AppleScript ``id is X or id is Y`` snippet for targeted ops.

    Input is validated through ``core.normalize_message_ids`` so only
    numeric Mail message ids ever reach AppleScript — this is the safe
    write-path use of ``whose`` (small, in-process id list, no remote
    materialization).

    Hard-capped at ``MAX_WHOSE_IDS`` (50): Mail's AppleScript parser
    rejects or hangs on very long ``or``-chained predicates. Callers
    needing to act on more messages at once must chunk via
    ``iter_id_chunks`` and loop the AppleScript invocation.
    """
    clean = normalize_message_ids(message_ids)
    if not clean:
        raise ToolError(
            code="INVALID_SCAN_WINDOW",
            message="build_whose_id_list requires at least one numeric message id.",
        )
    if len(clean) > MAX_WHOSE_IDS:
        raise ToolError(
            code="WHOSE_ID_LIST_TOO_LARGE",
            message=(
                f"build_whose_id_list received {len(clean)} message ids; "
                f"hard cap is {MAX_WHOSE_IDS}. Mail's AppleScript parser "
                "rejects or hangs on very long `id is X or id is Y ...` "
                "predicates."
            ),
            remediation={
                "preferred": (
                    f"Chunk message_ids into batches of {MAX_WHOSE_IDS} or fewer and call the tool once per batch"
                ),
                "helper": "apple_mail_mcp.bounded_scan.iter_id_chunks",
            },
        )
    return " or ".join(f"id is {mid}" for mid in clean)


def iter_id_chunks(
    message_ids: list[str],
    chunk_size: int = MAX_WHOSE_IDS,
) -> Iterator[list[str]]:
    """Yield successive chunks of normalized message ids, each ≤ ``chunk_size``.

    Callers that need to act on more than ``MAX_WHOSE_IDS`` messages must
    drive the AppleScript helper once per chunk:

        for chunk in iter_id_chunks(message_ids):
            condition = build_whose_id_list(chunk)
            run_applescript(script_using(condition), ...)

    Ids are normalized (non-numeric and empty entries dropped) before
    chunking, so the yielded chunks are safe to pass directly to
    ``build_whose_id_list``.
    """
    if chunk_size <= 0 or chunk_size > MAX_WHOSE_IDS:
        raise ToolError(
            code="INVALID_SCAN_WINDOW",
            message=(f"iter_id_chunks requires 0 < chunk_size ≤ {MAX_WHOSE_IDS}; got {chunk_size!r}."),
        )
    clean = normalize_message_ids(message_ids)
    for i in range(0, len(clean), chunk_size):
        yield clean[i : i + chunk_size]


__all__ = [
    "MAX_SCAN_DAYS",
    "MAX_SCAN_LIMIT",
    "MAX_WHOSE_IDS",
    "bounded_inbox_scan",
    "build_bounded_message_scan",
    "build_bounded_filtered_scan",
    "compute_scan_upper_bound",
    "build_whose_id_list",
    "iter_id_chunks",
]
