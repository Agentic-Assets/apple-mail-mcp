"""Mailbox enumeration preserves valid siblings and discloses missing rows."""

import json
import shutil
import subprocess
from unittest.mock import patch

import pytest
from apple_mail_mcp.tools import inbox


def _inventory(raw: str, **kwargs):
    with patch.object(inbox, "run_applescript", return_value=raw):
        return json.loads(inbox.list_mailboxes(output_format="json", **kwargs))


def test_mailbox_read_error_keeps_healthy_rows():
    result = _inventory("ERROR_MAILBOX|||Work|||Folder enumeration failed\nPersonal|||Archive|||Archive|||-1|||-1")

    assert result["mailboxes"] == [{"account": "Personal", "name": "Archive", "path": "Archive"}]
    assert result["errors"] == [{"account": "Work", "message": "Folder enumeration failed"}]


def test_malformed_mailbox_row_is_disclosed():
    result = _inventory("Work|||Primary|||Primary|||-1|||-1\nWork|||bad row")

    assert result["returned"] == 1
    assert "malformed" in result["errors"][0]["message"].lower()


def test_cap_applies_to_each_account_and_only_reports_observed_omissions():
    result = _inventory(
        "Work|||First|||First|||-1|||-1\nWork|||Second|||Second|||-1|||-1\nPersonal|||Archive|||Archive|||-1|||-1",
        max_mailboxes=2,
    )

    assert result["returned"] == 3
    assert result["truncated"] is False


def _inventory_from_fixture(fixture: str, **kwargs):
    def render(script, timeout):
        script = (
            script.replace('tell application "Mail"', fixture)
            .rsplit("end tell", 1)[0]
            .replace("every account", "fakeAccounts")
            .replace("every mailbox of", "fakeBoxes of")
            .replace("name of", "fakeName of")
        )
        run = subprocess.run(
            ["osascript", "-"],
            input=script,
            text=True,
            capture_output=True,
            check=True,
            timeout=timeout,
        )
        return run.stdout.strip()

    with patch.object(inbox, "run_applescript", side_effect=render):
        return inbox.list_mailboxes(**kwargs)


requires_applescript = pytest.mark.skipif(shutil.which("osascript") is None, reason="requires macOS AppleScript")


@requires_applescript
def test_folder_names_with_transport_delimiters_survive_real_applescript():
    """Replay the tool's real wire encoder against AppleScript records, without Mail.app."""
    result = json.loads(
        _inventory_from_fixture(
            """
        set sampleBox to {fakeName:"Project|||Café%7C" & linefeed & "2026", fakeBoxes:{}}
        set fakeAccounts to {{fakeName:"Work", fakeBoxes:{sampleBox}}}
        """,
            account="Work",
            output_format="json",
        )
    )

    name = "Project|||Café%7C\n2026"
    assert result["mailboxes"] == [{"account": "Work", "name": name, "path": name}]


@requires_applescript
@pytest.mark.parametrize("output_format", ["json", "text"])
def test_child_omissions_are_reported_with_the_same_cap_in_both_formats(output_format):
    result = _inventory_from_fixture(
        """
        set firstChild to {fakeName:"Included child"}
        set secondChild to {fakeName:"Omitted child"}
        set parentBox to {fakeName:"Included parent", fakeBoxes:{firstChild, secondChild}}
        set fakeAccounts to {{fakeName:"Work", fakeBoxes:{parentBox}}}
        """,
        output_format=output_format,
        max_mailboxes=2,
    )

    if output_format == "json":
        payload = json.loads(result)
        assert [row["name"] for row in payload["mailboxes"]] == ["Included parent", "Included child"]
        assert payload["truncated"] is True
    else:
        assert "Included parent" in result
        assert "Included child" in result
        assert "Omitted child" not in result
        assert "Truncated" in result


@requires_applescript
def test_applescript_reports_unreadable_account_and_keeps_its_sibling():
    result = json.loads(
        _inventory_from_fixture(
            """
        set archiveBox to {fakeName:"Archive", fakeBoxes:{}}
        set fakeAccounts to {{fakeName:"Work"}, {fakeName:"Personal", fakeBoxes:{archiveBox}}}
        """,
            output_format="json",
        )
    )

    assert [row["account"] for row in result["mailboxes"]] == ["Personal"]
    assert [error["account"] for error in result["errors"]] == ["Work"]


def test_invalid_count_preserves_inventory_row_and_reports_failure():
    result = _inventory("Work|||Archive|||Archive|||²|||5", include_counts=True)

    assert result["mailboxes"][0]["message_count"] == -1
    assert result["mailboxes"][0]["unread_count"] == 5
    assert "count" in result["errors"][0]["message"].lower()


def test_nonpositive_inventory_limit_does_not_call_mail():
    with patch.object(inbox, "run_applescript", return_value="") as runner:
        payload = json.loads(inbox.list_mailboxes(max_mailboxes=0, output_format="json"))

    assert payload["error"] == "invalid_max_mailboxes"
    runner.assert_not_called()
