"""JSON schema contract tests for search MCP tools.

Tools covered:
- get_email_thread — text return shape, JSON return shape + error codes
- search_emails (output_format="json") — success + error shapes

The JSON Schema assertions here serve as the living wire contract.
"""

import asyncio
import json
import unittest
from unittest.mock import patch

import jsonschema
from apple_mail_mcp.tools import search as search_tools


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Canned payloads
# ---------------------------------------------------------------------------

# search_emails returns pipe-delimited records:
# mail_app_id|||internet_message_id|||subject|||sender|||mailbox|||account|||is_read|||received_date[|||content_preview]
SEARCH_PAYLOAD = "\n".join(
    [
        "301|||<thread1@example.com>|||Re: Project Update|||alice@example.com|||INBOX|||Work|||false|||2026-05-20T10:00:00",
        "302|||<thread1@example.com>|||Project Update|||bob@example.com|||INBOX|||Work|||true|||2026-05-19T09:00:00",
        "303|||<thread2@example.com>|||Kick-off meeting|||carol@example.com|||Archive|||Work|||true|||2026-05-18T08:00:00",
    ]
)

# get_email_thread uses the same parser under the hood.
THREAD_PAYLOAD = "\n".join(
    [
        "401|||<thread3@example.com>|||Re: Budget Review|||alice@example.com|||INBOX|||Work|||false|||2026-05-22T14:00:00",
        "402|||<thread3@example.com>|||Budget Review|||finance@example.com|||INBOX|||Work|||true|||2026-05-21T10:00:00",
    ]
)

THREAD_JSON_PAYLOAD = "\n".join(
    [
        "THREAD_STRATEGY|||subject",
        "401|||<reply@example.com>|||Re: Budget Review|||alice@example.com|||INBOX|||Work|||false|||2026-05-22T14:00:00||||||||||||<root@example.com>|||<root@example.com>|||",
        "402|||<root@example.com>|||Renamed Budget Decision|||finance@example.com|||Sent|||Work|||true|||2026-05-21T10:00:00|||||||||||||||",
    ]
)


# ---------------------------------------------------------------------------
# JSON Schemas
# ---------------------------------------------------------------------------

# search_emails json mode returns a JSON-encoded string (not a dict).
SEARCH_EMAILS_JSON_SCHEMA = {
    "type": "object",
    "required": ["items", "offset", "limit", "returned", "has_more", "sort"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "message_id",
                    "internet_message_id",
                    "subject",
                    "sender",
                    "mailbox",
                    "account",
                    "is_read",
                    "received_date",
                ],
                "properties": {
                    "message_id": {"type": "string"},
                    "internet_message_id": {"type": "string"},
                    "subject": {"type": "string"},
                    "sender": {"type": "string"},
                    "mailbox": {"type": "string"},
                    "account": {"type": "string"},
                    "is_read": {"type": "boolean"},
                    "received_date": {"type": "string"},
                    "mail_link": {"type": "string"},
                    "content_preview": {"type": "string"},
                    "already_replied": {"type": "boolean"},
                },
            },
        },
        "offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1},
        "returned": {"type": "integer", "minimum": 0},
        "has_more": {"type": "boolean"},
        "next_offset": {"type": ["integer", "null"]},
        "sort": {"type": "string"},
        "recent_days_applied": {"type": "number"},
    },
    "additionalProperties": True,
}

SEARCH_EMAILS_ACCOUNT_NOT_FOUND_SCHEMA = {
    "type": "object",
    "required": ["error", "account"],
    "properties": {
        "error": {"type": "string", "enum": ["account_not_found"]},
        "account": {"type": "string"},
        "available_accounts": {"type": "array"},
    },
    "additionalProperties": True,
}

GET_EMAIL_THREAD_JSON_SCHEMA = {
    "type": "object",
    "required": [
        "items",
        "returned",
        "account",
        "mailbox",
        "mailboxes",
        "subject_keyword",
        "strategy",
        "selection_strategy",
        "subject_fallback_used",
        "include_preview",
        "recent_days_applied",
        "max_messages",
    ],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "message_id",
                    "internet_message_id",
                    "subject",
                    "sender",
                    "mailbox",
                    "account",
                    "is_read",
                    "received_date",
                ],
                "properties": {
                    "message_id": {"type": "string"},
                    "internet_message_id": {"type": "string"},
                    "subject": {"type": "string"},
                    "sender": {"type": "string"},
                    "mailbox": {"type": "string"},
                    "account": {"type": "string"},
                    "is_read": {"type": "boolean"},
                    "received_date": {"type": "string"},
                    "in_reply_to": {"type": "string"},
                    "references": {"type": "string"},
                    "content_preview": {"type": "string"},
                    "mail_link": {"type": "string"},
                },
            },
        },
        "returned": {"type": "integer", "minimum": 0},
        "account": {"type": "string"},
        "mailbox": {"type": "string"},
        "mailboxes": {"type": "array", "items": {"type": "string"}},
        "subject_keyword": {"type": "string"},
        "strategy": {"type": "string", "enum": ["subject", "header_first"]},
        "selection_strategy": {"type": "string", "enum": ["subject", "header", "subject_fallback"]},
        "subject_fallback_used": {"type": "boolean"},
        "include_preview": {"type": "boolean"},
        "recent_days_applied": {"type": "number"},
        "max_messages": {"type": "integer", "minimum": 1},
        "anchor": {"type": "object"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": True,
}


# ---------------------------------------------------------------------------
# search_emails contract tests
# ---------------------------------------------------------------------------


class SearchEmailsContractTests(unittest.TestCase):
    """Schema contract tests for search_emails output_format='json'."""

    def _run_search(self, payload: str = SEARCH_PAYLOAD, **kwargs) -> str:
        with patch(
            "apple_mail_mcp.tools.search.run_applescript",
            return_value=payload,
        ):
            return _run(
                search_tools.search_emails(
                    account="Work",
                    subject_keyword="Project",
                    output_format="json",
                    recent_days=7.0,
                    **kwargs,
                )
            )

    def test_success_shape_matches_schema(self):
        """Success JSON must validate against the documented schema."""
        raw = self._run_search()
        self.assertIsInstance(raw, str)
        parsed = json.loads(raw)
        jsonschema.validate(instance=parsed, schema=SEARCH_EMAILS_JSON_SCHEMA)

    def test_items_have_required_fields(self):
        """Every item in items[] must have the eight required stable keys."""
        raw = self._run_search()
        parsed = json.loads(raw)
        required = {
            "message_id",
            "internet_message_id",
            "subject",
            "sender",
            "mailbox",
            "account",
            "is_read",
            "received_date",
        }
        for item in parsed.get("items", []):
            missing = required - item.keys()
            self.assertEqual(missing, set(), f"Item missing fields {missing}: {item}")
            self.assertIsInstance(item["is_read"], bool)

    def test_mail_link_present_for_messages_with_internet_id(self):
        """Messages with internet_message_id must have a mail_link field."""
        raw = self._run_search()
        parsed = json.loads(raw)
        for item in parsed.get("items", []):
            if item.get("internet_message_id"):
                self.assertIn("mail_link", item, f"Missing mail_link for {item}")
                self.assertTrue(
                    item["mail_link"].startswith("message://"),
                    f"mail_link has unexpected scheme: {item['mail_link']}",
                )

    def test_returned_equals_len_items(self):
        """returned field must equal the actual length of items[]."""
        raw = self._run_search()
        parsed = json.loads(raw)
        self.assertEqual(parsed["returned"], len(parsed["items"]))

    def test_empty_payload_returns_empty_items(self):
        """An empty AppleScript output must yield items=[] without error."""
        raw = self._run_search(payload="")
        parsed = json.loads(raw)
        self.assertEqual(parsed["items"], [])
        self.assertEqual(parsed["returned"], 0)
        self.assertFalse(parsed["has_more"])

    def test_account_not_found_shape(self):
        """account_not_found must match the documented error schema."""
        raw = _run(
            search_tools.search_emails(
                account="Missing",
                subject_keyword="hello",
                output_format="json",
            )
        )
        self.assertIsInstance(raw, str)
        parsed = json.loads(raw)
        jsonschema.validate(instance=parsed, schema=SEARCH_EMAILS_ACCOUNT_NOT_FOUND_SCHEMA)


# ---------------------------------------------------------------------------
# get_email_thread contract tests
# ---------------------------------------------------------------------------


class GetEmailThreadContractTests(unittest.TestCase):
    """Contract tests for get_email_thread."""

    def test_account_not_found_returns_error_string(self):
        """Unknown account must return an 'Error:' string."""
        result = search_tools.get_email_thread(
            account="Missing",
            subject_keyword="Budget Review",
        )
        self.assertIsInstance(result, str)
        self.assertTrue(
            result.startswith("Error:") or "account_not_found" in result,
            f"Expected error response, got: {result!r}",
        )

    def test_zero_recent_days_returns_unbounded_scan_error(self):
        """recent_days=0 must return a structured UNBOUNDED_SCAN_REQUIRED error."""
        result = search_tools.get_email_thread(
            account="Work",
            subject_keyword="anything",
            recent_days=0,
        )
        self.assertIsInstance(result, str)
        self.assertIn("UNBOUNDED_SCAN_REQUIRED", result)

    def test_negative_max_messages_returns_error_string(self):
        """max_messages <= 0 must return an error string."""
        result = search_tools.get_email_thread(
            account="Work",
            subject_keyword="hello",
            max_messages=-1,
            recent_days=7,
        )
        self.assertIsInstance(result, str)
        self.assertIn("Error", result)

    def test_success_shape_is_text_string(self):
        """Successful thread retrieval must return a non-empty string."""
        with patch(
            "apple_mail_mcp.tools.search.run_applescript",
            return_value=THREAD_PAYLOAD,
        ):
            result = search_tools.get_email_thread(
                account="Work",
                subject_keyword="Budget Review",
                max_messages=10,
                recent_days=7,
            )

        self.assertIsInstance(result, str)
        # Must contain the subject keywords from the payload
        self.assertIn("Budget Review", result)

    def test_json_success_shape_matches_schema(self):
        """JSON output exposes stable ids, headers, strategy, and preview state."""
        with patch(
            "apple_mail_mcp.tools.search.run_applescript",
            return_value=THREAD_JSON_PAYLOAD,
        ):
            result = search_tools.get_email_thread(
                account="Work",
                subject_keyword="Budget Review",
                max_messages=10,
                recent_days=7,
                include_preview=False,
                output_format="json",
            )

        parsed = json.loads(result)
        jsonschema.validate(instance=parsed, schema=GET_EMAIL_THREAD_JSON_SCHEMA)
        self.assertEqual(parsed["returned"], len(parsed["items"]))
        self.assertEqual(parsed["strategy"], "subject")
        self.assertEqual(parsed["selection_strategy"], "subject")
        self.assertFalse(parsed["subject_fallback_used"])
        self.assertFalse(parsed["include_preview"])
        self.assertEqual(parsed["items"][0]["in_reply_to"], "<root@example.com>")
        self.assertEqual(parsed["items"][0]["references"], "<root@example.com>")

    def test_unbounded_scan_json_contains_remediation(self):
        """UNBOUNDED_SCAN_REQUIRED JSON must include remediation and fallback_tool."""
        result = search_tools.get_email_thread(
            account="Work",
            subject_keyword="test",
            recent_days=0,
        )
        parsed = json.loads(result)
        self.assertIn("code", parsed)
        self.assertEqual(parsed["code"], "UNBOUNDED_SCAN_REQUIRED")
        self.assertIn("remediation", parsed)
        self.assertIn("fallback_tool", parsed["remediation"])


if __name__ == "__main__":
    unittest.main()
