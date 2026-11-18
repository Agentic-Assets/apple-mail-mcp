import hashlib
import json
import plistlib
import sqlite3
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "plugin/skills/apple-mail-archive-export/scripts"
sys.path.insert(0, str(SCRIPTS))
from export_emlx import SCHEMA

sys.path.pop(0)


def run(script, archive, *options):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), "--archive", str(archive), *options],
        capture_output=True,
        text=True,
        check=False,
    )


def write_manifest(archive, manifest):
    (archive / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def artifact(archive, mail_id, kind, payload, folders):
    relative = f"messages/{mail_id}.{kind}"
    (archive / relative).write_bytes(payload)
    return {
        "mail_id": str(mail_id),
        "format": kind,
        "file": relative,
        "folders": folders,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "source": "fixture",
    }


def message(subject, contents):
    mail = EmailMessage()
    mail["Subject"] = subject
    mail["From"] = "Sender <sender@example.com>"
    mail["To"] = "Receiver <receiver@example.com>"
    mail["Date"] = "Fri, 07 Aug 2026 15:30:00 -0400"
    mail["Message-ID"] = "<shared@example.com>"
    mail.set_content(contents, cte="base64")
    mail.add_attachment(b"fixture", maintype="application", subtype="pdf", filename="résumé.pdf")
    return mail.as_bytes()


@pytest.fixture
def archive(tmp_path):
    (tmp_path / "messages").mkdir()
    (tmp_path / "calendar-metadata").mkdir()
    first = artifact(tmp_path, 101, "eml", message("A résumé", "Searchable magnolia"), ["Inbox", "Archive/History"])
    second = artifact(tmp_path, 102, "eml", message("Distinct copy", "Second body"), ["Sent"])
    second["flags"] = 0
    ics = (
        b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:fixture-event\r\nDTSTART:20261118T090000Z\r\n"
        b"SUMMARY:Research seminar\r\nDESCRIPTION:Verify lexical search\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    calendar = artifact(tmp_path, 103, "ics", ics, ["Calendar"])
    metadata = plistlib.dumps({"flags": 1, "private-note": "unindexedquartz"})
    calendar.update(
        calendar_metadata="calendar-metadata/103.plist",
        calendar_metadata_bytes=len(metadata),
        calendar_metadata_sha256=hashlib.sha256(metadata).hexdigest(),
        installed_raw_sha256=hashlib.sha256(ics + metadata).hexdigest(),
    )
    (tmp_path / calendar["calendar_metadata"]).write_bytes(metadata)
    manifest = {
        "snapshot_folders": {"Inbox": ["101"], "Archive/History": ["101"], "Sent": ["102"], "Calendar": ["103"]},
        "records": [first, second, calendar],
    }
    write_manifest(tmp_path, manifest)
    return tmp_path, manifest


def test_indexes_verified_mail_and_calendar_with_folder_aliases(archive):
    directory, _ = archive

    result = run("index_manifest.py", directory)

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(directory / "index.sqlite") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT count(*) FROM messages WHERE message_id = '<shared@example.com>'").fetchone() == (2,)
        assert dict(db.execute("SELECT emlx_id, read FROM messages")) == {101: None, 102: 0, 103: 1}
        assert db.execute("SELECT count(*) FROM archive_records WHERE source_format = 'ics'").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM message_mailboxes").fetchone() == (4,)
    lexical = run("search.py", directory, "--mailbox", "Archive/History", "magnolia")
    assert lexical.returncode == 0 and "A résumé" in lexical.stdout
    assert "A résumé" in run("search.py", directory, "--has-attachment", '"résumé"').stdout
    assert "Research seminar" in run("search.py", directory, "--year", "2026", "lexical").stdout
    assert "no matches" in run("search.py", directory, "unindexedquartz").stdout


def test_second_index_run_preserves_messages_and_does_not_duplicate_rows(archive):
    directory, _ = archive
    before = {path: path.read_bytes() for path in (directory / "messages").iterdir()}

    for _ in range(2):
        result = run("index_manifest.py", directory)
        assert result.returncode == 0, result.stderr

    assert all(path.read_bytes() == data for path, data in before.items())
    with sqlite3.connect(directory / "index.sqlite") as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone() == (3,)


def test_checksum_failure_preserves_previous_index(archive):
    directory, manifest = archive
    built = run("index_manifest.py", directory)
    assert built.returncode == 0, built.stderr
    index = directory / "index.sqlite"
    before = index.read_bytes()
    target = directory / manifest["records"][0]["file"]
    contents = target.read_bytes()
    target.write_bytes(b"X" + contents[1:])

    failed = run("index_manifest.py", directory)

    assert failed.returncode != 0 and "SHA-256" in failed.stderr
    assert index.read_bytes() == before
    assert list(directory.glob(".archive-index-*")) == []


@pytest.mark.parametrize(
    "problem", ["duplicate-id", "duplicate-path", "traversal", "membership", "format", "placeholders"]
)
def test_rejects_invalid_manifest_records(archive, problem):
    directory, manifest = archive
    if problem == "duplicate-id":
        manifest["records"].append(manifest["records"][0])
    elif problem == "duplicate-path":
        manifest["records"][1]["file"] = manifest["records"][0]["file"]
    elif problem == "traversal":
        manifest["records"][0]["file"] = "messages/../../outside.eml"
    elif problem == "membership":
        manifest["snapshot_folders"]["Inbox"] = []
    elif problem == "format":
        manifest["records"][0]["format"] = "txt"
    else:
        manifest["records"][0]["attachment_placeholders"] = "invalid"
    write_manifest(directory, manifest)

    failed = run("index_manifest.py", directory)

    assert failed.returncode != 0 and "archive index" in failed.stderr
    assert not (directory / "index.sqlite").exists()


def test_rejects_symlinked_archive_file_even_when_bytes_match(archive):
    directory, manifest = archive
    original = directory / manifest["records"][0]["file"]
    elsewhere = directory / "outside.eml"
    original.rename(elsewhere)
    original.symlink_to(elsewhere)

    failed = run("index_manifest.py", directory)

    assert failed.returncode != 0 and "path" in failed.stderr
    assert not (directory / "index.sqlite").exists()


def test_rejects_inconsistent_calendar_source_digest(archive):
    directory, manifest = archive
    manifest["records"][2]["installed_raw_sha256"] = "0" * 64
    write_manifest(directory, manifest)

    failed = run("index_manifest.py", directory)

    assert failed.returncode != 0 and "calendar source SHA-256" in failed.stderr
    assert not (directory / "index.sqlite").exists()


def test_search_mailbox_filter_still_works_with_existing_skill_index(tmp_path):
    with sqlite3.connect(tmp_path / "index.sqlite") as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO messages (mailbox, subject, n_attachments, eml_path) VALUES (?, ?, ?, ?)",
            ("Inbox", "Legacy subject", 0, "eml/1.eml"),
        )

    result = run("search.py", tmp_path, "--mailbox", "Inbox")

    assert result.returncode == 0 and "Legacy subject" in result.stdout
