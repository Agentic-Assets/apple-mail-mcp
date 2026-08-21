"""Bounded scan helpers — the only sanctioned producers of ScanWindow tokens.

Phase A of the ``whose``-elimination refactor. See
``tasks/archive/2026-05/whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md`` and
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

4.  ``messages of MB``. AppleScript treats it as identical to ``every
    message of MB``, so it is the same full-mailbox materialization under
    a spelling the lint's ``every message of`` rule never matched. On a
    24K+ Exchange mailbox it presents as a hang rather than an error,
    which is worse than failing. Bind ``messages 1 thru N of MB`` — and
    when ``N`` may exceed the mailbox size, recover against
    ``count of messages of MB`` (a cheap property read, not an
    enumeration) rather than falling back to the unbounded spelling.

5.  Trusting ``count of messages`` as an authority. It is a *cached*
    property that reads stale-high (the slice then throws, loudly) and
    stale-low (the slice then silently under-scans, or binds ``{}`` on a
    mailbox that is not empty). Slice for the window you want first and
    treat the count as a recovery hint. Never bind ``{}`` on the strength
    of a zero count alone: probe ``message 1`` and raise an
    ``ERROR_MAILBOX|||`` marker when the count is contradicted, so "the
    mailbox is empty" and "the count read zero and nothing was scanned"
    stay distinguishable. Never emit a slice whose upper bound may be 0 —
    ``messages 1 thru 0`` silently returns the *first* message on all
    four backends rather than an empty list.

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


def _unbounded_remediation() -> dict[str, Any]:
    return {
        "preferred": f"Pass recent_days=7 or limit={SCAN_BOUNDS['SEARCH_WINDOW_CAP']}",
        "note": "Full-mailbox scans are disabled; bound this call.",
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
    raised with remediation describing how to bound the call (recent_days,
    limit, or since); full-mailbox scans are disabled, so the caller must
    narrow the request rather than fall back to an unbounded sweep.
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
                remediation=_unbounded_remediation(),
            )
        bounded = True

    if limit is not None:
        if limit <= 0 or limit > MAX_SCAN_LIMIT:
            raise ToolError(
                code="UNBOUNDED_SCAN_REQUIRED",
                message=(f"limit must be in (0, {MAX_SCAN_LIMIT}]; got {limit!r}."),
                remediation=_unbounded_remediation(),
            )
        bounded = True

    if since is not None:
        if since <= 0:
            raise ToolError(
                code="UNBOUNDED_SCAN_REQUIRED",
                message=f"since must be a positive epoch timestamp; got {since!r}.",
                remediation=_unbounded_remediation(),
            )
        bounded = True

    if not bounded:
        raise ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=("bounded_inbox_scan requires at least one of recent_days, limit, or since."),
            remediation=_unbounded_remediation(),
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

    Every bind is a slice; none enumerates (AGENTIC-2355, forbidden pattern 4
    in the module docstring). ``messages 1 thru N`` raises when the mailbox
    holds fewer than ``N`` messages, so the emitted script tries the full
    window first and only consults ``count of messages`` in the recovery arm.

    That ordering is the point. ``count of messages`` is a *cached* property
    and Mail is documented here to read it both stale-high and stale-low (a
    shipped tool reported 3,236 unread against a true 10,016 — see
    ``tools/unread_provenance.py``). Reading the count first and slicing to
    it made every stale-low read a silent under-scan; asking for the full
    window first means a low count is never consulted when the mailbox
    actually holds ``limit`` messages.

    The recovery arm carries the same per-slice guard as the two sibling
    call sites (``tools/search/script.py``,
    ``tools/compose/drafts_scripts.py``) and raises an ``ERROR_MAILBOX|||``
    marker instead of binding a false empty:

    * count ≥ ``limit`` yet the slice threw — the count and the mailbox
      disagree; raise rather than re-issue the identical failing slice.
    * count < ``limit`` and ``message <count>+1`` is still reachable — the
      count under-reports, so raise rather than bind a short window.
      ``messages 1 thru 0`` silently returns the *first* message on all four
      backends, so this probe is an explicit single-message reference, never
      a slice, and no slice is emitted unless ``_mbCount > 0``.
    * the probe throws — nothing exists past the count, so the count is
      corroborated: bind ``messages 1 thru _mbCount``, or leave the
      pre-initialized ``{}`` when the mailbox is genuinely empty. A true
      empty result is not a failure and stays silent.

    A slice that still throws after the count corroborated it propagates
    uncaught, which is deliberate: ``run_applescript`` raises on a nonzero
    ``osascript`` exit, so the caller sees a loud failure rather than a
    confident empty list.
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

    # `_mbCountProbeFailed` is named for the failure it records on purpose: a
    # throw from the probe is the *good* outcome (nothing exists past the
    # count), and the flag is what lets the empty bind below stay silent for a
    # genuinely empty mailbox while a stale-low count raises instead.
    return (
        f"set candidateMessages to {{}}\n"
        f"            try\n"
        f"                set candidateMessages to messages 1 thru {limit} of {mailbox_var}\n"
        f"            on error _mbSliceError\n"
        f"                set _mbCount to count of messages of {mailbox_var}\n"
        f"                if _mbCount ≥ {limit} then\n"
        f'                    error "ERROR_MAILBOX|||" & (name of {mailbox_var}) & "|||bounded slice 1 thru {limit} '
        f'failed (" & _mbSliceError & ") while count of messages reads " & (_mbCount as string) & '
        f'"; the cached count and the mailbox disagree, so 0 of {limit} requested message(s) were scanned"\n'
        f"                end if\n"
        f"                -- Does a message exist just past what the count admits?\n"
        f"                set _mbCountProbeFailed to false\n"
        f"                try\n"
        f"                    set _mbProbe to id of message (_mbCount + 1) of {mailbox_var}\n"
        f"                on error\n"
        f"                    set _mbCountProbeFailed to true\n"
        f"                end try\n"
        f"                if not _mbCountProbeFailed then\n"
        f'                    error "ERROR_MAILBOX|||" & (name of {mailbox_var}) & "|||count of messages reads " & '
        f'(_mbCount as string) & " but message " & ((_mbCount + 1) as string) & " is reachable, so the cached count '
        f"under-reports; 0 of {limit} requested message(s) were scanned rather than reporting a false empty mailbox "
        f'(" & _mbSliceError & ")"\n'
        f"                end if\n"
        f"                if _mbCount > 0 then\n"
        f"                    set candidateMessages to messages 1 thru _mbCount of {mailbox_var}\n"
        f"                end if\n"
        f"            end try"
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
