"""Caller-visible reporting for per-message export write failures.

Every builder in :mod:`export_helpers` runs its per-message work inside a
``try`` so one unreadable message cannot abort a bounded page. Those arms used
to be bare: a failed file write incremented no counter and printed nothing,
while the count reported as "Exported" had already been incremented *before*
the write. A swallowed failure therefore produced a confident fabrication
("Exported: 50" over a directory holding 49 files), and in the ``correspondent``
scope the same pre-increment advanced the ``offset`` bookkeeping, so the next
page stepped over a message that was never written with nothing in the output to
show it had ever existed.

These fragments are the producer half of the P1 pattern in
``tools/search/script.py``: a per-item failure counter plus a dangling
``on error`` arm spliced into an existing loop, surfaced as a ``PARTIAL:``
summary. There is no Python consumer to pair with, because ``analytics/export.py``
returns the AppleScript output verbatim instead of parsing it, so the report is
emitted as human-readable lines rather than a machine marker.

Counter vocabulary, since the distinction is the whole point:

``exportCount`` / ``totalExportCount``
    Per-attempt filename index. It has to be assigned *before* the write because
    it names the file (and, for attachment bundles, the bundle directory), so it
    is never an honest report of what reached disk.
``writtenCount``
    Files actually written and closed. The only count reported as "Exported",
    incremented *after* ``close access``.
``exportFailureCount``
    Messages whose export threw. Non-zero turns the summary into ``PARTIAL:``.
``exportHalted``
    Set by a halting failure arm (see :func:`export_failure_arm`). Builders whose
    paging is positional never read it; leaving it initialized everywhere keeps
    one init fragment for all four builders.
``exportAttempted``
    Per-iteration flag a halting builder sets once a message has passed the
    ``offset`` gate, so the arm can tell "this message consumed an offset slot
    and produced no file" (unrecoverable, halt) from "this message was never
    counted as matched" (report it, but keep scanning — otherwise one unreadable
    message wedges the scope permanently, since a retry lands on it again).

None of this helps where a Mail read *hangs* instead of throwing (documented at
``tools/search/script.py``): no ``on error`` arm of any shape runs for a hang,
and only the call timeout bounds it.
"""

# Retry guidance, as AppleScript string expressions spliced into the summary.
ID_EXPORT_RETRY_HINT = '"the message_id(s) reported above were not written; re-export them by message_id."'
PAGE_EXPORT_RETRY_HINT = (
    '"the message_id(s) reported above were not written. This page window is unchanged, '
    'so re-export them by message_id rather than expecting the next page to include them."'
)


def correspondent_retry_hint(offset: int) -> str:
    """Return the ``correspondent`` scope's resume hint expression.

    ``offset`` counts matched messages, and a halting arm stops at the first
    message that could not be written, so ``offset + writtenCount`` is exactly
    the position of the failure: retrying there re-attempts the failed message
    instead of stepping over it.
    """
    return (
        '"export halted at the first message that could not be written; nothing after it was '
        f'scanned. Retry with offset=" & ({offset} + writtenCount) & " once the error is resolved, '
        'or export that message_id directly."'
    )


def export_failure_init() -> str:
    """Initialize honest-count and failure-report state for an export loop."""
    return """set writtenCount to 0
                set exportFailureCount to 0
                set exportHalted to false
                set fileRef to missing value"""


def export_write_recorded() -> str:
    """Record one file that reached disk. Splice directly after ``close access``."""
    return """set fileRef to missing value
                                set writtenCount to writtenCount + 1"""


def export_failure_arm(*, id_var: str | None = None, message_var: str = "aMessage", halt: bool = False) -> str:
    """Return the dangling ``on error`` arm for a per-message export loop.

    Splice it immediately before the loop body's own ``end try``: it opens with
    ``on error`` and deliberately does not close the ``try``, exactly like
    ``search/script.py``'s ``_SCAN_FAILURE_ARM``.

    ``id_var`` names an already-bound id string (the by-id builders have one).
    Otherwise the id is resolved from ``message_var`` **inside the handler**, so
    the happy path pays no extra Mail property read and only a failing message
    is asked for its id.

    ``halt=True`` stops the scan at the first message that consumed an ``offset``
    slot and produced no file. Use it wherever ``offset`` counts *matched*
    messages (the ``correspondent`` scope): the reported count is then the exact
    resume position, so ``offset + Exported`` re-attempts the failed message
    instead of stepping over it. It halts only when ``exportAttempted`` is set —
    a failure before the offset gate consumed nothing, and halting on it would
    let one unreadable message wedge the scope forever. Positional page windows
    (``messages pageStart thru pageEnd``) do not need any of this, because their
    next page boundary does not depend on how many messages were written.
    """
    if id_var:
        resolve_id = ""
        id_ref = id_var
    else:
        resolve_id = f"""
                                set failedMessageId to "unknown"
                                try
                                    set failedMessageId to (id of {message_var}) as string
                                end try"""
        id_ref = "failedMessageId"
    halt_block = ""
    if halt:
        halt_block = """
                                if exportAttempted then
                                    set exportHalted to true
                                    exit repeat
                                end if"""
    return f"""on error exportErr
                                if fileRef is not missing value then
                                    try
                                        close access fileRef
                                    end try
                                    set fileRef to missing value
                                end if
                                set exportFailureCount to exportFailureCount + 1{resolve_id}
                                set outputText to outputText & "Error exporting message_id " & {id_ref} & ": " & exportErr & return{halt_block}"""


def export_count_report(*, retry_hint: str) -> str:
    """Report the honest written count, plus a ``PARTIAL:`` block when anything failed."""
    return f"""set outputText to outputText & "Exported: " & writtenCount & return
                if exportFailureCount > 0 then
                    set outputText to outputText & "Failed: " & exportFailureCount & return
                    set outputText to outputText & "PARTIAL: " & {retry_hint} & return
                end if"""


def export_result_banner(*, ok_text: str, warn_text: str) -> str:
    """Return a success banner that degrades to a warning when anything failed."""
    return f"""if exportFailureCount > 0 then
                set outputText to outputText & "{warn_text}" & return & return
            else
                set outputText to outputText & "{ok_text}" & return & return
            end if"""
