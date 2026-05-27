"""Tests for Tier 3 mechanical hardening fixes (fixes #8, #9, #10, #11, #12).

Fix #8  — search_emails body-read cap: when body_text is set without an explicit
           date_from, scan_cap is capped at 100 messages.
Fix #9  — search.py inner-AS timeout: _build_search_script templates the AppleScript
           ``with timeout of N seconds`` from the same timeout the outer run_applescript
           wrapper will use (inner = max(30, timeout - 10)).
Fix #10 — synchronize_account all_accounts outer timeout scales with account count
           (PER_ACCOUNT_TIMEOUT_S * account_count + 5).
Fix #11 — save_email_attachment size limit: probe attachment size and refuse with
           ATTACHMENT_TOO_LARGE when it exceeds max_size_bytes; also refuse when
           disk space is insufficient.
Fix #12 — compose_email / reply_to_email / forward_email mode='open' refuses to open
           a new window when the open-compose-window count is already at
           MAX_OPEN_COMPOSE_WINDOWS (5) with code TOO_MANY_OPEN_DRAFTS.
"""

import asyncio
import json
import unittest
from unittest.mock import patch, MagicMock

from apple_mail_mcp.tools import compose as compose_tools
from apple_mail_mcp.tools import manage as manage_tools
from apple_mail_mcp.tools import search as search_tools


# ---------------------------------------------------------------------------
# Fix #8 — body-read cap in _build_search_script
# ---------------------------------------------------------------------------


class BodySearchCapTests(unittest.TestCase):
    """_build_search_script caps scan_cap at 100 when body_text is set and no
    explicit date_from was supplied."""

    def _build(self, **kwargs):
        """Call _build_search_script with sensible defaults, return the script string."""
        defaults = dict(
            account="Work",
            mailbox="INBOX",
            subject_terms=None,
            sender=None,
            has_attachments=None,
            read_status="all",
            date_from=None,
            date_to=None,
            include_content=False,
            content_length=300,
            offset=0,
            limit=20,
            body_text=None,
            recent_days=2.0,
        )
        defaults.update(kwargs)
        return search_tools._build_search_script(**defaults)[0]

    def test_body_search_without_date_from_explicit_caps_at_100(self):
        """Without explicit date_from, body_text triggers auto-cap at 100."""
        script = self._build(body_text="needle", date_from_explicit=False)
        self.assertIn("set scanUpperBound to 100", script)

    def test_body_search_with_date_from_explicit_no_cap(self):
        """When date_from_explicit=True the auto-cap is skipped; scan_cap stays at the
        window-based value (e.g. 300 for recent_days=2.0)."""
        script = self._build(
            body_text="needle",
            date_from="2026-05-20",
            date_from_explicit=True,
        )
        # scan_cap should be >= 300 (window-based), not capped at 100
        self.assertNotIn("set scanUpperBound to 100", script)

    def test_no_body_search_not_capped(self):
        """Without body_text, no auto-cap is applied (scan_cap stays window-based)."""
        script = self._build(body_text=None, sender="boss@example.com")
        # scan_cap for 2-day window with sender filter = 300
        self.assertIn("set scanUpperBound to 300", script)
        self.assertNotIn("set scanUpperBound to 100", script)

    def test_body_search_capped_flag_returned_true_when_cap_fires(self):
        """_build_search_script returns body_search_capped=True when the cap fires."""
        result = search_tools._build_search_script(
            account="Work",
            mailbox="INBOX",
            subject_terms=None,
            sender=None,
            has_attachments=None,
            read_status="all",
            date_from=None,
            date_to=None,
            include_content=False,
            content_length=300,
            offset=0,
            limit=20,
            body_text="needle",
            recent_days=2.0,
            date_from_explicit=False,
        )
        # result is (script, body_search_capped[, ...])
        self.assertTrue(result[1], "body_search_capped should be True when cap fires")

    def test_body_search_capped_flag_false_with_explicit_date_from(self):
        """_build_search_script returns body_search_capped=False when date_from_explicit."""
        result = search_tools._build_search_script(
            account="Work",
            mailbox="INBOX",
            subject_terms=None,
            sender=None,
            has_attachments=None,
            read_status="all",
            date_from="2026-05-20",
            date_to=None,
            include_content=False,
            content_length=300,
            offset=0,
            limit=20,
            body_text="needle",
            recent_days=2.0,
            date_from_explicit=True,
        )
        self.assertFalse(result[1], "body_search_capped should be False when date_from_explicit=True")

    def test_search_emails_surfaces_body_cap_warning_in_json(self):
        """search_emails JSON output includes body_search_capped and a warning when cap fires."""

        def fake_run(script, timeout=180):
            return ""  # no matches

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = json.loads(
                asyncio.run(
                    search_tools.search_emails(
                        account="Work",
                        body_text="needle",
                        output_format="json",
                    )
                )
            )

        self.assertTrue(response.get("body_search_capped"))
        self.assertIn("body_search_cap_warning", response)

    def test_search_emails_surfaces_body_cap_warning_in_text(self):
        """search_emails text output contains a WARNING when body cap fires."""

        def fake_run(script, timeout=180):
            return ""  # no matches

        with patch("apple_mail_mcp.tools.search.run_applescript", side_effect=fake_run):
            response = asyncio.run(
                search_tools.search_emails(
                    account="Work",
                    body_text="needle",
                    output_format="text",
                )
            )

        self.assertIn("WARNING", response)
        self.assertIn("body_text", response)


# ---------------------------------------------------------------------------
# Fix #9 — inner-AS timeout templated from outer timeout
# ---------------------------------------------------------------------------


class InnerTimeoutTemplatingTests(unittest.TestCase):
    """_build_search_script templates the inner AS timeout from the outer value."""

    def _script(self, timeout):
        return search_tools._build_search_script(
            account="Work",
            mailbox="INBOX",
            subject_terms=None,
            sender="person@example.com",
            has_attachments=None,
            read_status="all",
            date_from=None,
            date_to=None,
            include_content=False,
            content_length=300,
            offset=0,
            limit=20,
            body_text=None,
            recent_days=2.0,
            timeout=timeout,
        )[0]

    def test_timeout_60_produces_inner_timeout_50(self):
        """timeout=60 → inner_timeout = max(30, 60-10) = 50."""
        script = self._script(timeout=60)
        self.assertIn("with timeout of 50 seconds", script)

    def test_timeout_180_produces_inner_timeout_170(self):
        """timeout=180 (default) → inner_timeout = max(30, 180-10) = 170."""
        script = self._script(timeout=180)
        self.assertIn("with timeout of 170 seconds", script)

    def test_timeout_none_produces_inner_timeout_170(self):
        """timeout=None → treated as 180 → inner_timeout = 170."""
        script = self._script(timeout=None)
        self.assertIn("with timeout of 170 seconds", script)

    def test_very_short_timeout_floored_at_30(self):
        """timeout=35 → max(30, 35-10) = 30 (floored)."""
        script = self._script(timeout=35)
        self.assertIn("with timeout of 30 seconds", script)

    def test_literal_180_not_in_script_when_timeout_given(self):
        """The legacy hardcoded 180 must not appear when a different timeout is passed."""
        script = self._script(timeout=60)
        # Should NOT have the literal 180 hardcoded inner timeout
        self.assertNotIn("with timeout of 180 seconds", script)


# ---------------------------------------------------------------------------
# Fix #10 — synchronize_account outer timeout scales with account count
# ---------------------------------------------------------------------------


class SynchronizeAccountOuterTimeoutTests(unittest.TestCase):
    """When all_accounts=True, outer run_applescript timeout scales with account count."""

    def test_single_account_uses_per_account_plus_5(self):
        """Single-account sync uses PER_ACCOUNT_TIMEOUT_S + 5 = 13."""
        captured_timeout = []

        def fake_run(script, timeout=None):
            captured_timeout.append(timeout)
            return "Synchronized: Work"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            manage_tools.synchronize_account(account="Work", confirm_sync=True)

        self.assertEqual(captured_timeout[-1], 13)

    def test_all_accounts_timeout_scales_with_account_count(self):
        """all_accounts=True with 4 accounts → outer_timeout = 8*4 + 5 = 37."""
        call_count = [0]
        captured_timeout = []

        def fake_run(script, timeout=None):
            call_count[0] += 1
            captured_timeout.append(timeout)
            if "acctNames" in script and "repeat with a in accounts" in script:
                # The main sync script
                return "Synchronized all accounts: A, B, C, D"
            # list_mail_account_names probe
            return "AccountA\nAccountB\nAccountC\nAccountD"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.synchronize_account(
                all_accounts=True, confirm_sync=True
            )

        # The outer timeout for the main sync script should be 8*4 + 5 = 37
        main_sync_timeout = captured_timeout[-1]
        self.assertEqual(main_sync_timeout, 37, f"Expected 37 for 4 accounts, got {main_sync_timeout}")

    def test_all_accounts_timeout_minimum_when_probe_fails(self):
        """When account probe times out, falls back to at least 8*1 + 5 = 13."""
        from apple_mail_mcp.core import AppleScriptTimeout

        call_count = [0]
        captured_timeout = []

        def fake_run(script, timeout=None):
            call_count[0] += 1
            captured_timeout.append(timeout)
            if "acctNames" in script and "repeat with a in accounts" in script:
                return "Synchronized all accounts: Work"
            # Probe fails
            raise AppleScriptTimeout("probe timed out")

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            manage_tools.synchronize_account(all_accounts=True, confirm_sync=True)

        # Falls back to 1 account minimum → 8*1 + 5 = 13
        main_sync_timeout = captured_timeout[-1]
        self.assertGreaterEqual(main_sync_timeout, 13)


# ---------------------------------------------------------------------------
# Fix #11 — save_email_attachment size limit
# ---------------------------------------------------------------------------


class SaveEmailAttachmentSizeLimitTests(unittest.TestCase):
    """save_email_attachment refuses oversized attachments and low disk space."""

    def _base_kwargs(self):
        import os
        home = os.path.expanduser("~")
        return dict(
            account="Work",
            message_ids=["12345"],
            attachment_name="report.pdf",
            save_path=os.path.join(home, "Downloads", "report.pdf"),
            max_size_bytes=10 * 1024 * 1024,  # 10 MB cap
        )

    def test_attachment_too_large_returns_tool_error(self):
        """Returns ATTACHMENT_TOO_LARGE when probe returns size > max_size_bytes."""
        call_count = [0]

        def fake_run(script, timeout=None):
            call_count[0] += 1
            if "file size of anAttachment" in script:
                # Return 20 MB (larger than 10 MB cap)
                return str(20 * 1024 * 1024)
            return "saved"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            result = manage_tools.save_email_attachment(**self._base_kwargs())

        payload = json.loads(result)
        self.assertEqual(payload["code"], "ATTACHMENT_TOO_LARGE")
        self.assertIn("actual_size_bytes", payload.get("remediation", {}))

    def test_attachment_within_limit_proceeds_to_save(self):
        """When attachment size is within cap, the save proceeds normally."""
        call_count = [0]

        def fake_run(script, timeout=None):
            call_count[0] += 1
            if "file size of anAttachment" in script:
                return str(5 * 1024 * 1024)  # 5 MB — under the 10 MB cap
            return "✓ Attachment saved successfully!"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            with patch("os.makedirs"):
                with patch("shutil.disk_usage") as mock_disk:
                    mock_disk.return_value = MagicMock(free=500 * 1024 * 1024)
                    result = manage_tools.save_email_attachment(**self._base_kwargs())

        self.assertNotIn("ATTACHMENT_TOO_LARGE", result)
        self.assertIn("saved", result)

    def test_probe_failure_fails_open(self):
        """When the probe fails (returns -1), the save is not blocked."""
        def fake_run(script, timeout=None):
            if "file size of anAttachment" in script:
                return "-1"  # probe failed
            return "✓ Attachment saved successfully!"

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            with patch("os.makedirs"):
                with patch("shutil.disk_usage") as mock_disk:
                    mock_disk.return_value = MagicMock(free=500 * 1024 * 1024)
                    result = manage_tools.save_email_attachment(**self._base_kwargs())

        # Should NOT return a ToolError — probe failure is fail-open
        self.assertNotIn("ATTACHMENT_TOO_LARGE", result)

    def test_insufficient_disk_space_returns_tool_error(self):
        """Returns ATTACHMENT_TOO_LARGE when disk free space is below attachment + 100 MB."""
        def fake_run(script, timeout=None):
            if "file size of anAttachment" in script:
                return str(50 * 1024 * 1024)  # 50 MB attachment (under 10MB cap... wait)
            return "saved"

        # Increase cap to 100 MB so size check passes but disk check fails
        kwargs = self._base_kwargs()
        kwargs["max_size_bytes"] = 200 * 1024 * 1024  # 200 MB cap

        with patch("apple_mail_mcp.tools.manage.run_applescript", side_effect=fake_run):
            with patch("os.makedirs"):
                with patch("shutil.disk_usage") as mock_disk:
                    # Only 10 MB free — not enough for 50 MB attachment + 100 MB buffer
                    mock_disk.return_value = MagicMock(free=10 * 1024 * 1024)
                    result = manage_tools.save_email_attachment(**kwargs)

        payload = json.loads(result)
        self.assertEqual(payload["code"], "ATTACHMENT_TOO_LARGE")
        self.assertIn("free_bytes", payload.get("remediation", {}))

    def test_default_max_size_bytes_is_100mb(self):
        """The default max_size_bytes is 100 MB."""
        import inspect
        sig = inspect.signature(manage_tools.save_email_attachment)
        default = sig.parameters["max_size_bytes"].default
        self.assertEqual(default, 100 * 1024 * 1024)


# ---------------------------------------------------------------------------
# Fix #12 — Compose mode="open" window-count guard
# ---------------------------------------------------------------------------


class ComposeOpenWindowCapTests(unittest.TestCase):
    """compose_email / reply_to_email / forward_email refuse mode='open' at cap."""

    CAP = compose_tools.MAX_OPEN_COMPOSE_WINDOWS

    def _fake_run_at_cap(self, main_return="ok"):
        """Returns a fake_run that simulates CAP open windows on the probe."""
        def fake_run(script, timeout=None):
            if "count of outgoing messages" in script:
                return str(self.CAP)  # already at cap
            return main_return
        return fake_run

    def _fake_run_below_cap(self, main_return="ok"):
        """Returns a fake_run that simulates CAP-1 open windows on the probe."""
        def fake_run(script, timeout=None):
            if "count of outgoing messages" in script:
                return str(self.CAP - 1)  # below cap
            return main_return
        return fake_run

    # compose_email
    def test_compose_open_refused_at_cap(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript",
                   side_effect=self._fake_run_at_cap()):
            result = compose_tools.compose_email(
                account="Work",
                to="x@example.com",
                subject="Test",
                body="Body",
                mode="open",
            )
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TOO_MANY_OPEN_DRAFTS")
        self.assertEqual(payload["remediation"]["open_window_count"], self.CAP)

    def test_compose_open_allowed_below_cap(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript",
                   side_effect=self._fake_run_below_cap("✓ Email opened")):
            result = compose_tools.compose_email(
                account="Work",
                to="x@example.com",
                subject="Test",
                body="Body",
                mode="open",
            )
        self.assertNotIn("TOO_MANY_OPEN_DRAFTS", result)

    def test_compose_draft_mode_not_affected(self):
        """mode='draft' skips the window-count probe entirely."""
        called_probe = [False]

        def fake_run(script, timeout=None):
            if "count of outgoing messages" in script:
                called_probe[0] = True
            return "✓ Email saved as draft!"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            result = compose_tools.compose_email(
                account="Work",
                to="x@example.com",
                subject="Test",
                body="Body",
                mode="draft",
            )
        self.assertFalse(called_probe[0], "Window probe should not run for mode='draft'")

    # reply_to_email
    def test_reply_open_refused_at_cap(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript",
                   side_effect=self._fake_run_at_cap()):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply",
                mode="open",
            )
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TOO_MANY_OPEN_DRAFTS")

    def test_reply_open_allowed_below_cap(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript",
                   side_effect=self._fake_run_below_cap("Reply opened in Mail.")):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply",
                mode="open",
            )
        self.assertNotIn("TOO_MANY_OPEN_DRAFTS", result)

    # forward_email
    def test_forward_open_refused_at_cap(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript",
                   side_effect=self._fake_run_at_cap()):
            result = compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="x@example.com",
                mode="open",
            )
        payload = json.loads(result)
        self.assertEqual(payload["code"], "TOO_MANY_OPEN_DRAFTS")

    def test_forward_open_allowed_below_cap(self):
        with patch("apple_mail_mcp.tools.compose.run_applescript",
                   side_effect=self._fake_run_below_cap("Forward opened.")):
            result = compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="x@example.com",
                mode="open",
            )
        self.assertNotIn("TOO_MANY_OPEN_DRAFTS", result)

    def test_probe_failure_fails_open(self):
        """When the probe errors, mode='open' is NOT blocked (fail-open)."""
        def fake_run(script, timeout=None):
            if "count of outgoing messages" in script:
                raise Exception("Mail not running")
            return "✓ Email opened"

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
            result = compose_tools.compose_email(
                account="Work",
                to="x@example.com",
                subject="Test",
                body="Body",
                mode="open",
            )
        # Should NOT return TOO_MANY_OPEN_DRAFTS on probe failure
        self.assertNotIn("TOO_MANY_OPEN_DRAFTS", result)

    def test_max_open_compose_windows_constant_is_5(self):
        self.assertEqual(compose_tools.MAX_OPEN_COMPOSE_WINDOWS, 5)

    def test_too_many_drafts_remediation_points_to_draft_mode(self):
        """The remediation should explicitly mention mode='draft'."""
        with patch("apple_mail_mcp.tools.compose.run_applescript",
                   side_effect=self._fake_run_at_cap()):
            result = compose_tools.compose_email(
                account="Work",
                to="x@example.com",
                subject="Test",
                body="Body",
                mode="open",
            )
        payload = json.loads(result)
        remediation = payload.get("remediation", {})
        preferred = remediation.get("preferred", "")
        self.assertIn("draft", preferred.lower())


if __name__ == "__main__":
    unittest.main()
