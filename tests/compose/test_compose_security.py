"""Security regression tests for compose.py bugs.

Covers three confirmed bugs fixed in compose.py:

Bug 1 (HIGH - security): create_rich_email_draft output_path bypassed
  validate_save_path, allowing writes to sensitive dirs like ~/.ssh.

Bug 2 (MED - shell quoting): AppleScript shell commands used bare
  single-quoted paths instead of ``quoted form of`` for html_temp_path
  and fwd_html_temp_path variables.

Bug 3 (LOW - uncaught exception): subprocess.run(["open", ...]) raised
  CalledProcessError uncaught, violating the structured-error contract.
"""

from __future__ import annotations

import subprocess
import tempfile
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from apple_mail_mcp.tools import compose as compose_tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_applescript_result(returncode=0, stdout=b"ok", stderr=b""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class _ScriptCapture:
    """Records every AppleScript passed to run_applescript."""

    def __init__(self, return_value="ok"):
        self.scripts: list[str] = []
        self._rv = return_value

    def __call__(self, script, timeout=120):
        self.scripts.append(script)
        if isinstance(self._rv, list):
            return self._rv.pop(0) if self._rv else ""
        return self._rv

    @property
    def last_script(self) -> str:
        return self.scripts[-1] if self.scripts else ""


# ---------------------------------------------------------------------------
# Bug 1 — sensitive-path regression (parametrized)
# ---------------------------------------------------------------------------

SENSITIVE_PATHS = [
    "~/.ssh/authorized_keys",
    "~/.ssh/id_rsa",
    "~/.aws/credentials",
    "~/.claude/settings.json",
    "~/Library/Keychains/login.keychain-db",
]


@pytest.mark.parametrize("evil_path", SENSITIVE_PATHS)
def test_bug1_sensitive_output_path_is_rejected(evil_path):
    """Bug 1 regression: sensitive output_path must return a structured error,
    never write anything to disk."""
    write_calls = []

    original_write_bytes = Path.write_bytes

    def capturing_write_bytes(self, data):
        write_calls.append(str(self))
        return original_write_bytes(self, data)

    cap = _ScriptCapture(return_value=["sender@example.com"])

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap),
        patch("apple_mail_mcp.tools.compose.subprocess.run"),
        patch.object(Path, "write_bytes", capturing_write_bytes),
    ):
        result = compose_tools.create_rich_email_draft(
            account="Work",
            subject="Test",
            to="test@example.com",
            text_body="Body",
            output_path=evil_path,
            open_in_mail=False,
        )

    # (a) Must return a structured error string
    assert isinstance(result, str), "Tool must return a string, not raise"
    assert result.startswith("Error:"), (
        f"Expected structured error for evil_path={evil_path!r}, got: {result!r}"
    )

    # (b) No write must have occurred to the sensitive path
    resolved = os.path.realpath(os.path.expanduser(evil_path))
    for written in write_calls:
        written_resolved = os.path.realpath(written)
        assert written_resolved != resolved, (
            f"write_bytes was called for sensitive path {evil_path!r} "
            f"(resolved: {resolved})"
        )


# ---------------------------------------------------------------------------
# Bug 1 — happy path: safe output_path succeeds
# ---------------------------------------------------------------------------

def test_bug1_safe_output_path_succeeds():
    """Bug 1 happy path: a path under ~/Desktop should succeed (mocked write)."""
    cap = _ScriptCapture(return_value=["sender@example.com", "saved"])
    write_calls = []

    def mock_write_bytes(self, data):
        write_calls.append(str(self))

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap),
        patch("apple_mail_mcp.tools.compose.subprocess.run"),
        patch.object(Path, "mkdir"),
        patch.object(Path, "write_bytes", mock_write_bytes),
    ):
        result = compose_tools.create_rich_email_draft(
            account="Work",
            subject="Safe Draft",
            to="test@example.com",
            text_body="Body",
            output_path="~/Desktop/draft.eml",
            open_in_mail=False,
        )

    assert not result.startswith("Error:"), (
        f"Expected success for ~/Desktop/draft.eml, got: {result!r}"
    )
    assert write_calls, "write_bytes should have been called for safe path"


# ---------------------------------------------------------------------------
# Bug 2 — shell quoting: quoted form of must appear for html_temp_path
# ---------------------------------------------------------------------------

def test_bug2_html_temp_path_uses_quoted_form_in_compose():
    """Bug 2 regression: compose_email / _send_html_email AppleScript must use
    quoted form of for html_temp_path, not bare single-quoted path."""
    cap = _ScriptCapture(return_value="ok")

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap),
        patch("apple_mail_mcp.tools.compose.subprocess.run"),
    ):
        compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="HTML test",
            body="Plain fallback",
            body_html="<b>Hello</b>",
            mode="draft",
        )

    # At least one script should contain a shell command (the HTML path writes a temp file)
    all_scripts = "\n".join(cap.scripts)
    for script in cap.scripts:
        _assert_no_bare_single_quoted_paths(script)


def test_bug2_html_temp_path_uses_quoted_form_in_reply():
    """Bug 2 regression: reply_to_email AppleScript must use quoted form of
    for html_temp_path in both cat and rm -f shell commands."""
    cap = _ScriptCapture(return_value="Reply saved as draft!")

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap),
        patch("apple_mail_mcp.tools.compose.subprocess.run"),
    ):
        compose_tools.reply_to_email(
            account="Work",
            message_id="<test@example.com>",
            reply_body="Plain reply",
            body_html="<b>Reply</b>",
            send=False,
        )

    for script in cap.scripts:
        _assert_no_bare_single_quoted_paths(script)


def test_bug2_fwd_html_temp_path_uses_quoted_form_in_forward():
    """Bug 2 regression: forward_email AppleScript must use quoted form of
    for fwd_html_temp_path in both cat and rm -f shell commands."""
    cap = _ScriptCapture(return_value="Forward saved as draft!")

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap),
        patch("apple_mail_mcp.tools.compose.subprocess.run"),
    ):
        # forward_email triggers fwd_html_temp_path when message= is provided
        compose_tools.forward_email(
            account="Work",
            message_id="<orig@example.com>",
            to="fwd@example.com",
            message="See below",
            mode="draft",
        )

    for script in cap.scripts:
        _assert_no_bare_single_quoted_paths(script)


def _assert_no_bare_single_quoted_paths(script: str) -> None:
    """Assert that every 'do shell script "cat ...' or 'do shell script "rm -f ...'
    line in the AppleScript uses 'quoted form of' concatenation, not a bare
    single-quoted path literal like do shell script "cat '/tmp/foo.html'".

    The fixed pattern looks like:
        do shell script "cat " & quoted form of "/tmp/foo.html"
    The broken pattern looks like:
        do shell script "cat '/tmp/foo.html'"
    """
    import re
    # Find each line containing a shell script invocation of cat or rm -f
    for line in script.splitlines():
        if 'do shell script' not in line:
            continue
        if not re.search(r'(?:cat|rm -f)', line):
            continue
        # The OLD broken pattern has a single-quoted path inside the double-quoted string
        # e.g. "cat '/tmp/foo.html'"  — detect this with a bare ' after cat/rm -f
        broken = re.search(r'do shell script\s+"(?:cat|rm -f)\s+\'', line)
        assert broken is None, (
            f"Shell command uses bare single-quoted path instead of "
            f"'quoted form of': {line.strip()!r}"
        )
        # Also assert the fixed pattern IS present (quoted form of follows the command)
        assert "quoted form of" in line, (
            f"Shell command missing 'quoted form of' pattern: {line.strip()!r}"
        )


# ---------------------------------------------------------------------------
# Bug 3 — subprocess.CalledProcessError is caught and returned as structured error
# ---------------------------------------------------------------------------

def test_bug3_subprocess_called_process_error_returns_structured_error():
    """Bug 3 regression: CalledProcessError from subprocess.run must be caught
    and returned as a structured error string, never re-raised."""
    cap = _ScriptCapture(return_value=["sender@example.com", "saved"])

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test.eml"

        with (
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap),
            patch(
                "apple_mail_mcp.tools.compose.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, ["open"]),
            ),
        ):
            result = compose_tools.create_rich_email_draft(
                account="Work",
                subject="Test Subject",
                to="test@example.com",
                text_body="Body",
                output_path=str(output_path),
                open_in_mail=True,
            )

    assert isinstance(result, str), "Tool must return str, not raise"
    assert result.startswith("Error:"), (
        f"Expected structured error on CalledProcessError, got: {result!r}"
    )
    assert "Mail" in result or "draft" in result.lower(), (
        f"Error message should mention Mail or draft: {result!r}"
    )


def test_bug3_file_not_found_error_returns_structured_error():
    """Bug 3 regression: FileNotFoundError from subprocess.run (open not on PATH)
    must be caught and returned as a structured error string."""
    cap = _ScriptCapture(return_value=["sender@example.com", "saved"])

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test.eml"

        with (
            patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=cap),
            patch(
                "apple_mail_mcp.tools.compose.subprocess.run",
                side_effect=FileNotFoundError("open: command not found"),
            ),
        ):
            result = compose_tools.create_rich_email_draft(
                account="Work",
                subject="Test Subject",
                to="test@example.com",
                text_body="Body",
                output_path=str(output_path),
                open_in_mail=True,
            )

    assert isinstance(result, str), "Tool must return str, not raise"
    assert result.startswith("Error:"), (
        f"Expected structured error on FileNotFoundError, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# Hypothesis property test — output_path never raises out of tool boundary
# ---------------------------------------------------------------------------

@given(path=st.text(min_size=1))
@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
def test_hypothesis_output_path_never_raises(path):
    """Property: for any non-empty output_path string, create_rich_email_draft
    must either succeed (with mocked write) or return a structured error string.
    It must never raise a Python exception out of the tool boundary."""
    mock_write = MagicMock()

    try:
        with (
            patch("apple_mail_mcp.tools.compose.run_applescript", return_value="sender@example.com"),
            patch("apple_mail_mcp.tools.compose.subprocess.run"),
            patch.object(Path, "mkdir"),
            patch.object(Path, "write_bytes", mock_write),
        ):
            result = compose_tools.create_rich_email_draft(
                account="Work",
                subject="Fuzz",
                to="test@example.com",
                text_body="Body",
                output_path=path,
                open_in_mail=False,
            )
    except Exception as exc:
        pytest.fail(
            f"create_rich_email_draft raised {type(exc).__name__} for "
            f"output_path={path!r}: {exc}"
        )

    assert isinstance(result, str), (
        f"Tool must return str for output_path={path!r}, got {type(result)}"
    )
