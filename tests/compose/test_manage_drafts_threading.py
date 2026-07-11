"""AGENTIC-1214 (Bug 2): manage_drafts(action="create") threading contract.

Covers two fixes, kept in a module separate from test_compose_tools.py so the
reply-body typed-path work (owned elsewhere on this branch) does not collide
with these edits:

1. ``manage_drafts(action="create", in_reply_to=...)`` refuses with a
   structured ``CREATE_CANNOT_THREAD`` error instead of silently dropping
   ``in_reply_to`` and saving an unthreaded draft. The refusal fires before
   any AppleScript runs and before the reply-like standalone guard, so a
   caller who supplied ``in_reply_to`` gets the specific "create cannot
   thread" message rather than the generic reply-like warning.
2. ``_standalone_compose_thread_warning`` now names the calling tool in its
   message instead of always saying "compose_email"; ``manage_drafts`` wires
   ``tool_name='manage_drafts(action="create")'`` through its own call site.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import compose as compose_tools


class ManageDraftsCreateThreadingTests(unittest.TestCase):
    """manage_drafts(action="create") + in_reply_to hard refusal."""

    def test_create_with_in_reply_to_refuses_before_applescript(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Project update",
                to="team@example.com",
                body="Standalone body with no reply markers.",
                in_reply_to="<source-message@example.com>",
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "CREATE_CANNOT_THREAD")
        self.assertIn("cannot", payload["message"])
        self.assertIn("reply_to_email", payload["remediation"]["preferred"])
        self.assertIn('action="find"', payload["remediation"]["find_existing"])
        self.assertEqual(payload["remediation"]["in_reply_to"], "<source-message@example.com>")

    def test_create_with_in_reply_to_refuses_even_with_standalone_confirmed(self):
        """standalone_confirmed only overrides the reply-like heuristic guard;
        it cannot make create honor in_reply_to, because create structurally
        cannot set threading headers regardless of caller intent."""
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Project update",
                to="team@example.com",
                body="Standalone body with no reply markers.",
                in_reply_to="<source-message@example.com>",
                standalone_confirmed=True,
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "CREATE_CANNOT_THREAD")

    def test_create_with_in_reply_to_and_reply_like_body_returns_threading_error_not_standalone_warning(self):
        """When both in_reply_to and reply-like signals (threaded subject,
        quoted markers) are present, the caller gets the specific threading
        refusal, not the generic "looks like a reply" standalone warning,
        because the threading refusal explains WHY create cannot help and
        the standalone warning does not."""
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Re: Project update",
                to="team@example.com",
                body="On Mon, Jan 1, 2026, Geoff <geoff@example.com> wrote:\n> original text",
                in_reply_to="<source-message@example.com>",
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "CREATE_CANNOT_THREAD")

    def test_create_without_in_reply_to_still_saves_normally(self):
        """Regression guard: the new refusal must not fire when in_reply_to
        is omitted, so ordinary standalone draft creation is unaffected."""
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Draft created"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Project update",
                to="team@example.com",
                body="Standalone body with no reply markers.",
            )

        self.assertEqual(len(captured), 1)
        self.assertNotIn("CREATE_CANNOT_THREAD", result)
        self.assertIn("Draft created", result)

    def test_create_reply_like_guard_without_in_reply_to_names_manage_drafts(self):
        """The standalone reply-like guard (no in_reply_to supplied) must now
        name manage_drafts as the calling tool, not compose_email, so the
        agent-facing message matches the tool it actually called."""
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Re: Project update",
                to="team@example.com",
                body="On Mon, Jan 1, 2026, Geoff <geoff@example.com> wrote:\n> original text",
            )

        mock_run.assert_not_called()
        self.assertIn('manage_drafts(action="create")', result)
        self.assertNotIn("Error: compose_email creates", result)


class StandaloneComposeThreadWarningToolNameTests(unittest.TestCase):
    """Direct-call coverage for the parameterized guard message (payload.py)."""

    def test_default_call_still_names_compose_email(self):
        message = compose_tools._standalone_compose_thread_warning("Re: x", "On Mon, X wrote: quoted body", None, False)
        self.assertIsNotNone(message)
        assert message is not None
        self.assertIn("compose_email", message)

    def test_manage_drafts_tool_name_is_reflected_in_message(self):
        message = compose_tools._standalone_compose_thread_warning(
            "Re: x",
            "On Mon, X wrote: quoted body",
            None,
            False,
            tool_name='manage_drafts(action="create")',
        )
        self.assertIsNotNone(message)
        assert message is not None
        self.assertIn('manage_drafts(action="create")', message)
        self.assertNotIn("Error: compose_email creates", message)

    def test_create_rich_email_draft_tool_name_is_reflected_in_message(self):
        message = compose_tools._standalone_compose_thread_warning(
            "Fwd: x",
            "-- original message --",
            None,
            False,
            tool_name="create_rich_email_draft",
        )
        self.assertIsNotNone(message)
        assert message is not None
        self.assertIn("create_rich_email_draft", message)

    def test_standalone_confirmed_suppresses_warning_regardless_of_tool_name(self):
        message = compose_tools._standalone_compose_thread_warning(
            "Re: x",
            "On Mon, X wrote: quoted body",
            None,
            True,
            tool_name='manage_drafts(action="create")',
        )
        self.assertIsNone(message)


if __name__ == "__main__":
    unittest.main()
