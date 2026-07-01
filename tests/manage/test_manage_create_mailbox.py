"""Characterization tests for ``create_mailbox`` AppleScript generation.

These pin the nested-path ``create_blocks`` f-string and the
``_INVALID_MAILBOX_CHARS`` rejection so the upcoming ``manage.py`` -> ``manage/``
package split stays byte-for-byte behavior-preserving. The existing suite only
exercised a single-segment name via ``test_create_mailbox_uses_default_account``;
the nested-creation loop and char-validation path were previously unpinned.

``account='Work'`` passes through the autouse ``validate_account_name`` stub in
``conftest.py``; ``run_applescript`` is patched at the module attribute
(``apple_mail_mcp.tools.manage.run_applescript``) the same way the rest of the
manage suite does, so the seam survives the package split.
"""

import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import manage as manage_tools


class _Capture:
    def __init__(self, return_value="ok"):
        self.scripts: list[str] = []
        self._return_value = return_value

    def __call__(self, script, timeout=120):
        self.scripts.append(script)
        return self._return_value

    @property
    def last_script(self) -> str:
        return self.scripts[-1]


class CreateMailboxNestedPathTests(unittest.TestCase):
    def test_nested_path_generates_parent_ref_chain(self):
        cap = _Capture()
        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=cap):
            manage_tools.create_mailbox(account="Work", name="Projects/2024/Client")
        script = cap.last_script
        # Depth 0 anchors on the account; deeper levels chain off parentRef.
        self.assertIn('set parentRef to mailbox "Projects" of targetAccount', script)
        self.assertIn('make new mailbox at targetAccount with properties {name:"Projects"}', script)
        self.assertIn('set parentRef to mailbox "2024" of parentRef', script)
        self.assertIn('make new mailbox at parentRef with properties {name:"2024"}', script)
        self.assertIn('set parentRef to mailbox "Client" of parentRef', script)
        self.assertIn('make new mailbox at parentRef with properties {name:"Client"}', script)
        # Reported path joins the segments back together.
        self.assertIn("Path: Projects/2024/Client", script)

    def test_parent_mailbox_prepends_segments(self):
        cap = _Capture()
        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=cap):
            manage_tools.create_mailbox(account="Work", name="2024", parent_mailbox="Projects")
        script = cap.last_script
        self.assertIn('set parentRef to mailbox "Projects" of targetAccount', script)
        self.assertIn('set parentRef to mailbox "2024" of parentRef', script)
        self.assertIn("Path: Projects/2024", script)

    def test_invalid_characters_rejected_without_run(self):
        cap = _Capture()
        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=cap):
            result = manage_tools.create_mailbox(account="Work", name="Bad:Name")
        self.assertIn("Invalid characters in mailbox name segment 'Bad:Name'", result)
        # Validation happens before any AppleScript runs.
        self.assertEqual(cap.scripts, [])

    def test_empty_name_rejected_without_run(self):
        cap = _Capture()
        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=cap):
            result = manage_tools.create_mailbox(account="Work", name="   ")
        self.assertIn("Mailbox name cannot be empty", result)
        self.assertEqual(cap.scripts, [])


if __name__ == "__main__":
    unittest.main()
