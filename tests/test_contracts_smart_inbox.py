"""JSON schema contract tests for smart_inbox MCP tools.

Tools covered:
- get_awaiting_reply (output_format="json") — success + error shapes

The schemas here serve as living documentation of the JSON contract.
Rename a key → test fails before deployment.
"""

import json
import unittest
from unittest.mock import patch

import jsonschema

from apple_mail_mcp.tools import smart_inbox as smart_inbox_tools


# ---------------------------------------------------------------------------
# Canned AppleScript payloads
# ---------------------------------------------------------------------------

# Sent mailbox rows: SENT|||mail_app_id|||internet_message_id|||subject|||recipient|||date
SENT_PAYLOAD = "\n".join([
    "SENT|||201|||<sent1@example.com>|||Proposal for Q3|||client@acme.com|||May 20, 2026 at 10:00 AM",
    "SENT|||202|||<sent2@example.com>|||Invoice follow-up|||billing@partner.com|||May 18, 2026 at 3:00 PM",
    "SENT|||203|||<sent3@example.com>|||Meeting request|||noreply@calendar.com|||May 17, 2026 at 9:00 AM",
])

# Inbox replied-id rows (no replies in inbox for the above sent messages)
INBOX_HEADER_PAYLOAD = ""

# Inbox with a reply to the first sent message
INBOX_HEADER_WITH_REPLY = "INBOXHDR|||in-reply-to|||<sent1@example.com>"


# ---------------------------------------------------------------------------
# JSON Schemas
# ---------------------------------------------------------------------------

AWAITING_REPLY_SUCCESS_SCHEMA = {
    "type": "object",
    "required": ["account", "days_back", "max_results", "awaiting", "errors"],
    "properties": {
        "account": {"type": "string"},
        "days_back": {"type": "integer", "minimum": 0},
        "max_results": {"type": "integer", "minimum": 1},
        "awaiting": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["subject", "recipient", "sent_at", "message_id", "mail_app_id"],
                "properties": {
                    "subject": {"type": "string"},
                    "recipient": {"type": "string"},
                    "sent_at": {"type": "string"},
                    "message_id": {"type": "string"},
                    "mail_app_id": {"type": "string"},
                },
            },
        },
        "errors": {"type": "array"},
    },
    "additionalProperties": True,
}

AWAITING_REPLY_ERROR_SCHEMA = {
    "type": "object",
    "required": ["errors", "awaiting"],
    "properties": {
        "errors": {"type": "array"},
        "awaiting": {"type": "array", "maxItems": 0},
        "error": {"type": "string"},
    },
    "additionalProperties": True,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class GetAwaitingReplyContractTests(unittest.TestCase):
    """Schema contract tests for get_awaiting_reply output_format='json'."""

    def _run_with_payloads(
        self,
        sent_payload: str = SENT_PAYLOAD,
        inbox_payload: str = INBOX_HEADER_PAYLOAD,
        days_back: int = 7,
        max_results: int = 20,
        exclude_noreply: bool = True,
    ) -> dict:
        call_count = [0]

        def fake_run(script, timeout=120):
            call_count[0] += 1
            # First call is inbox scan, second is sent scan.
            if call_count[0] == 1:
                return inbox_payload
            return sent_payload

        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            side_effect=fake_run,
        ):
            return smart_inbox_tools.get_awaiting_reply(
                account="Work",
                days_back=days_back,
                max_results=max_results,
                exclude_noreply=exclude_noreply,
                output_format="json",
            )

    def test_success_shape_matches_schema(self):
        """Success response must validate against the documented JSON schema."""
        result = self._run_with_payloads()
        self.assertIsInstance(result, dict)
        jsonschema.validate(instance=result, schema=AWAITING_REPLY_SUCCESS_SCHEMA)

    def test_awaiting_items_have_required_fields(self):
        """Every item in awaiting[] must have the five required stable keys."""
        result = self._run_with_payloads(exclude_noreply=False)
        required = {"subject", "recipient", "sent_at", "message_id", "mail_app_id"}
        for item in result.get("awaiting", []):
            missing = required - item.keys()
            self.assertEqual(missing, set(), f"awaiting item missing {missing}: {item}")

    def test_noreply_addresses_excluded_by_default(self):
        """With exclude_noreply=True, no-reply addresses must be filtered out."""
        result = self._run_with_payloads(exclude_noreply=True)
        for item in result.get("awaiting", []):
            recipient = item.get("recipient", "")
            self.assertNotIn("noreply", recipient.lower())

    def test_already_replied_message_excluded(self):
        """A message whose ID appears in inbox In-Reply-To must be excluded from awaiting."""
        result = self._run_with_payloads(
            inbox_payload=INBOX_HEADER_WITH_REPLY,
            exclude_noreply=False,
        )
        message_ids_in_awaiting = [item["message_id"] for item in result.get("awaiting", [])]
        self.assertNotIn("<sent1@example.com>", message_ids_in_awaiting)

    def test_errors_is_always_a_list(self):
        """The errors key must always be a list even when empty."""
        result = self._run_with_payloads()
        self.assertIsInstance(result.get("errors"), list)

    def test_account_not_found_shape_matches_error_schema(self):
        """account_not_found error must match the documented error schema."""
        result = smart_inbox_tools.get_awaiting_reply(
            account="Missing",
            output_format="json",
        )
        self.assertIsInstance(result, dict)
        jsonschema.validate(instance=result, schema=AWAITING_REPLY_ERROR_SCHEMA)
        self.assertIsInstance(result.get("awaiting"), list)

    def test_awaiting_count_respects_max_results(self):
        """The returned awaiting list must not exceed max_results."""
        result = self._run_with_payloads(max_results=1, exclude_noreply=False)
        self.assertLessEqual(len(result.get("awaiting", [])), 1)

    def test_empty_sent_mailbox_returns_empty_awaiting(self):
        """An empty Sent mailbox must yield an empty awaiting list, not an error."""
        result = self._run_with_payloads(sent_payload="", inbox_payload="")
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("awaiting"), [])
        self.assertIsInstance(result.get("errors"), list)

    def test_days_back_field_preserved_in_response(self):
        """days_back in response must match the requested days_back."""
        result = self._run_with_payloads(days_back=14)
        self.assertEqual(result.get("days_back"), 14)

    def test_max_results_field_preserved_in_response(self):
        """max_results in response must match the requested max_results."""
        result = self._run_with_payloads(max_results=5, exclude_noreply=False)
        self.assertEqual(result.get("max_results"), 5)


if __name__ == "__main__":
    unittest.main()
