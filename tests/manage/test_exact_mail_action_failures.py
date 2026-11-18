"""Exercise exact-ID mutation reporting without accessing Mail or changing messages."""

import re
import shutil
import subprocess

import pytest
from apple_mail_mcp.tools import manage

pytestmark = pytest.mark.skipif(shutil.which("osascript") is None, reason="requires the AppleScript interpreter")


@pytest.mark.parametrize("action", ["move_email", "move_to_trash", "delete_permanent"])
def test_exact_mail_action_reports_failed_id_and_keeps_successful_count(monkeypatch, action):
    def synthetic_mail(script: str, timeout: int) -> str:
        script = script.strip().removeprefix('tell application "Mail"').removesuffix("end tell")
        script, lookups = re.subn(
            r"set targetAccount to .*?set matchingMessages to [^\n]+",
            "set matchingMessages to {501, 502, 503}",
            script,
            flags=re.DOTALL,
        )
        assert lookups == 1
        for field, fixture in [
            ("id", "contents of aMessage"),
            ("subject", '"Synthetic subject"'),
            ("sender", '"sender@example.com"'),
            ("date received", '"2026-01-01"'),
        ]:
            script = script.replace(f"{field} of aMessage", fixture)
        script, actions = re.subn(
            r"move aMessage to \w+Mailbox|delete aMessage",
            'if contents of aMessage is 502 then error "Synthetic action failure"',
            script,
        )
        assert actions == 1
        return subprocess.run(
            ["osascript", "-"], input=script, text=True, capture_output=True, timeout=10, check=True
        ).stdout.strip()

    monkeypatch.setattr(manage, "run_applescript", synthetic_mail)
    kwargs = {"account": "Work", "message_ids": ["501", "502", "503"], "dry_run": False}
    if action == "move_email":
        result = manage.move_email(to_mailbox="Archive", **kwargs)
    else:
        result = manage.manage_trash(action=action, **kwargs)

    assert "TOTAL: 2 email(s)" in result
    assert "PARTIAL:" in result
    assert "502" in result
    assert "Synthetic action failure" in result


@pytest.mark.parametrize("fails", [True, False], ids=["stops-on-error", "capped-success"])
def test_empty_trash_reports_only_confirmed_progress(monkeypatch, fails):
    def synthetic_mail(script: str, timeout: int) -> str:
        script = script.strip().removeprefix('tell application "Mail"').removesuffix("end tell")
        targets = "{501, 502, 503}" if fails else "{501, 502}"
        script, lookups = re.subn(
            r"set targetAccount to .*?set trashMessages to messages of trashMailbox\s+end if",
            f"set trashMessages to {targets}\nset messageCount to 3\nset deleteCount to 0",
            script,
            flags=re.DOTALL,
        )
        assert lookups == 1
        script = script.replace("id of aMessage", "contents of aMessage")
        script = script.replace(
            "delete aMessage", 'if contents of aMessage is 502 then error "Synthetic trash failure"' if fails else ""
        )
        return subprocess.run(
            ["osascript", "-"], input=script, text=True, capture_output=True, timeout=10, check=True
        ).stdout.strip()

    monkeypatch.setattr(manage, "run_applescript", synthetic_mail)
    result = manage.manage_trash(
        account="Work", action="empty_trash", confirm_empty=True, dry_run=False, max_deletes=3 if fails else 2
    )

    if fails:
        assert "Deleted 1 of 3 message(s)" in result
        assert "PARTIAL:" in result and "502" in result
    else:
        assert "Deleted 2 of 3 message(s)" in result
        assert "Emptied trash" not in result
