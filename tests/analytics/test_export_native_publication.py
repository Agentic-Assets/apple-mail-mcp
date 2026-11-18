"""Exercise the real export writer and checkpoint reader without Mail.app."""

import json
import plistlib
import shutil
import subprocess
import time

import pytest
from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript
from apple_mail_mcp.tools import analytics
from apple_mail_mcp.tools.analytics.export_formatting import mailbox_lookup_block

pytestmark = pytest.mark.skipif(shutil.which("osascript") is None, reason="requires the AppleScript interpreter")

_FIRST = "From: sender@example.com\nSubject: Same subject\n\nFirst body"
_SECOND = "From: sender@example.com\nSubject: Same subject\n\nSecond body"
_ROWS = [(2, 2147483647, _FIRST), (1, 202, _SECOND)]
_MULTIPART = (
    'From: sender@example.com\nContent-Type: multipart/mixed; boundary="native"\n\n'
    "--native\nContent-Type: text/plain\n\nBody\n--native--\n"
)
_STALE_BOUNDARY = _MULTIPART.replace('boundary="native"', 'boundary="stale"')
_CALENDAR = (
    "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Example//Calendar//EN\n"
    "BEGIN:VEVENT\nUID:fixture@example.com\n"
    "DTSTAMP:20250301T000000Z\nDTSTART:20250302T000000Z\nEND:VEVENT\nEND:VCALENDAR\n"
)


def _synthetic_mailbox(script: str, rows: list[tuple[int, int, str]]) -> str:
    """Substitute only the Mail Apple events with in-memory record lookups."""
    account_lookup = f'set targetAccount to account "Work"\n            {mailbox_lookup_block("INBOX")}'
    records = ", ".join(
        f"{{fixtureDate:{date}, fixtureId:{message_id}, "
        f'fixtureSubject:"Same subject", fixtureSource:"{escape_applescript(source)}"}}'
        for date, message_id, source in rows
    )
    assert account_lookup in script
    script = script.replace(account_lookup, f"set targetMailbox to {{{records}}}", 1)
    script = script.replace('tell application "Mail"', "", 1).rsplit("end tell", 1)[0]
    assert "tell application" not in script
    for property_name, fixture_name in (
        ("messages of targetMailbox", "targetMailbox"),
        ("messages pageStart thru pageEnd of targetMailbox", "items pageStart thru pageEnd of targetMailbox"),
        ("date received of ", "fixtureDate of "),
        ("id of ", "fixtureId of "),
        ("subject of ", "fixtureSubject of "),
        ("source of ", "fixtureSource of "),
    ):
        script = script.replace(property_name, fixture_name)
    return script


@pytest.fixture
def export_page(monkeypatch, tmp_path):
    monkeypatch.setattr("apple_mail_mcp.tools.analytics.export.validate_save_path", lambda *_args: None)

    def export(rows=_ROWS, *, max_emails=1, offset=0, interrupt=False, second_source=None, prior_companion=None):
        def run(script, timeout):
            harness = _synthetic_mailbox(script, rows)
            if second_source is not None:
                reader = f'''
                property sourceReads : 0
                on fixtureSourceRead(currentMessage)
                    set sourceReads to sourceReads + 1
                    if sourceReads is 1 then return fixtureSource of currentMessage
                    if sourceReads is 2 then return "{escape_applescript(second_source)}"
                    error "Export exceeded the source-read budget"
                end fixtureSourceRead
                '''
                harness = reader + harness.replace("fixtureSource of aMessage", "my fixtureSourceRead(aMessage)")
            if prior_companion:
                inject = (
                    'do shell script "/bin/cp " & metadataStage & " " & metadataPath'
                    if prior_companion == "matching"
                    else 'do shell script "/usr/bin/printf %s sentinel > " & metadataPath'
                )
                assert "set calendarCommand" in harness
                harness = harness.replace("set calendarCommand", inject + "\nset calendarCommand", 1)
            if interrupt:
                _interrupt_before_publication(harness, tmp_path)
            completed = subprocess.run(
                ["osascript", "-"], input=harness, text=True, capture_output=True, timeout=10, check=True
            )
            return completed.stdout.strip().replace("\r", "\n")

        monkeypatch.setattr(analytics, "run_applescript", run)
        return analytics.export_emails(
            account="Work", save_directory=str(tmp_path), format="eml", max_emails=max_emails, offset=offset
        )

    return export


def _interrupt_before_publication(script, directory):
    rename = 'do shell script "/bin/mv -f " & quoted form of stagingPath'
    assert rename in script
    ready = directory / "second-write-ready"
    pause = f"""if messageId is "202" then
        do shell script "/usr/bin/touch " & quoted form of "{escape_applescript(str(ready))}"
        delay 30
    end if
    """
    script = script.replace(rename, pause + rename, 1)
    with subprocess.Popen(["osascript", "-e", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    pytest.fail(f"export exited before interruption: {stdout!r} {stderr!r}")
                if ready.is_file():
                    raise AppleScriptTimeout("interrupted before publishing the second message")
                time.sleep(0.01)
            pytest.fail("the second message never reached staging")
        finally:
            if process.poll() is None:
                process.kill()


def test_two_pages_with_the_same_subject_preserve_both_messages_and_an_existing_file(export_page, tmp_path):
    mailbox = tmp_path / "INBOX_export"
    mailbox.mkdir()
    old_path = mailbox / "1_Same subject.eml"
    old_path.write_text("Existing file")

    assert "Exported: 1" in export_page()
    assert "Exported: 1" in export_page(offset=1)

    assert old_path.read_text() == "Existing file"
    files = {path.name: path.read_text() for path in mailbox.glob("batch-*/*.eml")}
    plans = {plan.read_text() for plan in mailbox.glob("batch-*/plan.ids")}
    assert files == {"2147483647_Same subject.eml": _FIRST, "202_Same subject.eml": _SECOND}
    assert plans == {"2147483647", "202"}


def test_interrupted_staging_file_stays_pending_in_the_native_checkpoint(export_page, tmp_path):
    response = json.loads(export_page(max_emails=2, interrupt=True))
    assert response["code"] == "EXPORT_BATCH_TIMEOUT"
    recovery = response["remediation"]
    batch = tmp_path / "INBOX_export" / recovery["batch"]
    first = batch / "2147483647_Same subject.eml"
    assert recovery["completed"] == [{"message_id": "2147483647", "path": str(first)}]
    assert recovery["pending_message_ids"] == ["202"]
    assert first.read_text() == _FIRST
    assert (batch / "message.partial").read_text() == _SECOND
    assert not (batch / "202_Same subject.eml").exists()


def test_empty_message_source_reports_failure_and_leaves_no_final_file(export_page, tmp_path):
    output = export_page([(1, 202, "")])

    assert "Exported: 0" in output
    assert "Error exporting message_id 202" in output
    assert not list(tmp_path.glob("INBOX_export/batch-*/*.eml"))


def test_export_retries_a_stale_mime_header_before_publication(export_page, tmp_path):
    output = export_page([(1, 202, _STALE_BOUNDARY)], second_source=_MULTIPART)

    assert "Exported: 1" in output
    (saved,) = tmp_path.glob("INBOX_export/batch-*/*.eml")
    assert saved.read_bytes() == _MULTIPART.encode()


def test_export_keeps_a_persistently_malformed_message_pending(export_page, tmp_path):
    output = export_page([(1, 202, _STALE_BOUNDARY)], second_source=_STALE_BOUNDARY)

    assert "Exported: 0" in output
    assert "MIME" in output
    assert not list(tmp_path.glob("INBOX_export/batch-*/*.eml"))


def test_calendar_and_metadata_are_checkpointed_as_one_record(export_page, tmp_path):
    metadata = plistlib.dumps({"flags": 1})
    source = _CALENDAR + metadata.decode()
    response = json.loads(export_page([(2, 303, source), (1, 202, _SECOND)], max_emails=2, interrupt=True))

    progress = response["remediation"]
    batch = tmp_path / "INBOX_export" / progress["batch"]
    calendar = batch / "303_Same subject.ics"
    assert progress["completed"] == [{"message_id": "303", "path": str(calendar)}]
    assert progress["pending_message_ids"] == ["202"]
    assert calendar.read_bytes() == _CALENDAR.encode()
    assert calendar.with_suffix(".ics.plist").read_bytes() == metadata
    assert not calendar.with_suffix(".eml").exists()


@pytest.mark.parametrize("prior_companion", ["matching", "different"])
def test_calendar_resume_preserves_an_existing_companion(export_page, tmp_path, prior_companion):
    metadata = plistlib.dumps({"flags": 1})
    response = export_page([(1, 303, _CALENDAR + metadata.decode())], prior_companion=prior_companion)
    (companion,) = tmp_path.glob("INBOX_export/batch-*/*.ics.plist")
    calendar = companion.with_suffix("")

    if prior_companion == "matching":
        assert "Exported: 1" in response
        assert companion.read_bytes() == metadata
        assert calendar.read_bytes() == _CALENDAR.encode()
    else:
        assert "Exported: 0" in response
        assert companion.read_bytes() == b"sentinel"
        assert not calendar.exists()


@pytest.mark.parametrize("scope", ["single_email", "correspondent", "thread"])
@pytest.mark.parametrize("failure", ["empty_source", "interrupted_write"])
def test_legacy_scope_failed_write_preserves_the_previous_export(monkeypatch, tmp_path, scope, failure):
    monkeypatch.setattr("apple_mail_mcp.tools.analytics.export.validate_save_path", lambda *_args: None)
    monkeypatch.setattr(
        "apple_mail_mcp.tools.search.get_email_thread",
        lambda **_kwargs: json.dumps({"items": [{"message_id": "202", "mailbox": "INBOX"}]}),
    )
    scripts = []

    def capture(script, timeout):
        scripts.append(script)
        return "Exported: 1"

    monkeypatch.setattr(analytics, "run_applescript", capture)
    analytics.export_emails(
        account="Work",
        save_directory=str(tmp_path),
        scope=scope,
        format="eml",
        message_id="202",
        email_address="sender@example.com",
    )
    assert len(scripts) == 1

    # Inject a source read and interrupt the actual writer just after it closes
    # the handle and before it can publish. The original file must survive both
    # a missing source and a process that cannot reach publication.
    script = scripts[0]
    start = script.index("set exportContent to source of aMessage")
    end = script.index("close access fileRef", start) + len("close access fileRef")
    existing = tmp_path / "previous.eml"
    existing.write_text(_FIRST)
    content = "" if failure == "empty_source" else _SECOND
    writer = script[start:end].replace("source of aMessage", f'"{escape_applescript(content)}"', 1)
    harness = f'''set exportDir to "{escape_applescript(str(tmp_path))}"
    set filePath to "{escape_applescript(str(existing))}"
    {writer}
    error number -128
    '''
    interrupted = subprocess.run(["osascript", "-"], input=harness, text=True, capture_output=True, timeout=10)
    assert existing.read_text() == _FIRST
    assert interrupted.returncode != 0
