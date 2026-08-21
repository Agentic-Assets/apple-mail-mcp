"""Pure, Mail-free helpers for ``get_email_thread``.

Split out of ``thread.py`` the same way ``search/script.py`` sits beside
``search/emails.py``: subject-prefix stripping, Message-ID header tokens, and
the candidate-scan failure channel. Nothing here calls ``run_applescript`` or
opens an AppleScript ``try``, so no package-namespace routing or patch seam is
needed, and the module adds no entry to the bare-``try`` ratchet.

``_thread_mailbox_script`` deliberately stayed behind in ``thread.py``: its
INBOX/Inbox fallback is a pre-existing ``silent``-tier lint entry, and moving
it would relocate that entry into a file the ratchet baseline has never seen,
which reads as a new violation rather than the file move it is.

The candidate-scan channel below is the sibling of the render-loop counters
that live in ``thread.py``. A thread message can be lost in two different
places, and a caller has to be able to tell them apart:

* **candidate collection** — the per-mailbox and per-message ``try`` that runs
  *before* ``FOUND N`` is computed. A message that throws while being matched
  never enters ``threadMessages``, so it is never counted in ``FOUND``. The
  matched-vs-returned reconciliation in ``thread.py`` cannot see it: both
  numbers are consistently wrong together, and the caller gets a short thread
  with ``render_incomplete: false`` and a clean banner.
* **render** — a message that matched, was counted in ``FOUND N``, and then
  threw while its row was built. That one *is* visible as ``matched >
  returned``.

Same in-band channel as ``script.py``'s ``_SCAN_FAILURE_REPORT`` (pattern P1):
one ``ERROR_MAILBOX|||`` row that ``records._parse_search_records`` already
routes into ``mailbox_errors``, plus a ``PARTIAL:`` line for text mode.
"""

import re

from apple_mail_mcp.constants import THREAD_PREFIXES
from apple_mail_mcp.core import escape_applescript

# Single source for the wording that both produces the AppleScript message
# (``candidate_failure_report``) and classifies it back in Python
# (``_thread_error_type``). Candidate-scan rows are typed
# ``candidate_scan_error`` in ``error_details`` so a caller can tell a
# pre-match loss from the render-loop's ``mailbox_error``.
CANDIDATE_SCAN_FAILURE_PREFIX = "candidate scan failed for "


def _thread_strip_prefixes_handler() -> str:
    """AppleScript handler to strip Re:/Fwd:/etc. prefixes from subjects."""
    prefix_checks = ""
    for prefix in THREAD_PREFIXES:
        escaped = escape_applescript(prefix)
        prefix_checks += f'''
                ignoring case
                    if baseSubj starts with "{escaped}" then
                        set baseSubj to text {len(prefix) + 1} thru -1 of baseSubj
                        repeat while baseSubj starts with " "
                            set baseSubj to text 2 thru -1 of baseSubj
                        end repeat
                        set didStrip to true
                    end if
                end ignoring
'''
    return f"""
    on stripThreadPrefixes(subj)
        set baseSubj to subj
        set didStrip to true
        repeat while didStrip
            set didStrip to false
            {prefix_checks}
        end repeat
        return baseSubj
    end stripThreadPrefixes
"""


def thread_loss_report(*, counter_var: str, loss_var: str, message_expr: str, escaped_scope: str) -> str:
    """One "N thread message(s) were lost" report, on both output channels.

    The three loss counters (``threadCandidateFailures``,
    ``threadMailboxFailures``, ``threadRenderFailures``) all report the same
    way, and having to report on *both* channels is what makes the shape worth
    sharing: a report that reached only ``recordRows`` would be invisible to
    text mode, and one that reached only ``outputText`` would be invisible to
    JSON mode. Emitting them from one place is what keeps that pair intact.

    *message_expr* is an AppleScript string expression (not a Python string)
    because every message interpolates its own counters. It is bound to
    *loss_var* once and then read twice, so the two channels can never
    disagree about the wording.

    Assumes ``recordRows`` and ``outputText`` are in scope. *escaped_scope* is
    the already-escaped mailbox scope the thread searched.
    """
    return f"""
            if {counter_var} > 0 then
                set {loss_var} to {message_expr}
                set end of recordRows to "ERROR_MAILBOX|||{escaped_scope}|||" & {loss_var}
                set outputText to outputText & "PARTIAL: " & {loss_var} & return
            end if"""


def candidate_failure_report(escaped_scope: str) -> str:
    """AppleScript reporting candidate-collection losses, run after the scan loop.

    Splice directly after the mailbox ``repeat`` loop and *before* the
    ``FOUND N`` banner, so the caveat precedes the count it undermines.
    Assumes ``threadCandidateFailures`` / ``threadCandidateScanned`` /
    ``threadMailboxFailures`` (armed by the two ``on error`` arms in
    ``thread.py``) are in scope, plus the variables
    :func:`thread_loss_report` needs.

    Two counters rather than one because the causes differ: a per-message
    throw loses one candidate, a per-mailbox throw loses every message in that
    mailbox, and ``threadCandidateScanned`` is not even a meaningful
    denominator for the second (the slice that would have supplied it is what
    failed).
    """
    per_message = thread_loss_report(
        counter_var="threadCandidateFailures",
        loss_var="threadCandidateLoss",
        message_expr=(
            f'"{CANDIDATE_SCAN_FAILURE_PREFIX}" & (threadCandidateFailures as string) & " of " '
            '& (threadCandidateScanned as string) & " scanned message(s) before thread matching; '
            'those messages were never counted in FOUND, so this thread may be missing messages"'
        ),
        escaped_scope=escaped_scope,
    )
    per_mailbox = thread_loss_report(
        counter_var="threadMailboxFailures",
        loss_var="threadMailboxLoss",
        message_expr=(
            f'"{CANDIDATE_SCAN_FAILURE_PREFIX}" & (threadMailboxFailures as string) '
            '& " mailbox(es) before thread matching; those mailboxes contributed no thread messages"'
        ),
        escaped_scope=escaped_scope,
    )
    return f"{per_message}{per_mailbox}\n    "


def render_failure_report(escaped_scope: str) -> str:
    """AppleScript reporting render-loop losses, run after the display loop.

    The sibling of :func:`candidate_failure_report` on the other side of the
    ``FOUND N`` banner. This loss *is* the gap between ``matched`` and
    ``returned``; a candidate loss is not (see the module docstring). Assumes
    ``threadRenderFailures`` and ``threadMatchedCount`` are in scope.
    """
    return thread_loss_report(
        counter_var="threadRenderFailures",
        loss_var="threadRenderLoss",
        message_expr=(
            '"render failed for " & (threadRenderFailures as string) & " of " '
            '& (threadMatchedCount as string) & " thread message(s); results are incomplete"'
        ),
        escaped_scope=escaped_scope,
    )


def is_candidate_scan_failure(message: str) -> bool:
    """True when an ``ERROR_MAILBOX`` message came from candidate collection."""
    return message.startswith(CANDIDATE_SCAN_FAILURE_PREFIX)


def _thread_error_type(message: str) -> str:
    """Type an ``ERROR_MAILBOX`` row by which loop lost the thread message.

    Candidate-collection losses and render losses arrive on the same in-band
    channel but have different causes and different consequences: a candidate
    loss is invisible in ``matched``/``returned`` (both are short together),
    a render loss is exactly the gap between them.
    """
    return "candidate_scan_error" if is_candidate_scan_failure(message) else "mailbox_error"


_HEADER_MESSAGE_ID_RE = re.compile(r"<([^<>]+)>|([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+)")


def _normalize_thread_header_id(value: str) -> str:
    """Normalize a Message-ID-like token for thread graph comparisons."""
    return value.strip().strip("<>").strip().lower()


def _extract_thread_header_tokens(*values: str | None) -> list[str]:
    """Return normalized header Message-ID tokens from Message-ID/References fields."""
    tokens: set[str] = set()
    for value in values:
        if not value:
            continue
        for bracketed, bare in _HEADER_MESSAGE_ID_RE.findall(value):
            token = _normalize_thread_header_id(bracketed or bare)
            if token:
                tokens.add(token)
    return sorted(tokens)


def _applescript_string_list(values: list[str]) -> str:
    """Render a Python string list as an AppleScript list literal."""
    return "{" + ", ".join(f'"{escape_applescript(value)}"' for value in values) + "}"
