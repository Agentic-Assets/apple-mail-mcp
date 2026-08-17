"""Provenance for Mail.app's cached ``unread count`` mailbox property.

``unread count of <mailbox>`` is a **cached aggregate** that Mail maintains
incrementally. It is not recomputed from per-message ``read status``, and it
drifts — always low, sometimes hugely.

Measured 2026-08-17 on a 25,012-message Exchange Inbox: Mail reported **3,236**
unread where per-message truth was **10,016**, a 68% under-report. That was
ruled out as a scope artifact: ``count of messages`` returned 25,012 and Mail's
own ``Envelope Index`` held 25,012 rows for the same mailbox, so both sides see
the same message set; on a 60-message sample AppleScript's *per-message* ``read
status`` agreed with the index's ``read`` column 60 of 60 (48 unread each side,
0 disagreements). Per-message state is consistent everywhere. Only the cached
mailbox aggregate disagrees. An independent earlier sighting on a different
account ran the same direction — Mail's sidebar badge said 6 unread where
per-message state said 19, and Mail's on-disk ``flags`` bit agreed with 19 on
51 of 51 messages (see ``.agents/skills/apple-mail-archive-export/references/
importing.md`` § 6).

**Why the tools still report the cached number.** Recomputing it through
AppleScript was measured, not assumed. ``count of (messages of <mb> whose read
status is false)`` — the one ``whose`` predicate the bounded-scan lint allows on
a mailbox (``tests/core/test_no_unbounded_whose.py``) — was timed on three
mailboxes of the same Exchange account on 2026-08-17:

=========  ======  ========  =============  ===============================
messages   cached  computed  computed cost  verdict
=========  ======  ========  =============  ===============================
393        86      86        <1 s           agrees
1,549      1,506   1,507     4 s            **off by 1**
25,012     3,236   (none)    no result at   cached is 68% low (truth
                             240 s, none    10,016)
                             at 300 s
=========  ======  ========  =============  ===============================

``count of messages`` and ``unread count`` each returned in about a second on
that same 25K mailbox, so the 300 s figure is the ``whose`` clause's own cost,
not an unresponsive Mail.

That table is the whole argument. The computed count is affordable only where
the cached value is already right, and is unaffordable exactly where the cached
value is badly wrong — Mail appears to materialize the remote mailbox to apply
``whose``, so cost grows superlinearly (~2.6 ms/message at 1.5K, no result at
25K). Worse, at 4 s for a single 1,549-message mailbox it cannot be spent per
mailbox: ``get_mailbox_unread_counts`` enumerates up to 100 mailboxes per
account across every account, so even a handful of mid-size folders would blow
the 120 s default timeout. A per-message ``repeat`` loop is strictly worse
again, and the bounded-scan contract caps per-call message reads at 50
(``constants.SCAN_BOUNDS``), which cannot count a 25K mailbox at all.

So switching these tools to a computed count buys no correctness on the case
that matters while making the calls fail. Building the cheap exact path means
reading Mail's ``Envelope Index`` directly, which is tracked separately as
AGENTIC-2345 and is deliberately not attempted here.

What is fixable is the *honesty* of the output: every tool that reports this
number labels it as Mail's cache rather than a measurement, and flags the cases
where the cached value can be shown to be wrong from data the tool already
holds. Two such checks are free:

``cached_unread_exceeds_message_count``
    A mailbox cannot hold more unread messages than messages. Available
    wherever the tool already reads ``count of messages``.

``sampled_unread_exceeds_cached_unread``
    Unread messages counted in a bounded newest-first sample are a strict lower
    bound on true unread. If that lower bound exceeds the cached aggregate, the
    aggregate is provably too low. Available wherever the tool already reads
    per-message ``read status`` (``get_inbox_overview``'s recent slice).

Neither check fires on every wrong value — the measured 3,236-vs-10,016 case
trips neither, because 3,236 is below 25,012 and above any 10-message sample.
That is precisely why the unconditional label carries the weight: the label is
the fix, and the suspect flags are a bonus for the blatant cases.
"""

from typing import Any

#: Value of the ``unread_count_source`` field: Mail's cached mailbox aggregate.
UNREAD_SOURCE_CACHED = "mail_cached_aggregate"

#: Value of the ``unread_count_source`` field: counted from per-message ``read
#: status`` inside the caller's bounded sample. Trustworthy for what it covers,
#: which is the sample rather than the whole mailbox.
UNREAD_SOURCE_MEASURED = "per_message_read_status"

#: Agent-facing note attached next to every cached unread number.
UNREAD_COUNT_NOTE = (
    "Unread totals are Mail.app's cached `unread count` mailbox aggregate, not a measured count. "
    "Mail maintains it incrementally and it drifts low: measured 2026-08-17 on a 25,012-message "
    "Exchange Inbox, Mail reported 3,236 unread where per-message truth was 10,016 (68% under-report); "
    "a 1,549-message folder on the same account was off by 1. Do not present it as exact, and do not "
    "derive a read count from it. To measure unread for real, page bounded per-message reads with "
    "list_inbox_emails(read_status='unread'); a single AppleScript recount is not affordable (measured: "
    "no result after 300s on that same 25K mailbox)."
)

#: The same note as one text-mode line. Shared so every text surface prints the
#: identical glyph and prose, including ``get_statistics``'s mailbox_breakdown
#: report, which embeds it as an AppleScript string literal.
UNREAD_COUNT_NOTE_LINE = f"ℹ️  {UNREAD_COUNT_NOTE}"

SUSPECT_OVER_TOTAL = "cached_unread_exceeds_message_count"
SUSPECT_UNDER_SAMPLE = "sampled_unread_exceeds_cached_unread"


def _suspect_fields(
    cached_unread: int | None,
    total_messages: int | None,
    sampled_unread: int | None,
) -> dict[str, Any]:
    """Return the ``unread_count_suspect*`` fields, or ``{}`` when nothing is provable.

    Callers merge the result, so "no verdict" needs no separate branch. A
    negative ``cached_unread`` is the tools' "count unavailable" sentinel rather
    than a count, and disproves nothing.
    """
    if cached_unread is None or cached_unread < 0:
        return {}
    if total_messages is not None and total_messages >= 0 and cached_unread > total_messages:
        reason = SUSPECT_OVER_TOTAL
        detail = (
            f"Mail's cached unread count ({cached_unread}) exceeds the mailbox message count "
            f"({total_messages}), so the cached aggregate is stale."
        )
    elif sampled_unread is not None and sampled_unread > cached_unread:
        reason = SUSPECT_UNDER_SAMPLE
        detail = (
            f"{sampled_unread} unread message(s) were counted in the sampled newest slice, which is a "
            f"strict lower bound, yet Mail's cached unread count is only {cached_unread}. The cached "
            f"aggregate is too low by at least {sampled_unread - cached_unread}."
        )
    else:
        return {}
    return {
        "unread_count_suspect": True,
        "unread_count_suspect_reason": reason,
        "unread_count_suspect_detail": detail,
    }


def unread_count_disclosure(
    *,
    cached_unread: int | None = None,
    total_messages: int | None = None,
    sampled_unread: int | None = None,
    include_note: bool = True,
) -> dict[str, Any]:
    """Build the provenance fields for a reported cached unread count.

    Every key is additive; no existing numeric field changes shape or type.
    Callers merge the result onto their payload (``payload.update(...)``), the
    same way ``calendar.helpers.recurring_lookback_disclosure`` is consumed.

    Args:
        cached_unread: The cached ``unread count`` value being reported, when a
            single number is in scope. Negative values (the tools' error
            sentinel) and ``None`` suppress the suspect checks.
        total_messages: ``count of messages`` for the same mailbox when the
            caller already read it. Enables the ``cached_unread_exceeds_
            message_count`` check.
        sampled_unread: Unread messages counted in a bounded per-message sample
            of the same mailbox, when the caller already read ``read status``.
            Enables the ``sampled_unread_exceeds_cached_unread`` check.
        include_note: Include the long ``unread_count_note`` prose. Pass False
            on repeated per-row/per-account blocks so the note appears once at
            the payload envelope instead of N times.

    Returns:
        ``unread_count_source`` / ``unread_count_measured`` always (plus
        ``unread_count_note`` unless suppressed), then
        ``unread_count_suspect``, ``unread_count_suspect_reason``, and
        ``unread_count_suspect_detail`` when the cached value is provably wrong.
    """
    disclosure: dict[str, Any] = {
        "unread_count_source": UNREAD_SOURCE_CACHED,
        "unread_count_measured": False,
    }
    if include_note:
        disclosure["unread_count_note"] = UNREAD_COUNT_NOTE
    disclosure.update(_suspect_fields(cached_unread, total_messages, sampled_unread))
    return disclosure


def measured_unread_disclosure() -> dict[str, Any]:
    """Provenance for an unread number counted from per-message ``read status``.

    The counterpart to :func:`unread_count_disclosure`. Tools that read each
    message's own ``read status`` inside their bounded sample are *not* quoting
    Mail's cache, and saying so is as useful as flagging the ones that are.
    """
    return {
        "unread_count_source": UNREAD_SOURCE_MEASURED,
        "unread_count_measured": True,
        "unread_count_note": (
            "Unread was counted from each message's own `read status` within this call's bounded sample, "
            "not read from Mail's cached mailbox aggregate. It is exact for the messages sampled and says "
            "nothing about messages outside the sample."
        ),
    }


def unread_count_text_label(suspect: bool = False) -> str:
    """Inline text-mode marker for a cached unread number.

    Mirrors the calendar surface's ``[engine: …]`` inline provenance marker.
    """
    return " [Mail cached, SUSPECT]" if suspect else " [Mail cached, unverified]"


def unread_count_text_footer(suspect_details: list[str] | None = None) -> list[str]:
    """Text-mode footer lines explaining the cached-count provenance."""
    lines = [UNREAD_COUNT_NOTE_LINE]
    for detail in suspect_details or []:
        lines.append(f"⚠️  Suspect unread count: {detail}")
    return lines
