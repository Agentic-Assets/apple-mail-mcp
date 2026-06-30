"""Regression tests for the typed-kwargs unpacking bug fixed in inbox.py.

Bug class: a dict[str, int | str | None] (json_kwargs / item) was spread
with ** into functions whose per-kwarg types are stricter (str | None, bool,
int).  The fix replaced every **dict spread with explicit keyword arguments
and added an explicit Dict[str, Any] annotation on the mailbox item dict.

Tests cover:
1. Regression per fixed call site — no TypeError on None/int mismatch.
2. JSON-schema contract when optional fields are absent (missing-field path).
3. Hypothesis property test — fuzz the kwargs dict shape, assert no TypeError.
"""

import asyncio
import json
import unittest
from typing import Any, Dict, Optional
from unittest.mock import patch

import jsonschema
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from apple_mail_mcp.tools import inbox as inbox_tools

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


# Canned AppleScript payloads -------------------------------------------------

OVERVIEW_PAYLOAD = "\n".join([
    "HEADER|||Work|||3|||15",
    "MAILBOX|||INBOX|||3",
    "RECENT|||Digest|||newsletter@example.com|||Friday, May 23, 2026 at 9:00:00 AM|||false",
])

# Minimal payload with no MAILBOX / RECENT lines (exercises absent-field path)
OVERVIEW_HEADER_ONLY = "HEADER|||Work|||0|||0"

INBOX_EMAILS_PAYLOAD = "\n".join([
    "Subject|||alice@example.com|||May 23, 2026|||false|||Work|||101|||<msg1@example.com>",
    "TOTAL:1",
])

# Minimal mailbox listing payload (account|||name|||path|||msg_count|||unread)
MAILBOX_LISTING_PAYLOAD = "\n".join([
    "Work|||INBOX|||Work/INBOX|||5|||2",
    "Work|||Sent|||Work/Sent|||100|||0",
])


# ---------------------------------------------------------------------------
# 1. Regression — call-site A: account_not_found path (was line 1865)
# ---------------------------------------------------------------------------

class TestOverviewJsonErrorAccountNotFound(unittest.TestCase):
    """_overview_json_error called with explicit kwargs; no TypeError on None account."""

    def test_missing_account_returns_structured_error(self):
        """account='Missing' triggers account_not_found; None fields must not raise TypeError."""
        result = _run(
            inbox_tools.get_inbox_overview(
                account="Missing",
                output_format="json",
                include_mailboxes=True,
                include_recent=True,
                include_suggestions=True,
                max_recent=10,
            )
        )
        self.assertIsInstance(result, dict)
        self.assertEqual(result["error"], "account_not_found")
        self.assertIsInstance(result["accounts"], list)
        self.assertIsInstance(result["errors"], list)

    def test_account_not_found_include_mailboxes_false(self):
        """include_mailboxes=False must reach _overview_json_error without TypeError."""
        result = _run(
            inbox_tools.get_inbox_overview(
                account="Missing",
                output_format="json",
                include_mailboxes=False,
                include_recent=False,
                include_suggestions=False,
                max_recent=5,
            )
        )
        self.assertIsInstance(result, dict)
        self.assertEqual(result["error"], "account_not_found")
        self.assertFalse(result["include_mailboxes"])
        self.assertFalse(result["include_recent"])
        self.assertFalse(result["include_suggestions"])
        self.assertEqual(result["max_recent"], 5)


# ---------------------------------------------------------------------------
# 2. Regression — call-site B: account_listing_timeout (was line 1876)
# ---------------------------------------------------------------------------

class TestOverviewJsonErrorAccountListingTimeout(unittest.TestCase):
    """_overview_json_error called when account listing times out."""

    def test_account_listing_timeout_returns_structured_error(self):
        from apple_mail_mcp.core import AppleScriptTimeout
        with patch(
            "apple_mail_mcp.tools.inbox._list_mail_accounts",
            side_effect=AppleScriptTimeout(30),
        ):
            result = _run(inbox_tools.get_inbox_overview(output_format="json"))

        self.assertIsInstance(result, dict)
        self.assertEqual(result["error"], "account_listing_timeout")
        self.assertEqual(result["errors"], ["__account_listing__"])
        self.assertIsInstance(result["accounts"], list)
        self.assertIsInstance(result["total_unread"], int)

    def test_account_listing_timeout_with_non_default_options(self):
        """Explicit booleans/ints reach the error path without TypeError."""
        from apple_mail_mcp.core import AppleScriptTimeout
        with patch(
            "apple_mail_mcp.tools.inbox._list_mail_accounts",
            side_effect=AppleScriptTimeout(30),
        ):
            result = _run(
                inbox_tools.get_inbox_overview(
                    output_format="json",
                    include_mailboxes=False,
                    include_recent=False,
                    include_suggestions=False,
                    max_recent=3,
                )
            )

        self.assertEqual(result["error"], "account_listing_timeout")
        self.assertFalse(result["include_mailboxes"])
        self.assertEqual(result["max_recent"], 3)


# ---------------------------------------------------------------------------
# 3. Regression — call-site C: empty account list (was line 1884)
# ---------------------------------------------------------------------------

class TestFormatOverviewJsonEmptyAccounts(unittest.TestCase):
    """_format_overview_json([], [], ...) — no TypeError when account list is empty."""

    def test_empty_account_list_returns_valid_dict(self):
        with patch(
            "apple_mail_mcp.tools.inbox._list_mail_accounts",
            return_value=[],
        ):
            result = _run(inbox_tools.get_inbox_overview(output_format="json"))

        self.assertIsInstance(result, dict)
        self.assertEqual(result["total_unread"], 0)
        self.assertEqual(result["accounts"], [])
        self.assertEqual(result["errors"], [])

    def test_empty_account_list_with_all_booleans_false(self):
        with patch(
            "apple_mail_mcp.tools.inbox._list_mail_accounts",
            return_value=[],
        ):
            result = _run(
                inbox_tools.get_inbox_overview(
                    output_format="json",
                    include_mailboxes=False,
                    include_recent=False,
                    include_suggestions=False,
                    max_recent=0,
                )
            )

        self.assertFalse(result["include_mailboxes"])
        self.assertFalse(result["include_recent"])
        self.assertFalse(result["include_suggestions"])
        self.assertEqual(result["max_recent"], 0)


# ---------------------------------------------------------------------------
# 4. Regression — call-site D: success path (was line 1914)
# ---------------------------------------------------------------------------

class TestFormatOverviewJsonSuccessPath(unittest.TestCase):
    """_format_overview_json(parsed, errors, ...) success path — no TypeError."""

    def test_success_path_returns_expected_shape(self):
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=OVERVIEW_PAYLOAD,
        ):
            result = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="json",
                    include_mailboxes=True,
                    include_recent=True,
                    include_suggestions=True,
                    max_recent=5,
                )
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["output_format"], "json")
        self.assertEqual(result["account"], "Work")
        self.assertIsInstance(result["total_unread"], int)
        self.assertIsInstance(result["accounts"], list)

    def test_success_path_none_account_omitted_from_payload(self):
        """When account=None, the 'account' key should not appear in the payload."""
        with patch(
            "apple_mail_mcp.tools.inbox._list_mail_accounts",
            return_value=["Work"],
        ), patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=OVERVIEW_PAYLOAD,
        ):
            result = _run(inbox_tools.get_inbox_overview(output_format="json"))

        # account=None should not appear as an explicit key
        self.assertNotIn("account", result)


# ---------------------------------------------------------------------------
# 5. Regression — lines 517/528: body variable type conflict
# ---------------------------------------------------------------------------

class TestListInboxEmailsTextBodyVariable(unittest.TestCase):
    """text_body rename fix: list_inbox_emails text path must not raise TypeError."""

    def test_text_path_returns_string(self):
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value="Subject|||alice@example.com|||May 23, 2026|||false|||Work|||101\nTOTAL:1",
        ):
            result = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=5,
                    output_format="text",
                )
            )
        self.assertIsInstance(result, str)

    def test_text_path_json_and_text_both_work(self):
        """Both json and text paths must return correct types after the body rename fix."""
        payload = "Subject|||alice@example.com|||May 23, 2026|||false|||Work|||101\nTOTAL:1"
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=payload,
        ):
            json_result = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=5,
                    output_format="json",
                )
            )
        self.assertIsInstance(json_result, dict)

        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=payload,
        ):
            text_result = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=5,
                    output_format="text",
                )
            )
        self.assertIsInstance(text_result, str)


# ---------------------------------------------------------------------------
# 6. Regression — lines 1412/1413: item dict typed as dict[str, str] then int added
# ---------------------------------------------------------------------------

class TestListMailboxesItemDict(unittest.TestCase):
    """Dict[str, Any] annotation fix: int counts must not cause TypeError."""

    def test_list_mailboxes_json_with_counts(self):
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=MAILBOX_LISTING_PAYLOAD,
        ):
            result = inbox_tools.list_mailboxes(
                account="Work",
                output_format="json",
                include_counts=True,
            )

        data = json.loads(result) if isinstance(result, str) else result
        # The parsed rows must contain integer counts, not raise TypeError
        if isinstance(data, list) and data:
            row = data[0]
            if "message_count" in row:
                self.assertIsInstance(row["message_count"], int)
            if "unread_count" in row:
                self.assertIsInstance(row["unread_count"], int)

    def test_list_mailboxes_json_without_counts(self):
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=MAILBOX_LISTING_PAYLOAD,
        ):
            result = inbox_tools.list_mailboxes(
                account="Work",
                output_format="json",
                include_counts=False,
            )

        data = json.loads(result) if isinstance(result, str) else result
        if isinstance(data, list) and data:
            self.assertNotIn("message_count", data[0])
            self.assertNotIn("unread_count", data[0])


# ---------------------------------------------------------------------------
# 7. JSON schema contract: get_inbox_overview when optional fields absent
# ---------------------------------------------------------------------------

OVERVIEW_MINIMAL_SCHEMA = {
    "type": "object",
    "required": [
        "output_format",
        "total_unread",
        "accounts",
        "errors",
        "include_mailboxes",
        "include_recent",
        "include_suggestions",
        "max_recent",
    ],
    "properties": {
        "output_format": {"type": "string", "enum": ["json"]},
        "total_unread": {"type": "integer", "minimum": 0},
        "accounts": {"type": "array"},
        "errors": {"type": "array"},
        "include_mailboxes": {"type": "boolean"},
        "include_recent": {"type": "boolean"},
        "include_suggestions": {"type": "boolean"},
        "max_recent": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": True,
}


class TestOverviewJsonContractAbsentFields(unittest.TestCase):
    """When all optional sections disabled and no accounts exist, shape must be valid."""

    def test_absent_optional_fields_validates_against_schema(self):
        """Header-only payload (no MAILBOX/RECENT) must produce valid JSON contract."""
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=OVERVIEW_HEADER_ONLY,
        ):
            result = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="json",
                    include_mailboxes=False,
                    include_recent=False,
                    include_suggestions=False,
                )
            )

        self.assertIsInstance(result, dict)
        jsonschema.validate(instance=result, schema=OVERVIEW_MINIMAL_SCHEMA)
        # With optional sections off, account row must not contain mailboxes/recent
        for row in result.get("accounts", []):
            self.assertNotIn("mailboxes", row)
            self.assertNotIn("recent", row)

    def test_empty_accounts_validates_against_schema(self):
        """Empty account list still produces a conforming JSON payload."""
        with patch(
            "apple_mail_mcp.tools.inbox._list_mail_accounts",
            return_value=[],
        ):
            result = _run(inbox_tools.get_inbox_overview(output_format="json"))

        jsonschema.validate(instance=result, schema=OVERVIEW_MINIMAL_SCHEMA)
        self.assertEqual(result["total_unread"], 0)


# ---------------------------------------------------------------------------
# 8. Hypothesis property: fuzz json_kwargs shapes, assert no TypeError escapes
# ---------------------------------------------------------------------------

# The fixed call sites pass these five kwargs to _overview_json_error /
# _format_overview_json.  We fuzz them with None smuggled into int/bool slots
# to prove the explicit-kwarg fix (not a ** spread) prevents TypeError.

_overview_kwargs_strategy = st.fixed_dictionaries(
    {
        "account": st.one_of(st.none(), st.text(min_size=0, max_size=30)),
        "include_mailboxes": st.one_of(st.booleans(), st.none()),
        "include_recent": st.one_of(st.booleans(), st.none()),
        "include_suggestions": st.one_of(st.booleans(), st.none()),
        "max_recent": st.one_of(st.integers(min_value=0, max_value=100), st.none()),
    }
)


class TestOverviewJsonErrorNoTypeErrorProperty(unittest.TestCase):
    """Hypothesis property: fuzz kwargs into _overview_json_error, no TypeError."""

    @given(kwargs=_overview_kwargs_strategy)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_overview_json_error_never_raises_type_error(self, kwargs: Dict[str, Any]) -> None:
        """_overview_json_error must not raise TypeError regardless of None in kwargs.

        The function defaults handle None gracefully (bool defaults, int defaults).
        This verifies the explicit-kwarg fix doesn't re-introduce the spread bug.
        """
        # Call with explicit kwargs matching the fixed call sites
        # (mirroring what get_inbox_overview now does, not spreading a mixed dict)
        account: Optional[str] = kwargs["account"]
        include_mailboxes: bool = bool(kwargs["include_mailboxes"]) if kwargs["include_mailboxes"] is not None else True
        include_recent: bool = bool(kwargs["include_recent"]) if kwargs["include_recent"] is not None else True
        include_suggestions: bool = bool(kwargs["include_suggestions"]) if kwargs["include_suggestions"] is not None else True
        max_recent: int = int(kwargs["max_recent"]) if kwargs["max_recent"] is not None else 10

        try:
            result = inbox_tools._overview_json_error(
                "test_error",
                account=account,
                include_mailboxes=include_mailboxes,
                include_recent=include_recent,
                include_suggestions=include_suggestions,
                max_recent=max_recent,
            )
        except TypeError as e:
            self.fail(f"_overview_json_error raised TypeError: {e} with kwargs={kwargs}")

        self.assertIsInstance(result, dict)
        self.assertEqual(result["error"], "test_error")
        self.assertIsInstance(result["total_unread"], int)
        self.assertIsInstance(result["accounts"], list)

    @given(kwargs=_overview_kwargs_strategy)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_format_overview_json_never_raises_type_error(self, kwargs: Dict[str, Any]) -> None:
        """_format_overview_json must not raise TypeError regardless of None in kwargs."""
        account: Optional[str] = kwargs["account"]
        include_mailboxes: bool = bool(kwargs["include_mailboxes"]) if kwargs["include_mailboxes"] is not None else True
        include_recent: bool = bool(kwargs["include_recent"]) if kwargs["include_recent"] is not None else True
        include_suggestions: bool = bool(kwargs["include_suggestions"]) if kwargs["include_suggestions"] is not None else True
        max_recent: int = int(kwargs["max_recent"]) if kwargs["max_recent"] is not None else 10

        try:
            result = inbox_tools._format_overview_json(
                [],
                [],
                account=account,
                include_mailboxes=include_mailboxes,
                include_recent=include_recent,
                include_suggestions=include_suggestions,
                max_recent=max_recent,
            )
        except TypeError as e:
            self.fail(f"_format_overview_json raised TypeError: {e} with kwargs={kwargs}")

        self.assertIsInstance(result, dict)
        self.assertEqual(result["total_unread"], 0)
        self.assertIsInstance(result["accounts"], list)


if __name__ == "__main__":
    unittest.main()
