import hashlib
import json
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "plugin/skills/apple-mail-archive-export/scripts"


def run(script, *args):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), *map(str, args)], capture_output=True, text=True
    )


@pytest.fixture
def batch_plan(tmp_path):
    root = tmp_path / "exports"
    first = root / "INBOX_export" / f"batch-{'a' * 32}"
    retry = root / "message_id_export" / f"batch-{'b' * 32}"
    write = lambda path, data: path.write_bytes(data)  # noqa: E731
    for folder in (first, retry):
        folder.mkdir(parents=True)
    write(first / "plan.ids", b"10\n11")
    write(retry / "plan.ids", b"11")
    write(first / "10_Message.eml", b"From: sender@example.com\nSubject: Budget review\n\nBody text\n")
    write(retry / "11_Seminar.ics", b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Seminar\nEND:VEVENT\nEND:VCALENDAR\n")
    write(retry / "11_Seminar.ics.plist", b"\n" + plistlib.dumps({"flags": 1}))
    write(first / "message.partial", b"in-flight")
    plan = tmp_path / "selection.json"
    plan.write_text(
        json.dumps(
            {
                "snapshot_folders": {"Inbox": ["10", "11"], "Archive/History": ["10"]},
                "batches": [{"mailbox": "Inbox", "path": p.relative_to(root).as_posix()} for p in (first, retry)],
            }
        ),
        encoding="utf-8",
    )
    return root, first, retry, plan, tmp_path / "archive"


def assemble(fixture):
    root, _, _, plan, archive = fixture
    return run("assemble_batches.py", "--export-root", root, "--plan", plan, "--archive", archive)


def test_assembles_completed_records_from_partial_and_retry_batches(batch_plan):
    _, first, retry, _, archive = batch_plan

    created = assemble(batch_plan)

    assert created.returncode == 0, created.stderr
    assert (first / "message.partial").read_bytes() == b"in-flight"
    manifest = json.loads((archive / "manifest.json").read_text())
    records = {row["mail_id"]: row for row in manifest["records"]}
    assert set(records) == {"10", "11"}
    assert records["10"]["folders"] == ["Inbox", "Archive/History"]
    assert (archive / records["10"]["file"]).read_bytes() == (first / "10_Message.eml").read_bytes()
    calendar = records["11"]
    payload = (archive / calendar["file"]).read_bytes()
    trailer = (archive / calendar["calendar_metadata"]).read_bytes()
    assert payload == (retry / "11_Seminar.ics").read_bytes()
    assert trailer == (retry / "11_Seminar.ics.plist").read_bytes()
    assert calendar["installed_raw_sha256"] == hashlib.sha256(payload + trailer).hexdigest()
    indexed = run("index_manifest.py", "--archive", archive)
    assert indexed.returncode == 0, indexed.stderr
    result = run("search.py", "--archive", archive, "--mailbox", "Archive/History", "budget")
    assert result.returncode == 0 and "Budget review" in result.stdout


@pytest.mark.parametrize(
    "problem", ["missing", "repeat-plan", "duplicate", "unexpected", "orphan", "wrong-folder", "symlink"]
)
def test_invalid_batch_cannot_publish_archive(batch_plan, problem):
    _, first, retry, plan, archive = batch_plan
    calendar = retry / "11_Seminar.ics"
    if problem in {"missing", "orphan"}:
        calendar.unlink()
        if problem == "missing":
            calendar.with_suffix(".ics.plist").unlink()
    elif problem == "repeat-plan":
        (first / "plan.ids").write_bytes(b"10\n10\n11")
    elif problem == "duplicate":
        (first / calendar.name).write_bytes(calendar.read_bytes())
    elif problem == "unexpected":
        (first / "12_Message.eml").write_bytes((first / "10_Message.eml").read_bytes())
    elif problem == "wrong-folder":
        data = json.loads(plan.read_text())
        data["batches"][1]["mailbox"] = "Archive/History"
        plan.write_text(json.dumps(data))
    else:
        calendar.rename(first / "untrusted.ics")
        calendar.symlink_to(first / "untrusted.ics")

    assert assemble(batch_plan).returncode != 0
    assert not archive.exists()


def test_refuses_existing_destination(batch_plan):
    _, _, _, _, archive = batch_plan
    archive.mkdir()
    (archive / "sentinel").write_bytes(b"keep")

    assert assemble(batch_plan).returncode != 0
    assert list(archive.iterdir()) == [archive / "sentinel"]
    assert (archive / "sentinel").read_bytes() == b"keep"
