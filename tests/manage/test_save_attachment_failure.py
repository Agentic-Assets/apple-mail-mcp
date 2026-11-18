"""Run attachment resolution and error handling with synthetic Mail data."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from apple_mail_mcp.tools import manage

pytestmark = pytest.mark.skipif(shutil.which("osascript") is None, reason="requires the AppleScript interpreter")


def _run_save_control_flow(monkeypatch, tmp_path, *, mailbox: str, save_command: str) -> str:
    def synthetic_mail(script: str, timeout: int) -> str:
        if "set probeMessages to" in script:
            return "0|||-1"  # The separate size probe could not inspect these attachments.

        # Replace Mail data access and the save event. The script's targeting,
        # looping, and error handling run unchanged without launching Mail.
        harness = script.strip().removeprefix('tell application "Mail"').removesuffix("end tell")
        harness, lookups = re.subn(
            r"set targetAccount to .*?set inboxMessages to [^\n]+",
            lambda _match: f"set inboxMessages to {mailbox}",
            harness,
            flags=re.DOTALL,
        )
        assert lookups == 1
        harness = harness.replace("mail attachments of aMessage", "contents of aMessage")
        harness = harness.replace("name of anAttachment", "anAttachment")
        harness = re.sub(r"subject of \w+", '"Synthetic subject"', harness)
        harness, saves = re.subn(r"save \w+ in POSIX file [^\n]+", lambda _match: save_command, harness)
        assert saves == 1
        return subprocess.run(
            ["osascript", "-"], input=harness, capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()

    monkeypatch.setattr(manage, "run_applescript", synthetic_mail)
    monkeypatch.setattr("apple_mail_mcp.tools.manage.attachments.validate_save_path", lambda *args, **kwargs: None)
    return manage.save_email_attachment(
        account="Work",
        message_ids=["501", "502"],
        attachment_name="report",
        save_path=str(Path(tmp_path) / "report.txt"),
    )


def test_attachment_save_failure_preserves_diagnostic(monkeypatch, tmp_path):
    result = _run_save_control_flow(
        monkeypatch,
        tmp_path,
        mailbox='{{"report.txt"}}',
        save_command='error "Synthetic disk write failure"',
    )
    assert result.startswith("Error:")
    assert "Synthetic disk write failure" in result


def test_attachment_save_rechecks_filename_ambiguity_when_probe_is_unavailable(monkeypatch, tmp_path):
    result = _run_save_control_flow(
        monkeypatch,
        tmp_path,
        mailbox='{{"report.txt"}, {"report-final.txt"}}',
        save_command='error "Save must not run for ambiguous selection"',
    )
    assert json.loads(result)["code"] == "AMBIGUOUS_ATTACHMENT_SELECTOR"


def test_attachment_save_with_no_match_reports_not_found(monkeypatch, tmp_path):
    result = _run_save_control_flow(
        monkeypatch,
        tmp_path,
        mailbox='{{"notes.txt"}}',
        save_command='error "Save must not run for a missing attachment"',
    )
    assert "Attachment not found" in result
