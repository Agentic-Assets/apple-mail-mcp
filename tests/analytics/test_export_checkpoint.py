"""Recover a batch when the Mail process cannot return its stdout."""

import json
import re

from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import analytics


def test_mailbox_timeout_reports_only_committed_files(monkeypatch, tmp_path):
    monkeypatch.setattr("apple_mail_mcp.tools.analytics.export.validate_save_path", lambda *_args: None)

    def interrupted(script, timeout):
        batch_name = re.search(r'set exportDir to exportDir & "/(batch-[a-f0-9]+)"', script)
        if batch_name:
            batch = tmp_path / "INBOX_export" / batch_name[1]
            batch.mkdir(parents=True)
            (batch / "plan.ids").write_text("101\n202\n303")
            (batch / "101_Same subject.eml").write_bytes(b"From: sender@example.com\r\n\r\nBody")
            (batch / "message.partial").write_bytes(b"Part")
        raise AppleScriptTimeout("interrupted")

    monkeypatch.setattr(analytics, "run_applescript", interrupted)
    payload = json.loads(analytics.export_emails(account="Work", save_directory=str(tmp_path), format="eml"))
    assert payload["code"] == "EXPORT_BATCH_TIMEOUT"
    recovery = payload["remediation"]
    assert recovery["planned_message_ids"] == ["101", "202", "303"]
    assert recovery["pending_message_ids"] == ["202", "303"]
    assert recovery["completed"] == [
        {"message_id": "101", "path": str(tmp_path / "INBOX_export" / recovery["batch"] / "101_Same subject.eml")}
    ]


def test_mailbox_timeout_before_selection_does_not_invent_a_resume_point(monkeypatch, tmp_path):
    monkeypatch.setattr("apple_mail_mcp.tools.analytics.export.validate_save_path", lambda *_args: None)

    def interrupted(*_args, **_kwargs):
        raise AppleScriptTimeout("before selection")

    monkeypatch.setattr(analytics, "run_applescript", interrupted)
    recovery = json.loads(analytics.export_emails(account="Work", save_directory=str(tmp_path)))["remediation"]
    assert recovery["planned_message_ids"] is None
    assert recovery["pending_message_ids"] is None
    assert recovery["completed"] == []


def test_exact_id_timeout_before_mail_lookup_keeps_requested_ids(monkeypatch, tmp_path):
    monkeypatch.setattr("apple_mail_mcp.tools.analytics.export.validate_save_path", lambda *_args: None)

    def interrupted(*_args, **_kwargs):
        raise AppleScriptTimeout("before lookup")

    monkeypatch.setattr(analytics, "run_applescript", interrupted)
    recovery = json.loads(
        analytics.export_emails(account="Work", save_directory=str(tmp_path), message_ids=["00101", "101", "２０２"])
    )["remediation"]
    assert recovery["pending_message_ids"] == ["101", "202"]


def test_filtered_timeout_reports_prior_and_unstarted_mailbox_batches(monkeypatch, tmp_path):
    monkeypatch.setattr("apple_mail_mcp.tools.analytics.export.validate_save_path", lambda *_args: None)
    monkeypatch.setattr(
        "apple_mail_mcp.tools.search._search_mail_records_sync",
        lambda **_kwargs: [
            {"message_id": "101", "mailbox": "First"},
            {"message_id": "202", "mailbox": "Second"},
            {"message_id": "303", "mailbox": "Third"},
        ],
    )

    def interrupted(script, timeout):
        if 'mailbox "First" of targetAccount' in script:
            return "Exported: 1"
        assert 'mailbox "Second" of targetAccount' in script
        raise AppleScriptTimeout("interrupted")

    monkeypatch.setattr(analytics, "run_applescript", interrupted)
    recovery = json.loads(
        analytics.export_emails(
            account="Work", save_directory=str(tmp_path), scope="filtered", sender_domain="example.com"
        )
    )["remediation"]
    assert recovery["mailbox"] == "Second"
    assert recovery["pending_message_ids"] == ["202"]
    assert recovery["prior_batch_results"] == ["Exported: 1"]
    assert recovery["unstarted_batches"] == [{"mailbox": "Third", "message_ids": ["303"]}]
