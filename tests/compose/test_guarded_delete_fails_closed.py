"""The guarded ``manage_drafts(action="delete")`` recipient check fails closed.

The outer guard already failed closed for an empty recipient list. The inner
per-recipient ``try`` did not: it was bare, so a recipient whose ``address``
could not be read was silently dropped from ``actualToAddresses``. That blinded
the *reverse* membership check — "no actual recipient outside the expected set"
— which is the half of the guard that exists to catch a drifted draft carrying
an EXTRA recipient. An unreadable extra recipient is exactly what such a draft
would plausibly carry, and dropping it made "unreadable" indistinguishable from
"absent" on a destructive path.

Both arms now record the failure and abort the delete. These tests assert the
emitted AppleScript really has the error arms (no bare handler survives on this
path), that the abort happens *before* ``delete foundDraft``, and that the
sentinel is converted into the structured error envelope.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.tools import compose as compose_tools

_OSACOMPILE = shutil.which("osacompile")

GUARD_KWARGS = {
    "account": "Work",
    "action": "delete",
    "draft_id": "84054",
    "expected_in_reply_to": "<source@example.com>",
    "expected_subject": "Current subject",
    "expected_to": "recipient@example.com",
}

SENTINEL = "DRAFT_DELETE_RECIPIENTS_UNREADABLE"


def _guarded_delete_script() -> str:
    captured: list[str] = []

    def _capture(script, *args, **kwargs):
        captured.append(script)
        return "DELETING DRAFT"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=_capture):
        compose_tools.manage_drafts(**GUARD_KWARGS)
    return captured[0]


class GuardedDeleteScriptTests(unittest.TestCase):
    def setUp(self):
        self.script = _guarded_delete_script()

    def _recipient_block(self) -> str:
        start = self.script.index("set actualToAddresses to")
        end = self.script.index("set expectedToAddresses to")
        return self.script[start:end]

    def test_recipient_reads_have_no_bare_try(self):
        """Every `try` opened on this path pairs with a real `on error` arm."""
        lines = [line.strip() for line in self._recipient_block().splitlines()]
        openers = sum(1 for line in lines if line == "try")
        closers = sum(1 for line in lines if line.startswith("end try"))
        arms = sum(1 for line in lines if line.startswith("on error"))
        self.assertEqual((openers, closers), (2, 2))
        self.assertEqual(arms, openers)

    def test_per_recipient_failure_is_recorded(self):
        self.assertIn("on error recipientErrMsg", self._recipient_block())

    def test_recipient_list_failure_is_recorded(self):
        self.assertIn("on error recipientListErrMsg", self._recipient_block())

    def test_unreadable_recipient_returns_before_any_delete(self):
        """The abort must precede `delete foundDraft`, not merely flag it."""
        abort = self.script.index(f'return "{SENTINEL}|||"')
        delete = self.script.index("delete foundDraft")
        self.assertLess(abort, delete)

    def test_abort_is_unconditional_on_any_read_failure(self):
        self.assertRegex(
            self._recipient_block(),
            re.compile(r'if recipientReadFailure is not "" then return "' + SENTINEL),
        )

    def test_failure_marker_is_initialised_before_the_reads(self):
        block = self._recipient_block()
        self.assertLess(block.index('set recipientReadFailure to ""'), block.index("repeat with aRecipient"))

    def test_both_membership_checks_still_run_when_reads_succeed(self):
        self.assertIn("if not expectedRecipientFound then set deleteIdentityMatches to false", self.script)
        self.assertIn("if not actualRecipientExpected then set deleteIdentityMatches to false", self.script)

    def test_unguarded_delete_is_unaffected(self):
        captured: list[str] = []

        def _capture(script, *args, **kwargs):
            captured.append(script)
            return "DELETING DRAFT"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=_capture):
            compose_tools.manage_drafts(account="Work", action="delete", draft_id="84054")

        self.assertNotIn(SENTINEL, captured[0])
        self.assertNotIn("recipientReadFailure", captured[0])


@unittest.skipIf(_OSACOMPILE is None, "osacompile not available on this platform")
class GuardedDeleteCompilesTests(unittest.TestCase):
    """The added `on error` arms must still be valid AppleScript.

    Sibling of ``tests/cross_cutting/test_applescript_builders_compile.py``,
    which covers the statistics scopes but not this destructive path.
    """

    def test_guarded_delete_script_compiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "guarded_delete.applescript"
            src.write_text(_guarded_delete_script(), encoding="utf-8")
            done = subprocess.run(
                [str(_OSACOMPILE), "-o", str(Path(tmp) / "out.scpt"), str(src)],
                capture_output=True,
                text=True,
            )
        self.assertEqual(done.returncode, 0, f"osacompile rejected the guarded delete script:\n{done.stderr}")


class GuardedDeleteErrorEnvelopeTests(unittest.TestCase):
    def _delete_with(self, applescript_result: str) -> dict:
        with patch("apple_mail_mcp.tools.compose.run_applescript", return_value=applescript_result):
            return json.loads(compose_tools.manage_drafts(**GUARD_KWARGS))

    def test_unreadable_recipient_blocks_the_delete(self):
        payload = self._delete_with(f"{SENTINEL}|||84055|||recipient address unreadable: Can't get address.")
        self.assertEqual(payload["code"], SENTINEL)
        self.assertIn("no draft was deleted", payload["message"])

    def test_envelope_reports_the_current_id_and_applescript_error(self):
        payload = self._delete_with(f"{SENTINEL}|||84055|||recipient list unreadable: Can't get to recipients.")
        self.assertEqual(payload["remediation"]["draft_id"], "84055")
        self.assertIn("Can't get to recipients.", payload["remediation"]["applescript_error"])

    def test_envelope_survives_a_detail_containing_the_delimiter(self):
        payload = self._delete_with(f"{SENTINEL}|||84055|||weird ||| detail")
        self.assertEqual(payload["remediation"]["draft_id"], "84055")
        self.assertIn("weird ||| detail", payload["remediation"]["applescript_error"])

    def test_envelope_tolerates_a_missing_detail_field(self):
        payload = self._delete_with(f"{SENTINEL}|||84055")
        self.assertEqual(payload["remediation"]["draft_id"], "84055")
        self.assertEqual(payload["remediation"]["applescript_error"], "unknown AppleScript error")

    def test_identity_drift_still_reports_its_own_code(self):
        """The new arm must not swallow the pre-existing drift sentinel."""
        payload = self._delete_with("DRAFT_DELETE_IDENTITY_DRIFT|||84055")
        self.assertEqual(payload["code"], "DRAFT_DELETE_IDENTITY_DRIFT")

    def test_successful_delete_is_returned_verbatim(self):
        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="DELETING DRAFT\n\n✓ Draft deleted successfully!\n",
        ):
            result = compose_tools.manage_drafts(**GUARD_KWARGS)
        self.assertIn("Draft deleted successfully", result)


if __name__ == "__main__":
    unittest.main()
