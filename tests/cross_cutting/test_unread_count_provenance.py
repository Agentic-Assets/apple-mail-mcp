"""AGENTIC-2346: Mail's cached ``unread count`` must never read as measured.

``unread count of <mailbox>`` is a cached aggregate that drifts low — measured
2026-08-17 on a 25,012-message Exchange Inbox, Mail said 3,236 unread where
per-message truth was 10,016. Recomputing it is not affordable (the same
mailbox produced no result from ``count of (messages ... whose read status is
false)`` after 300s), so the four surfaces that report it label it instead.

These tests lock: the labels are always attached; the two free cross-checks
fire when the cached value is provably wrong; the checks stay quiet on
agreement and on error sentinels; and the label text does not break the
statistics text→JSON re-parse.

Every test here is mocked. Passing these proves the Python contract, not that
the AppleScript works — see docs/AGENT_LIVE_TESTING.md for that.
"""

import asyncio
import json
import unittest
from unittest.mock import patch

from apple_mail_mcp import server as _server
from apple_mail_mcp.tools import analytics as analytics_tools
from apple_mail_mcp.tools import inbox as inbox_tools
from apple_mail_mcp.tools.inbox.unread_counts import PROVENANCE_KEY
from apple_mail_mcp.tools.unread_provenance import (
    SUSPECT_OVER_TOTAL,
    SUSPECT_UNDER_SAMPLE,
    UNREAD_SOURCE_CACHED,
    UNREAD_SOURCE_MEASURED,
    measured_unread_disclosure,
    unread_count_disclosure,
)


def _run(coro):
    return asyncio.run(coro)


class UnreadCountDisclosureHelperTests(unittest.TestCase):
    """The single source of truth for the provenance shape."""

    def test_cached_disclosure_always_labels_source_and_measured(self):
        d = unread_count_disclosure()
        self.assertEqual(d["unread_count_source"], UNREAD_SOURCE_CACHED)
        self.assertIs(d["unread_count_measured"], False)
        self.assertIn("cached", d["unread_count_note"].lower())
        self.assertNotIn("unread_count_suspect", d)

    def test_note_can_be_suppressed_for_repeated_rows(self):
        d = unread_count_disclosure(include_note=False)
        self.assertNotIn("unread_count_note", d)
        self.assertEqual(d["unread_count_source"], UNREAD_SOURCE_CACHED)

    def test_cached_above_message_count_is_impossible_and_flagged(self):
        d = unread_count_disclosure(cached_unread=40, total_messages=25)
        self.assertIs(d["unread_count_suspect"], True)
        self.assertEqual(d["unread_count_suspect_reason"], SUSPECT_OVER_TOTAL)
        self.assertIn("40", d["unread_count_suspect_detail"])
        self.assertIn("25", d["unread_count_suspect_detail"])

    def test_sample_lower_bound_above_cached_is_flagged(self):
        d = unread_count_disclosure(cached_unread=3, total_messages=500, sampled_unread=9)
        self.assertIs(d["unread_count_suspect"], True)
        self.assertEqual(d["unread_count_suspect_reason"], SUSPECT_UNDER_SAMPLE)
        # Understatement is at least sampled - cached.
        self.assertIn("6", d["unread_count_suspect_detail"])

    def test_consistent_values_are_not_flagged(self):
        d = unread_count_disclosure(cached_unread=86, total_messages=393, sampled_unread=4)
        self.assertNotIn("unread_count_suspect", d)

    def test_sample_equal_to_cached_is_not_flagged(self):
        """A lower bound only disproves the cache when it strictly exceeds it."""
        d = unread_count_disclosure(cached_unread=5, total_messages=100, sampled_unread=5)
        self.assertNotIn("unread_count_suspect", d)

    def test_measured_25k_divergence_trips_neither_check(self):
        """The real bug is undetectable from the cheap signals.

        3,236 cached vs 10,016 true on 25,012 messages: below the message count
        and above any 10-message sample. This is exactly why the unconditional
        label carries the fix and the suspect flags are only a bonus.
        """
        d = unread_count_disclosure(cached_unread=3236, total_messages=25012, sampled_unread=10)
        self.assertNotIn("unread_count_suspect", d)
        self.assertEqual(d["unread_count_source"], UNREAD_SOURCE_CACHED)

    def test_error_sentinel_and_none_suppress_the_checks(self):
        for cached in (None, -1):
            with self.subTest(cached=cached):
                d = unread_count_disclosure(cached_unread=cached, total_messages=0, sampled_unread=99)
                self.assertNotIn("unread_count_suspect", d)

    def test_measured_disclosure_is_the_positive_counterpart(self):
        d = measured_unread_disclosure()
        self.assertEqual(d["unread_count_source"], UNREAD_SOURCE_MEASURED)
        self.assertIs(d["unread_count_measured"], True)


class GetMailboxUnreadCountsProvenanceTests(unittest.TestCase):
    """This tool reads no message count, so it labels but never flags."""

    def test_summary_mode_carries_provenance_without_disturbing_counts(self):
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value="Work:3|Other:0"):
            result = inbox_tools.get_mailbox_unread_counts(summary_only=True)

        self.assertEqual(result["Work"], 3)
        self.assertEqual(result["Other"], 0)
        self.assertEqual(result[PROVENANCE_KEY]["unread_count_source"], UNREAD_SOURCE_CACHED)
        self.assertIs(result[PROVENANCE_KEY]["unread_count_measured"], False)
        self.assertNotIn("unread_count_suspect", result[PROVENANCE_KEY])

    def test_nested_mode_carries_provenance_alongside_truncation_marker(self):
        raw = "\n".join(["Work|||Inbox|||5", "Work|||__TRUNCATED__|||100"])
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            result = inbox_tools.get_mailbox_unread_counts(account="Work", max_mailboxes=100)

        self.assertEqual(result["Work"]["Inbox"], 5)
        self.assertTrue(result["Work"]["__truncated__"])
        self.assertEqual(result[PROVENANCE_KEY]["unread_count_source"], UNREAD_SOURCE_CACHED)

    def test_empty_result_still_carries_provenance(self):
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=""):
            result = inbox_tools.get_mailbox_unread_counts(account="Work")

        self.assertEqual(list(result), [PROVENANCE_KEY])

    def test_provenance_key_is_namespaced_so_it_cannot_shadow_an_account(self):
        self.assertTrue(PROVENANCE_KEY.startswith("__"))
        self.assertTrue(PROVENANCE_KEY.endswith("__"))


class GetInboxOverviewProvenanceTests(unittest.TestCase):
    """Overview reads both counts and per-message read status: both checks free."""

    @staticmethod
    def _payload(unread, total, recent_read_flags):
        lines = [f"HEADER|||Work|||{unread}|||{total}", f"MAILBOX|||INBOX|||{unread}"]
        for i, is_read in enumerate(recent_read_flags):
            lines.append(
                f"RECENT|||Subject {i}|||someone@example.com|||"
                f"Thursday, May 15, 2026 at 9:00:00 AM|||{str(is_read).lower()}"
            )
        return "\n".join(lines)

    def _overview_json(self, raw):
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            return _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="json",
                    include_draft_state=False,
                )
            )

    def test_json_labels_envelope_and_each_account_row(self):
        payload = self._overview_json(self._payload(2, 10, [False, True]))
        self.assertEqual(payload["unread_count_source"], UNREAD_SOURCE_CACHED)
        self.assertIs(payload["unread_count_measured"], False)
        self.assertIn("unread_count_note", payload)
        row = payload["accounts"][0]
        self.assertEqual(row["unread_count_source"], UNREAD_SOURCE_CACHED)
        # The long note is not repeated on every row.
        self.assertNotIn("unread_count_note", row)
        self.assertNotIn("unread_count_suspect", payload)

    def test_sampled_unread_above_cached_flags_the_account_and_envelope(self):
        # Cached says 1 unread, but 3 of the newest 4 are unread — a strict
        # lower bound of 3 disproves the cached 1.
        payload = self._overview_json(self._payload(1, 500, [False, False, False, True]))
        row = payload["accounts"][0]
        self.assertIs(row["unread_count_suspect"], True)
        self.assertEqual(row["unread_count_suspect_reason"], SUSPECT_UNDER_SAMPLE)
        # Promoted to the envelope so an agent reading only the top level sees it.
        self.assertEqual(payload["unread_count_suspect_reason"], SUSPECT_UNDER_SAMPLE)

    def test_cached_above_total_flags_over_total_reason(self):
        payload = self._overview_json(self._payload(40, 25, [True]))
        row = payload["accounts"][0]
        self.assertEqual(row["unread_count_suspect_reason"], SUSPECT_OVER_TOTAL)

    def test_errored_account_is_labelled_but_never_flagged(self):
        raw = "HEADER|||Work|||ERROR|||mailbox unavailable"
        payload = self._overview_json(raw)
        row = payload["accounts"][0]
        self.assertIn("error", row)
        self.assertNotIn("unread_count_suspect", row)
        # Envelope still states the source for the numbers it would have shown.
        self.assertEqual(payload["unread_count_source"], UNREAD_SOURCE_CACHED)

    def test_timeout_fallback_payload_is_still_labelled(self):
        payload = inbox_tools._overview_json_error("timed_out", account="Work")
        self.assertEqual(payload["unread_count_source"], UNREAD_SOURCE_CACHED)
        self.assertIs(payload["unread_count_measured"], False)

    def test_text_mode_labels_each_number_and_prints_the_note(self):
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=self._payload(2, 10, [False, True]),
        ):
            text = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="text",
                    include_suggestions=False,
                    include_draft_state=False,
                )
            )
        self.assertIn("2 unread [Mail cached, unverified] (10 total)", text)
        self.assertIn("TOTAL UNREAD: 2 across all accounts [Mail cached, unverified]", text)
        self.assertIn("cached `unread count` mailbox aggregate", text)

    def test_text_mode_marks_a_disproved_count_suspect_with_detail(self):
        with patch(
            "apple_mail_mcp.tools.inbox.run_applescript",
            return_value=self._payload(1, 500, [False, False, False]),
        ):
            text = _run(
                inbox_tools.get_inbox_overview(
                    account="Work",
                    output_format="text",
                    include_suggestions=False,
                    include_draft_state=False,
                )
            )
        self.assertIn("[Mail cached, SUSPECT]", text)
        self.assertIn("Suspect unread count:", text)


class ListMailboxesProvenanceTests(unittest.TestCase):
    def _json_rows(self, raw, **kwargs):
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            out = inbox_tools.list_mailboxes(
                account="Work",
                include_counts=True,
                output_format="json",
                **kwargs,
            )
        return json.loads(out)

    def test_each_row_names_its_source_and_flags_impossible_rows(self):
        raw = "\n".join(
            [
                "Work|||INBOX|||INBOX|||100|||20",
                "Work|||Bad|||Bad|||10|||40",
            ]
        )
        payload = self._json_rows(raw)
        rows = {row["name"]: row for row in payload["mailboxes"]}
        self.assertEqual(rows["INBOX"]["unread_count_source"], UNREAD_SOURCE_CACHED)
        self.assertNotIn("unread_count_suspect", rows["INBOX"])
        self.assertIs(rows["Bad"]["unread_count_suspect"], True)
        # Envelope disclosure is seeded from the offending row's real numbers.
        self.assertEqual(payload["unread_count_suspect_reason"], SUSPECT_OVER_TOTAL)
        self.assertIn("40", payload["unread_count_suspect_detail"])

    def test_count_unavailable_sentinel_is_not_flagged(self):
        raw = "Work|||INBOX|||INBOX|||-1|||-1"
        payload = self._json_rows(raw)
        self.assertNotIn("unread_count_suspect", payload["mailboxes"][0])
        self.assertNotIn("unread_count_suspect", payload)

    def test_counts_omitted_means_no_provenance_noise(self):
        raw = "Work|||INBOX|||INBOX|||-1|||-1"
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            payload = json.loads(
                inbox_tools.list_mailboxes(
                    account="Work",
                    include_counts=False,
                    output_format="json",
                )
            )
        self.assertNotIn("unread_count_source", payload)
        self.assertNotIn("unread_count_source", payload["mailboxes"][0])

    def test_text_mode_emits_both_label_branches_and_a_footer(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "MAILBOXES"

        with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
            text = inbox_tools.list_mailboxes(account="Work", include_counts=True)

        # The suspect branch is decided inside AppleScript, where both counts live.
        self.assertIn("if unreadCount > msgCount then", captured["script"])
        self.assertIn("[Mail cached, SUSPECT]", captured["script"])
        self.assertIn("[Mail cached, unverified]", captured["script"])
        self.assertIn("cached `unread count` mailbox aggregate", text)


class GetStatisticsProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_default = _server.DEFAULT_MAIL_ACCOUNT
        _server.DEFAULT_MAIL_ACCOUNT = "Work"

    @classmethod
    def tearDownClass(cls):
        _server.DEFAULT_MAIL_ACCOUNT = cls._saved_default

    def _stats(self, raw, **kwargs):
        with patch("apple_mail_mcp.tools.analytics.run_applescript", return_value=raw):
            return analytics_tools.get_statistics(account="Work", **kwargs)

    def test_account_overview_json_labels_the_cached_aggregate(self):
        raw = "\n".join(["MBOX|||INBOX|||7|||2", "ROW|||INBOX|||0|||0|||alice@example.com"])
        payload = self._stats(raw, scope="account_overview", days_back=7, output_format="json")
        self.assertEqual(payload["unread_count_source"], UNREAD_SOURCE_CACHED)
        self.assertIs(payload["unread_count_measured"], False)
        self.assertNotIn("unread_count_suspect", payload)

    def test_account_overview_flags_a_cached_sum_above_the_message_count(self):
        raw = "MBOX|||INBOX|||5|||9"
        payload = self._stats(raw, scope="account_overview", days_back=7, output_format="json")
        self.assertEqual(payload["unread_count_suspect_reason"], SUSPECT_OVER_TOTAL)

    def test_sender_stats_is_measured_not_cached(self):
        text = "SENDER STATISTICS\n\nTotal emails: 4\nUnread: 1\nWith attachments: 2\n"
        payload = self._stats(
            text,
            scope="sender_stats",
            sender="alice@example.com",
            days_back=7,
            output_format="json",
        )
        self.assertEqual(payload["unread_count_source"], UNREAD_SOURCE_MEASURED)
        self.assertIs(payload["unread_count_measured"], True)

    def test_mailbox_breakdown_labels_both_unread_and_derived_read(self):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return "MAILBOX STATISTICS\n\nTotal messages: 100\nUnread: 20\nRead: 80\n"

        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run):
            payload = analytics_tools.get_statistics(
                account="Work",
                scope="mailbox_breakdown",
                mailbox="INBOX",
                output_format="json",
            )

        script = captured["script"]
        self.assertIn('set outputText to outputText & "Unread: " & unreadMessages & unreadLabel', script)
        self.assertIn(
            'set outputText to outputText & "Read: " & (totalMessages - unreadMessages) & unreadLabel',
            script,
        )
        self.assertIn("if unreadMessages > totalMessages then", script)
        self.assertEqual(payload["unread_count_source"], UNREAD_SOURCE_CACHED)

    def test_inline_label_does_not_break_the_text_to_json_percent_reparse(self):
        """Regression guard: the label is appended after the percentage.

        ``get_statistics`` builds a text report and re-parses it with regexes
        such as ``Unread: (\\d+)(?: \\((\\d+)%\\))?``. Inserting the label
        before the percentage would silently drop ``unread_percent``.
        """
        raw = "\n".join(["MBOX|||INBOX|||10|||2", "ROW|||INBOX|||0|||0|||alice@example.com"])
        payload = self._stats(raw, scope="account_overview", days_back=7, output_format="json")
        stats = payload["statistics"]
        self.assertEqual(stats["unread"], 2)
        self.assertEqual(stats["unread_percent"], 20)
        self.assertEqual(stats["read"], 8)
        self.assertEqual(stats["read_percent"], 80)

    def test_note_inside_volume_metrics_is_not_parsed_as_a_sender_or_mailbox_row(self):
        raw = "\n".join(["MBOX|||INBOX|||10|||2", "ROW|||INBOX|||0|||0|||alice@example.com"])
        payload = self._stats(raw, scope="account_overview", days_back=7, output_format="json")
        stats = payload["statistics"]
        self.assertEqual([s["sender"] for s in stats["top_senders"]], ["alice@example.com"])
        self.assertEqual([m["mailbox"] for m in stats["mailbox_distribution"]], ["INBOX"])


class DashboardProvenanceTests(unittest.TestCase):
    def test_sentinel_is_lifted_out_of_the_account_map(self):
        """The dashboard UI iterates account keys, so the sentinel must not stay."""

        async def fake_recent(**kwargs):
            return []

        with (
            patch("apple_mail_mcp.tools.inbox.run_applescript", return_value="Work:3"),
            patch(
                "apple_mail_mcp.tools.analytics._get_recent_emails_structured_async",
                side_effect=fake_recent,
            ),
        ):
            payload = _run(analytics_tools.inbox_dashboard(account="Work", output_format="json"))

        self.assertEqual(payload["accounts"], {"Work": 3})
        self.assertEqual(payload["unread_count_source"], UNREAD_SOURCE_CACHED)
        self.assertIs(payload["unread_count_measured"], False)


if __name__ == "__main__":
    unittest.main()
