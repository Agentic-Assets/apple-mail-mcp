"""Characterization tests for ``get_top_senders(group_by_domain=True)``.

The domain-extraction AppleScript branch and the ``TOP SENDER DOMAINS`` title
were previously unexercised by the suite. These tests pin the generated
AppleScript substrings and the text/JSON contract so the smart_inbox package
split can move that branch verbatim with a regression net.
"""

import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import smart_inbox as smart_inbox_tools

_DOMAIN_RAW = (
    "ROW|||example.com\n"
    "ROW|||example.com\n"
    "ROW|||vendor.io\n"
    "TOTAL|||3\n"
    "MAILBOX_COUNT|||50"
)


class _ScriptCapture:
    def __init__(self, return_value: str):
        self.return_value = return_value
        self.script = ""

    def __call__(self, script, timeout=120):
        self.script = script
        return self.return_value


class GetTopSendersGroupByDomainTests(unittest.TestCase):
    def test_domain_branch_emits_domain_extraction_applescript(self):
        cap = _ScriptCapture(return_value=_DOMAIN_RAW)
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=cap):
            result = smart_inbox_tools.get_top_senders(
                account="Work",
                days_back=7,
                top_n=5,
                group_by_domain=True,
            )

        # Domain-extraction AppleScript block must be present byte-for-byte.
        self.assertIn("-- Extract domain from sender address", cap.script)
        self.assertIn(
            "set senderKey to text (atPos + 1) thru endPos of messageSender",
            cap.script,
        )
        # Domain title label is used.
        self.assertIn("TOP SENDER DOMAINS", result)
        self.assertNotIn("TOP SENDERS\n", result)
        # Aggregation over domain ROW keys still works.
        self.assertIn("1. example.com: 2 emails", result)
        self.assertIn("2. vendor.io: 1 emails", result)

    def test_domain_branch_json_sets_group_by_domain_true(self):
        cap = _ScriptCapture(return_value=_DOMAIN_RAW)
        with patch("apple_mail_mcp.tools.smart_inbox.run_applescript", side_effect=cap):
            result = smart_inbox_tools.get_top_senders(
                account="Work",
                days_back=7,
                top_n=5,
                group_by_domain=True,
                output_format="json",
            )

        self.assertIsInstance(result, dict)
        self.assertTrue(result["group_by_domain"])
        keys = {entry["key"]: entry["count"] for entry in result["senders"]}
        self.assertEqual(keys["example.com"], 2)
        self.assertEqual(keys["vendor.io"], 1)


if __name__ == "__main__":
    unittest.main()
