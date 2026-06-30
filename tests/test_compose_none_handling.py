"""Regression + property tests for None-handling bugs in compose.py.

Covers six confirmed mypy/runtime bugs:
  1. Bug 1 (line 566): account.strip() on str | None after _resolve_account
  2. Bug 2 (line 647): "Account: " + account where account is str | None
  3. Bug 3 (line 950): return lookup_error typed as object not str (reply_to_email)
  4. Bug 4 (line 969): escape_applescript(account) where account is str | None
  5. Bug 5 (lines 1351, 1336): escape_applescript(account) / _send_html_email(account=account)
     in compose_email
  6. Bug 6 (lines 1529, 1554): same pattern in forward_email
  7. Bug 7 (line 1792): escape_applescript(account) in manage_drafts

All tests mock subprocess.run so they work on Linux CI with no osascript.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from apple_mail_mcp import server as _server
from apple_mail_mcp.tools import compose as compose_tools
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proc(returncode=0, stdout=b"saved", stderr=b""):
    """Build a mock subprocess.CompletedProcess."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class _ScriptCapture:
    """Record every AppleScript passed to run_applescript."""

    def __init__(self, return_value="ok"):
        self.scripts: list[str] = []
        self.timeouts: list[int] = []
        self._rv = return_value

    def __call__(self, script, timeout=120):
        self.scripts.append(script)
        self.timeouts.append(timeout)
        if isinstance(self._rv, list):
            return self._rv.pop(0) if self._rv else ""
        return self._rv

    @property
    def last_script(self) -> str:
        return self.scripts[-1] if self.scripts else ""

    @property
    def all_scripts(self) -> str:
        return "\n".join(self.scripts)


# ---------------------------------------------------------------------------
# Bug 1 + 2: create_rich_email_draft — account.strip() and "Account: " + account
#
# _resolve_account returns (None, error_str) when account cannot be resolved.
# After `if account_error: return account_error`, account is guaranteed non-None
# at runtime — but mypy sees str | None from the return annotation.
# The bug manifests when DEFAULT_MAIL_ACCOUNT is unset and no account is passed.
# ---------------------------------------------------------------------------


class TestCreateRichEmailDraftNoneHandling(unittest.TestCase):
    """Bug 1 & 2: account.strip() and string concat on Optional account."""

    def _patch_all(self, cap):
        """Context manager stack for create_rich_email_draft."""
        return (
            patch.object(_server, "DEFAULT_MAIL_ACCOUNT", ""),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap),
            patch("subprocess.run", return_value=_make_proc()),
        )

    def test_no_account_no_default_returns_structured_error_not_attribute_error(self):
        """BUG 1+2 regression: account=None with no DEFAULT should return error string, not raise."""
        with (
            patch.object(_server, "DEFAULT_MAIL_ACCOUNT", ""),
        ):
            result = compose_tools.create_rich_email_draft(account=None, subject="Hello")

        # Must be a string, not raise AttributeError
        self.assertIsInstance(result, str)
        # Must contain an error indicator
        self.assertIn("Error", result)

    def test_valid_account_does_not_raise_on_strip(self):
        """Bug 1 non-regression: valid account path should not raise AttributeError."""
        cap = _ScriptCapture(return_value=["", ""])  # alias lookup returns ""
        with (
            patch.object(_server, "DEFAULT_MAIL_ACCOUNT", "Work"),
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap),
            patch("subprocess.run", return_value=_make_proc()),
        ):
            result = compose_tools.create_rich_email_draft(
                account="Work",
                subject="Test Rich",
                to="user@example.com",
                text_body="Hello",
            )
        self.assertIsInstance(result, str)
        # Should not contain AttributeError
        self.assertNotIn("AttributeError", result)

    def test_account_strip_on_resolved_account_no_exception(self):
        """Bug 1: After fix, account.strip() should succeed on the resolved str."""
        cap = _ScriptCapture(return_value="")
        with (
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap),
            patch("subprocess.run", return_value=_make_proc()),
        ):
            # With account="Work" (non-None), must not raise
            result = compose_tools.create_rich_email_draft(
                account="Work",
                subject="S",
                to="x@x.com",
            )
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# Bug 3: reply_to_email — `return lookup_error` typed as `object`
#
# _build_found_message_lookup returned Tuple[str, Optional[object]].
# After `if isinstance(lookup_error, ToolError)` check, the else branch returned
# `lookup_error` which mypy typed as `object`, not `str`.  The return type of
# the helper should be Optional[ToolError] to let mypy validate the str return.
# ---------------------------------------------------------------------------


class TestReplyToEmailLookupErrorType(unittest.TestCase):
    """Bug 3: reply_to_email serialize_tool_error / lookup_error return."""

    def test_reply_with_message_id_not_found_returns_string(self):
        """BUG 3 regression: lookup_error on reply path must return a str."""
        cap = _ScriptCapture(return_value="SAVING REPLY AS DRAFT\n\nReply saved as draft!")

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="99999",
                reply_body="Reply here",
            )

        self.assertIsInstance(result, str)

    def test_reply_with_subject_keyword_returns_structured_deprecation_error(self):
        """Bug 3 regression: structured lookup errors must stay serialized strings."""
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.reply_to_email(
                account="Work",
                subject_keyword="test",
                reply_body="body",
                recent_days=0,
            )

        self.assertIsInstance(result, str)
        self.assertIn("TARGET_SELECTOR_DEPRECATED", result)
        self.assertIn("message_id", result)
        mock_run.assert_not_called()

    def test_forward_with_subject_keyword_returns_structured_deprecation_error(self):
        """Bug 6 (forward_email): same structured error return must be str."""
        with patch("apple_mail_mcp.tools.compose.run_applescript") as mock_run:
            result = compose_tools.forward_email(
                account="Work",
                subject_keyword="test",
                to="x@x.com",
                recent_days=0,
            )

        self.assertIsInstance(result, str)
        self.assertIn("TARGET_SELECTOR_DEPRECATED", result)
        self.assertIn("message_id", result)
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Bug 4: reply_to_email — escape_applescript(account) where account is str | None
# Bug 5: compose_email — same (lines 1351, 1336)
# Bug 6: forward_email — same (line 1554)
# Bug 7: manage_drafts — same (line 1792)
#
# These are all "after error guard, account is guaranteed non-None, but mypy
# doesn't know that because _resolve_account returns Optional[str]."
# The killer test: if account is None and escape_applescript stringifies it,
# the literal word "None" appears in the AppleScript. After the fix, that
# can never happen because account is narrowed to str before the call.
# ---------------------------------------------------------------------------


class TestNoNoneLiteralInAppleScript(unittest.TestCase):
    """Verify no 'None' literal leaks into AppleScript synthesis after fix."""

    def _check_no_none_literal(self, scripts: list[str]) -> None:
        """Assert 'None' doesn't appear as a quoted string value in AppleScript."""
        import re

        for script in scripts:
            # Look for "None" as a quoted value in AppleScript (the literal injection)
            matches = re.findall(r'"None"', script)
            self.assertEqual(
                matches,
                [],
                f'Found literal "None" in AppleScript: {script[:200]!r}',
            )

    def test_compose_email_no_none_in_applescript(self):
        """Bug 5: compose_email must not inject 'None' into AppleScript."""
        cap = _ScriptCapture(return_value="SAVING EMAIL AS DRAFT\n\n✓ Email saved as draft!\n")

        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap):
            result = compose_tools.compose_email(
                account="Work",
                to="test@example.com",
                subject="Subject",
                body="Body text",
                mode="draft",
            )

        self.assertIsInstance(result, str)
        self._check_no_none_literal(cap.scripts)

    def test_reply_to_email_no_none_in_applescript(self):
        """Bug 4: reply_to_email must not inject 'None' into AppleScript."""
        cap = _ScriptCapture(return_value="SAVING REPLY AS DRAFT\n\nReply saved as draft!\n")
        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap):
            result = compose_tools.reply_to_email(
                account="Work",
                message_id="12345",
                reply_body="Reply text",
            )

        self.assertIsInstance(result, str)
        self._check_no_none_literal(cap.scripts)

    def test_forward_email_no_none_in_applescript(self):
        """Bug 6: forward_email must not inject 'None' into AppleScript."""
        cap = _ScriptCapture(return_value="SAVING FORWARD AS DRAFT\n\nForward saved as draft.\n")
        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap):
            result = compose_tools.forward_email(
                account="Work",
                message_id="12345",
                to="fwd@example.com",
                mode="draft",
            )

        self.assertIsInstance(result, str)
        self._check_no_none_literal(cap.scripts)

    def test_manage_drafts_list_no_none_in_applescript(self):
        """Bug 7: manage_drafts list action must not inject 'None' into AppleScript."""
        cap = _ScriptCapture(return_value="DRAFT EMAILS - Work\n\nFound 0 draft(s)\n")
        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap):
            result = compose_tools.manage_drafts(account="Work", action="list")

        self.assertIsInstance(result, str)
        self._check_no_none_literal(cap.scripts)

    def test_create_rich_email_draft_account_in_output_not_none_string(self):
        """Bug 2: 'Account: ' line in output must not read 'Account: None'."""
        cap = _ScriptCapture(return_value="")
        with (
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap),
            patch("subprocess.run", return_value=_make_proc()),
        ):
            result = compose_tools.create_rich_email_draft(
                account="Work",
                subject="My draft",
                to="x@example.com",
                text_body="hi",
            )

        self.assertIsInstance(result, str)
        self.assertNotIn("Account: None", result)
        # The account name must appear in the output correctly
        self.assertIn("Account: Work", result)


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

# Safe text strategy (no surrogates)
_safe_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    max_size=100,
)

# Optional string: either None or a safe text value
_opt_str = st.one_of(st.none(), _safe_text)

# Optional string EXCLUDING the literal word "None"/"none" — used by the
# "no Python None leaks as literal 'None' in AppleScript" property test,
# where a user-provided string of "None" is legitimate input that the
# regex `"None"` matcher cannot distinguish from a stringified Python None.
_opt_str_no_none_literal = st.one_of(
    st.none(),
    _safe_text.filter(lambda s: "None" not in s and "none" not in s),
)


@given(
    subject=_safe_text,
    body=_opt_str,
    cc=_opt_str,
    bcc=_opt_str,
)
@settings(max_examples=80, suppress_health_check=[HealthCheck.too_slow])
def test_compose_email_optional_params_never_raise(
    subject: str,
    body: str | None,
    cc: str | None,
    bcc: str | None,
) -> None:
    """Property: compose_email with any combination of optional=None never raises.

    The return value must always be a dict-shaped string (structured error) or a
    success string — never a raw exception escaping the MCP boundary.
    """
    cap = _ScriptCapture(return_value="SAVING EMAIL AS DRAFT\n\n✓ Email saved as draft!\n")

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap):
        try:
            result = compose_tools.compose_email(
                account="Work",
                to="test@example.com",
                subject=subject,
                body=body or "",
                cc=cc,
                bcc=bcc,
                mode="draft",
            )
        except Exception as exc:
            raise AssertionError(f"compose_email raised {type(exc).__name__}: {exc}") from exc

    assert isinstance(result, str), f"Expected str, got {type(result)}"


@given(
    body=_opt_str_no_none_literal,
    cc=_opt_str_no_none_literal,
    bcc=_opt_str_no_none_literal,
)
@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
def test_no_none_literal_in_captured_applescript(
    body: str | None,
    cc: str | None,
    bcc: str | None,
) -> None:
    """Property: Python None values must never appear as a quoted 'None' AppleScript literal.

    This is the definitive regression guard: if any optional param (that is actually
    Python None, not the string "None") is stringified via str(None), it would appear
    as the literal word None inside an AppleScript "..." string, causing silent
    corruption or injection.

    We use a fixed subject that cannot produce a false positive (does not contain
    the substring 'None'), and use None for the optional params to verify they
    are handled without leaking the Python None repr into the script.
    """
    import re

    cap = _ScriptCapture(return_value="SAVING EMAIL AS DRAFT\n\n✓ Email saved as draft!\n")

    # Fixed subject that cannot produce a false positive
    subject = "Hello World Test"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap):
        # Plain-text path so we don't need to mock subprocess
        compose_tools.compose_email(
            account="Work",
            to="test@example.com",
            subject=subject,
            body=body or "",
            cc=cc,
            bcc=bcc,
            mode="draft",
            body_html=None,  # force plain-text path
        )

    for script in cap.scripts:
        # Find any quoted AppleScript string literal containing exactly "None"
        # (the Python None repr that would appear if a None param was stringified).
        # Since subject is fixed to "Hello World Test", any "None" here is
        # from a None parameter being str()-ed.
        injections = re.findall(r'"None"', script)
        assert injections == [], (
            f'Python None stringified as literal "None" in AppleScript.\n'
            f"Inputs: body={body!r} cc={cc!r} bcc={bcc!r}\n"
            f"Script snippet: {script[:300]!r}"
        )


# ---------------------------------------------------------------------------
# Additional: reply and forward also never inject "None" with optional params
# ---------------------------------------------------------------------------


class TestReplyForwardNoNoneLiteralProperty(unittest.TestCase):
    """Targeted None-literal tests for reply and forward tools."""

    def setUp(self):
        self._validate_patcher = patch(
            "apple_mail_mcp.tools.compose.validate_account_name",
            return_value=None,
        )
        self._validate_patcher.start()

    def tearDown(self):
        self._validate_patcher.stop()

    def test_reply_with_none_cc_bcc_no_none_literal(self):
        """reply_to_email cc=None, bcc=None must not put 'None' in script."""
        import re

        cap = _ScriptCapture(return_value="SAVING REPLY AS DRAFT\n\nReply saved as draft!\n")
        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap):
            compose_tools.reply_to_email(
                account="Work",
                message_id="42",
                reply_body="body",
                cc=None,
                bcc=None,
            )

        for script in cap.scripts:
            self.assertNotRegex(script, r'"None"')

    def test_forward_with_none_message_no_none_literal(self):
        """forward_email message=None must not put 'None' in script."""
        import re

        cap = _ScriptCapture(return_value="SAVING FORWARD AS DRAFT\n\nForward saved as draft.\n")
        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap):
            compose_tools.forward_email(
                account="Work",
                message_id="42",
                to="x@example.com",
                message=None,
                cc=None,
                bcc=None,
                mode="draft",
            )

        for script in cap.scripts:
            self.assertNotRegex(script, r'"None"')

    def test_manage_drafts_create_with_none_cc_bcc_no_none_literal(self):
        """manage_drafts create with cc=None, bcc=None must not inject 'None'."""
        import re

        cap = _ScriptCapture(return_value="CREATING DRAFT\n\n✓ Draft created successfully!\n")
        with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap):
            compose_tools.manage_drafts(
                account="Work",
                action="create",
                subject="Test subject",
                to="x@example.com",
                body="body text",
                cc=None,
                bcc=None,
            )

        for script in cap.scripts:
            self.assertNotRegex(script, r'"None"')
