"""JSON schema contract tests for inbox-area MCP tools.

Each test feeds a canned AppleScript-output string through the tool and
validates the response against a JSON Schema that documents the stable
wire contract. When a field is renamed or removed, the test fails loudly
before it reaches Claude Desktop / Claude Code.

Tools covered:
- get_inbox_overview (output_format="json") — success + error shapes
- list_inbox_emails (output_format="json") — success + error shapes

Uses the _ScriptCapture pattern from test_modernization_3_1_5.py.
"""

import asyncio
import json
import unittest
from unittest.mock import patch

import jsonschema

from apple_mail_mcp.tools import inbox as inbox_tools


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Shared canned payloads
# ---------------------------------------------------------------------------

# get_inbox_overview: HEADER|||account|||unread|||total, MAILBOX|||, RECENT|||
OVERVIEW_PAYLOAD = "\n".join([
    "HEADER|||Work|||3|||15",
    "MAILBOX|||INBOX|||3",
    "RECENT|||Weekly digest|||newsletter@example.com|||Friday, May 23, 2026 at 9:00:00 AM|||false",
    "RECENT|||Meeting notes|||alice@example.com|||Thursday, May 22, 2026 at 2:30:00 PM|||true",
    "RECENT|||Invoice #1234|||billing@example.com|||Wednesday, May 21, 2026 at 11:00:00 AM|||false",
])

# list_inbox_emails JSON script output: pipe-delimited rows
# Schema: subject|||sender|||date|||read|||account|||mail_app_id[|||internet_message_id]
INBOX_EMAILS_PAYLOAD = "\n".join([
    "Project update|||alice@example.com|||May 23, 2026|||false|||Work|||101|||<msg1@example.com>",
    "Re: Budget|||bob@example.com|||May 22, 2026|||true|||Work|||102|||<msg2@example.com>",
    "Newsletter|||no-reply@news.com|||May 21, 2026|||false|||Work|||103|||",
    "TOTAL:3",
])

# ---------------------------------------------------------------------------
# JSON Schemas
# ---------------------------------------------------------------------------

# Schema for get_inbox_overview success (output_format="json")
INBOX_OVERVIEW_SUCCESS_SCHEMA = {
    "type": "object",
    "required": [
        "output_format",
        "account",
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
        "account": {"type": ["string", "null"]},
        "total_unread": {"type": "integer", "minimum": 0},
        "accounts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["account", "unread", "total"],
                "properties": {
                    "account": {"type": "string"},
                    "unread": {"type": "integer", "minimum": 0},
                    "total": {"type": "integer", "minimum": 0},
                },
            },
        },
        "errors": {"type": "array"},
        "include_mailboxes": {"type": "boolean"},
        "include_recent": {"type": "boolean"},
        "include_suggestions": {"type": "boolean"},
        "max_recent": {"type": "integer", "minimum": 0},
        "suggestions": {"type": "array"},
    },
    "additionalProperties": True,
}

INBOX_OVERVIEW_ACCOUNT_ROW_SCHEMA = {
    "type": "object",
    "required": ["account", "unread", "total"],
    "properties": {
        "account": {"type": "string"},
        "unread": {"type": "integer", "minimum": 0},
        "total": {"type": "integer", "minimum": 0},
        "mailboxes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                    "unread": {"type": "integer", "minimum": 0},
                },
            },
        },
        "recent": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["subject", "sender", "date", "is_read"],
                "properties": {
                    "subject": {"type": "string"},
                    "sender": {"type": "string"},
                    "date": {"type": "string"},
                    "is_read": {"type": "boolean"},
                },
            },
        },
    },
}

INBOX_OVERVIEW_ERROR_SCHEMA = {
    "type": "object",
    "required": ["error", "account", "accounts", "errors"],
    "properties": {
        "error": {"type": "string"},
        "account": {"type": "string"},
        "accounts": {"type": "array", "maxItems": 0},
        "errors": {"type": "array"},
    },
    "additionalProperties": True,
}

# Schema for list_inbox_emails success (output_format="json")
LIST_INBOX_EMAILS_SUCCESS_SCHEMA = {
    "type": "object",
    "required": ["emails", "errors"],
    "properties": {
        "emails": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["subject", "sender", "date", "is_read", "account", "message_id"],
                "properties": {
                    "subject": {"type": "string"},
                    "sender": {"type": "string"},
                    "date": {"type": "string"},
                    "is_read": {"type": "boolean"},
                    "account": {"type": "string"},
                    "message_id": {"type": "string"},
                    "internet_message_id": {"type": "string"},
                    "content_preview": {"type": "string"},
                    "already_replied": {"type": "boolean"},
                },
            },
        },
        "errors": {"type": "array"},
    },
    "additionalProperties": True,
}

LIST_INBOX_EMAILS_ACCOUNT_NOT_FOUND_SCHEMA = {
    "type": "object",
    "required": ["error", "account", "available_accounts", "emails"],
    "properties": {
        "error": {"type": "string", "enum": ["account_not_found"]},
        "account": {"type": "string"},
        "available_accounts": {"type": "array"},
        "emails": {"type": "array", "maxItems": 0},
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class GetInboxOverviewContractTests(unittest.TestCase):
    """Schema contract tests for get_inbox_overview output_format='json'."""

    def test_success_shape_matches_schema(self):
        """Success response must validate against the documented JSON schema."""
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
                    max_recent=3,
                )
            )

        self.assertIsInstance(result, dict)
        jsonschema.validate(instance=result, schema=INBOX_OVERVIEW_SUCCESS_SCHEMA)

    def test_account_row_shape_matches_schema(self):
        """Each account row in accounts[] must match the account-row sub-schema."""
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
                )
            )

        for row in result.get("accounts", []):
            jsonschema.validate(instance=row, schema=INBOX_OVERVIEW_ACCOUNT_ROW_SCHEMA)

    def test_account_not_found_shape_matches_schema(self):
        """account_not_found error must match the documented error schema."""
        result = _run(
            inbox_tools.get_inbox_overview(
                account="Missing",
                output_format="json",
            )
        )
        self.assertIsInstance(result, dict)
        jsonschema.validate(instance=result, schema=INBOX_OVERVIEW_ERROR_SCHEMA)
        self.assertEqual(result["error"], "account_not_found")

    def test_total_unread_matches_sum_of_account_rows(self):
        """total_unread must equal the sum of account[].unread values."""
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=OVERVIEW_PAYLOAD,
        ):
            result = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="json",
                )
            )

        total = result.get("total_unread", 0)
        sum_from_rows = sum(row.get("unread", 0) for row in result.get("accounts", []))
        self.assertEqual(total, sum_from_rows)

    def test_errors_is_always_a_list(self):
        """The errors key must always be a list even when empty."""
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=OVERVIEW_PAYLOAD,
        ):
            result = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="json",
                )
            )

        self.assertIsInstance(result.get("errors"), list)

    def test_recent_items_have_required_fields(self):
        """Every item in accounts[].recent must have subject, sender, date, is_read."""
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=OVERVIEW_PAYLOAD,
        ):
            result = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="json",
                    include_recent=True,
                    max_recent=10,
                )
            )

        for row in result.get("accounts", []):
            for item in row.get("recent", []):
                self.assertIn("subject", item)
                self.assertIn("sender", item)
                self.assertIn("date", item)
                self.assertIn("is_read", item)
                self.assertIsInstance(item["is_read"], bool)


class ListInboxEmailsContractTests(unittest.TestCase):
    """Schema contract tests for list_inbox_emails output_format='json'."""

    def test_success_shape_matches_schema(self):
        """Success response must validate against the documented JSON schema."""
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=INBOX_EMAILS_PAYLOAD,
        ):
            result = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=10,
                    output_format="json",
                )
            )

        self.assertIsInstance(result, dict)
        jsonschema.validate(instance=result, schema=LIST_INBOX_EMAILS_SUCCESS_SCHEMA)

    def test_emails_is_always_a_list(self):
        """The emails key must always be a list."""
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value="",
        ):
            result = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=10,
                    output_format="json",
                )
            )

        self.assertIsInstance(result, dict)
        self.assertIsInstance(result.get("emails"), list)

    def test_account_not_found_shape_matches_schema(self):
        """account_not_found error must match the documented error schema."""
        result = _run(
            inbox_tools.list_inbox_emails(
                account="Missing",
                max_emails=10,
                output_format="json",
            )
        )

        self.assertIsInstance(result, dict)
        jsonschema.validate(instance=result, schema=LIST_INBOX_EMAILS_ACCOUNT_NOT_FOUND_SCHEMA)

    def test_each_email_row_has_required_fields(self):
        """Every email in emails[] must have the required stable keys."""
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=INBOX_EMAILS_PAYLOAD,
        ):
            result = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=10,
                    output_format="json",
                )
            )

        required_fields = {"subject", "sender", "date", "is_read", "account", "message_id"}
        for email in result.get("emails", []):
            missing = required_fields - email.keys()
            self.assertEqual(
                missing, set(), f"Email row missing fields {missing}: {email}"
            )
            self.assertIsInstance(email["is_read"], bool)

    def test_errors_key_is_list(self):
        """The errors key must be a list."""
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=INBOX_EMAILS_PAYLOAD,
        ):
            result = _run(
                inbox_tools.list_inbox_emails(
                    account="Work",
                    max_emails=10,
                    output_format="json",
                )
            )

        self.assertIsInstance(result.get("errors"), list)

    def test_invalid_output_format_returns_string_error(self):
        """An invalid output_format must return an error string, not a dict."""
        result = _run(
            inbox_tools.list_inbox_emails(
                account="Work",
                max_emails=10,
                output_format="xml",
            )
        )
        self.assertIsInstance(result, str)
        self.assertIn("Error", result)


if __name__ == "__main__":
    unittest.main()
