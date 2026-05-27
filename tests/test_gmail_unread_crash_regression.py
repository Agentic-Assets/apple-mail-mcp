"""Regression tests for the Gmail ``whose``-on-list crash in list_inbox_emails.

Bug: ``_build_inbox_collection_block(include_read=False)`` calls
``build_bounded_message_scan(..., whose_condition="read status is false")``,
which emits::

    set candidateMessages to messages 1 thru N of inboxMailbox
    set candidateMessages to (candidateMessages whose read status is false)

The second line applies a ``whose`` clause to an AppleScript **list value**
(not a mailbox specifier). On Gmail accounts, message refs point to
``[Gmail]/All Mail`` and Mail.app rejects the whose-on-list with::

    Can't get {message id N of mailbox "[Gmail]/All Mail" ...}
    whose read status = false.

Fix: replace the whose filter with an in-loop ``if`` filter (same pattern
``search_emails`` already uses safely). A new helper
``build_bounded_filtered_scan`` emits the safe pattern;
``build_bounded_message_scan`` loses its ``whose_condition`` parameter;
``list_inbox_emails`` gains a ``read_status: Literal["all","read","unread"]``
parameter with ``include_read`` kept as a deprecated alias.

These tests are written against the **pre-fix** codebase.  Tests 1, 2, 5, 6
confirm the bad/missing patterns; tests 3, 4, 7 assert the new API.  All
seven are expected to fail until the fix lands (import errors are considered
test failures, not expected failures).
"""

from __future__ import annotations

import asyncio
import json
import unittest
import warnings
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    if asyncio.iscoroutine(coro):
        return asyncio.run(coro)
    return coro


class _ScriptCapture:
    """Record every AppleScript sent to the mock runner."""

    def __init__(self, return_value: str | list = ""):
        self.scripts: list[str] = []
        self._rv = return_value

    def __call__(self, script: str, timeout: int = 120) -> str:
        self.scripts.append(script)
        if isinstance(self._rv, list):
            return self._rv.pop(0) if self._rv else ""
        return self._rv

    @property
    def last_script(self) -> str:
        return self.scripts[-1] if self.scripts else ""


# ---------------------------------------------------------------------------
# 1. _build_inbox_collection_block must NOT emit whose-on-list
# ---------------------------------------------------------------------------


class TestListInboxUnreadDoesNotEmitWhoseOnList(unittest.TestCase):
    """The generated AppleScript must not contain the unsafe whose-on-list form.

    Pre-fix this test FAILS because ``_build_inbox_collection_block(False)``
    calls ``build_bounded_message_scan(..., whose_condition="read status is false")``
    which appends ``(candidateMessages whose read status is false)``.
    """

    def test_list_inbox_unread_does_not_emit_whose_on_list(self):
        from apple_mail_mcp.tools.inbox import _build_inbox_collection_block

        script = _build_inbox_collection_block(max_emails=20, read_filter="unread")
        self.assertNotIn(
            "candidateMessages whose read status is false",
            script,
            "The whose-on-list pattern crashes Gmail accounts. "
            "The fix must replace it with an in-loop if filter.",
        )


# ---------------------------------------------------------------------------
# 2. _build_inbox_collection_block MUST emit the safe in-loop filter
# ---------------------------------------------------------------------------


class TestListInboxUnreadUsesInLoopFilter(unittest.TestCase):
    """The generated AppleScript must use an in-loop ``if`` filter.

    Pre-fix this test FAILS because no ``if read status of aMessage is false``
    appears in the emitted snippet.
    """

    def test_list_inbox_unread_uses_in_loop_filter(self):
        from apple_mail_mcp.tools.inbox import _build_inbox_collection_block

        script = _build_inbox_collection_block(max_emails=20, read_filter="unread")
        self.assertIn(
            "if read status of aMessage is false",
            script,
            "The fixed implementation must use an in-loop `if` filter — "
            "the pattern `search_emails` already uses safely.",
        )


# ---------------------------------------------------------------------------
# 3. read_status parameter is accepted by list_inbox_emails
# ---------------------------------------------------------------------------


class TestListInboxReadStatusParamAccepted(unittest.TestCase):
    """``list_inbox_emails`` must accept a ``read_status`` kwarg.

    Pre-fix this test FAILS because ``list_inbox_emails`` has no
    ``read_status`` parameter and Python raises ``TypeError: unexpected
    keyword argument``.

    Post-fix assertions:
    - ``read_status="unread"`` and ``include_read=False`` produce equivalent
      scripts (both filter unread).
    - ``read_status="read"`` filters to read-only.
    - ``read_status="all"`` applies no filter (same as ``include_read=True``).
    """

    def _capture(self, **kwargs) -> str:
        cap = _ScriptCapture(return_value="")
        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=cap):
            _run(
                __import__(
                    "apple_mail_mcp.tools.inbox", fromlist=["list_inbox_emails"]
                ).list_inbox_emails(account="Work", max_emails=5, **kwargs)
            )
        return cap.last_script

    def test_read_status_unread_equivalent_to_include_read_false(self):
        from apple_mail_mcp.tools import inbox as inbox_tools

        cap_new = _ScriptCapture()
        cap_old = _ScriptCapture()

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=cap_new):
            _run(
                inbox_tools.list_inbox_emails(
                    account="Work", max_emails=5, read_status="unread"
                )
            )

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=cap_old):
            _run(
                inbox_tools.list_inbox_emails(
                    account="Work", max_emails=5, include_read=False
                )
            )

        # Both paths should emit a read-status filter (in-loop if or equivalent).
        # They don't need to be byte-identical, but neither should contain the
        # dangerous whose-on-list form.
        for script, label in [(cap_new.last_script, "read_status='unread'"),
                               (cap_old.last_script, "include_read=False")]:
            self.assertNotIn(
                "candidateMessages whose read status is false",
                script,
                f"{label}: whose-on-list must not appear in the fixed output.",
            )

    def test_read_status_read_filters_to_read_only(self):
        from apple_mail_mcp.tools import inbox as inbox_tools

        cap = _ScriptCapture()
        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=cap):
            _run(
                inbox_tools.list_inbox_emails(
                    account="Work", max_emails=5, read_status="read"
                )
            )

        # The script should contain a filter for read messages (not unread).
        script = cap.last_script
        self.assertIn(
            "read status of aMessage is true",
            script,
            "read_status='read' must filter to read-only messages.",
        )

    def test_read_status_all_applies_no_filter(self):
        from apple_mail_mcp.tools import inbox as inbox_tools

        cap_all = _ScriptCapture()
        cap_default = _ScriptCapture()

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=cap_all):
            _run(
                inbox_tools.list_inbox_emails(
                    account="Work", max_emails=5, read_status="all"
                )
            )

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=cap_default):
            _run(
                inbox_tools.list_inbox_emails(
                    account="Work", max_emails=5, include_read=True
                )
            )

        # Neither script should apply a read-status filter clause. The display
        # loop unconditionally reads `read status of aMessage` to render the
        # ✓/✉ indicator, so we look only for the filter form ("if read status
        # of aMessage is ...") which is only emitted by the filtered paths.
        for script, label in [(cap_all.last_script, "read_status='all'"),
                               (cap_default.last_script, "include_read=True")]:
            self.assertNotIn(
                "if read status of aMessage is",
                script,
                f"{label}: no read-status filter should appear for the all-messages path.",
            )
            self.assertNotIn(
                "candidateMessages whose read status",
                script,
                f"{label}: the whose-on-list form must never appear.",
            )


# ---------------------------------------------------------------------------
# 4. include_read=False still works but emits a DeprecationWarning
# ---------------------------------------------------------------------------


class TestIncludeReadFalseEmitsDeprecationWarning(unittest.TestCase):
    """Calling with ``include_read=False`` must still work but warn.

    Pre-fix this test FAILS because no DeprecationWarning is issued today.
    """

    def test_include_read_false_emits_deprecation_warning(self):
        from apple_mail_mcp.tools import inbox as inbox_tools

        cap = _ScriptCapture()
        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=cap):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                _run(
                    inbox_tools.list_inbox_emails(
                        account="Work",
                        max_emails=5,
                        include_read=False,
                    )
                )

        deprecation_msgs = [
            str(w.message)
            for w in caught
            if issubclass(w.category, DeprecationWarning)
        ]
        self.assertTrue(
            deprecation_msgs,
            "include_read=False must emit a DeprecationWarning pointing users "
            "at read_status='unread'. No DeprecationWarning was raised.",
        )


# ---------------------------------------------------------------------------
# 5. build_bounded_message_scan rejects / no longer accepts whose_condition
# ---------------------------------------------------------------------------


class TestBuildBoundedMessageScanRejectsWhoseCondition(unittest.TestCase):
    """After the fix, ``build_bounded_message_scan`` must not accept
    ``whose_condition``.

    Pre-fix this test FAILS because the parameter still exists and is accepted.

    Post-fix behaviour (either outcome is valid):
    - Parameter removed → ``TypeError: unexpected keyword argument``
    - Parameter kept but guarded → ``ToolError`` with code INVALID_SCAN_WINDOW
    """

    def test_build_bounded_message_scan_rejects_whose_condition(self):
        from apple_mail_mcp.bounded_scan import build_bounded_message_scan
        from apple_mail_mcp.backend.base import ToolError

        with self.assertRaises((TypeError, ToolError)):
            build_bounded_message_scan(
                mailbox_var="inboxMailbox",
                limit=100,
                whose_condition="read status is false",
            )


# ---------------------------------------------------------------------------
# 6. build_bounded_filtered_scan emits the safe in-loop pattern
# ---------------------------------------------------------------------------


class TestBuildBoundedFilteredScanEmitsInLoopPattern(unittest.TestCase):
    """The new ``build_bounded_filtered_scan`` helper must emit the safe pattern.

    Pre-fix this test FAILS because the helper does not exist yet
    (``ImportError: cannot import name 'build_bounded_filtered_scan'``).
    """

    def test_build_bounded_filtered_scan_emits_in_loop_pattern(self):
        from apple_mail_mcp.bounded_scan import build_bounded_filtered_scan  # type: ignore[attr-defined]

        snippet = build_bounded_filtered_scan(
            mailbox_var="inboxMailbox",
            scan_cap=200,
            target_max=20,
            condition_expr="read status of aMessage is false",
        )

        self.assertIn(
            "repeat with",
            snippet,
            "build_bounded_filtered_scan must emit a `repeat with` loop.",
        )
        self.assertIn(
            "if",
            snippet,
            "build_bounded_filtered_scan must emit an `if` condition inside the loop.",
        )
        self.assertIn(
            "set end of",
            snippet,
            "build_bounded_filtered_scan must accumulate matches with `set end of`.",
        )
        self.assertNotIn(
            "whose",
            snippet,
            "build_bounded_filtered_scan must NEVER emit a `whose` clause — "
            "that is the unsafe Gmail-crashing pattern this helper replaces.",
        )


# ---------------------------------------------------------------------------
# 7. End-to-end contract: Gmail whose-on-list error does not propagate
# ---------------------------------------------------------------------------

# The historical error string Mail.app returns on Gmail accounts when a
# whose clause is applied to an in-memory list of message refs that point
# to [Gmail]/All Mail.
_GMAIL_WHOSE_ERROR = (
    "Can't get {message id 12345 of mailbox \"[Gmail]/All Mail\" of "
    "account \"Cayman - Agentic Assets\"} whose read status = false."
)


def _gmail_simulating_runner(script: str, timeout: int = 120) -> str:
    """Simulate a Gmail Mail.app session.

    * If the script contains the banned ``(candidateMessages whose read status
      is false)`` pattern, return the historical crash error string.
    * Otherwise return a single valid pipe-delimited email row so the caller
      has something to parse.
    """
    if "(candidateMessages whose read status is false)" in script:
        return _GMAIL_WHOSE_ERROR
    # Happy-path: one unread email row
    return "Subject|||sender@gmail.com|||Wed, May 27, 2026|||false|||Cayman - Agentic Assets|||99999"


class TestGmailUnreadCrashDoesNotReproduce(unittest.TestCase):
    """End-to-end contract: the Gmail whose-on-list crash must not reproduce.

    Pre-fix this test FAILS because ``list_inbox_emails(include_read=False)``
    triggers the ``_gmail_simulating_runner`` to return the error string,
    which propagates into the result.

    Post-fix: the in-loop filter is used instead, the runner returns the
    valid email row, and the result contains at least one email with no
    error string.
    """

    def test_gmail_unread_crash_does_not_reproduce(self):
        from apple_mail_mcp.tools import inbox as inbox_tools

        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            side_effect=_gmail_simulating_runner,
        ):
            result = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=10,
                    include_read=False,
                    output_format="json",
                )
            )

        # Result must be a dict (JSON-mode), not a crash-error string.
        self.assertIsInstance(
            result,
            dict,
            f"Expected a dict result; got {type(result).__name__}: {result!r}",
        )

        result_str = json.dumps(result)

        # The historical error substring must not appear anywhere in the output.
        self.assertNotIn(
            "Can't get",
            result_str,
            "The Gmail whose-on-list error propagated into the result. "
            "The fix must use an in-loop `if` filter instead of `whose`.",
        )
        self.assertNotIn(
            "[Gmail]/All Mail",
            result_str,
            "Gmail mailbox error reference appeared in the result — "
            "the crash is still reproducing.",
        )

        # After the fix, the runner returns a valid row, so we should have emails.
        emails = result.get("emails", [])
        self.assertGreater(
            len(emails),
            0,
            "Expected at least one email in the result after the fix; "
            f"got emails={emails!r}. errors={result.get('errors')!r}.",
        )


if __name__ == "__main__":
    unittest.main()
