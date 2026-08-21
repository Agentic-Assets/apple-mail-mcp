"""Correspondent matching for ``export_emails(scope="correspondent")``.

The ``messageHasCorrespondent`` handler used to wrap each *whole* recipient
``repeat`` in a bare ``try`` and fall through to ``return false``:

.. code-block:: applescript

    try
        repeat with aRecipient in recipients of aMessage
            if address of aRecipient contains emailNeedle then return true
        end repeat
    end try

``address of aRecipient`` is ``missing value`` for unresolved, distribution-list
and X.500 entries, and ``missing value contains "…"`` raises -1700. Because the
``try`` wrapped the loop rather than the read, the first such recipient aborted
the remaining recipients in that list, the message dropped out of the match set,
no counter moved, and the export's own failure arm never ran — so the output was
the clean success path. That matters more here than in a listing tool:
``plugin/skills/email-archive-cleanup/SKILL.md`` prescribes this exact call as
the evidence snapshot taken *before* an irreversible
``manage_trash(action="delete_permanent")``, so a silent under-export means mail
is deleted that was never written to disk.

Two changes, both required:

1. The ``try`` is narrowed to the smallest unit that can throw — one recipient
   read — so one unreadable recipient can no longer hide the rest of the list.
   ``missing value`` is checked explicitly rather than left to the ``on error``
   catch-all, because it is the *expected* trigger, not an anomaly.
2. The handler returns ``{matchFound:…, readFailures:…}`` instead of a bare
   boolean, so "did not match" is distinguishable from "could not be read".
   The caller counts the unmatched-and-unreadable messages and reports them as a
   ``PARTIAL:`` line, exactly the producer half of pattern P1 documented in
   ``tests/core/test_no_bare_applescript_try.py`` (``tools/search/script.py``'s
   ``scanReadFailures`` -> ``ERROR_MAILBOX|||`` -> ``PARTIAL:``). As with the
   sibling fragments in :mod:`export_failure_reporting`, there is no Python
   consumer to pair with: ``analytics/export.py`` returns the AppleScript output
   verbatim, so the report is emitted as human-readable lines.

A read failure still cannot make the export *wrong* in the other direction: a
message whose recipients could not be read is never claimed as exported, and the
count of such messages is reported next to the honest ``Exported:`` count. What
the caller gets back is "N exported, K messages I could not finish checking",
which is the distinction the pre-delete snapshot needs.

Not covered here (same limit as every other arm in this package): a Mail read
that *hangs* instead of throwing. No ``on error`` arm runs for a hang; only the
call timeout bounds it.
"""

# ``recipients`` is Mail's superset of the other three; all four are read
# because remote accounts have been observed populating the specific fields
# without the aggregate. Each field fetch gets its own ``try`` so an unreadable
# ``bcc recipients`` cannot cost the ``to recipients`` that follow it.
RECIPIENT_FIELDS = ("recipients", "to recipients", "cc recipients", "bcc recipients")

_RECIPIENT_FIELD_SCAN = "\n".join(
    f"""                    set fieldRecipients to {{}}
                    try
                        set fieldRecipients to ({field} of aMessage)
                    on error
                        set readFailureCount to readFailureCount + 1
                    end try
                    repeat with aRecipient in fieldRecipients
                        try
                            set recipientAddress to address of aRecipient
                            if recipientAddress is missing value then
                                set readFailureCount to readFailureCount + 1
                            else if recipientAddress contains emailNeedle then
                                return {{matchFound:true, readFailures:readFailureCount}}
                            end if
                        on error
                            set readFailureCount to readFailureCount + 1
                        end try
                    end repeat"""
    for field in RECIPIENT_FIELDS
)

_MATCH_HANDLER = f"""        using terms from application "Mail"
            on messageHasCorrespondent(aMessage, emailNeedle)
                set readFailureCount to 0
                ignoring case
                    try
                        set senderText to sender of aMessage
                        if senderText is missing value then
                            set readFailureCount to readFailureCount + 1
                        else if senderText contains emailNeedle then
                            return {{matchFound:true, readFailures:readFailureCount}}
                        end if
                    on error
                        set readFailureCount to readFailureCount + 1
                    end try
{_RECIPIENT_FIELD_SCAN}
                end ignoring
                return {{matchFound:false, readFailures:readFailureCount}}
            end messageHasCorrespondent
        end using terms from"""

_READ_FAILURE_HINT = (
    '"a recipient or sender address could not be read on " & unreadableRecipientMessages '
    '& " scanned message(s). Those messages did not match and were NOT exported, so this '
    "snapshot may be missing mail from this correspondent. Re-check them (search_emails on "
    'the same window) before deleting anything."'
)


def correspondent_match_handler() -> str:
    """Return the ``messageHasCorrespondent`` handler and its ``using terms from`` block.

    Splice at the top of the correspondent export script, before its
    ``tell application "Mail"`` block. The handler returns a record, not a
    boolean: ``matchFound`` drives the export, ``readFailures`` counts the
    sender/recipient reads that threw or came back ``missing value``.
    """
    return _MATCH_HANDLER


def correspondent_read_failure_init() -> str:
    """Initialize the unreadable-message counter for a correspondent scan."""
    return "set unreadableRecipientMessages to 0"


def correspondent_match_block(safe_email_address: str) -> str:
    """Call the handler and retain its raw match result for the caller.

    A matched message is exported regardless of how many of its other recipients
    were unreadable, so it is not a candidate for silent omission. The caller
    applies its date filter before counting unreadable non-matches, so
    out-of-window messages do not produce a partial-export warning.
    """
    return f"""set correspondentMatch to my messageHasCorrespondent(aMessage, "{safe_email_address}")
                            set correspondentMatched to matchFound of correspondentMatch
                            set shouldExport to true"""


def correspondent_read_failure_count() -> str:
    """Count unreadable non-matches that are eligible for this export window."""
    return """set isWithinDateWindow to shouldExport
                            if (readFailures of correspondentMatch) > 0 and not correspondentMatched and isWithinDateWindow then set unreadableRecipientMessages to unreadableRecipientMessages + 1
                            set shouldExport to correspondentMatched and isWithinDateWindow"""


def correspondent_read_failure_report() -> str:
    """Report messages whose correspondent match could not be decided.

    Kept separate from ``exportFailureCount`` on purpose: that counter means
    "matched, attempted, produced no file" and drives the halt/resume
    bookkeeping. This one means "never matched, because Mail would not tell us",
    which is the failure mode that makes a clean-looking export incomplete.
    """
    return f"""if unreadableRecipientMessages > 0 then
                    set outputText to outputText & "Unreadable addresses on: " & unreadableRecipientMessages & " scanned message(s)" & return
                    set outputText to outputText & "PARTIAL: " & {_READ_FAILURE_HINT} & return
                end if"""
