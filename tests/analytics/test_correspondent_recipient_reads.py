"""One unreadable recipient must not delete a message from a correspondent export.

``export_emails(scope="correspondent")`` matched a message by running four
``repeat`` loops over its recipient lists, each loop wrapped whole in a bare
``try`` that fell through to ``return false``:

.. code-block:: applescript

    try
        repeat with aRecipient in recipients of aMessage
            if address of aRecipient contains emailNeedle then return true
        end repeat
    end try

``address of aRecipient`` is ``missing value`` for unresolved, distribution-list
and X.500 entries, and ``missing value contains "…"`` raises -1700. Because the
``try`` wrapped the *loop* rather than the *read*, the first such recipient
aborted every recipient after it, the message silently left the match set, no
counter moved, and the export's failure arm never ran — the caller got the clean
success path. ``plugin/skills/email-archive-cleanup/SKILL.md`` prescribes this
exact call as the evidence snapshot taken before an irreversible
``manage_trash(action="delete_permanent")``, so the under-export is what makes
this a data-loss defect rather than a reporting one.

WHAT THESE TESTS PROVE
----------------------
The shape tests assert on the emitted script (narrow ``try``, explicit
``missing value`` arm, guarded report), which is all that is decidable offline.

``TestCorrespondentMatchBehavior`` goes further and *executes* the shipped
handler under ``osascript`` against hand-built stand-in messages, so
"the third recipient is still evaluated after the second throws" is measured
rather than inferred. Nothing there sends an Apple Event to Mail: the harness
emits no ``tell application "Mail"`` block, and ``using terms from`` reads the
dictionary the same way the existing ``osacompile`` tests already do. Every
address is synthetic.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from apple_mail_mcp.tools.analytics import correspondent_matching
from apple_mail_mcp.tools.analytics import export_helpers as helpers

_OSASCRIPT = shutil.which("osascript")

_NEEDLE = "person@example.com"


def _correspondent_script(tmp_path: Path, *, offset: int = 0) -> str:
    return helpers.build_correspondent_export_script(
        safe_account="Work",
        safe_email_address=_NEEDLE,
        safe_format="txt",
        safe_save_dir=str(tmp_path),
        mailbox="INBOX",
        scan_upper_bound=250,
        max_emails=25,
        offset=offset,
        include_sent=True,
        date_setup="",
        date_filter="",
    )


# ---------------------------------------------------------------------------
# Shape — the try must wrap the read, not the loop
# ---------------------------------------------------------------------------


def test_no_try_wraps_a_whole_recipient_loop(tmp_path):
    script = _correspondent_script(tmp_path)

    # The pre-fix shape, verbatim. Its defining property is that `repeat` opens
    # while a `try` is already open with nothing between them.
    assert "repeat with aRecipient in recipients of aMessage" not in script
    for field in correspondent_matching.RECIPIENT_FIELDS:
        assert f"repeat with aRecipient in {field} of aMessage" not in script

    # The list is fetched under its own try, then iterated outside it, so a
    # failure to read one field cannot cost the fields that follow.
    for field in correspondent_matching.RECIPIENT_FIELDS:
        assert f"set fieldRecipients to ({field} of aMessage)" in script
    assert script.count("repeat with aRecipient in fieldRecipients") == len(correspondent_matching.RECIPIENT_FIELDS)


def test_every_try_in_the_match_handler_has_an_on_error_arm():
    handler = correspondent_matching.correspondent_match_handler()
    lines = [line.strip() for line in handler.splitlines()]

    opens = sum(1 for line in lines if line == "try")
    closes = sum(1 for line in lines if line == "end try")
    arms = sum(1 for line in lines if line.startswith("on error"))

    assert opens == closes, "unbalanced try blocks in the correspondent match handler"
    assert opens > 0
    assert arms == opens, "a bare try in this handler is the defect itself"
    # Every arm does something observable with the failure.
    assert handler.count("set readFailureCount to readFailureCount + 1") >= arms


def test_missing_value_is_handled_explicitly_not_by_the_catch_all():
    """``missing value`` is the expected trigger, so it gets its own branch.

    Leaving it to ``on error`` would still count it, but only because
    ``missing value contains "…"`` happens to raise. An explicit branch keeps the
    behaviour if Mail ever returns an empty string instead.
    """
    handler = correspondent_matching.correspondent_match_handler()

    assert "if recipientAddress is missing value then" in handler
    assert "if senderText is missing value then" in handler
    # The compare is only reached once the value is known to be readable.
    assert handler.index("if recipientAddress is missing value then") < handler.index(
        "else if recipientAddress contains emailNeedle then"
    )


# ---------------------------------------------------------------------------
# Reporting — an under-export must not look like a clean success
# ---------------------------------------------------------------------------


def test_unreadable_messages_are_counted_and_reported(tmp_path):
    script = _correspondent_script(tmp_path)

    assert "set unreadableRecipientMessages to 0" in script
    assert "set correspondentMatch to my messageHasCorrespondent" in script
    assert "set correspondentMatched to matchFound of correspondentMatch" in script
    assert (
        "if (readFailures of correspondentMatch) > 0 and not correspondentMatched and isWithinDateWindow then "
        "set unreadableRecipientMessages to unreadableRecipientMessages + 1" in script
    )
    assert 'set outputText to outputText & "Unreadable addresses on: " & unreadableRecipientMessages' in script
    assert "PARTIAL: " in script
    assert "may be missing mail from this correspondent" in script


def test_out_of_window_read_failures_are_not_counted(tmp_path):
    """An unreadable non-match outside the requested window is irrelevant."""
    script = helpers.build_correspondent_export_script(
        safe_account="Work",
        safe_email_address=_NEEDLE,
        safe_format="txt",
        safe_save_dir=str(tmp_path),
        mailbox="INBOX",
        scan_upper_bound=250,
        max_emails=25,
        offset=0,
        include_sent=True,
        date_setup="",
        date_filter="\n                            if messageDate < fromDate then set shouldExport to false\n",
    )

    date_filtered = script.index("if messageDate < fromDate then set shouldExport to false")
    counted = script.index("set unreadableRecipientMessages to unreadableRecipientMessages + 1")
    assert date_filtered < counted
    assert "set isWithinDateWindow to shouldExport" in script[date_filtered:counted]


def test_in_window_unreadable_nonmatches_are_counted(tmp_path):
    """The date filter must not hide an unreadable non-match in the window."""
    script = _correspondent_script(tmp_path)

    counted = script.index("set unreadableRecipientMessages to unreadableRecipientMessages + 1")
    assert "set isWithinDateWindow to shouldExport" in script[:counted]
    assert "set shouldExport to correspondentMatched and isWithinDateWindow" in script[counted:]


def test_a_clean_correspondent_export_reports_no_unreadable_messages(tmp_path):
    """Mirror image: the new line must never appear on a scan that read cleanly."""
    script = _correspondent_script(tmp_path)
    lines = script.splitlines()

    for index, line in enumerate(lines):
        if '"Unreadable addresses on: "' not in line and "may be missing mail from this correspondent" not in line:
            continue
        guarded = False
        for previous in reversed(lines[:index]):
            stripped = previous.strip()
            if stripped == "end if":
                break
            if stripped.startswith("if unreadableRecipientMessages > 0 then"):
                guarded = True
                break
        assert guarded, f"line {index} reports unreadable messages unconditionally: {line.strip()}"


def test_read_failures_stay_separate_from_export_write_failures(tmp_path):
    """The two counters mean different things and must not be merged.

    ``exportFailureCount`` means "matched, attempted, produced no file" and drives
    the halt/resume bookkeeping (``offset + writtenCount`` is a resume position).
    Counting an undecidable match there would halt the scope on a message that
    consumed no offset slot, which wedges it permanently.
    """
    script = _correspondent_script(tmp_path, offset=7)

    counted = script.index("set unreadableRecipientMessages to unreadableRecipientMessages + 1")
    assert "set exportFailureCount to exportFailureCount + 1" not in script[counted : counted + 200]
    assert "set exportHalted to true" not in script[counted : counted + 200]


# ---------------------------------------------------------------------------
# Behaviour — run the shipped handler against stand-in messages
# ---------------------------------------------------------------------------


def _probe_terminology() -> bool:
    """True when ``using terms from application "Mail"`` resolves on this host."""
    if _OSASCRIPT is None:
        return False
    probe = 'using terms from application "Mail"\n    set x to 1\nend using terms from\nreturn "ok"\n'
    try:
        completed = subprocess.run(  # noqa: S603 - offline terminology probe, no tell block
            [_OSASCRIPT, "-"],
            input=probe,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - host without osascript
        return False
    return completed.returncode == 0


_TERMINOLOGY_AVAILABLE = _probe_terminology()

# Stand-in recipients. ``brokenRecipient`` has no ``address`` at all, so reading
# it raises -1728 exactly like an object Mail refuses to resolve; ``unreadable``
# reproduces the ``missing value`` address that raises -1700 on compare.
_HARNESS_TAIL = """

on fakeField(aMessage, fieldName)
    if fieldName is "recipients" then return allRecipients of aMessage
    return {}
end fakeField

using terms from application "Mail"
    on fakeRecipient(addr)
        return {address:addr}
    end fakeRecipient
    on brokenRecipient()
        return {yearsOld:3}
    end brokenRecipient
    on fakeMessage(senderText, recipientList)
        return {sender:senderText, allRecipients:recipientList}
    end fakeMessage
end using terms from

set goodRecipient to my fakeRecipient("someone@example.com")
set targetRecipient to my fakeRecipient("person@example.com")
set unreadableRecipient to my fakeRecipient(missing value)
set brokenRecipient to my brokenRecipient()

set cases to {}
set end of cases to my fakeMessage("other@example.com", {goodRecipient, targetRecipient})
set end of cases to my fakeMessage("other@example.com", {unreadableRecipient, targetRecipient})
set end of cases to my fakeMessage("other@example.com", {brokenRecipient, targetRecipient})
set end of cases to my fakeMessage("other@example.com", {goodRecipient, brokenRecipient, targetRecipient})
set end of cases to my fakeMessage("other@example.com", {goodRecipient, unreadableRecipient, targetRecipient})
set end of cases to my fakeMessage("other@example.com", {unreadableRecipient})
set end of cases to my fakeMessage("PERSON@EXAMPLE.COM", {goodRecipient})
set end of cases to my fakeMessage("other@example.com", {goodRecipient})
set end of cases to my fakeMessage(missing value, {goodRecipient})

set out to ""
repeat with aCase in cases
    set matchResult to my messageHasCorrespondent(aCase, "person@example.com")
    set out to out & (matchFound of matchResult) & "/" & (readFailures of matchResult) & " "
end repeat
return out
"""


def _run_match_harness() -> list[tuple[bool, int]]:
    """Execute the shipped handler over the stand-in messages; return per-case results.

    Only the four ``<field> of aMessage`` reads are substituted, because a record
    cannot stand in for Mail's recipient *elements*. Every other line — the
    narrowed ``try``, the ``missing value`` branch, the counter, the record
    return — is the text this package ships. The substitution count is asserted
    so a reworded fragment fails loudly instead of silently testing nothing.
    """
    handler = correspondent_matching.correspondent_match_handler()
    for field in correspondent_matching.RECIPIENT_FIELDS:
        original = f"set fieldRecipients to ({field} of aMessage)"
        assert handler.count(original) == 1, f"expected exactly one {field!r} read to substitute"
        handler = handler.replace(original, f'set fieldRecipients to (my fakeField(aMessage, "{field}"))')

    completed = subprocess.run(  # noqa: S603 - offline harness, emits no tell block
        [_OSASCRIPT, "-"],
        input=handler + _HARNESS_TAIL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, (completed.stderr or completed.stdout).strip()

    results: list[tuple[bool, int]] = []
    for token in completed.stdout.split():
        matched, _, failures = token.partition("/")
        results.append((matched == "true", int(failures)))
    return results


@pytest.mark.skipif(
    not _TERMINOLOGY_AVAILABLE,
    reason='osascript or the "Mail" terminology dictionary is unavailable on this host',
)
class TestCorrespondentMatchBehavior:
    """Executed behaviour of the shipped handler. No Apple Event reaches Mail."""

    @pytest.fixture(scope="class")
    def results(self) -> list[tuple[bool, int]]:
        return _run_match_harness()

    def test_all_cases_ran(self, results):
        assert len(results) == 9

    def test_readable_recipient_list_still_matches(self, results):
        assert results[0] == (True, 0)

    def test_missing_value_recipient_does_not_hide_a_later_match(self, results):
        """The shipped defect: the target sat behind a ``missing value`` address.

        Measured before the fix on this same harness, this case returned
        ``false`` — the message was dropped from the export with nothing in the
        output to show it had existed.
        """
        assert results[1] == (True, 1)

    def test_throwing_recipient_does_not_hide_a_later_match(self, results):
        assert results[2] == (True, 1)

    def test_third_recipient_is_still_evaluated_after_the_second_throws(self, results):
        """Position matters: the failure is in the middle, the match is last."""
        assert results[3] == (True, 1)
        assert results[4] == (True, 1), "a missing value in the middle must not end the loop either"

    def test_unmatched_message_with_an_unreadable_recipient_is_distinguishable(self, results):
        """A zero match and an undecidable match must not look the same."""
        assert results[5] == (False, 1)

    def test_sender_match_stays_case_insensitive(self, results):
        assert results[6] == (True, 0)

    def test_clean_non_match_reports_no_read_failure(self, results):
        assert results[7] == (False, 0)

    def test_missing_value_sender_is_counted_not_swallowed(self, results):
        assert results[8] == (False, 1)
