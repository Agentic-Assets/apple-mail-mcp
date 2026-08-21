"""``get_statistics`` must not ship a derived read count as a bare number.

The payload's own ``unread_count_note`` says "do not derive a read count from
it" about Mail's cached ``unread count`` aggregate, and the same payload then
shipped exactly that derivation as ``statistics.read`` / ``read_percent`` with
no marking of its own. The cached count has been measured at 3,236 against a
per-message truth of 10,016 on a 25,012-message Exchange Inbox, so the derived
read was over-reported by 6,780 while looking as authoritative as
``total_emails`` beside it.

These tests pin the fix in both directions: the derived field carries its own
provenance at the payload envelope AND an inline flag inside ``statistics``, so
a consumer reading only ``payload["statistics"]["read"]`` still cannot mistake
it for a measurement. The genuinely measured ``sender_stats`` scope must stay
free of read-derivation labelling.
"""

from __future__ import annotations

import unittest

from apple_mail_mcp.tools.analytics.statistics_parsing import (
    _build_account_overview_report,
    _format_statistics_json,
    _parse_statistics_text,
)
from apple_mail_mcp.tools.unread_provenance import (
    READ_COUNT_NOTE,
    READ_DERIVED_FLAG_KEY,
    READ_SOURCE_DERIVED,
    UNREAD_SOURCE_MEASURED,
    derived_read_disclosure,
    derived_read_text_label,
    unread_count_text_label,
)

OVERVIEW_ROWS = "\n".join(
    [
        "MBOX|||INBOX|||25012|||3236",
        "ROW|||INBOX|||0|||0|||sender@example.com",
        "ROW|||INBOX|||1|||1|||other@example.com",
    ]
)


class DerivedReadDisclosureTests(unittest.TestCase):
    def test_disclosure_marks_the_value_as_derived_and_unmeasured(self):
        disclosure = derived_read_disclosure()
        self.assertEqual(disclosure["read_count_source"], READ_SOURCE_DERIVED)
        self.assertFalse(disclosure["read_count_measured"])
        self.assertEqual(disclosure["read_count_note"], READ_COUNT_NOTE)

    def test_note_can_be_suppressed_for_repeated_blocks(self):
        self.assertNotIn("read_count_note", derived_read_disclosure(include_note=False))

    def test_read_label_is_distinct_from_the_cached_unread_label(self):
        self.assertNotEqual(derived_read_text_label(), unread_count_text_label())
        self.assertNotEqual(derived_read_text_label(), unread_count_text_label(suspect=True))

    def test_note_never_forges_a_parseable_metric_line(self):
        """The note is emitted into the same text the metric regexes scan."""
        self.assertNotIn("Read: ", READ_COUNT_NOTE)
        self.assertNotIn("Unread: ", READ_COUNT_NOTE)
        self.assertNotIn("Total Emails: ", READ_COUNT_NOTE)


class AccountOverviewPayloadTests(unittest.TestCase):
    def _payload(self) -> dict:
        report = _build_account_overview_report(OVERVIEW_ROWS, "Work")
        statistics = _parse_statistics_text("account_overview", report)
        return _format_statistics_json(
            scope="account_overview",
            account="Work",
            days_back=30,
            statistics=statistics,
        )

    def test_derived_read_is_still_reported(self):
        """Provenance is added; the number itself is not withdrawn."""
        self.assertEqual(self._payload()["statistics"]["read"], 25012 - 3236)

    def test_payload_envelope_carries_read_provenance(self):
        payload = self._payload()
        self.assertEqual(payload["read_count_source"], READ_SOURCE_DERIVED)
        self.assertFalse(payload["read_count_measured"])
        self.assertIn("read_count_note", payload)

    def test_inline_flag_travels_with_the_value(self):
        """A consumer that never reads the envelope must still see the caveat."""
        statistics = self._payload()["statistics"]
        self.assertIn("read", statistics)
        self.assertTrue(statistics[READ_DERIVED_FLAG_KEY])

    def test_read_percent_lives_beside_the_same_flag(self):
        statistics = self._payload()["statistics"]
        self.assertIn("read_percent", statistics)
        self.assertTrue(statistics[READ_DERIVED_FLAG_KEY])

    def test_unread_provenance_is_unchanged(self):
        payload = self._payload()
        self.assertEqual(payload["unread_count_source"], "mail_cached_aggregate")
        self.assertFalse(payload["unread_count_measured"])

    def test_formatting_does_not_mutate_the_caller_statistics_dict(self):
        statistics = {"total_emails": 10, "unread": 4, "read": 6}
        _format_statistics_json(
            scope="account_overview",
            account="Work",
            days_back=30,
            statistics=statistics,
        )
        self.assertNotIn(READ_DERIVED_FLAG_KEY, statistics)


class MailboxBreakdownPayloadTests(unittest.TestCase):
    def _payload(self) -> dict:
        statistics = _parse_statistics_text(
            "mailbox_breakdown",
            "Total messages: 25012\nUnread: 3236 [Mail cached, unverified]\n"
            f"Read: 21776{derived_read_text_label()}\n",
        )
        return _format_statistics_json(
            scope="mailbox_breakdown",
            account="Work",
            days_back=0,
            statistics=statistics,
            mailbox="INBOX",
        )

    def test_breakdown_read_is_parsed_despite_the_new_label(self):
        self.assertEqual(self._payload()["statistics"]["read"], 21776)

    def test_breakdown_carries_read_provenance_and_inline_flag(self):
        payload = self._payload()
        self.assertEqual(payload["read_count_source"], READ_SOURCE_DERIVED)
        self.assertTrue(payload["statistics"][READ_DERIVED_FLAG_KEY])


class SenderStatsPayloadTests(unittest.TestCase):
    def test_measured_scope_gets_no_read_derivation_labelling(self):
        payload = _format_statistics_json(
            scope="sender_stats",
            account="Work",
            days_back=30,
            statistics={"total_emails": 12, "unread": 3, "with_attachments": 1},
            sender="sender@example.com",
        )
        self.assertEqual(payload["unread_count_source"], UNREAD_SOURCE_MEASURED)
        self.assertNotIn("read_count_source", payload)
        self.assertNotIn(READ_DERIVED_FLAG_KEY, payload["statistics"])


class AccountOverviewTextTests(unittest.TestCase):
    def _report(self, rows: str = OVERVIEW_ROWS) -> str:
        return _build_account_overview_report(rows, "Work")

    def test_read_line_carries_the_derived_label(self):
        read_line = next(line for line in self._report().splitlines() if line.startswith("Read: "))
        self.assertIn(derived_read_text_label().strip(), read_line)

    def test_read_line_does_not_reuse_the_cached_unread_label(self):
        read_line = next(line for line in self._report().splitlines() if line.startswith("Read: "))
        self.assertNotIn(unread_count_text_label().strip(), read_line)

    def test_report_footer_explains_the_derivation(self):
        self.assertIn(READ_COUNT_NOTE, self._report())

    def test_zero_message_report_still_labels_read(self):
        report = self._report("MBOX|||INBOX|||0|||0")
        read_line = next(line for line in report.splitlines() if line.startswith("Read: "))
        self.assertIn(derived_read_text_label().strip(), read_line)

    def test_labels_do_not_break_the_metric_parsers(self):
        statistics = _parse_statistics_text("account_overview", self._report())
        self.assertEqual(statistics["total_emails"], 25012)
        self.assertEqual(statistics["unread"], 3236)
        self.assertEqual(statistics["read"], 21776)


if __name__ == "__main__":
    unittest.main()
