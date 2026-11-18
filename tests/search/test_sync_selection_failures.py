"""Synchronous callers must act only on an intact mailbox selection."""

import pytest
from apple_mail_mcp.tools import analytics, manage, search
from apple_mail_mcp.tools.search.dispatch import _search_mail_records_sync

_ROW = (
    "101|||<101@example.com>|||Synthetic subject|||sender@example.com|||Archive|||Work|||false|||2026-01-01T00:00:10|||"
)
_MAILBOX_ERROR = "ERROR_MAILBOX|||INBOX|||could not read candidate messages"


@pytest.mark.parametrize("account", ["Work", None], ids=["one-account", "all-accounts"])
@pytest.mark.parametrize("with_matches", [False, True], ids=["empty", "partial"])
def test_sync_selection_rejects_failed_mailbox_even_with_other_matches(monkeypatch, account, with_matches):
    def run_mail(script, timeout=None):
        if "set searchAccounts" not in script:
            return "Work\nOther"
        if 'set searchAccounts to {account "Work"}' in script:
            return _MAILBOX_ERROR if account is None or not with_matches else f"{_MAILBOX_ERROR}\n{_ROW}"
        return _ROW.replace("|||Work|||", "|||Other|||") if with_matches else ""

    monkeypatch.setattr(search, "run_applescript", run_mail)

    with pytest.raises(ValueError, match="Work.*INBOX.*could not read candidate messages"):
        _search_mail_records_sync(account=account, limit=5)


def test_sync_selection_accepts_rows_at_the_normal_scan_ceiling(monkeypatch):
    monkeypatch.setattr(search, "run_applescript", lambda *_a, **_kw: f"SCAN_CEILING|||INBOX|||50\n{_ROW}")

    rows = _search_mail_records_sync(account="Work", limit=5)

    assert [row["message_id"] for row in rows] == ["101"]


@pytest.mark.parametrize(
    "consumer,kwargs",
    [
        pytest.param(manage.move_email, {"to_mailbox": "Target", "dry_run": True}, id="move-preview"),
        pytest.param(manage.move_email, {"to_mailbox": "Target"}, id="move-execute"),
        pytest.param(manage.manage_trash, {"dry_run": True}, id="trash-preview"),
        pytest.param(manage.manage_trash, {"dry_run": False}, id="trash-execute"),
        pytest.param(manage.update_email_status, {"action": "mark_read"}, id="mark-read"),
        pytest.param(analytics.export_emails, {"scope": "filtered", "date_from": "2026-01-01"}, id="filtered-export"),
    ],
)
def test_filter_consumers_report_failure_before_acting_on_partial_rows(monkeypatch, tmp_path, consumer, kwargs):
    monkeypatch.setattr(search, "run_applescript", lambda *_a, **_kw: f"{_ROW}\n{_MAILBOX_ERROR}")
    monkeypatch.setattr("apple_mail_mcp.tools.analytics.export.validate_save_path", lambda *_a: None)

    def refuse_action(*_args, **_kwargs):
        pytest.fail("a partial search result was used to act on a message")

    monkeypatch.setattr(manage, "run_applescript", refuse_action)
    monkeypatch.setattr(analytics, "run_applescript", refuse_action)
    if consumer is analytics.export_emails:
        kwargs = {**kwargs, "save_directory": str(tmp_path)}
    else:
        kwargs = {**kwargs, "older_than_days": 10, "allow_filter_scan": True}

    result = consumer(account="Work", **kwargs)

    assert "Error:" in result
    assert "INBOX" in result
    assert "could not read candidate messages" in result
