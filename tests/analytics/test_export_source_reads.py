"""The export compiler requests only the content property the format needs."""

import re
from unittest.mock import patch

import pytest
from apple_mail_mcp.tools import analytics
from apple_mail_mcp.tools.analytics.export_helpers import build_multi_mailbox_id_export_script

_BODY_READ = re.compile(r"\b(content|source) of (?:aMessage|foundMessage)\b")


def _capture_tool_script(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setenv("HOME", str(tmp_path))
    with patch.object(analytics, "run_applescript", return_value="Exported: 1") as runner:
        analytics.export_emails(account="Work", save_directory=str(tmp_path), **kwargs)
    return runner.call_args.args[0]


@pytest.mark.parametrize(
    "selection",
    [
        pytest.param({"scope": "entire_mailbox", "max_emails": 1}, id="mailbox-page"),
        pytest.param({"scope": "single_email", "message_id": "42"}, id="single-message"),
        pytest.param({"message_ids": ["42", "43"]}, id="exact-ids"),
        pytest.param({"scope": "correspondent", "email_address": "sender@example.com"}, id="correspondent"),
    ],
)
def test_eml_export_reads_raw_source_without_rendered_body(tmp_path, monkeypatch, selection):
    script = _capture_tool_script(tmp_path, monkeypatch, format="eml", **selection)

    assert _BODY_READ.findall(script) == ["source"]


def test_thread_eml_export_reads_raw_source_without_rendered_body():
    script = build_multi_mailbox_id_export_script(
        safe_account="Work",
        safe_format="eml",
        safe_save_dir="/tmp/export",
        candidate_mailboxes=["INBOX", "Sent"],
        message_ids=["42"],
    )

    assert _BODY_READ.findall(script) == ["source"]


@pytest.mark.parametrize("format", ["txt", "html"])
def test_rendered_export_reads_body_without_raw_source(tmp_path, monkeypatch, format):
    script = _capture_tool_script(tmp_path, monkeypatch, scope="single_email", message_id="42", format=format)

    assert _BODY_READ.findall(script) == ["content"]
