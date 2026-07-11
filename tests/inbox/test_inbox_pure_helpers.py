"""Characterization tests for pure inbox helpers that cross the split boundary.

These pin the exact return values / error strings of small pure helpers that
are currently only exercised indirectly through ``list_inbox_emails``. They
guard the planned ``inbox.py`` -> ``inbox/`` package split: the helpers move
verbatim into a leaf module and must keep importing as
``apple_mail_mcp.tools.inbox.<name>``.
"""

import unittest

from apple_mail_mcp.tools import inbox as inbox_tools


class ResolveReadFilterTests(unittest.TestCase):
    def test_read_status_wins_when_provided(self):
        self.assertEqual(inbox_tools._resolve_read_filter("read", include_read=False), "read")
        self.assertEqual(inbox_tools._resolve_read_filter("unread", include_read=True), "unread")
        self.assertEqual(inbox_tools._resolve_read_filter("all", include_read=False), "all")

    def test_include_read_bool_fallback(self):
        self.assertEqual(inbox_tools._resolve_read_filter(None, include_read=True), "all")
        self.assertEqual(inbox_tools._resolve_read_filter(None, include_read=False), "unread")

    def test_invalid_read_status_raises_valueerror(self):
        with self.assertRaises(ValueError) as ctx:
            inbox_tools._resolve_read_filter("bogus", include_read=True)
        self.assertIn("read_status must be one of", str(ctx.exception))
        self.assertIn("bogus", str(ctx.exception))


class ReadFilterConditionTests(unittest.TestCase):
    def test_predicates_are_byte_exact(self):
        self.assertEqual(inbox_tools._read_filter_condition("unread"), "read status of aMessage is false")
        self.assertEqual(inbox_tools._read_filter_condition("read"), "read status of aMessage is true")
        self.assertIsNone(inbox_tools._read_filter_condition("all"))


if __name__ == "__main__":
    unittest.main()
