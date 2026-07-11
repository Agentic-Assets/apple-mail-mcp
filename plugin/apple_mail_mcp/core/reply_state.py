"""Reply-state annotation plumbing: native ``was replied to`` + Drafts correlation.

Two independent signals feed the ``was_replied_to`` / ``has_draft`` fields
described in ``tasks/active/reply-state-annotation/plan-2026-07-10.md``:

1. ``was_replied_fragment`` — an AppleScript block that reads Mail's native
   read-only ``was replied to`` boolean off a message variable already in
   scope in an existing per-message loop. No new AppleScript round trip.
2. ``build_drafts_snapshot_script`` / ``fetch_drafts_snapshot`` — one bounded,
   newest-first Drafts scan per account per tool call, whose rows are then
   correlated against candidate emails via ``DraftsSnapshot.matches()``.

Mirrors the Sent-mailbox scan in ``core/replied.py``: bounded newest-first
slice, a header-read sub-cap so header correlation cannot fan out into
per-message IMAP downloads on a large Drafts mailbox, and an injectable
``runner`` seam so ``patch('apple_mail_mcp.core.run_applescript')`` reaches
``fetch_drafts_snapshot`` the same way it reaches ``fetch_replied_ids``.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from apple_mail_mcp import core
from apple_mail_mcp.applescript_snippets import sanitize_field_handler, thread_headers_block
from apple_mail_mcp.constants import SCAN_BOUNDS, THREAD_PREFIXES
from apple_mail_mcp.core.applescript import AppleScriptRunner, AppleScriptTimeout
from apple_mail_mcp.core.escaping import escape_applescript

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Drafts mailbox name fallbacks
# ---------------------------------------------------------------------------
# Drafts never had a locale-fallback resolver before this module (Inbox has
# 11 localized names, Sent has 3); non-English Mail.app accounts previously
# fell through to a hardcoded "Drafts" lookup in manage_drafts. These four
# cover the same French/German/Spanish variants already carried for Inbox
# and Sent elsewhere in this codebase.
DRAFTS_MAILBOX_NAMES = ["Drafts", "Brouillons", "Entwürfe", "Borradores"]

_WHITESPACE_RE = re.compile(r"\s+")
_EMAIL_ANGLE_RE = re.compile(r"<([^<>]+)>")


# ---------------------------------------------------------------------------
# was replied to (native property, embedded in an existing per-message loop)
# ---------------------------------------------------------------------------


def was_replied_fragment(var: str = "aMessage") -> str:
    """Return an AppleScript block that reads ``was replied to`` off *var*.

    Sets the local variable ``wasRepliedToken`` to the text token ``"true"``
    or ``"false"`` so a caller's existing per-message row-building loop can
    splice ``wasRepliedToken`` directly into its pipe-row concatenation line,
    the same way it already references ``messageSubject`` / ``messageSender``.
    No new AppleScript round trip: this reads off the same message variable
    the loop already holds. Wrapped in ``try`` so a message that raises on
    the native property lookup degrades to ``"false"`` instead of aborting
    the row.
    """
    return f"""
                set wasRepliedToken to "false"
                try
                    if (was replied to of {var}) then
                        set wasRepliedToken to "true"
                    end if
                end try"""


# ---------------------------------------------------------------------------
# Drafts mailbox resolution (locale fallback)
# ---------------------------------------------------------------------------


def drafts_mailbox_block(var_name: str = "draftsMailbox", account_var: str = "targetAccount") -> str:
    """Return AppleScript that resolves *account_var*'s Drafts mailbox with locale fallbacks.

    Tries "Drafts" -> "Brouillons" (French) -> "Entwürfe" (German) ->
    "Borradores" (Spanish), mirroring the nested-try Sent-mailbox fallback
    style in ``core/replied.py`` (``sent_mailbox_resolve_script``) and
    ``tools/smart_inbox/awaiting_reply.py`` (``_sent_mailbox_script``). Sets
    *var_name* to ``missing value`` when none of the names resolve; the
    caller decides how to react (an empty snapshot, a skipped scan, etc.).
    """
    return f"""
            set {var_name} to missing value
            try
                set {var_name} to mailbox "Drafts" of {account_var}
            on error
                try
                    set {var_name} to mailbox "Brouillons" of {account_var}
                on error
                    try
                        set {var_name} to mailbox "Entwürfe" of {account_var}
                    on error
                        try
                            set {var_name} to mailbox "Borradores" of {account_var}
                        end try
                    end try
                end try
            end try
    """


# ---------------------------------------------------------------------------
# Bounded Drafts snapshot script
# ---------------------------------------------------------------------------


def build_drafts_snapshot_script(account_name: str, drafts_cap: int, header_cap: int) -> str:
    """Return one self-contained AppleScript producing a bounded Drafts snapshot.

    Emits one line per draft, newest first, shaped:

        ``DRAFT|||subject|||first_to_recipient_address|||date_iso_or_raw|||header_blob``

    *header_blob* is the space-joined In-Reply-To and References header
    values, read only for the newest *header_cap* drafts (empty string for
    the rest): full-header reads on Drafts cost roughly 72% more than a
    subject/recipient/date read (measured 2026-07-10), so header
    correlation is sub-capped the same way ``REPLIED_HEADER_READ_CAP`` caps
    Sent-mailbox header reads in ``core/replied.py``.

    Every per-draft read is wrapped in its own ``try``; a draft whose
    fields cannot be read still emits a (blank-field) ``DRAFT|||`` row
    rather than being dropped, so the trailing ``COUNT|||<n>`` line always
    equals the number of ``DRAFT|||`` rows emitted, and a partially-broken
    draft never silently shrinks the reported scan count.
    """
    safe_account = escape_applescript(account_name)
    sanitize_script = sanitize_field_handler()
    drafts_resolve_script = drafts_mailbox_block(var_name="draftsMailbox", account_var="targetAccount")
    thread_headers_script = thread_headers_block(
        message_var="aDraft",
        in_reply_to_var="inReplyToVal",
        references_var="refsVal",
        include_on_error=True,
    )
    return f'''
    {sanitize_script}

    on pad2(numberValue)
        if numberValue < 10 then
            return "0" & (numberValue as string)
        end if
        return numberValue as string
    end pad2

    on month_number(monthValue)
        set monthValues to {{January, February, March, April, May, June, July, August, September, October, November, December}}
        repeat with monthIndex from 1 to 12
            if item monthIndex of monthValues is monthValue then
                return monthIndex
            end if
        end repeat
        return 0
    end month_number

    on iso_datetime(dateValue)
        set yearValue to year of dateValue as integer
        set monthValue to my month_number(month of dateValue)
        set dayValue to day of dateValue as integer
        set hourValue to hours of dateValue
        set minuteValue to minutes of dateValue
        set secondValue to seconds of dateValue
        return (yearValue as string) & "-" & my pad2(monthValue) & "-" & my pad2(dayValue) & "T" & my pad2(hourValue) & ":" & my pad2(minuteValue) & ":" & my pad2(secondValue)
    end iso_datetime

    tell application "Mail"
        try
            set targetAccount to account "{safe_account}"
            {drafts_resolve_script}
            if draftsMailbox is missing value then
                return "COUNT|||0"
            end if

            set totalDrafts to count of messages of draftsMailbox
            set headEnd to totalDrafts
            if headEnd > {drafts_cap} then set headEnd to {drafts_cap}
            if totalDrafts is 0 then
                set draftMessages to {{}}
            else
                set draftMessages to messages 1 thru headEnd of draftsMailbox
            end if

            -- Limit full-header reads to headerReadCap drafts to avoid
            -- per-draft IMAP downloads on large remote Drafts mailboxes.
            set headerReadCap to {header_cap}
            set headerReadCount to 0
            set outputLines to {{}}

            repeat with aDraft in draftMessages
                try
                    set draftSubject to ""
                    try
                        set draftSubject to my sanitize_field(subject of aDraft)
                    end try

                    set draftToAddr to ""
                    try
                        set draftToRecipients to every to recipient of aDraft
                        if (count of draftToRecipients) > 0 then
                            set draftToAddr to my sanitize_field(address of item 1 of draftToRecipients)
                        end if
                    end try

                    set draftDateText to "(unknown)"
                    try
                        set draftReceivedDate to date received of aDraft
                        try
                            set draftDateText to my iso_datetime(draftReceivedDate)
                        on error
                            set draftDateText to (draftReceivedDate as string)
                        end try
                    end try

                    set headerBlob to ""
                    if headerReadCount < headerReadCap then
                        {thread_headers_script}
                        set headerBlob to inReplyToVal & " " & refsVal
                        set headerReadCount to headerReadCount + 1
                    end if

                    set end of outputLines to "DRAFT|||" & draftSubject & "|||" & draftToAddr & "|||" & draftDateText & "|||" & headerBlob
                on error
                    set end of outputLines to "DRAFT|||" & "" & "|||" & "" & "|||" & "" & "|||" & ""
                end try
            end repeat

            set AppleScript's text item delimiters to linefeed
            set draftLinesText to outputLines as string
            set AppleScript's text item delimiters to ""
            return draftLinesText & linefeed & "COUNT|||" & (count of outputLines)
        on error errMsg
            return "ERROR|||" & errMsg
        end try
    end tell
    '''


# ---------------------------------------------------------------------------
# Pure-Python parsing + correlation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DraftRow:
    subject: str
    first_to_recipient: str
    date_text: str
    header_blob: str


def _parse_drafts_snapshot_output(raw: str) -> tuple[list[_DraftRow], int]:
    """Parse ``build_drafts_snapshot_script`` output into rows + a scanned count."""
    rows: list[_DraftRow] = []
    scanned = 0
    for line in raw.splitlines():
        if line.startswith("DRAFT|||"):
            parts = line.split("|||", 4)
            if len(parts) != 5:
                continue
            _, subject, first_to, date_text, header_blob = parts
            rows.append(
                _DraftRow(
                    subject=subject,
                    first_to_recipient=first_to,
                    date_text=date_text,
                    header_blob=header_blob,
                )
            )
        elif line.startswith("COUNT|||"):
            _, count_text = line.split("|||", 1)
            try:
                scanned = int(count_text.strip())
            except ValueError:
                scanned = len(rows)
    if scanned == 0 and rows:
        scanned = len(rows)
    return rows, scanned


def normalize_thread_subject(subject: str) -> str:
    """Normalize a subject line for thread/draft correlation.

    Strips ``constants.THREAD_PREFIXES`` repeatedly and case-insensitively
    (so "Re: Re: Fwd: Quarterly report" and "RE: FWD: quarterly report"
    normalize to the same key), then casefolds and collapses/trims
    whitespace.
    """
    text = (subject or "").strip()
    stripped_again = True
    while stripped_again:
        stripped_again = False
        lowered = text.casefold()
        for prefix in THREAD_PREFIXES:
            prefix_cf = prefix.casefold()
            if lowered.startswith(prefix_cf):
                text = text[len(prefix) :].strip()
                stripped_again = True
                break
    return _WHITESPACE_RE.sub(" ", text).strip().casefold()


def _extract_bare_email(value: str) -> str:
    """Return the bare address from a "Name <addr@x>" string, or *value* stripped.

    Mail's own ``address of recipient`` already returns a bare address, but
    the ``sender`` field surfaced elsewhere in this codebase (``sender of
    aMessage``) commonly arrives as ``"Name <addr@x>"``, so ``matches()``
    normalizes both sides through this before comparing.
    """
    match = _EMAIL_ANGLE_RE.search(value)
    if match:
        return match.group(1).strip()
    return value.strip()


def _parse_iso_datetime(text: str | None) -> datetime | None:
    """Parse an ``iso_datetime``-formatted string, or return None when unparseable."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.strip())
    except ValueError:
        return None


@dataclass(frozen=True)
class DraftsSnapshot:
    """Result of one bounded Drafts scan for a single account.

    ``status`` is one of:

    - ``"ok"``: the scan ran; ``rows`` holds up to ``drafts_cap`` drafts.
    - ``"error"``: the scan failed (timeout, AppleScript error, missing
      account); ``error`` carries a human-readable reason. Callers must
      fail open (never exclude a candidate on ``has_draft`` when the scan
      itself errored).
    - ``"skipped"``: the scan was intentionally not run (for example, no
      account was available to scan).
    """

    status: Literal["ok", "error", "skipped"]
    scanned: int
    account: str
    error: str | None = None
    rows: tuple[_DraftRow, ...] = field(default_factory=tuple)

    def matches(
        self,
        subject: str,
        sender_email: str,
        internet_message_id: str | None,
        email_date: str | None,
    ) -> bool:
        """Return True when a scanned draft correlates to this candidate email.

        A draft matches when either:

        1. Its header blob (In-Reply-To + References, populated only for
           the newest ``header_cap`` drafts) contains *internet_message_id*.
        2. ``normalize_thread_subject`` equality on *subject* AND the
           draft's first to-recipient equals the bare address of
           *sender_email* (case-insensitive) AND the draft postdates
           *email_date* by no more than one day of slack
           (``draft_date >= email_date - 1 day``). When either date is
           unparseable, the date condition is treated as satisfied rather
           than rejecting the match.
        """
        if self.status != "ok" or not self.rows:
            return False

        bare_message_id = (internet_message_id or "").strip().strip("<>")
        normalized_subject = normalize_thread_subject(subject)
        bare_sender = _extract_bare_email(sender_email).casefold()
        email_dt = _parse_iso_datetime(email_date)

        for row in self.rows:
            if bare_message_id and row.header_blob and bare_message_id in row.header_blob:
                return True

            if not normalized_subject or normalize_thread_subject(row.subject) != normalized_subject:
                continue
            if not bare_sender or _extract_bare_email(row.first_to_recipient).casefold() != bare_sender:
                continue

            draft_dt = _parse_iso_datetime(row.date_text)
            if draft_dt is not None and email_dt is not None and draft_dt < email_dt - timedelta(days=1):
                continue

            return True

        return False


def fetch_drafts_snapshot(
    account: str | None,
    runner: AppleScriptRunner | None,
    timeout: int,
    drafts_cap: int | None = None,
    header_cap: int | None = None,
) -> DraftsSnapshot:
    """Fetch a bounded, newest-first Drafts snapshot for *account*.

    Mirrors the ``fetch_replied_ids`` injection seam: *runner* defaults to
    ``core.run_applescript`` resolved through the ``apple_mail_mcp.core``
    module attribute (not imported directly), so
    ``patch('apple_mail_mcp.core.run_applescript')`` reaches this function
    the same way it reaches ``fetch_replied_ids``. *drafts_cap* /
    *header_cap* default to ``SCAN_BOUNDS["DRAFT_LOOKUP"]`` /
    ``SCAN_BOUNDS["DRAFT_SNAPSHOT_HEADER_CAP"]`` when omitted.

    Never raises: every failure (missing account, timeout, AppleScript
    error, unexpected exception) degrades to a ``status="error"`` snapshot
    carrying the reason in ``error``, so callers can fail open on
    ``has_draft`` instead of crashing the whole tool call.
    """
    if not account:
        return DraftsSnapshot(status="skipped", scanned=0, account=account or "", error="no account provided")

    effective_drafts_cap = drafts_cap if drafts_cap is not None else SCAN_BOUNDS["DRAFT_LOOKUP"]
    effective_header_cap = header_cap if header_cap is not None else SCAN_BOUNDS["DRAFT_SNAPSHOT_HEADER_CAP"]

    script = build_drafts_snapshot_script(
        account_name=account,
        drafts_cap=effective_drafts_cap,
        header_cap=effective_header_cap,
    )
    fn = runner if runner is not None else core.run_applescript
    try:
        raw = fn(script, timeout=timeout)
    except AppleScriptTimeout as exc:
        logger.warning("fetch_drafts_snapshot timed out for account %r: %s", account, exc)
        return DraftsSnapshot(status="error", scanned=0, account=account, error=f"timeout: {exc}")
    except Exception as exc:
        logger.warning(
            "fetch_drafts_snapshot failed for account %r: %s: %s",
            account,
            type(exc).__name__,
            exc,
        )
        return DraftsSnapshot(status="error", scanned=0, account=account, error=f"{type(exc).__name__}: {exc}")

    if raw.startswith("ERROR|||"):
        message = raw.split("|||", 1)[1] if "|||" in raw else raw
        return DraftsSnapshot(status="error", scanned=0, account=account, error=message)

    rows, scanned = _parse_drafts_snapshot_output(raw)
    return DraftsSnapshot(status="ok", scanned=scanned, account=account, rows=tuple(rows))


# ---------------------------------------------------------------------------
# Shared per-candidate helpers (used by every tool-layer annotation site)
# ---------------------------------------------------------------------------


def resolve_has_draft(
    snapshot: DraftsSnapshot | None,
    *,
    subject: str,
    sender_email: str,
    internet_message_id: str | None,
    email_date: str | None,
) -> bool | None:
    """Return ``has_draft`` for one candidate against *snapshot*.

    ``None`` when *snapshot* is missing or did not come back ``"ok"`` (fail
    open: a skipped or errored scan never reports ``False``). Otherwise
    ``snapshot.matches(...)``. Every reply-state annotation call site
    (``tools.reply_state_wiring.annotate_rows_with_reply_state``,
    ``tools.inbox.parsing._annotate_text_rows_with_reply_state``,
    ``tools.smart_inbox.reply_state_glue._classify_needs_response_rows``)
    shares this so the fail-open rule lives in one place.
    """
    if snapshot is None or snapshot.status != "ok":
        return None
    return snapshot.matches(
        subject=subject,
        sender_email=sender_email,
        internet_message_id=internet_message_id,
        email_date=email_date,
    )


def reply_state_tags(was_replied: bool | None, has_draft: bool | None) -> list[str]:
    """Return the ``[REPLIED]`` / ``[HAS DRAFT]`` text-mode tag list for one row.

    Order matches the display convention used everywhere these tags are
    rendered (``tools.inbox.parsing``, ``tools.inbox.overview``,
    ``tools.search.records``): ``[REPLIED]`` before ``[HAS DRAFT]``, and
    only the tags that apply. Callers own their own join/spacing since
    the three render sites splice this list into slightly different
    surrounding text.
    """
    tags: list[str] = []
    if was_replied:
        tags.append("[REPLIED]")
    if has_draft:
        tags.append("[HAS DRAFT]")
    return tags
