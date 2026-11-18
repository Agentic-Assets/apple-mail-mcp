"""Search pages must include each match in the advertised sort order."""

import asyncio
import json
import re

from apple_mail_mcp.tools import search
from apple_mail_mcp.tools.search.dispatch import _search_mail_records_sync


def _mail_row(message_id: int, account: str, mailbox: str = "INBOX") -> str:
    return (
        f"{message_id}|||<{message_id}@example.com>|||Subject|||sender@example.com"
        f"|||{mailbox}|||{account}|||false|||2026-01-01T00:00:{message_id:02d}|||"
    )


def test_pages_merge_accounts_before_skipping_preceding_matches(monkeypatch):
    account_rows = {"Alpha": (10, 8), "Bravo": (9, 7)}

    def run_mail(script, timeout=None):
        if "set offsetRemaining" not in script:
            return "Alpha\nBravo"
        account = re.search(r'set searchAccounts to \{account "(Alpha|Bravo)"\}', script)[1]
        skip = int(re.search(r"set offsetRemaining to (\d+)", script)[1])
        collect = int(re.search(r"set collectLimit to (\d+)", script)[1])
        return "\n".join(_mail_row(message_id, account) for message_id in account_rows[account][skip : skip + collect])

    monkeypatch.setattr(search, "run_applescript", run_mail)
    monkeypatch.setattr("apple_mail_mcp.tools.search.emails.annotate_rows_with_reply_state", lambda *_a, **_k: {})

    ids = []
    offset = 0
    while offset is not None and len(ids) <= 4:
        result = json.loads(
            asyncio.run(
                search.search_emails(
                    all_accounts=True, offset=offset, limit=1, date_from="2026-01-01", output_format="json"
                )
            )
        )
        ids.extend(item["message_id"] for item in result["items"])
        offset = result["next_offset"]
    assert ids == ["10", "9", "8", "7"]
    assert offset is None


def test_sync_search_does_not_include_pagination_probe_in_bulk_selection(monkeypatch):
    monkeypatch.setattr(search, "run_applescript", lambda *_a, **_k: "\n".join(_mail_row(i, "Work") for i in (10, 9)))
    selected = _search_mail_records_sync(account="Work", limit=1)
    assert [row["message_id"] for row in selected] == ["10"]


def test_draft_exclusion_applies_before_paging(monkeypatch):
    def run_mail(script, timeout=None):
        skip = int(re.search(r"set offsetRemaining to (\d+)", script)[1])
        collect = int(re.search(r"set collectLimit to (\d+)", script)[1])
        return "\n".join(_mail_row(message_id, "Work") for message_id in (10, 9, 8, 7)[skip : skip + collect])

    def attach_draft_state(records, **_kwargs):
        for row in records:
            row["has_draft"] = row["message_id"] in ("10", "9")
        return {}

    monkeypatch.setattr(search, "run_applescript", run_mail)
    monkeypatch.setattr("apple_mail_mcp.tools.search.emails.annotate_rows_with_reply_state", attach_draft_state)

    result = json.loads(
        asyncio.run(
            search.search_emails(
                account="Work", exclude_drafted=True, limit=1, date_from="2026-01-01", output_format="json"
            )
        )
    )
    assert [row["message_id"] for row in result["items"]] == ["8"]
    assert result["next_offset"] == 1


def test_final_ascending_page_discloses_saturated_candidate_window(monkeypatch):
    def run_mail(script, timeout=None):
        # One of 50 inspected candidates fails the subject predicate.
        rows = [_mail_row(message_id, "Work") for message_id in range(49, 0, -1)]
        if '"SCAN_CEILING|||"' in script:
            rows.append("SCAN_CEILING|||INBOX|||50")
        return "\n".join(rows)

    monkeypatch.setattr(search, "run_applescript", run_mail)
    monkeypatch.setattr("apple_mail_mcp.tools.search.emails.annotate_rows_with_reply_state", lambda *_a, **_k: {})
    result = json.loads(
        asyncio.run(
            search.search_emails(
                account="Work", subject_keyword="Subject", sort="date_asc", offset=48, limit=1, output_format="json"
            )
        )
    )
    assert [row["message_id"] for row in result["items"]] == ["49"]
    assert result["has_more"] is False
    assert result["scan_ceiling_reached"] is True


def test_aliases_for_the_same_mailbox_do_not_repeat_matches(monkeypatch):
    row = _mail_row(10, "Work")
    monkeypatch.setattr(search, "run_applescript", lambda *_a, **_k: row + "\n" + row)
    monkeypatch.setattr("apple_mail_mcp.tools.search.emails.annotate_rows_with_reply_state", lambda *_a, **_k: {})
    result = json.loads(
        asyncio.run(search.search_emails(account="Work", mailboxes=["INBOX", "Inbox"], limit=1, output_format="json"))
    )
    assert [item["message_id"] for item in result["items"]] == ["10"]
    assert result["has_more"] is False
