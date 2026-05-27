"""Tests for the ``full_inbox_export`` tool (Phase A A5).

These tests mock the AppleScript runner — they do not require Mail.app.
"""

import asyncio
import json
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import analytics as analytics_tools
from apple_mail_mcp.tools.analytics import (
    _FULL_EXPORT_FIELD_SEP,
    _FULL_EXPORT_ROW_SEP,
    _FULL_EXPORT_DEFAULT_FIELDS,
)


def _run(coro):
    return asyncio.run(coro)


def _make_batch(start_id: int, count: int) -> str:
    """Return an AppleScript-shaped batch payload with ``count`` rows.

    Uses the default field ordering: subject, sender, date_received,
    read_status, message_id.
    """
    rows = []
    for i in range(count):
        message_id = start_id + i
        rows.append(
            _FULL_EXPORT_FIELD_SEP.join(
                [
                    f"Subject {message_id}",
                    f"sender{message_id}@example.com",
                    "Friday, May 22, 2026 at 10:00:00 AM",
                    "false" if (message_id % 2) else "true",
                    str(message_id),
                ]
            )
        )
    return _FULL_EXPORT_ROW_SEP.join(rows)


class FullInboxExportTests(unittest.TestCase):
    def test_full_inbox_export_accepts_comma_separated_fields(self):
        """mcporter named flags pass --fields as a comma-separated string."""

        calls = []

        def fake_run(script, timeout=120):
            calls.append(script)
            return _FULL_EXPORT_FIELD_SEP.join(
                ["Subject 1", "sender1@example.com", "1"]
            )

        with patch(
            "apple_mail_mcp.tools.analytics.run_applescript",
            side_effect=fake_run,
        ):
            raw = _run(
                analytics_tools.full_inbox_export(
                    account="Work",
                    fields="subject,sender,message_id",
                    max_emails=1,
                    batch_size=1,
                )
            )

        self.assertEqual(
            json.loads(raw),
            [
                {
                    "subject": "Subject 1",
                    "sender": "sender1@example.com",
                    "message_id": "1",
                }
            ],
        )
        self.assertIn("set fieldValue0", calls[0])

    def test_full_inbox_export_default_params(self):
        """Three batches of 500 + 500 + 200 -> 1200 messages, JSON output."""

        # 1200 messages total, default batch_size=500 -> three calls.
        batches = [
            _make_batch(1, 500),
            _make_batch(501, 500),
            _make_batch(1001, 200),
        ]
        calls = []

        def fake_run(script, timeout=120):
            calls.append({"script": script, "timeout": timeout})
            return batches[len(calls) - 1]

        with patch(
            "apple_mail_mcp.tools.analytics.run_applescript",
            side_effect=fake_run,
        ):
            raw = _run(analytics_tools.full_inbox_export(account="Work"))

        result = json.loads(raw)
        self.assertEqual(len(result), 1200)
        # Default fields are present and typed.
        self.assertEqual(
            set(result[0].keys()), set(_FULL_EXPORT_DEFAULT_FIELDS)
        )
        self.assertEqual(result[0]["message_id"], "1")
        self.assertEqual(result[-1]["message_id"], "1200")
        self.assertIsInstance(result[0]["read_status"], bool)

        # Three AppleScript invocations — one per batch.
        self.assertEqual(len(calls), 3)
        # Each batch binds a numeric window via `set startIndex to N`
        # / `set endIndex to M` (the script uses variables in the
        # `messages ... thru ...` slice itself).
        self.assertIn("set startIndex to 1", calls[0]["script"])
        self.assertIn("set endIndex to 500", calls[0]["script"])
        self.assertIn("set startIndex to 501", calls[1]["script"])
        self.assertIn("set endIndex to 1000", calls[1]["script"])
        # Third batch is requested at full batch_size=500 because the
        # caller didn't lower ``max_emails`` — the loop terminates when
        # the AppleScript returns fewer rows than asked for (mailbox
        # exhausted mid-batch).
        self.assertIn("set startIndex to 1001", calls[2]["script"])
        self.assertIn("set endIndex to 1500", calls[2]["script"])
        self.assertIn(
            "messages startIndex thru endIndex of targetMailbox",
            calls[0]["script"],
        )

    def test_full_inbox_export_respects_max_emails(self):
        """``max_emails=100`` must clamp the batch and stop after one call."""

        calls = []

        def fake_run(script, timeout=120):
            calls.append(script)
            return _make_batch(1, 100)

        with patch(
            "apple_mail_mcp.tools.analytics.run_applescript",
            side_effect=fake_run,
        ):
            raw = _run(
                analytics_tools.full_inbox_export(
                    account="Work",
                    max_emails=100,
                )
            )

        result = json.loads(raw)
        self.assertEqual(len(result), 100)
        self.assertEqual(len(calls), 1)
        # When max_emails < batch_size, only ``max_emails`` are requested.
        self.assertIn("set startIndex to 1", calls[0])
        self.assertIn("set endIndex to 100", calls[0])

    def test_full_inbox_export_ndjson_format(self):
        """``output_format='ndjson'`` returns newline-separated JSON objects."""

        def fake_run(script, timeout=120):
            return _make_batch(1, 3)

        with patch(
            "apple_mail_mcp.tools.analytics.run_applescript",
            side_effect=fake_run,
        ):
            raw = _run(
                analytics_tools.full_inbox_export(
                    account="Work",
                    max_emails=3,
                    batch_size=3,
                    output_format="ndjson",
                )
            )

        lines = raw.split("\n")
        self.assertEqual(len(lines), 3)
        parsed = [json.loads(line) for line in lines]
        self.assertEqual([row["message_id"] for row in parsed], ["1", "2", "3"])

    def test_full_inbox_export_handles_empty_inbox(self):
        """An empty first batch yields an empty JSON list — no infinite loop."""

        calls = []

        def fake_run(script, timeout=120):
            calls.append(script)
            return ""

        with patch(
            "apple_mail_mcp.tools.analytics.run_applescript",
            side_effect=fake_run,
        ):
            raw = _run(analytics_tools.full_inbox_export(account="Work"))

        self.assertEqual(json.loads(raw), [])
        self.assertEqual(len(calls), 1)


class FullExportFieldScriptTests(unittest.TestCase):
    """The per-field AppleScript snippets must avoid inline if-then-else."""

    def test_read_status_uses_as_string_not_inline_if(self):
        snippet = analytics_tools._full_export_field_script("read_status")
        self.assertNotIn("if (", snippet)
        self.assertIn("as string", snippet)
        self.assertIn("read status of aMessage", snippet)

    def test_flagged_status_uses_as_string_not_inline_if(self):
        snippet = analytics_tools._full_export_field_script("flagged_status")
        self.assertNotIn("if (", snippet)
        self.assertIn("as string", snippet)
        self.assertIn("flagged status of aMessage", snippet)

    def test_batch_script_has_no_inline_if_for_default_fields(self):
        script = analytics_tools._full_export_batch_script(
            account="Work",
            mailbox="INBOX",
            start_index=1,
            end_index=10,
            fields=list(_FULL_EXPORT_DEFAULT_FIELDS) + ["flagged_status"],
        )
        # No inline `if ( ... ) then "..." else "..."` should remain for
        # the bool-typed field branches.
        self.assertNotIn('then "true" else "false"', script)
        self.assertNotIn("(try", script)
        self.assertIn("set fieldValue0", script)
        self.assertIn("on error\n                        set fieldValue0 to \"\"", script)
        self.assertIn("(read status of aMessage) as string", script)
        self.assertIn("(flagged status of aMessage) as string", script)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
