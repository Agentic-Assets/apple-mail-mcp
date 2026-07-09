"""Contract tests for the bounded-scan capability token machinery.

``apple_mail_mcp.bounded_scan`` is the only sanctioned producer of
``ScanWindow`` tokens.

These tests exercise the contract end-to-end:

* ``bounded_inbox_scan`` stamps tokens and rejects unbounded calls with a
  structured ``UNBOUNDED_SCAN_REQUIRED`` error whose remediation carries an
  actionable bounded ``preferred`` fix and never points at the disabled
  ``full_inbox_export`` tool.
* The AppleScript helpers (``build_bounded_message_scan``,
  ``build_whose_id_list``, ``compute_scan_upper_bound``) emit the safe
  ``messages 1 thru N`` pattern and never the dangerous ``every message
  of <mailbox> whose ...`` form.
* Each retired tool surfaces the structured error when called with the
  legacy unbounded arguments (no AppleScript runs — ``subprocess`` is
  mocked).
"""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from unittest.mock import patch

import apple_mail_mcp  # noqa: F401  (registers tools as side effect)
import pytest
from apple_mail_mcp.backend.base import ScanWindow, ToolError
from apple_mail_mcp.bounded_scan import (
    MAX_SCAN_DAYS,
    MAX_SCAN_LIMIT,
    bounded_inbox_scan,
    build_bounded_filtered_scan,
    build_bounded_message_scan,
    build_whose_id_list,
    compute_scan_upper_bound,
)


def _run(coro):
    if asyncio.iscoroutine(coro):
        return asyncio.run(coro)
    return coro


def _coerce_result_dict(result: Any) -> dict[str, Any]:
    """Tools return either a dict, a JSON string, or a text error.

    Normalize to a dict for assertion. If the value is a plain text
    ``"Error: ..."`` string the test that called this should xfail with a
    surfaced-bug note — see ``GetEmailThreadStructuredErrorBug``.
    """
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"_raw_text": result}
    raise AssertionError(f"Unexpected result type: {type(result).__name__}")


# ---------------------------------------------------------------------------
# bounded_inbox_scan / ScanWindow stamping
# ---------------------------------------------------------------------------


class BoundedInboxScanTests(unittest.TestCase):
    def test_bounded_inbox_scan_issues_stamped_window(self):
        window = bounded_inbox_scan(mailbox="INBOX", recent_days=7)
        self.assertIsInstance(window, ScanWindow)
        self.assertEqual(window._issued_by, "core.bounded_inbox_scan")
        self.assertEqual(window.recent_days, 7)
        self.assertEqual(window.mailbox, "INBOX")

    def test_bounded_inbox_scan_accepts_limit_and_since(self):
        window_limit = bounded_inbox_scan(mailbox="INBOX", limit=100)
        self.assertEqual(window_limit.limit, 100)
        self.assertEqual(window_limit._issued_by, "core.bounded_inbox_scan")

        window_since = bounded_inbox_scan(mailbox="INBOX", since=1_700_000_000.0)
        self.assertEqual(window_since.since, 1_700_000_000.0)

    def test_bounded_inbox_scan_rejects_unbounded(self):
        with self.assertRaises(ToolError) as ctx:
            bounded_inbox_scan(mailbox="INBOX")
        err = ctx.exception
        self.assertEqual(err.code, "UNBOUNDED_SCAN_REQUIRED")
        self.assertIsNotNone(err.remediation)
        self.assertNotIn("full_inbox_export", str(err.remediation))
        self.assertTrue(err.remediation.get("preferred"))

    def test_bounded_inbox_scan_rejects_over_max_recent_days(self):
        with self.assertRaises(ToolError) as ctx:
            bounded_inbox_scan(mailbox="INBOX", recent_days=MAX_SCAN_DAYS + 1)
        self.assertEqual(ctx.exception.code, "UNBOUNDED_SCAN_REQUIRED")
        self.assertNotIn("full_inbox_export", str(ctx.exception.remediation))
        self.assertTrue(ctx.exception.remediation.get("preferred"))

    def test_bounded_inbox_scan_rejects_over_max_limit(self):
        with self.assertRaises(ToolError) as ctx:
            bounded_inbox_scan(mailbox="INBOX", limit=MAX_SCAN_LIMIT + 1)
        self.assertEqual(ctx.exception.code, "UNBOUNDED_SCAN_REQUIRED")
        self.assertNotIn("full_inbox_export", str(ctx.exception.remediation))
        self.assertTrue(ctx.exception.remediation.get("preferred"))

    def test_bounded_inbox_scan_rejects_blank_mailbox(self):
        with self.assertRaises(ToolError) as ctx:
            bounded_inbox_scan(mailbox="   ", recent_days=7)
        self.assertEqual(ctx.exception.code, "INVALID_SCAN_WINDOW")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# AppleScript helper emissions
# ---------------------------------------------------------------------------


class AppleScriptHelperEmissionTests(unittest.TestCase):
    def test_build_bounded_message_scan_emits_safe_pattern(self):
        # build_bounded_message_scan no longer accepts whose_condition —
        # callers must use build_bounded_filtered_scan instead.
        # Verify it raises UNSAFE_WHOSE_ON_LIST when whose_condition is passed.
        from apple_mail_mcp.backend.base import ToolError

        with self.assertRaises(ToolError) as ctx:
            build_bounded_message_scan("inboxMailbox", 100, whose_condition="read status is false")
        self.assertEqual(ctx.exception.code, "UNSAFE_WHOSE_ON_LIST")

        # The safe pattern is build_bounded_filtered_scan:
        # it emits `messages 1 thru N`, an in-loop `if`, and set end of.
        snippet = build_bounded_filtered_scan(
            mailbox_var="inboxMailbox",
            scan_cap=100,
            target_max=50,
            condition_expr="read status of aMessage is false",
        )
        self.assertIn("messages 1 thru 100 of inboxMailbox", snippet)
        # The dangerous full-mailbox whose must NEVER appear.
        self.assertNotIn("every message of inboxMailbox whose", snippet)
        # The safe in-loop if pattern must be present.
        self.assertIn("repeat with aMessage in", snippet)
        self.assertIn("if read status of aMessage is false then", snippet)
        self.assertIn("set end of", snippet)
        # The old `candidateMessages whose` slice-then-whose is gone.
        self.assertNotIn("candidateMessages whose read status is false", snippet)

    def test_build_bounded_message_scan_without_whose(self):
        snippet = build_bounded_message_scan("inboxMailbox", 50)
        self.assertIn("messages 1 thru 50 of inboxMailbox", snippet)
        self.assertNotIn("whose", snippet)

    def test_build_bounded_message_scan_rejects_bad_limit(self):
        with self.assertRaises(ToolError) as ctx:
            build_bounded_message_scan("inboxMailbox", 0)
        self.assertEqual(ctx.exception.code, "INVALID_SCAN_WINDOW")

    def test_compute_scan_upper_bound_caps_at_window_cap(self):
        # Anything that scales beyond the default 50-message window cap
        # must clamp to the window cap (AGENTIC-988 hard-ceiling retune).
        self.assertEqual(compute_scan_upper_bound(365), 50)
        self.assertEqual(compute_scan_upper_bound(1000), 50)

    def test_compute_scan_upper_bound_uses_base_cap_for_tiny_windows(self):
        self.assertEqual(compute_scan_upper_bound(0), 40)
        self.assertEqual(compute_scan_upper_bound(0.5), 41)

    def test_compute_scan_upper_bound_respects_custom_caps(self):
        result = compute_scan_upper_bound(7, base_cap=100, window_cap=300)
        # base_cap + (7 * 3) = 121 (days_scale not overridden, so it picks up
        # the new SEARCH_DAYS_SCALE default of 3; below custom window cap).
        self.assertEqual(result, 121)

    def test_build_whose_id_list_format(self):
        self.assertEqual(
            build_whose_id_list(["1", "2", "3"]),
            "id is 1 or id is 2 or id is 3",
        )

    def test_build_whose_id_list_filters_non_numeric(self):
        self.assertEqual(
            build_whose_id_list(["12345"]),
            "id is 12345",
        )

    def test_build_whose_id_list_rejects_empty(self):
        with self.assertRaises(ToolError) as ctx:
            build_whose_id_list([])
        self.assertEqual(ctx.exception.code, "INVALID_SCAN_WINDOW")

    def test_draft_lookup_uses_safe_pattern(self):
        """The Drafts subject lookup must use the in-loop pattern.

        The historical form pre-filtered with `every message of
        draftsMailbox whose subject contains "..."` and sliced after.
        On Gmail accounts the Drafts mailbox is `[Gmail]/Drafts` and the
        unfiltered `whose` carries the same evaluation risk as the inbox
        unread crash. This contract test fails if the regression returns.
        """
        from apple_mail_mcp.tools.compose import _build_draft_lookup

        snippet = _build_draft_lookup("invoice question")
        # Safe primitives must be present. The lookup checks bounded head/tail
        # windows and filters in-loop, so it tolerates Mail account ordering
        # differences without ever using `whose` or an unbounded folder scan.
        self.assertIn("messages 1 thru headEnd of draftsMailbox", snippet)
        self.assertIn("messages tailStart thru totalDrafts of draftsMailbox", snippet)
        self.assertIn("repeat with aMessage in candidateMessages", snippet)
        self.assertIn('(subject of aMessage) contains "invoice question"', snippet)
        # The unsafe forms must NEVER appear.
        self.assertNotIn("every message of draftsMailbox whose subject contains", snippet)
        self.assertNotIn("draftMessages whose subject contains", snippet)
        self.assertNotIn("candidateMessages whose", snippet)


# ---------------------------------------------------------------------------
# Per-tool structured-error contract
# ---------------------------------------------------------------------------

# Each entry: tool_name → (module_path, callable_name, kwargs that trip the
# unbounded check, is_async).
RETIRED_UNBOUNDED_CASES = [
    (
        "list_inbox_emails",
        "apple_mail_mcp.tools.inbox",
        "list_inbox_emails",
        {"account": "Work", "max_emails": 0, "output_format": "json"},
        True,
    ),
    (
        "search_emails",
        "apple_mail_mcp.tools.search",
        "search_emails",
        {"account": "Work", "recent_days": 0, "output_format": "json"},
        True,
    ),
    (
        "get_top_senders",
        "apple_mail_mcp.tools.smart_inbox",
        "get_top_senders",
        {"account": "Work", "days_back": 0},
        False,
    ),
    (
        "get_statistics",
        "apple_mail_mcp.tools.analytics",
        "get_statistics",
        {"account": "Work", "days_back": 0, "output_format": "json"},
        False,
    ),
]


@pytest.mark.parametrize(
    "label,module_path,fn_name,kwargs,is_async",
    RETIRED_UNBOUNDED_CASES,
    ids=[case[0] for case in RETIRED_UNBOUNDED_CASES],
)
def test_each_retired_tool_returns_structured_unbounded_error(label, module_path, fn_name, kwargs, is_async):
    """Every retired tool must surface UNBOUNDED_SCAN_REQUIRED + fallback."""
    import importlib

    module = importlib.import_module(module_path)
    fn = getattr(module, fn_name)

    # Mock subprocess so no AppleScript ever runs even if a tool slipped
    # past the unbounded check. The autouse account-validation fixture in
    # tests/conftest.py already pretends ``account='Work'`` is valid.
    with patch("subprocess.run") as mock_run:
        result = _run(fn(**kwargs))

    # The tool must short-circuit *before* invoking osascript.
    assert not mock_run.called, (
        f"{label} should reject unbounded args before running AppleScript, "
        f"but subprocess.run was called {mock_run.call_count} time(s)."
    )

    payload = _coerce_result_dict(result)
    assert payload.get("code") == "UNBOUNDED_SCAN_REQUIRED", (
        f"{label} returned {payload!r}; expected code='UNBOUNDED_SCAN_REQUIRED'."
    )
    assert payload.get("error") is True, f"{label} returned {payload!r}; expected error=True flag."
    remediation = payload.get("remediation") or {}
    assert "full_inbox_export" not in str(remediation), (
        f"{label} remediation must NOT point at the disabled full_inbox_export tool. Got: {remediation!r}"
    )
    assert remediation.get("preferred"), (
        f"{label} remediation must carry an actionable bounded `preferred` fix. Got: {remediation!r}"
    )


def test_get_email_thread_returns_structured_unbounded_error():
    from apple_mail_mcp.tools import search as search_tools

    with patch("subprocess.run") as mock_run:
        result = search_tools.get_email_thread(
            account="Work",
            subject_keyword="anything",
            recent_days=0,
        )

    assert not mock_run.called
    payload = _coerce_result_dict(result)
    assert payload.get("code") == "UNBOUNDED_SCAN_REQUIRED"
    assert payload.get("error") is True
    remediation = payload.get("remediation") or {}
    assert "full_inbox_export" not in str(remediation)
    assert remediation.get("preferred")


# ---------------------------------------------------------------------------
# Envelope shape: every -> str tool must return a JSON string (not a raw dict)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,module_path,fn_name,kwargs,is_async",
    RETIRED_UNBOUNDED_CASES,
    ids=[case[0] for case in RETIRED_UNBOUNDED_CASES],
)
def test_retired_tool_unbounded_envelope_is_json_string(label, module_path, fn_name, kwargs, is_async):
    """Every retired tool's `-> str` signature requires a JSON-encoded string."""
    import importlib

    module = importlib.import_module(module_path)
    fn = getattr(module, fn_name)

    with patch("subprocess.run"):
        result = _run(fn(**kwargs))

    assert isinstance(result, str), (
        f"{label} returned {type(result).__name__}; the tool signature is "
        f"`-> str` and the unbounded-scan envelope must be JSON-encoded."
    )
    parsed = json.loads(result)
    assert isinstance(parsed, dict), f"{label} JSON did not decode to a dict: {parsed!r}"
    assert parsed.get("code") == "UNBOUNDED_SCAN_REQUIRED"


def test_get_email_thread_unbounded_envelope_is_json_string():
    """get_email_thread's `-> str` signature requires a JSON-encoded envelope."""
    from apple_mail_mcp.tools import search as search_tools

    with patch("subprocess.run"):
        result = search_tools.get_email_thread(
            account="Work",
            subject_keyword="anything",
            recent_days=0,
        )

    assert isinstance(result, str)
    parsed = json.loads(result)
    assert isinstance(parsed, dict)
    assert parsed.get("code") == "UNBOUNDED_SCAN_REQUIRED"


if __name__ == "__main__":
    unittest.main()
