"""Tests for compose and rich draft helpers."""

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from apple_mail_mcp import server as _server
from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import compose as compose_tools


def _make_subprocess_result(returncode=0, stdout=b"", stderr=b""):
    """Build a MagicMock shaped like subprocess.CompletedProcess."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def _assert_ordered(testcase, text, *snippets):
    """Assert snippets appear in text in the provided order."""
    last_position = -1
    for snippet in snippets:
        position = text.find(snippet)
        testcase.assertGreater(position, last_position)
        last_position = position


def _main_reply_script(scripts):
    """Return the generated reply script, skipping helper probes."""
    reply_scripts = [script for script in scripts if "reply foundMessage" in script]
    if len(reply_scripts) != 1:
        raise AssertionError(f"expected one reply script, got {len(reply_scripts)}")
    return reply_scripts[0]


def _save_draft_script(scripts):
    """Return the save-as-draft script, skipping the sender/snapshot probes."""
    save_scripts = [script for script in scripts if "save targetMessage" in script]
    if len(save_scripts) != 1:
        raise AssertionError(f"expected one save-draft script, got {len(save_scripts)}")
    return save_scripts[0]


def _saved_reply_draft_output(
    *,
    to="Sender <sender@example.com>",
    subject="Re: Test",
    draft_id=None,
    quote_needle=None,
):
    lines = [
        "SAVING REPLY AS DRAFT",
        "",
        "Reply saved as draft!",
        f"To: {to}",
        f"Subject: {subject}",
    ]
    if draft_id is not None:
        lines.append(f"Draft ID: {draft_id}")
    if quote_needle is not None:
        lines.append(f"Quote Needle: {quote_needle}")
    return "\n".join(lines) + "\n"


def _saved_forward_draft_output(*, to="recipient@example.com", subject="Fwd: Test", draft_id=None):
    lines = [
        "SAVING FORWARD AS DRAFT",
        "",
        "Forward saved as draft.",
        f"To: {to}",
        f"Subject: {subject}",
    ]
    if draft_id is not None:
        lines.append(f"Draft ID: {draft_id}")
    return "\n".join(lines) + "\n"


class DefaultMailSignatureSupportTests(unittest.TestCase):
    def test_server_exposes_default_mail_signature_env_setting(self):
        self.assertTrue(hasattr(_server, "DEFAULT_MAIL_SIGNATURE"))

    def test_compose_email_signature_parameters_are_in_tool_signature(self):
        params = inspect.signature(compose_tools.compose_email).parameters

        self.assertIn("include_signature", params)
        self.assertTrue(params["include_signature"].default)
        self.assertIn("signature_name", params)
        self.assertIsNone(params["signature_name"].default)

    def test_default_signature_applies_to_plain_draft_via_mail_signature_property(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "saved"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                mode="draft",
            )

        script = captured[0]
        _assert_ordered(
            self,
            script,
            'set message signature of newMessage to signature "TU"',
            "save newMessage",
        )

    def test_include_signature_false_suppresses_default_signature_assignment(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "saved"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                include_signature=False,
            )

        self.assertNotIn("message signature of newMessage", captured[0])

    def test_html_compose_applies_signature_without_selecting_all(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "saved"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            compose_tools.compose_email(
                account="Work",
                to="team@example.com",
                subject="Hi",
                body="Plain",
                body_html="<p>Hi</p>",
                mode="draft",
            )

        script = captured[0]
        self.assertIn('set message signature of newMsg to signature "TU"', script)
        self.assertNotIn('keystroke "a" using command down', script)

    def test_reply_and_forward_accept_signature_options(self):
        for tool, message_var, kwargs in [
            (
                compose_tools.reply_to_email,
                "replyMessage",
                {"message_id": "12345", "reply_body": "Thanks"},
            ),
            (
                compose_tools.forward_email,
                "forwardMessage",
                {"message_id": "12345", "to": "recipient@example.com"},
            ),
        ]:
            with self.subTest(tool=tool.__name__):
                captured = []

                def fake_run(script, timeout=120, captured=captured):
                    captured.append(script)
                    if "count of outgoing messages" in script:
                        return "0"
                    if "availableSignatures" in script:
                        return ""
                    return "saved"

                with patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    side_effect=fake_run,
                ):
                    tool(
                        account="Work",
                        include_signature=True,
                        signature_name="TU",
                        **kwargs,
                    )

                self.assertTrue(
                    any(f'set message signature of {message_var} to signature "TU"' in script for script in captured),
                    captured,
                )

    def test_default_signature_applies_to_reply_via_mail_signature_property(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "availableSignatures" in script:
                return ""
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|detected"
            return "ok"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        script = _main_reply_script(captured)
        _assert_ordered(
            self,
            script,
            'set message signature of replyMessage to signature "TU"',
            "set composedReplyContent to replyBodyText & return & return & quotedOriginalText",
            "save replyMessage",
        )
        verifier_script = next(script for script in captured if "set signatureWasRequested" in script)
        self.assertIn("set signatureWasRequested to true", verifier_script)
        self.assertIn("Signature Verification Status: detected", result)


class ComposeToolTests(unittest.TestCase):
    def test_create_rich_email_draft_blocks_reply_like_subject_without_confirmation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "blocked.eml"

            with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Re: Complex Request",
                    to="sender@example.com",
                    text_body="Thread-like draft",
                    output_path=str(output_path),
                    open_in_mail=False,
                )

        mock_run.assert_not_called()
        self.assertIn("standalone new message", result)
        self.assertFalse(output_path.exists())

    def test_create_rich_email_draft_allows_reply_like_subject_when_confirmed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "confirmed.eml"

            with patch(
                "apple_mail_mcp.tools.compose.run_applescript",
                return_value="sender@example.com",
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Re: standalone project name",
                    to="team@example.com",
                    text_body="This is a new standalone draft.",
                    output_path=str(output_path),
                    open_in_mail=False,
                    standalone_confirmed=True,
                )

            self.assertTrue(output_path.exists())
            self.assertIn("Rich draft prepared successfully", result)

    def test_create_rich_email_draft_writes_multipart_eml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "weekly-update.eml"
            scripts = []

            def fake_run_applescript(script, timeout=120):
                scripts.append(script)
                if len(scripts) == 1:
                    return "sender@example.com"
                return "saved"

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    side_effect=fake_run_applescript,
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run") as mock_run,
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Weekly Update",
                    to="team@example.com",
                    text_body="Plain fallback",
                    html_body="<html><body><h1>Weekly Update</h1></body></html>",
                    output_path=str(output_path),
                    open_in_mail=True,
                )

            payload = output_path.read_text()
            self.assertIn("multipart/alternative", payload)
            self.assertIn("<h1>Weekly Update</h1>", payload)
            self.assertIn("Subject: Weekly Update", payload)
            self.assertIn("Opened in Mail: yes", result)
            self.assertIn("Saved in Drafts: yes", result)
            mock_run.assert_called_once_with(["open", "-a", "Mail", str(output_path)], check=True)
            save_script = _save_draft_script(scripts)
            self.assertNotIn("every outgoing message whose subject is", save_script)
            # Save the newly-opened .eml compose object (the outgoing message
            # whose id was not present before the open), then close that exact
            # compose window without a second persist or a blind item-1 grab.
            self.assertNotIn("item 1 of outgoing messages", save_script)
            _assert_ordered(
                self,
                save_script,
                "set priorIds to {",
                "repeat with candidateMessage in outgoing messages",
                "if priorIds does not contain candidateId then",
                "save targetMessage",
                "close (window of targetMessage) saving no",
            )
            self.assertNotIn("System Events", save_script)
            self.assertNotIn('keystroke "s" using command down', save_script)
            self.assertNotIn("close window 1 saving no", save_script)
            self.assertNotIn("close window 1 saving yes", save_script)

    def test_create_rich_email_draft_default_saves_and_closes_mail_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "default-saved.eml"
            scripts = []

            def fake_run_applescript(script, timeout=120):
                scripts.append(script)
                if len(scripts) == 1:
                    return "sender@example.com"
                return "saved"

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    side_effect=fake_run_applescript,
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run") as mock_run,
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Default Saved",
                    to="team@example.com",
                    text_body="Plain fallback",
                    html_body="<p>Hi</p>",
                    output_path=str(output_path),
                )

            mock_run.assert_called_once_with(["open", "-a", "Mail", str(output_path)], check=True)
            self.assertIn("Saved in Drafts: yes", result)
            self.assertIn("Left open for review: no", result)
            save_script = _save_draft_script(scripts)
            self.assertNotIn("every outgoing message whose subject is", save_script)
            # Save the newly-opened .eml compose object (id-diff against the
            # pre-open snapshot), then close that exact compose window.
            self.assertNotIn("item 1 of outgoing messages", save_script)
            _assert_ordered(
                self,
                save_script,
                "set priorIds to {",
                "repeat with candidateMessage in outgoing messages",
                "if priorIds does not contain candidateId then",
                "save targetMessage",
                "close (window of targetMessage) saving no",
            )
            self.assertNotIn("System Events", save_script)
            self.assertNotIn('keystroke "s" using command down', save_script)
            self.assertNotIn("close window 1 saving no", save_script)
            self.assertNotIn("close window 1 saving yes", save_script)

    def test_create_rich_email_draft_allows_partial_details(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "partial.eml"

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="sender@example.com",
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run"),
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    output_path=str(output_path),
                    open_in_mail=False,
                )

            payload = output_path.read_text()
            self.assertIn("Draft outline", payload)
            self.assertIn("Missing details: subject, to, body", result)
            self.assertIn("Opened in Mail: no", result)

    def test_create_rich_email_draft_empty_subject_does_not_open_mail_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "empty-subject.eml"
            scripts = []

            def fake_run_applescript(script, timeout=120):
                scripts.append(script)
                return "sender@example.com"

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    side_effect=fake_run_applescript,
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run") as mock_run,
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="",
                    output_path=str(output_path),
                )

            payload = output_path.read_text()
            self.assertIn("Draft outline", payload)
            self.assertIn("Missing details: subject, to, body", result)
            self.assertIn("Opened in Mail: no", result)
            mock_run.assert_not_called()
            self.assertEqual(len(scripts), 1)
            self.assertNotIn("every outgoing message whose subject is", scripts[0])

    def test_create_rich_email_draft_can_save_to_drafts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "saved.eml"
            # Three AppleScript calls: sender resolution, pre-open outgoing-id
            # snapshot, then the save-as-draft script.
            run_results = ["sender@example.com", "", "saved"]

            def fake_run_applescript(script, timeout=120):
                return run_results.pop(0)

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    side_effect=fake_run_applescript,
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run"),
            ):
                result = compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Saved Draft",
                    output_path=str(output_path),
                    open_in_mail=True,
                    save_as_draft=True,
                )

            self.assertIn("Saved in Drafts: yes", result)


class SaveNewComposeWindowAsDraftTests(unittest.TestCase):
    def test_saves_new_compose_window_without_subject_lookup(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "saved"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools._save_new_compose_window_as_draft(close_after_save=True)

        self.assertTrue(result)
        self.assertEqual(len(captured), 1)
        self.assertNotIn("every outgoing message whose subject is", captured[0])
        # Save through Mail's outgoing-message object model via id-diff, not a
        # blind item-1 grab and not System Events.
        self.assertNotIn("item 1 of outgoing messages", captured[0])
        _assert_ordered(
            self,
            captured[0],
            "set priorIds to {",
            "repeat with candidateMessage in outgoing messages",
            "if priorIds does not contain candidateId then",
            "save targetMessage",
            "close (window of targetMessage) saving no",
        )
        self.assertNotIn("System Events", captured[0])
        self.assertNotIn('keystroke "s" using command down', captured[0])
        self.assertNotIn("close window 1 saving no", captured[0])
        self.assertNotIn("close window 1 saving yes", captured[0])

    def test_can_leave_new_compose_window_open_for_review(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "saved"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools._save_new_compose_window_as_draft(close_after_save=False)

        self.assertTrue(result)
        self.assertIn("save targetMessage", captured[0])
        self.assertNotIn("System Events", captured[0])
        self.assertNotIn('keystroke "s" using command down', captured[0])
        self.assertNotIn("close (window of targetMessage) saving no", captured[0])
        self.assertNotIn("close window 1 saving yes", captured[0])

    def test_prior_outgoing_ids_are_excluded_from_save_target(self):
        """A pre-existing compose window (id in the snapshot) is never saved."""
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "saved"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools._save_new_compose_window_as_draft(
                prior_outgoing_ids={"41", "57"},
                close_after_save=False,
            )

        self.assertTrue(result)
        # Both pre-open ids land in the priorIds literal so the diff skips them.
        self.assertIn('"41"', captured[0])
        self.assertIn('"57"', captured[0])
        self.assertIn("if priorIds does not contain candidateId then", captured[0])

    def test_no_new_outgoing_window_returns_false(self):
        """When only pre-existing windows exist, the save reports failure."""

        def fake_run(script, timeout=120):
            return "not-found"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools._save_new_compose_window_as_draft(
                prior_outgoing_ids={"7"},
                retries=2,
                delay_seconds=0,
            )

        self.assertFalse(result)


class StripCdataTests(unittest.TestCase):
    def test_none_passes_through(self):
        self.assertIsNone(compose_tools._strip_cdata_wrappers(None))

    def test_empty_passes_through(self):
        self.assertEqual("", compose_tools._strip_cdata_wrappers(""))

    def test_unwraps_symmetric_block(self):
        self.assertEqual(
            "<p>Hello</p>",
            compose_tools._strip_cdata_wrappers("<![CDATA[<p>Hello</p>]]>"),
        )

    def test_unwraps_multiline_block(self):
        self.assertEqual(
            "\n<p>Hi</p>\n",
            compose_tools._strip_cdata_wrappers("<![CDATA[\n<p>Hi</p>\n]]>"),
        )

    def test_strips_stray_closing_marker(self):
        # This is the symptom users actually see — HTML parsers hide the
        # opening `<![CDATA[`, but the trailing `]]>` renders as text.
        self.assertEqual(
            "<p>Hello</p>",
            compose_tools._strip_cdata_wrappers("<p>Hello</p>]]>"),
        )

    def test_strips_stray_opening_marker(self):
        self.assertEqual(
            "<p>Hello</p>",
            compose_tools._strip_cdata_wrappers("<![CDATA[<p>Hello</p>"),
        )

    def test_leaves_normal_html_untouched(self):
        html = "<html><body><h1>Weekly Update</h1></body></html>"
        self.assertEqual(html, compose_tools._strip_cdata_wrappers(html))


class CreateRichEmailDraftCdataTests(unittest.TestCase):
    def test_cdata_wrapped_html_body_is_stripped_in_eml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "cdata.eml"

            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="sender@example.com",
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run"),
            ):
                compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="CDATA Test",
                    to="team@example.com",
                    text_body="Plain fallback",
                    html_body="<![CDATA[<html><body><h1>Hi</h1></body></html>]]>",
                    output_path=str(output_path),
                    open_in_mail=False,
                )

            payload = output_path.read_text()
            self.assertIn("<h1>Hi</h1>", payload)
            self.assertNotIn("<![CDATA[", payload)
            self.assertNotIn("]]>", payload)


class ValidateFromAddressTests(unittest.TestCase):
    def test_none_skips_lookup(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            override, error = compose_tools._validate_from_address("Work", None)
        self.assertIsNone(override)
        self.assertIsNone(error)
        mock_run.assert_not_called()

    def test_blank_skips_lookup(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            override, error = compose_tools._validate_from_address("Work", "   ")
        self.assertIsNone(override)
        self.assertIsNone(error)
        mock_run.assert_not_called()

    def test_matches_case_insensitively_and_trims(self):
        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="Default@Example.com\nSecondary@Example.org",
        ):
            override, error = compose_tools._validate_from_address("Work", "  SECONDARY@example.ORG ")
        self.assertEqual(override, "Secondary@Example.org")
        self.assertIsNone(error)

    def test_unknown_alias_returns_error(self):
        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="default@example.com",
        ):
            override, error = compose_tools._validate_from_address("Work", "other@example.com")
        self.assertIsNone(override)
        self.assertIn("is not configured on account", error)
        self.assertIn("default@example.com", error)

    def test_missing_aliases_returns_error(self):
        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="",
        ):
            override, error = compose_tools._validate_from_address("Work", "anything@example.com")
        self.assertIsNone(override)
        self.assertIn("Could not read email addresses", error)


class ComposeEmailSenderOverrideTests(unittest.TestCase):
    def test_compose_blocks_reply_like_subject_without_standalone_confirmation(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.compose_email(
                account="Work",
                to="norman@example.com",
                subject="Re: Forwarded notes",
                body="Thanks, I will take a look.",
            )

        mock_run.assert_not_called()
        self.assertIn("compose_email creates a standalone new message", result)
        self.assertIn("Use reply_to_email(message_id=...)", result)

    def test_compose_allows_reply_like_subject_when_standalone_is_confirmed(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Email saved as draft!"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.compose_email(
                account="Work",
                to="norman@example.com",
                subject="Re: standalone project name",
                body="This is not a reply to an existing email.",
                standalone_confirmed=True,
            )

        self.assertEqual(len(captured), 1)
        self.assertIn("SAVING EMAIL AS DRAFT", captured[0])
        self.assertIn("saved as draft", result)

    def test_compose_defaults_to_draft_mode(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Email saved as draft!"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
            )

        self.assertIn("SAVING EMAIL AS DRAFT", captured[0])
        self.assertIn("save newMessage", captured[0])
        self.assertNotIn("close window 1 saving yes", captured[0])
        self.assertNotIn("send newMessage", captured[0])

    def test_compose_open_mode_saves_before_leaving_open_for_review(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            # First call is the window-count probe (Fix #12); return 0 open windows.
            if "count of outgoing messages" in script:
                return "0"
            return "✓ Email opened in Mail for review."

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                mode="open",
            )

        # captured[0] is the window-count probe; find the main compose script.
        main_scripts = [s for s in captured if "OPENING EMAIL FOR REVIEW" in s]
        self.assertEqual(len(main_scripts), 1)
        self.assertIn("save newMessage", main_scripts[0])
        self.assertIn("activate", main_scripts[0])
        self.assertIn("review", result)

    def test_draft_safe_blocks_explicit_send(self):
        with patch.object(compose_tools.server, "DRAFT_SAFE", True):
            result = compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                mode="send",
            )

        self.assertIn("draft-safe mode", result)

    def test_default_emits_single_alias_fallback_block(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Email sent successfully!"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                mode="draft",
            )

        self.assertEqual(len(captured), 1)
        script = captured[0]
        self.assertIn("email addresses of targetAccount", script)
        self.assertIn("if (count of emailAddrs) is 1 then", script)
        self.assertIn("set sender of newMessage to item 1 of emailAddrs", script)
        self.assertNotIn('set sender of newMessage to "', script)

    def test_injects_sender_when_from_address_is_valid(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            if len(scripts) == 1:
                return "default@example.com\nsecondary@example.org"
            return "✓ Email sent successfully!"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                mode="draft",
                from_address="secondary@example.org",
            )

        self.assertEqual(len(scripts), 2)
        main_script = scripts[1]
        self.assertIn('set sender of newMessage to "secondary@example.org"', main_script)
        self.assertNotIn("if (count of emailAddrs) is 1 then", main_script)

    def test_rejects_invalid_from_address_without_sending(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            return "default@example.com"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.compose_email(
                account="Work",
                to="self@example.com",
                subject="Test",
                body="Body",
                mode="draft",
                from_address="unknown@example.com",
            )

        self.assertEqual(len(scripts), 1)
        self.assertTrue(result.startswith("Error: 'from_address'"))


class AccountDefaultAliasIfSingleTests(unittest.TestCase):
    def test_returns_sole_alias(self):
        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="solo@example.com",
        ):
            self.assertEqual(
                compose_tools._account_default_alias_if_single("Solo"),
                "solo@example.com",
            )

    def test_returns_none_when_empty(self):
        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            return_value="",
        ):
            self.assertIsNone(compose_tools._account_default_alias_if_single("Multi"))


class ComposeSenderScriptTests(unittest.TestCase):
    def test_override_sets_sender_directly(self):
        script = compose_tools._compose_sender_script("newMessage", "targetAccount", "chosen@example.com")
        self.assertEqual(script, 'set sender of newMessage to "chosen@example.com"')

    def test_without_override_emits_single_alias_fallback(self):
        script = compose_tools._compose_sender_script("newMessage", "targetAccount", None)
        self.assertIn("email addresses of targetAccount", script)
        self.assertIn("if (count of emailAddrs) is 1 then", script)
        self.assertIn("set sender of newMessage to item 1 of emailAddrs", script)

    def test_override_value_is_escaped(self):
        script = compose_tools._compose_sender_script("newMessage", "targetAccount", 'weird"quote@example.com')
        self.assertIn(r"\"quote@example.com", script)


class CreateRichEmailDraftFromAddressTests(unittest.TestCase):
    def test_omits_from_header_for_multi_alias_account(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "multi.eml"
            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="",
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run"),
            ):
                compose_tools.create_rich_email_draft(
                    account="Multi",
                    subject="No From",
                    to="team@example.com",
                    text_body="Body",
                    output_path=str(output_path),
                    open_in_mail=False,
                )

            payload = output_path.read_text()
            header_block = payload.split("\n\n", 1)[0]
            self.assertNotIn("From:", header_block)

    def test_stamps_from_header_for_single_alias_account(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "single.eml"
            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="solo@example.com",
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run"),
            ):
                compose_tools.create_rich_email_draft(
                    account="Solo",
                    subject="Single",
                    to="team@example.com",
                    text_body="Body",
                    output_path=str(output_path),
                    open_in_mail=False,
                )

            payload = output_path.read_text()
            self.assertIn("From: solo@example.com", payload)

    def test_stamps_from_header_when_address_is_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "stamped.eml"
            with (
                patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    return_value="default@example.com\nsecondary@example.org",
                ),
                patch("apple_mail_mcp.tools.compose.subprocess.run"),
            ):
                compose_tools.create_rich_email_draft(
                    account="Work",
                    subject="Stamped",
                    to="team@example.com",
                    text_body="Body",
                    output_path=str(output_path),
                    open_in_mail=False,
                    from_address="secondary@example.org",
                )

            payload = output_path.read_text()
            self.assertIn("From: secondary@example.org", payload)


class ReplyToEmailSenderOverrideTests(unittest.TestCase):
    def test_reply_uses_native_mail_reply_and_preserves_native_quote_by_default(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        script = _main_reply_script(captured)
        _assert_ordered(
            self,
            script,
            "set sourceContent to content of foundMessage as string",
            "set replyBodyText to do shell script",
            "set replyMessage to reply foundMessage",
            'set quotedOriginalNeedle to "On " & sourceDate & ", " & sourceSender & " wrote:"',
            "set quotedOriginalText to quotedOriginalNeedle & return & sourceContent",
            "set composedReplyContent to replyBodyText & return & return & quotedOriginalText",
            "set content of replyMessage to (composedReplyContent as rich text)",
            "save replyMessage",
        )
        self.assertNotIn("make new outgoing message", script)
        self.assertNotIn("content:fullBody", script)
        self.assertNotIn("set quotedBody", script)
        self.assertNotIn("quoted original truncated", script)
        self.assertNotIn("set existingReplyContent to content of replyMessage", script)
        self.assertNotIn("NSPasteboard", script)
        self.assertNotIn("System Events", script)
        self.assertNotIn('keystroke "v"', script)

    def test_reply_to_email_accepts_output_format_parameter(self):
        params = inspect.signature(compose_tools.reply_to_email).parameters

        self.assertIn("output_format", params)
        self.assertEqual(params["output_format"].default, "text")

    def test_empty_reply_body_keeps_body_assignment_guarded(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="",
            )

        script = _main_reply_script(captured)
        self.assertIn('if replyBodyText is not "" then', script)
        self.assertIn("set content of replyMessage to (composedReplyContent as rich text)", script)

    def test_reply_draft_success_outputs_artifact_id_for_exact_verification(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        self.assertIn("Draft ID: 84053", result)
        self.assertIn("Verification Status: found", result)
        self.assertIn("Verified Draft ID: 84053", result)
        verifier_script = next(script for script in captured if "set targetDraftId to" in script)
        self.assertIn('set targetDraftIdText to "84053"', verifier_script)
        self.assertIn("every message of draftsMailbox whose id is targetDraftId", verifier_script)
        self.assertIn("return exactResult", verifier_script)

    def test_reply_draft_success_json_outputs_contract(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["mode"], "draft")
        self.assertFalse(payload["sent"])
        self.assertEqual(payload["subject"], "Re: Test")
        self.assertEqual(payload["draft_id"], "84053")
        self.assertEqual(payload["verified_draft_id"], "84053")
        self.assertEqual(payload["verification_status"], "found")
        self.assertTrue(payload["exact_id_verified"])
        self.assertTrue(payload["body_present"])
        self.assertEqual(payload["attachment_status"], "not_requested")
        self.assertEqual(payload["signature_status"], "not_requested")
        self.assertEqual(payload["mailbox"], "Drafts")

    def test_reply_draft_success_outputs_attachment_and_signature_verification(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "availableSignatures" in script:
                return ""
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|verified|missing|1|support.pdf::2048;;"
            return "ok"

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "apple_mail_mcp.tools.compose.run_applescript",
                side_effect=fake_run,
            ),
            patch("apple_mail_mcp.tools.compose._validate_attachment_paths") as mock_validate,
        ):
            attachment = Path(tmpdir) / "support.pdf"
            attachment.write_text("pdf")
            mock_validate.return_value = ([str(attachment)], None)
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                attachments=str(attachment),
                include_signature=True,
                signature_name="TU",
            )

        self.assertIn("Attachment Verification Status: verified", result)
        self.assertIn("Attachments Applied Count: 1", result)
        self.assertIn("support.pdf (2048 bytes)", result)
        self.assertIn("Signature Verification Status: missing", result)
        self.assertIn("requested Mail signature was not detected", result)
        verifier_script = next(script for script in captured if "set expectedAttachmentCount to" in script)
        self.assertIn('using terms from application "Mail"', verifier_script)
        self.assertIn("set expectedAttachmentCount to 1", verifier_script)
        self.assertIn('set expectedAttachmentNames to {"support.pdf"}', verifier_script)
        self.assertIn("set signatureWasRequested to true", verifier_script)
        self.assertIn('set expectedSignatureName to "TU"', verifier_script)
        self.assertIn("if (name of sig as string) is expectedSignatureName then", verifier_script)
        self.assertIn("set expectedSigText to content of sig as string", verifier_script)
        self.assertIn("expectedAttachmentName", verifier_script)
        self.assertIn("file size of anAttachment", verifier_script)

    def test_reply_draft_attachment_verification_checks_requested_filenames(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "FOUND|84053|missing|not_requested|1|wrong.pdf::2048;;"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            verification = compose_tools._verify_saved_reply_draft(
                "Work",
                "Re: Test",
                "Reply body",
                draft_id="84053",
                expected_attachment_count=1,
                expected_attachment_names=["support.pdf"],
                signature_requested=False,
            )

        self.assertTrue(verification.ok)
        self.assertEqual(verification.attachment_status, "missing")
        script = captured[0]
        self.assertIn('set expectedAttachmentNames to {"support.pdf"}', script)
        self.assertIn("if (expectedAttachmentName as string) is not in draftAttachmentNames then return \"missing\"", script)

    def test_reply_draft_attachment_warning_includes_applied_count(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|missing|not_requested|0|"
            return "ok"

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "apple_mail_mcp.tools.compose.run_applescript",
                side_effect=fake_run,
            ),
            patch("apple_mail_mcp.tools.compose._validate_attachment_paths") as mock_validate,
        ):
            attachment = Path(tmpdir) / "support.pdf"
            attachment.write_text("pdf")
            mock_validate.return_value = ([str(attachment)], None)
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                attachments=str(attachment),
                include_signature=False,
            )

        self.assertIn("Attachment Verification Status: missing", result)
        self.assertIn("Attachments Applied Count: 0", result)
        self.assertIn("requested attachments could not be verified", result)

    def test_reply_verification_parser_preserves_pipe_in_attachment_filename(self):
        verification = compose_tools._reply_verification_from_output(
            "FOUND|84053|verified|not_requested|1|support|final.pdf::2048;;"
        )

        self.assertTrue(verification.ok)
        self.assertEqual(verification.attachment_status, "verified")
        self.assertEqual(verification.attachment_count, 1)
        self.assertEqual(
            verification.attachments_applied,
            [{"filename": "support|final.pdf", "size": 2048}],
        )

    def test_reply_success_text_hides_attachment_count_when_not_requested(self):
        verification = compose_tools._reply_verification_from_output(
            "FOUND|84053|not_requested|not_requested|1|leftover.pdf::2048;;"
        )

        result = compose_tools._format_reply_verification_lines(verification, "84053")

        self.assertIn("Attachment Verification Status: not_requested", result)
        self.assertNotIn("Attachments Applied Count", result)
        self.assertNotIn("leftover.pdf", result)

    def test_reply_draft_success_json_includes_attachment_and_signature_status(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "availableSignatures" in script:
                return ""
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84054|verified|missing|1|support|final.pdf::2048;;"
            return "ok"

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "apple_mail_mcp.tools.compose.run_applescript",
                side_effect=fake_run,
            ),
            patch("apple_mail_mcp.tools.compose._validate_attachment_paths") as mock_validate,
        ):
            attachment = Path(tmpdir) / "support.pdf"
            attachment.write_text("pdf")
            mock_validate.return_value = ([str(attachment)], None)
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                attachments=str(attachment),
                include_signature=True,
                signature_name="TU",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["draft_id"], "84053")
        self.assertEqual(payload["verified_draft_id"], "84054")
        self.assertEqual(payload["verification_status"], "found")
        self.assertFalse(payload["exact_id_verified"])
        self.assertEqual(payload["attachment_status"], "verified")
        self.assertEqual(payload["attachment_count"], 1)
        self.assertEqual(
            payload["attachments_applied"],
            [{"filename": "support|final.pdf", "size": 2048}],
        )
        self.assertEqual(payload["signature_status"], "missing")
        self.assertFalse(payload["sent"])

    def test_reply_draft_success_text_warns_when_fallback_verified_different_draft(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(to="native reply recipients", draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84054|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        self.assertIn("Draft ID: 84053", result)
        self.assertIn("Verified Draft ID: 84054", result)
        self.assertIn("verified by bounded Drafts fallback", result)

    def test_reply_all_with_attachment_preserves_single_body_and_verifies_exact_draft(self):
        captured = []
        body_sentinel = "AA-REPLY-ALL-BODY-SENTINEL-84053"

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(
                    to="native reply recipients",
                    draft_id="84053",
                    quote_needle="On Today, Sender <sender@example.com> wrote:",
                )
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|verified|not_requested"
            return "ok"

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch(
                "apple_mail_mcp.tools.compose.run_applescript",
                side_effect=fake_run,
            ),
            patch("apple_mail_mcp.tools.compose._validate_attachment_paths") as mock_validate,
        ):
            attachment = Path(tmpdir) / "support.pdf"
            attachment.write_text("pdf")
            mock_validate.return_value = ([str(attachment)], None)
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body=f"{body_sentinel}\n\nReply body",
                reply_to_all=True,
                attachments=str(attachment),
                include_signature=False,
            )

        self.assertIn("Reply saved as draft!", result)
        self.assertIn("Verification Status: found", result)
        self.assertIn("Verified Draft ID: 84053", result)
        self.assertIn("Attachment Verification Status: verified", result)

        reply_script = _main_reply_script(captured)
        self.assertIn("set replyMessage to reply foundMessage with reply to all", reply_script)
        self.assertNotIn("System Events", reply_script)
        self.assertNotIn('keystroke "v"', reply_script)
        self.assertNotIn("set the clipboard to replyBodyText", reply_script)
        self.assertEqual(reply_script.count("set content of replyMessage to"), 1)
        self.assertEqual(reply_script.count("replyBodyText & return & return & quotedOriginalText"), 1)
        _assert_ordered(
            self,
            reply_script,
            "set replyMessage to reply foundMessage with reply to all",
            "set composedReplyContent to replyBodyText & return & return & quotedOriginalText",
            "set content of replyMessage to (composedReplyContent as rich text)",
            "make new attachment with properties {file name:theFile} at after the last paragraph of content",
            "save replyMessage",
        )

        verifier_script = next(script for script in captured if "set targetDraftIdText" in script)
        self.assertIn('set targetDraftIdText to "84053"', verifier_script)
        self.assertIn(f'set replyBodyNeedle to "{body_sentinel}"', verifier_script)
        self.assertIn("set expectedAttachmentCount to 1", verifier_script)
        self.assertIn("every message of draftsMailbox whose id is targetDraftId", verifier_script)

    def test_reply_defaults_to_draft_mode(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        script = _main_reply_script(captured)
        self.assertIn("SAVING REPLY AS DRAFT", script)
        # Native Mail reply: Mail constructs the quoted prior conversation and
        # the tool inserts the requested body into the native composer.
        self.assertIn("set replyMessage to reply foundMessage", script)
        self.assertNotIn("set replyMessage to reply foundMessage with opening window", script)
        self.assertGreaterEqual(script.count("save replyMessage"), 1)
        self.assertNotIn("close (window of replyMessage)", script)
        self.assertNotIn("close front window", script)
        self.assertIn("set sourceSubject to subject of foundMessage as string", script)
        self.assertNotIn("set replySubject to subject of replyMessage as string", script)
        self.assertIn('set outputText to outputText & "Subject: " & replySubject', script)
        self.assertIn("set quotedOriginalText to", script)
        self.assertIn(
            "set composedReplyContent to replyBodyText & return & return & quotedOriginalText",
            script,
        )
        self.assertNotIn("content of replyMessage as string", script)
        self.assertNotIn("NSPasteboard", script)
        self.assertNotIn("System Events", script)
        self.assertNotIn('keystroke "v"', script)
        self.assertNotIn("send replyMessage", script)

    def test_reply_draft_success_runs_bounded_saved_draft_verifier(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output()
            if "repeat with verifyAttempt from 1 to 20" in script:
                return "FOUND"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        self.assertIn("Reply saved as draft!", result)
        self.assertIn("Verification Status: found", result)
        verifier_script = next(script for script in captured if "repeat with verifyAttempt from 1 to 20" in script)
        self.assertIn("messages 1 thru headEnd of draftsMailbox", verifier_script)
        self.assertIn('set replyBodyNeedle to "Reply body"', verifier_script)
        self.assertIn('if "Re: Test" is "" or draftSubject is "Re: Test" then', verifier_script)
        self.assertIn("my replyBodyIsBeforeQuote(draftContent, replyBodyNeedle, quotedNeedle)", verifier_script)
        self.assertIn('return "BODY_MISSING|" & bodyMissingDraftId', verifier_script)

    def test_reply_draft_verifier_falls_back_when_exact_id_is_not_yet_resolvable(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "FOUND|84054|not_requested|not_requested"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            verification = compose_tools._verify_saved_reply_draft(
                "Work",
                "Re: Test",
                "Reply body",
                draft_id="84053",
            )

        self.assertTrue(verification.ok)
        self.assertEqual(verification.matched_artifact_id, "84054")
        script = captured[0]
        exact_lookup = script.index("set targetDrafts to every message of draftsMailbox whose id is targetDraftId")
        fallback_lookup = script.index("set candidateDrafts to messages 1 thru headEnd of draftsMailbox")
        self.assertLess(exact_lookup, fallback_lookup)
        exact_branch = script[exact_lookup:fallback_lookup]
        self.assertNotIn('return "NOT_FOUND"', exact_branch)

    def test_reply_signature_verification_runs_when_signature_requested_without_resolved_name(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                include_signature=True,
            )

        verifier_script = next(script for script in captured if "set signatureWasRequested" in script)
        self.assertIn("set signatureWasRequested to true", verifier_script)

    def test_reply_signature_verification_targets_resolved_signature_name(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "availableSignatures" in script:
                return ""
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|detected"
            return "ok"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                include_signature=True,
            )

        self.assertIn("Signature Verification Status: detected", result)
        verifier_script = next(script for script in captured if "set expectedSignatureName" in script)
        self.assertIn('set expectedSignatureName to "TU"', verifier_script)
        self.assertIn("if (name of sig as string) is expectedSignatureName then", verifier_script)
        self.assertIn("set expectedSigText to content of sig as string", verifier_script)

    def test_reply_attachment_validation_error_removes_body_temp_file(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp_path = Path(temp_dir.name) / "mail_reply_body.txt"

        class FakeTempFile:
            name = str(temp_path)

            def __enter__(self):
                self.handle = temp_path.open("w", encoding="utf-8")
                return self.handle

            def __exit__(self, exc_type, exc, tb):
                self.handle.close()

        with (
            patch("apple_mail_mcp.tools.compose.tempfile.NamedTemporaryFile", return_value=FakeTempFile()),
            patch(
                "apple_mail_mcp.tools.compose._validate_attachment_paths",
                return_value=([], "Error: Attachment file does not exist: missing.pdf"),
            ),
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                attachments="missing.pdf",
            )

        self.assertIn("Attachment file does not exist", result)
        self.assertFalse(temp_path.exists())

    def test_reply_draft_success_reports_structured_artifact_error_when_body_missing(self):
        sentinel = "AA-REPLY-BODY-SENTINEL-84053"

        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "BODY_MISSING|84053"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body=f"{sentinel}\n\nReply body",
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "REPLY_DRAFT_BODY_MISSING")
        self.assertEqual(payload["remediation"]["artifact_message_id"], "84053")
        self.assertEqual(payload["remediation"]["draft_id"], "84053")
        self.assertEqual(payload["remediation"]["expected_body_needle"], sentinel)
        self.assertIn("No email was sent", payload["message"])

    def test_reply_draft_verifier_timeout_preserves_saved_draft_id(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                raise AppleScriptTimeout("simulated verifier timeout")
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "REPLY_DRAFT_VERIFICATION_TIMEOUT")
        self.assertEqual(payload["remediation"]["artifact_message_id"], "84053")
        self.assertEqual(payload["remediation"]["draft_id"], "84053")
        self.assertEqual(payload["remediation"]["verification_status"], "verification_timeout")
        self.assertIn("No email was sent", payload["message"])

    def test_reply_draft_verifier_error_preserves_saved_draft_id(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                raise RuntimeError("simulated verifier failure")
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "REPLY_DRAFT_VERIFICATION_ERROR")
        self.assertEqual(payload["remediation"]["artifact_message_id"], "84053")
        self.assertEqual(payload["remediation"]["draft_id"], "84053")
        self.assertEqual(payload["remediation"]["verification_status"], "applescript_error")
        self.assertIn("No email was sent", payload["message"])

    def test_reply_to_email_rejects_json_mode_send_before_main_script(self):
        with (
            patch.object(compose_tools._server, "READ_ONLY", False),
            patch.object(compose_tools._server, "DRAFT_SAFE", False),
            patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                mode="send",
                output_format="json",
            )

        mock_run.assert_not_called()
        self.assertIn("output_format='json' is only supported", result)

    def test_reply_to_email_rejects_json_send_alias_before_main_script(self):
        with (
            patch.object(compose_tools._server, "READ_ONLY", False),
            patch.object(compose_tools._server, "DRAFT_SAFE", False),
            patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                send=True,
                output_format="json",
            )

        mock_run.assert_not_called()
        self.assertIn("output_format='json' is only supported", result)

    def test_reply_draft_verifier_rejects_body_after_quoted_original(self):
        def fake_run(script, timeout=120):
            if 'set targetDraftIdText to "84053"' in script:
                return "BODY_AFTER_QUOTE|84053"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            verification = compose_tools._verify_saved_reply_draft(
                "Work",
                "Re: Test",
                "Unique body sentinel",
                draft_id="84053",
                quoted_needle="Original message text",
            )

        self.assertFalse(verification.ok)
        self.assertEqual(verification.body_missing_artifact_id, "84053")
        self.assertEqual(verification.status, "body_after_quote")

    def test_reply_draft_reports_structured_error_when_body_saved_after_quote(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(
                    draft_id="84053",
                    quote_needle="On Today, Sender <sender@example.com> wrote:",
                )
            if 'set targetDraftIdText to "84053"' in script:
                return "BODY_AFTER_QUOTE|84053"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Unique body sentinel",
            )

        payload = json.loads(result)
        self.assertEqual(payload["code"], "REPLY_DRAFT_BODY_AFTER_QUOTE")
        self.assertEqual(payload["remediation"]["artifact_message_id"], "84053")
        self.assertEqual(payload["remediation"]["verification_status"], "body_after_quote")

    def test_reply_draft_success_reports_error_when_saved_draft_not_verified(self):
        def fake_run(script, timeout=120):
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(draft_id="84053")
            if 'set targetDraftIdText to "84053"' in script:
                return "NOT_FOUND"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
            )

        self.assertIn("did not verify it in the newest Drafts window", result)
        self.assertIn("No email was sent", result)

    def test_reply_open_mode_saves_before_leaving_open_for_review(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            # First call is the window-count probe (Fix #12); return 0 open windows.
            if "count of outgoing messages" in script:
                return "0"
            return "Reply opened in Mail for review."

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                mode="open",
            )

        # captured[0] is the window-count probe; find the main reply script.
        reply_scripts = [s for s in captured if "OPENING REPLY FOR REVIEW" in s]
        self.assertEqual(len(reply_scripts), 1)
        self.assertIn("reply foundMessage with opening window", reply_scripts[0])
        self.assertIn("save replyMessage", reply_scripts[0])
        self.assertIn("review", result)

    def test_reply_open_success_outputs_verification_status(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            if "OPENING REPLY FOR REVIEW" in script:
                return (
                    _saved_reply_draft_output(
                        subject="Re: Test",
                        draft_id="84053",
                        quote_needle="On Today, Sender <sender@example.com> wrote:",
                    )
                    .replace("SAVING REPLY AS DRAFT", "OPENING REPLY FOR REVIEW")
                    .replace(
                        "Reply saved as draft!",
                        "Reply opened in Mail for review. Edit and send when ready.",
                    )
                )
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                mode="open",
            )

        self.assertIn("Draft ID: 84053", result)
        self.assertIn("Verification Status: found", result)
        self.assertIn("Verified Draft ID: 84053", result)

    def test_reply_open_success_json_outputs_open_mode(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            if "OPENING REPLY FOR REVIEW" in script:
                return (
                    _saved_reply_draft_output(
                        subject="Re: Test",
                        draft_id="84053",
                        quote_needle="On Today, Sender <sender@example.com> wrote:",
                    )
                    .replace("SAVING REPLY AS DRAFT", "OPENING REPLY FOR REVIEW")
                    .replace(
                        "Reply saved as draft!",
                        "Reply opened in Mail for review. Edit and send when ready.",
                    )
                )
            if 'set targetDraftIdText to "84053"' in script:
                return "FOUND|84053|not_requested|not_requested"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                mode="open",
                output_format="json",
            )

        payload = json.loads(result)
        self.assertEqual(payload["mode"], "open")
        self.assertFalse(payload["sent"])
        self.assertEqual(payload["subject"], "Re: Test")
        self.assertEqual(payload["draft_id"], "84053")
        self.assertEqual(payload["verified_draft_id"], "84053")
        self.assertEqual(payload["verification_status"], "found")

    def test_default_emits_single_alias_fallback_for_reply_message(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                send=False,
            )

        script = _main_reply_script(captured)
        self.assertIn("if (count of emailAddrs) is 1 then", script)
        self.assertIn("set sender of replyMessage to item 1 of emailAddrs", script)
        self.assertNotIn('set sender of replyMessage to "', script)

    def test_reply_to_all_uses_native_mail_reply_all(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                reply_to_all=True,
            )

        script = _main_reply_script(captured)
        self.assertIn(
            "reply foundMessage with reply to all",
            script,
        )
        self.assertNotIn("to recipients of foundMessage", script)
        self.assertNotIn("cc recipients of foundMessage", script)
        self.assertNotIn(
            "if rAddr is not senderAddr and rAddr is not in myAddrs",
            script,
        )

    def test_reply_without_all_uses_native_plain_reply(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                reply_to_all=False,
            )

        script = _main_reply_script(captured)
        self.assertIn("set replyMessage to reply foundMessage", script)
        self.assertNotIn("set replyMessage to reply foundMessage with opening window", script)
        self.assertNotIn("reply to all", script)
        self.assertNotIn("cc recipients of foundMessage", script)
        self.assertNotIn(
            "make new to recipient at end of to recipients of replyMessage",
            script,
        )

    def test_reply_signature_is_applied_before_body_insert(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "availableSignatures" in script:
                return ""
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                signature_name="TU",
            )

        script = _main_reply_script(captured)
        _assert_ordered(
            self,
            script,
            "set replyMessage to reply foundMessage",
            'set message signature of replyMessage to signature "TU"',
            "set composedReplyContent to replyBodyText & return & return & quotedOriginalText",
            "set content of replyMessage to (composedReplyContent as rich text)",
        )
        self.assertNotIn("set the clipboard to replyBodyText", script)
        self.assertNotIn("System Events", script)
        self.assertNotIn('keystroke "v"', script)

    def test_include_signature_false_still_inserts_reply_body(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Unique body sentinel 84053",
                include_signature=False,
            )

        script = _main_reply_script(captured)
        self.assertIn("set message signature of replyMessage to missing value", script)
        _assert_ordered(
            self,
            script,
            "set message signature of replyMessage to missing value",
            'if replyBodyText is not "" then',
            "set composedReplyContent to replyBodyText & return & return & quotedOriginalText",
            "set content of replyMessage to (composedReplyContent as rich text)",
            "save replyMessage",
        )

    def test_include_signature_false_suppresses_default_signature_and_verifies_one_draft(self):
        captured = []
        body_sentinel = "AA-NO-SIGNATURE-BODY-SENTINEL-81121"

        def fake_run(script, timeout=120):
            captured.append(script)
            if "reply foundMessage" in script:
                return _saved_reply_draft_output(
                    to="native reply recipients",
                    draft_id="81121",
                    quote_needle="On Today, Sender <sender@example.com> wrote:",
                )
            if 'set targetDraftIdText to "81121"' in script:
                return "FOUND|81121|not_requested|not_requested"
            return "ok"

        with (
            patch.object(compose_tools.server, "DEFAULT_MAIL_SIGNATURE", "TU", create=True),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body=f"{body_sentinel}\n\nReply body",
                include_signature=False,
            )

        self.assertIn("Draft ID: 81121", result)
        self.assertIn("Verification Status: found", result)
        self.assertIn("Verified Draft ID: 81121", result)
        self.assertIn("Signature Verification Status: not_requested", result)

        script = _main_reply_script(captured)
        self.assertIn("set replyMessage to reply foundMessage", script)
        self.assertIn("set message signature of replyMessage to missing value", script)
        self.assertNotIn('set message signature of replyMessage to signature "TU"', script)
        self.assertNotIn("with opening window", script)
        self.assertNotIn("close (window of replyMessage)", script)
        self.assertNotIn("close front window", script)
        self.assertEqual(script.count("save replyMessage"), 1)
        _assert_ordered(
            self,
            script,
            "set message signature of replyMessage to missing value",
            "set composedReplyContent to replyBodyText & return & return & quotedOriginalText",
            "set content of replyMessage to (composedReplyContent as rich text)",
            "save replyMessage",
            "set replyDraftId to id of replyMessage as string",
        )

        verifier_script = next(script for script in captured if "set signatureWasRequested" in script)
        self.assertIn('set targetDraftIdText to "81121"', verifier_script)
        self.assertIn(f'set replyBodyNeedle to "{body_sentinel}"', verifier_script)
        self.assertIn("set signatureWasRequested to false", verifier_script)
        self.assertIn("every message of draftsMailbox whose id is targetDraftId", verifier_script)

    def test_invalid_reply_signature_is_rejected_before_native_reply(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            if "availableSignatures" in script:
                return 'Error: Mail signature "Missing" not found. Available signatures: TU, Agentic Assets'
            if "count of outgoing messages" in script:
                return "0"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                signature_name="Missing",
            )

        self.assertIn('Mail signature "Missing" not found', result)
        self.assertFalse(
            any("reply foundMessage" in script for script in captured),
            captured,
        )

    def test_injects_sender_when_from_address_is_valid(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            if len(scripts) == 1:
                return "default@example.com\nsecondary@example.org"
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                from_address="secondary@example.org",
                send=False,
            )

        self.assertEqual(len(scripts), 2)
        script = _main_reply_script(scripts)
        self.assertIn('set sender of replyMessage to "secondary@example.org"', script)
        self.assertNotIn("if (count of emailAddrs) is 1 then", script)

    def test_rejects_invalid_from_address_without_running_main_script(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            return "default@example.com"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply body",
                from_address="unknown@example.com",
                send=False,
            )

        self.assertEqual(len(scripts), 1)
        self.assertTrue(result.startswith("Error: 'from_address'"))


class ForwardEmailSenderOverrideTests(unittest.TestCase):
    def test_forward_defaults_to_draft_mode(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Forward saved"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
            )

        self.assertEqual(len(captured), 1)
        script = captured[0]
        self.assertIn("SAVING FORWARD AS DRAFT", script)
        # Object-model forward: race-free `make new outgoing message`, single
        # `save forwardMessage`, NO GUI window, NO clipboard, NO System Events.
        self.assertIn("make new outgoing message", script)
        self.assertEqual(script.count("save forwardMessage"), 1)
        self.assertNotIn("close window 1 saving no", script)
        self.assertNotIn('keystroke "v"', script)
        self.assertNotIn("NSPasteboard", script)
        self.assertNotIn("forward foundMessage with opening window", script)
        self.assertNotIn("send forwardMessage", script)

    def test_forward_open_mode_saves_before_leaving_open_for_review(self):
        captured = []
        call_count = [0]

        def fake_run(script, timeout=120):
            captured.append(script)
            call_count[0] += 1
            # First call is the window-count probe (Fix #12); return 0 open windows.
            if "count of outgoing messages" in script:
                return "0"
            return "Forward opened in Mail for review."

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
                mode="open",
            )

        # captured[0] is the window-count probe; captured[1] is the forward script.
        forward_scripts = [s for s in captured if "OPENING FORWARD FOR REVIEW" in s]
        self.assertEqual(len(forward_scripts), 1)
        self.assertIn("save forwardMessage", forward_scripts[0])
        self.assertIn("review", result)

    def test_forward_draft_success_outputs_draft_id_and_verification(self):
        captured = []
        verify_calls = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return _saved_forward_draft_output(draft_id="84055")

        def fake_verify(**kwargs):
            verify_calls.append(kwargs)
            return json.dumps({"draft_id": kwargs["draft_id"], "found": True, "warnings": []})

        with (
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
            patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify),
        ):
            result = compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
                message="Please review\nMore context",
                include_signature=False,
            )

        self.assertIn("Draft ID: 84055", result)
        self.assertIn("Verification Status: found", result)
        self.assertIn("Verified Draft ID: 84055", result)
        self.assertEqual(len(verify_calls), 1)
        self.assertEqual(
            verify_calls[0],
            {
                "account": "Work",
                "draft_id": "84055",
                "expected_to": "recipient@example.com",
                "expected_subject": "Fwd: Test",
                "expected_body_contains": "Please review",
                "expected_signature": False,
                "timeout": None,
            },
        )
        script = captured[0]
        self.assertIn("set forwardDraftId to id of forwardMessage as string", script)
        self.assertIn('"Draft ID: " & forwardDraftId', script)

    def test_forward_draft_reports_verification_warnings(self):
        def fake_run(script, timeout=120):
            return _saved_forward_draft_output(draft_id="84055")

        def fake_verify(**kwargs):
            return json.dumps({"draft_id": kwargs["draft_id"], "found": True, "warnings": ["signature_unexpected"]})

        with (
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
            patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify),
        ):
            result = compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
                include_signature=False,
            )

        self.assertIn("Verification Status: found_with_warnings", result)
        self.assertIn("Verification Warnings: signature_unexpected", result)

    def test_default_emits_single_alias_fallback_for_forward_message(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Forwarded"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
            )

        self.assertEqual(len(captured), 1)
        script = captured[0]
        self.assertIn("if (count of emailAddrs) is 1 then", script)
        self.assertIn("set sender of forwardMessage to item 1 of emailAddrs", script)
        self.assertNotIn('set sender of forwardMessage to "', script)

    def test_injects_sender_when_from_address_is_valid(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            if len(scripts) == 1:
                return "default@example.com\nsecondary@example.org"
            return "✓ Forwarded"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
                from_address="secondary@example.org",
            )

        self.assertEqual(len(scripts), 2)
        main_script = scripts[1]
        self.assertIn(
            'set sender of forwardMessage to "secondary@example.org"',
            main_script,
        )
        self.assertNotIn("if (count of emailAddrs) is 1 then", main_script)

    def test_rejects_invalid_from_address_without_running_main_script(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            return "default@example.com"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
                from_address="unknown@example.com",
            )

        self.assertEqual(len(scripts), 1)
        self.assertTrue(result.startswith("Error: 'from_address'"))


class ManageDraftsCreateSenderOverrideTests(unittest.TestCase):
    def test_manage_drafts_accepts_exact_draft_id_parameter(self):
        params = inspect.signature(compose_tools.manage_drafts).parameters

        self.assertIn("draft_id", params)
        self.assertIsNone(params["draft_id"].default)

    def test_create_draft_blocks_reply_like_subject_without_confirmation(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Re: Complex Request",
                to="sender@example.com",
                body="Thread-like draft",
            )

        mock_run.assert_not_called()
        self.assertIn("standalone new message", result)
        self.assertIn("Use reply_to_email(message_id=...)", result)

    def test_create_draft_allows_reply_like_subject_when_confirmed(self):
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
                subject="Re: standalone project name",
                to="team@example.com",
                body="This is a new standalone draft.",
                standalone_confirmed=True,
            )

        self.assertEqual(len(captured), 1)
        self.assertIn("CREATING DRAFT", captured[0])
        self.assertIn("save newDraft", captured[0])
        self.assertIn("set draftId to id of newDraft as string", captured[0])
        self.assertIn('set outputText to outputText & "Draft ID: " & draftId', captured[0])
        self.assertIn("Draft created", result)

    def test_default_emits_single_alias_fallback_for_new_draft(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Draft created"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Draft",
                to="recipient@example.com",
                body="Body",
            )

        self.assertEqual(len(captured), 1)
        script = captured[0]
        self.assertIn("if (count of emailAddrs) is 1 then", script)
        self.assertIn("set sender of newDraft to item 1 of emailAddrs", script)
        self.assertIn("save newDraft", script)
        self.assertIn("set draftId to id of newDraft as string", script)
        self.assertNotIn('set sender of newDraft to "', script)

    def test_injects_sender_when_from_address_is_valid(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            if len(scripts) == 1:
                return "default@example.com\nsecondary@example.org"
            return "✓ Draft created"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Draft",
                to="recipient@example.com",
                body="Body",
                from_address="secondary@example.org",
            )

        self.assertEqual(len(scripts), 2)
        main_script = scripts[1]
        self.assertIn('set sender of newDraft to "secondary@example.org"', main_script)
        self.assertNotIn("if (count of emailAddrs) is 1 then", main_script)

    def test_rejects_invalid_from_address_without_running_main_script(self):
        scripts = []

        def fake_run(script, timeout=120):
            scripts.append(script)
            return "default@example.com"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Draft",
                to="recipient@example.com",
                body="Body",
                from_address="unknown@example.com",
            )

        self.assertEqual(len(scripts), 1)
        self.assertTrue(result.startswith("Error: 'from_address'"))

    def test_send_draft_prefers_exact_draft_id(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Draft sent successfully!"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.manage_drafts(
                account="Work",
                action="send",
                draft_subject="Duplicate Subject",
                draft_id="84053",
            )

        self.assertIn("Draft sent", result)
        script = captured[0]
        self.assertIn("every message of draftsMailbox whose id is 84053", script)
        self.assertIn('set outputText to outputText & "Draft ID: " & draftId', script)
        self.assertNotIn('contains "Duplicate Subject"', script)

    def test_send_draft_subject_returns_deprecation_before_read_only_send_guard(self):
        with (
            patch.object(compose_tools._server, "READ_ONLY", True),
            patch.object(compose_tools._server, "DRAFT_SAFE", False),
            patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run,
        ):
            result = compose_tools.manage_drafts(
                account="Work",
                action="send",
                draft_subject="Duplicate Subject",
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TARGET_SELECTOR_DEPRECATED")
        self.assertEqual(payload["remediation"]["exact_selector"], "draft_id")

    def test_send_draft_subject_returns_deprecation_before_draft_safe_send_guard(self):
        with (
            patch.object(compose_tools._server, "READ_ONLY", False),
            patch.object(compose_tools._server, "DRAFT_SAFE", True),
            patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run,
        ):
            result = compose_tools.manage_drafts(
                account="Work",
                action="send",
                draft_subject="Duplicate Subject",
            )

        mock_run.assert_not_called()
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TARGET_SELECTOR_DEPRECATED")
        self.assertEqual(payload["remediation"]["exact_selector"], "draft_id")

    def test_open_and_delete_drafts_can_target_exact_draft_id(self):
        for action, expected_action in [("open", "open foundDraft"), ("delete", "delete foundDraft")]:
            with self.subTest(action=action):
                captured = []

                def fake_run(script, timeout=120, captured=captured):
                    captured.append(script)
                    return "ok"

                with patch(
                    "apple_mail_mcp.tools.compose.run_applescript",
                    side_effect=fake_run,
                ):
                    compose_tools.manage_drafts(
                        account="Work",
                        action=action,
                        draft_id="84054",
                    )

                script = captured[0]
                self.assertIn("every message of draftsMailbox whose id is 84054", script)
                self.assertIn(expected_action, script)
                self.assertIn('set outputText to outputText & "Draft ID: " & draftId', script)
                self.assertNotIn("contains", script)

    def test_invalid_draft_id_is_rejected_before_applescript(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.manage_drafts(
                account="Work",
                action="delete",
                draft_id="not-a-number",
            )

        mock_run.assert_not_called()
        self.assertIn("'draft_id' must be a numeric", result)


class ManageDraftsListTests(unittest.TestCase):
    def test_subject_filter_builder_escapes_input_and_keeps_in_loop_filter(self):
        script = compose_tools._build_manage_drafts_subject_filter_script('Q3 "Report"', indent=4)

        self.assertIn("ignoring case", script)
        self.assertIn('does not contain "Q3 \\"Report\\""', script)
        self.assertIn("set skipThisDraft to true", script)
        self.assertNotIn("whose", script)

    def test_subject_filter_builder_omits_filter_when_unset(self):
        self.assertEqual(compose_tools._build_manage_drafts_subject_filter_script(None, indent=4), "")

    def test_list_builder_uses_clamped_limit_and_no_unbounded_enumeration(self):
        script = compose_tools._build_manage_drafts_list_script(
            safe_account="Work",
            list_limit=10,
            hide_empty=True,
            subject_contains="Q3",
        )

        self.assertIn("set hideEmpty to true", script)
        self.assertIn("if headEnd > 10 then set headEnd to 10", script)
        self.assertIn("if totalDrafts is 0 then", script)
        self.assertIn("messages 1 thru headEnd of draftsMailbox", script)
        self.assertIn("if shownCount >= 10 then exit repeat", script)
        self.assertIn('does not contain "Q3"', script)
        self.assertNotIn("every message of draftsMailbox", script)
        self.assertNotIn("current date", script)

    def test_find_builder_uses_bounded_header_scan(self):
        script = compose_tools._build_manage_drafts_find_script(
            safe_account="Work",
            list_limit=12,
            in_reply_to="<source@example.com>",
            subject_contains="Q3",
        )

        self.assertIn("if headEnd > 12 then set headEnd to 12", script)
        self.assertIn("if totalDrafts is 0 then", script)
        self.assertIn("messages 1 thru headEnd of draftsMailbox", script)
        self.assertIn("all headers of aDraft", script)
        self.assertIn('starts with "In-Reply-To:"', script)
        self.assertIn('starts with "References:"', script)
        self.assertIn('contains "source@example.com"', script)
        self.assertNotIn("every message of draftsMailbox", script)

    def test_verify_draft_returns_snapshot_json_with_expectation_warnings(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return (
                "FOUND|||Re: Test|||sender@example.com|||cc@example.com|||"
                "|||"
                "Hi there On Today, Sender wrote: Original|||<source@example.com>|||"
                "<source@example.com> <older@example.com>|||true|||false|||support.pdf::2048;;"
            )

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.verify_draft(
                account="Work",
                draft_id="84053",
                expected_to="sender@example.com",
                expected_cc="cc@example.com",
                expected_subject="Re: Test",
                expected_body_contains="Hi there",
                expected_attachments="support.pdf,missing.docx",
                expected_signature=True,
                require_quoted_original=True,
            )

        payload = json.loads(result)
        self.assertTrue(payload["found"])
        self.assertEqual(payload["draft_id"], "84053")
        self.assertEqual(payload["attachments"]["status"], "missing")
        self.assertEqual(payload["attachments"]["found"][0]["filename"], "support.pdf")
        self.assertEqual(payload["threading"]["in_reply_to"], "<source@example.com>")
        self.assertIn("expected_attachments_missing", payload["warnings"])
        self.assertIn("signature_missing", payload["warnings"])
        script = captured[0]
        self.assertIn('mailbox "Drafts" of targetAccount', script)
        self.assertIn("every message of draftsMailbox whose id is 84053", script)
        self.assertIn("all headers of aDraft", script)
        self.assertIn("mail attachments of aDraft", script)

    def test_verify_draft_rejects_non_numeric_draft_id(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.verify_draft(account="Work", draft_id="abc")

        mock_run.assert_not_called()
        self.assertIn("'draft_id' must be a numeric", result)

    def test_verify_draft_recipient_expectation_requires_exact_address(self):
        def fake_run(script, timeout=120):
            return "FOUND|||Subject|||joann@example.com|||||||||Body|||||||||false|||false|||"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.verify_draft(
                account="Work",
                draft_id="84053",
                expected_to="ann@example.com",
            )

        payload = json.loads(result)
        self.assertFalse(payload["checks"]["to_matches_expected"])
        self.assertIn("to_mismatch", payload["warnings"])

    def test_verify_drafts_preserves_order_and_reports_missing_invalid_ids(self):
        calls: list[dict[str, object]] = []

        def fake_verify(**kwargs):
            calls.append(kwargs)
            draft_id = kwargs["draft_id"]
            found = draft_id != "303"
            return json.dumps(
                {
                    "draft_id": draft_id,
                    "found": found,
                    "warnings": [] if found else ["draft_not_found"],
                    "checks": {"body_contains_expected": kwargs["expected_body_contains"] == "hello"},
                }
            )

        with patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify):
            result = compose_tools.verify_drafts(
                account="Work",
                draft_ids=["101", "bad", "202", "101", "303"],
                expected_body_contains="hello",
                expected_signature=True,
            )

        payload = json.loads(result)
        self.assertEqual(payload["draft_ids"], ["101", "202", "303"])
        self.assertEqual(payload["invalid_ids"], ["bad"])
        self.assertEqual(payload["missing_ids"], ["303"])
        self.assertEqual(payload["found"], 2)
        self.assertEqual(payload["chunk_size"], 50)
        self.assertEqual([item["draft_id"] for item in payload["items"]], ["101", "202", "303"])
        self.assertEqual([call["draft_id"] for call in calls], ["101", "202", "303"])
        self.assertTrue(all(call["expected_body_contains"] == "hello" for call in calls))
        self.assertTrue(all(call["expected_signature"] is True for call in calls))

    def test_verify_drafts_rejects_non_numeric_draft_ids_without_calling_verifier(self):
        with patch("apple_mail_mcp.tools.compose.verify_draft") as mock_verify:
            result = compose_tools.verify_drafts(account="Work", draft_ids=["abc", ""])

        mock_verify.assert_not_called()
        self.assertIn("'draft_ids' must contain one or more numeric", result)

    def test_verify_drafts_handles_120_ids(self):
        calls: list[str] = []

        def fake_verify(**kwargs):
            draft_id = kwargs["draft_id"]
            calls.append(draft_id)
            return json.dumps({"draft_id": draft_id, "found": False, "warnings": ["draft_not_found"]})

        ids = [str(i) for i in range(1, 121)]
        with patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify):
            result = compose_tools.verify_drafts(account="Work", draft_ids=ids)

        payload = json.loads(result)
        self.assertEqual(payload["draft_ids"], ids)
        self.assertEqual(payload["found"], 0)
        self.assertEqual(payload["missing_ids"], ids)
        self.assertEqual(payload["chunk_size"], 50)
        self.assertEqual(len(calls), 120)
        self.assertEqual(calls[0], "1")
        self.assertEqual(calls[50], "51")
        self.assertEqual(calls[100], "101")

    def test_list_uses_newest_first_slice(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 0 draft(s)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(account="Work", action="list")

        self.assertEqual(len(captured), 1)
        script = captured[0]
        # Bounded newest-first slice: real Mail Drafts accounts show newly
        # created native replies near the front. Never scan the whole folder.
        self.assertIn("set totalDrafts to count of messages of draftsMailbox", script)
        self.assertIn("if headEnd > 75 then set headEnd to 75", script)
        self.assertIn("messages 1 thru headEnd of draftsMailbox", script)
        self.assertIn("if shownCount >= 75 then exit repeat", script)
        self.assertNotIn("messages startIdx thru totalDrafts of draftsMailbox", script)
        self.assertNotIn("every message of draftsMailbox", script)

    def test_list_limit_caps_head_window_and_result_count(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 0 draft(s)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(account="Work", action="list", limit=10)

        script = captured[0]
        self.assertIn("if headEnd > 10 then set headEnd to 10", script)
        self.assertIn("if shownCount >= 10 then exit repeat", script)

    def test_list_subject_contains_adds_case_insensitive_filter_only(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 0 draft(s)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(
                account="Work",
                action="list",
                subject_contains="Q3 Report",
            )

        self.assertEqual(len(captured), 1)
        script = captured[0]
        # In-loop, case-insensitive subject filter.
        self.assertIn("ignoring case", script)
        self.assertIn('does not contain "Q3 Report"', script)
        # No date filter is ever added (would drop null-date new drafts).
        self.assertNotIn("recentCutoffDate", script)
        self.assertNotIn("current date", script)

    def test_list_subject_contains_filters_before_body_and_recipient_reads(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 0 draft(s)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(account="Work", action="list", subject_contains="Q3 Report")

        script = captured[0]
        _assert_ordered(
            self,
            script,
            'does not contain "Q3 Report"',
            'set draftBody to ""',
            'set draftTo to ""',
        )

    def test_list_without_subject_contains_omits_filter(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 0 draft(s)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.manage_drafts(account="Work", action="list")

        self.assertEqual(len(captured), 1)
        self.assertNotIn("ignoring case", captured[0])

    def test_find_by_in_reply_to_uses_bounded_header_scan(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "Found 1 matching draft(s)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.manage_drafts(
                account="Work",
                action="find",
                in_reply_to="<source@example.com>",
                subject_contains="Q3",
                limit=12,
            )

        self.assertIn("Found 1", result)
        script = captured[0]
        self.assertIn("if headEnd > 12 then set headEnd to 12", script)
        self.assertIn("messages 1 thru headEnd of draftsMailbox", script)
        self.assertNotIn("every message of draftsMailbox", script)
        self.assertIn("all headers of aDraft", script)
        self.assertIn('starts with "In-Reply-To:"', script)
        self.assertIn('starts with "References:"', script)
        self.assertIn('contains "source@example.com"', script)


class ComposeRunApplescriptMigrationTests(unittest.TestCase):
    def test_reply_to_email_forwards_timeout_to_run_applescript(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["timeout"] = timeout
            return "ok"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.reply_to_email(
                account="Work",
                message_id="888",
                reply_body="Thanks",
                timeout=240,
            )

        self.assertEqual(captured["timeout"], 240)

    def test_send_html_email_uses_run_applescript(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            captured["timeout"] = timeout
            return "Email saved as draft (HTML)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools.compose_email(
                account="Work",
                to="team@example.com",
                subject="Hi",
                body="Plain",
                body_html="<p>Hi</p>",
                mode="draft",
                timeout=90,
            )

        self.assertIn("use framework", captured["script"])
        self.assertEqual(captured["timeout"], 90)
        # FIX #1(c): single persist — save newMsg then close the CORRECT window
        # (window of newMsg, not positional window 1) and no redundant keystroke.
        self.assertIn("save newMsg", captured["script"])
        self.assertIn("close (window of newMsg) saving no", captured["script"])
        self.assertNotIn("close window 1 saving no", captured["script"])
        self.assertIn("set index of (window of newMsg) to 1", captured["script"])
        self.assertNotIn('keystroke "s" using command down', captured["script"])
        self.assertNotIn("close window 1 saving yes", captured["script"])
        self.assertIn("Email saved as draft (HTML)", result)

    def test_send_html_email_open_mode_saves_before_leaving_open(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "Email opened in Mail for review (HTML). Edit and send when ready."

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools._send_html_email(
                account="Work",
                to="team@example.com",
                subject="Hi",
                body_plain="Plain",
                body_html="<p>Hi</p>",
                mode="open",
            )

        self.assertIn("save newMsg", captured["script"])
        self.assertNotIn('keystroke "s" using command down', captured["script"])
        self.assertNotIn("close window 1 saving yes", captured["script"])
        self.assertIn("review", result)

    def test_send_html_email_send_mode_uses_mail_object_model_send(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "Email sent successfully (HTML)"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            result = compose_tools._send_html_email(
                account="Work",
                to="team@example.com",
                subject="Hi",
                body_plain="Plain",
                body_html="<p>Hi</p>",
                mode="send",
            )

        self.assertIn("send newMsg", captured["script"])
        self.assertNotIn('keystroke "d" using {command down, shift down}', captured["script"])
        self.assertIn("Email sent successfully (HTML)", result)

    def test_forward_with_message_uses_run_applescript(self):
        captured = []

        def fake_run(script, timeout=120):
            captured.append(script)
            return "✓ Forward saved"

        with patch(
            "apple_mail_mcp.tools.compose.run_applescript",
            side_effect=fake_run,
        ):
            compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="recipient@example.com",
                message="Please review",
            )

        self.assertEqual(len(captured), 1)
        script = captured[0]
        # Forward with a lead message now uses the race-free object model: the
        # message is read from a temp file and prepended as plain text — no
        # NSPasteboard/use framework clipboard injection.
        self.assertIn("make new outgoing message", script)
        self.assertIn("set fwdLeadText to", script)
        self.assertNotIn("use framework", script)
        self.assertNotIn("NSPasteboard", script)

    def test_split_addresses_dedup_filters_empty_segments(self):
        self.assertEqual(
            compose_tools._split_addresses("a@x.com, , b@y.com"),
            ["a@x.com", "b@y.com"],
        )
        self.assertEqual(compose_tools._split_addresses(""), [])
        self.assertEqual(compose_tools._split_addresses(None), [])

    def test_build_recipient_loops_message_var_and_addresses(self):
        cc_script, bcc_script, cc_addrs, bcc_addrs = compose_tools._build_recipient_loops(
            "a@x.com, b@y.com",
            "c@z.com",
            message_var="replyMessage",
        )
        self.assertEqual(cc_addrs, ["a@x.com", "b@y.com"])
        self.assertEqual(bcc_addrs, ["c@z.com"])
        self.assertIn(
            "make new cc recipient at end of cc recipients of replyMessage",
            cc_script,
        )
        self.assertIn('address:"a@x.com"', cc_script)
        self.assertIn(
            "make new bcc recipient at end of bcc recipients of replyMessage",
            bcc_script,
        )
        self.assertIn('address:"c@z.com"', bcc_script)

    def test_build_recipient_loops_compact_empty(self):
        cc_script, bcc_script, cc_addrs, bcc_addrs = compose_tools._build_recipient_loops(None, "", compact=True)
        self.assertEqual(cc_addrs, [])
        self.assertEqual(bcc_addrs, [])
        self.assertEqual(cc_script, "")
        self.assertEqual(bcc_script, "")
        cc_script, _, _, _ = compose_tools._build_recipient_loops("one@example.com", None, compact=True)
        self.assertEqual(
            cc_script,
            'make new cc recipient at end of cc recipients with properties {address:"one@example.com"}\n',
        )


if __name__ == "__main__":
    unittest.main()
