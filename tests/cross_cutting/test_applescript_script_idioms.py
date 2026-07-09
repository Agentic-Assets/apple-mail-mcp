"""Structural and behavioral assertions for AppleScript script builders.

PURPOSE
-------
Version 3.3.0 shipped a regression in ``_build_awaiting_reply_inbox_script``
where ``header value of header named "X"`` appeared in the generated script.
That form is not valid Mail.app dictionary syntax and fails at runtime with
osascript error -2740.  The existing unit tests only asserted the *parsed
output protocol* (``INBOXHDR|||`` rows), so the bad form passed CI, reached
production, and broke live Mail sessions.

These tests close the loop by asserting the *generated AppleScript source*
for every script-builder that had a gap:

  1. smart_inbox builders: positive idiom checks + explicit regression guards
  2. inbox builders: structural shape assertions
  3. analytics builders: structural shape assertions
  4. Edge-case parser tests for malformed row payloads

Every test in this file is entirely mocked — no Mail.app or osascript needed.
"""

import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import smart_inbox as smart_inbox_tools
from apple_mail_mcp.tools import inbox as inbox_tools
from apple_mail_mcp.tools import analytics as analytics_tools


# ---------------------------------------------------------------------------
# Group 1: smart_inbox builders — regression guards
# ---------------------------------------------------------------------------


class AwaitingReplyInboxScriptIdiomsTests(unittest.TestCase):
    """Pin the correct AppleScript header-iteration idiom in
    ``_build_awaiting_reply_inbox_script``.

    The 3.3.0 regression used ``header value of header named "X"`` which is
    NOT valid Mail.app dictionary syntax (error -2740).  The fix iterates
    ``headers of aMessage`` and reads ``name of aHeader`` / ``content of
    aHeader`` — we assert both the presence of the correct form and the
    absence of the broken form so a revert is caught immediately.
    """

    def _build(self, **kwargs):
        defaults = dict(
            escaped_account="Work",
            inbox_cap=10,
            days_back=7,
        )
        defaults.update(kwargs)
        return smart_inbox_tools._build_awaiting_reply_inbox_script(**defaults)

    def test_uses_headers_of_aMessage_not_header_value_of(self):
        """REGRESSION: broken form was `header value of header named "X"`.

        The correct Mail.app form is to iterate ``headers of aMessage`` and
        check each header's ``name`` and ``content`` properties.
        """
        script = self._build()
        # Correct form: iterate the headers collection
        self.assertIn("headers of aMessage", script)
        # Correct property accessors
        self.assertIn("name of aHeader", script)
        self.assertIn("content of aHeader", script)

    def test_does_not_contain_broken_header_value_idiom(self):
        """REGRESSION guard: the phrase 'header value of header named' must
        never appear in this script.  It was the exact string that failed with
        osascript -2740 in 3.3.0 and passed all tests at the time because
        tests only checked parsed output, not source."""
        script = self._build()
        self.assertNotIn("header value of header named", script)

    def test_emits_INBOXHDR_protocol_in_script(self):
        """The script source must embed the 'INBOXHDR|||' row format that the
        Python parser expects — a structural contract between builder and
        parser."""
        script = self._build()
        self.assertIn("INBOXHDR|||", script)
        self.assertIn("INBOXHDR|||in-reply-to|||", script)
        self.assertIn("INBOXHDR|||references|||", script)

    def test_script_contains_account_reference(self):
        script = self._build(escaped_account="MyWork")
        self.assertIn('account "MyWork"', script)

    def test_script_contains_inbox_mailbox_resolution(self):
        """The inbox script must set up the inbox mailbox reference using the
        shared ``inbox_mailbox_script`` helper — guards against drift."""
        script = self._build()
        self.assertIn("inboxMailbox", script)

    def test_script_uses_bounded_slice(self):
        """The inbox script must cap message reads with a bounded slice to
        avoid materializing the full remote mailbox."""
        script = self._build(inbox_cap=15)
        self.assertIn("inboxUpperBound", script)
        self.assertIn("messages 1 thru inboxUpperBound of inboxMailbox", script)

    def test_inbox_cap_value_appears_in_script(self):
        script = self._build(inbox_cap=42)
        self.assertIn("42", script)

    def test_date_check_included_when_days_back_positive(self):
        script = self._build(days_back=3)
        # Date cutoff should gate the iteration
        self.assertIn("cutoffDate", script)
        self.assertIn("if messageDate < cutoffDate then exit repeat", script)

    def test_no_date_check_when_days_back_zero(self):
        script = self._build(days_back=0)
        # When days_back=0 the date check is omitted so the loop walks freely
        self.assertNotIn("if messageDate < cutoffDate then exit repeat", script)

    def test_script_is_complete_tell_block(self):
        script = self._build()
        stripped = script.strip()
        self.assertTrue(stripped.startswith('tell application "Mail"'), script[:60])
        self.assertTrue(stripped.endswith("end tell"), script[-60:])


class AwaitingReplySentScriptIdiomsTests(unittest.TestCase):
    """Pin the correct idioms in ``_build_awaiting_reply_sent_script``."""

    def _build(self, **kwargs):
        defaults = dict(
            escaped_account="Work",
            sent_cap=20,
            days_back=7,
        )
        defaults.update(kwargs)
        return smart_inbox_tools._build_awaiting_reply_sent_script(**defaults)

    def test_reads_internet_message_id_via_message_id_property(self):
        """The sent script must use ``message id of aMessage`` — the standard
        Mail.app property for the RFC-2822 Message-ID header.  This is what
        the Python parser expects in the SENT||| row."""
        script = self._build()
        self.assertIn("message id of aMessage", script)

    def test_emits_SENT_protocol_in_script(self):
        script = self._build()
        self.assertIn('"SENT|||"', script)

    def test_script_uses_bounded_slice(self):
        script = self._build(sent_cap=25)
        self.assertIn("sentUpperBound", script)
        self.assertIn("messages 1 thru sentUpperBound of sentMailbox", script)

    def test_sent_cap_value_appears_in_script(self):
        script = self._build(sent_cap=17)
        self.assertIn("17", script)

    def test_does_not_contain_broken_header_value_idiom(self):
        """Regression guard: the sent script must never use the broken header
        accessor form that caused the 3.3.0 failure."""
        script = self._build()
        self.assertNotIn("header value of header named", script)

    def test_script_is_complete_tell_block(self):
        script = self._build()
        stripped = script.strip()
        self.assertTrue(stripped.startswith('tell application "Mail"'), script[:60])
        self.assertTrue(stripped.endswith("end tell"), script[-60:])


class NeedsResponseInboxScriptIdiomsTests(unittest.TestCase):
    """Pin correct idioms in ``_build_needs_response_inbox_script``."""

    def _build(self, **kwargs):
        defaults = dict(
            escaped_account="Work",
            escaped_mailbox="INBOX",
            days_back=7,
            inbox_cap=30,
            max_results=20,
            scan_body=False,
        )
        defaults.update(kwargs)
        return smart_inbox_tools._build_needs_response_inbox_script(**defaults)

    def test_emits_MSG_protocol_in_script(self):
        script = self._build()
        self.assertIn('"MSG|||"', script)

    def test_does_not_contain_broken_header_value_idiom(self):
        """Regression guard: needs_response should never use the broken form."""
        script = self._build()
        self.assertNotIn("header value of header named", script)

    def test_script_uses_bounded_slice(self):
        script = self._build(inbox_cap=40)
        self.assertIn("mailboxUpperBound", script)
        self.assertIn("messages 1 thru mailboxUpperBound of targetMailbox", script)

    def test_scan_body_false_excludes_content_scan(self):
        """When scan_body=False the script must not fetch message content — a
        common performance regression vector."""
        script = self._build(scan_body=False)
        self.assertNotIn("content of aMessage", script)

    def test_scan_body_true_includes_content_scan(self):
        script = self._build(scan_body=True)
        self.assertIn("content of aMessage", script)

    def test_newsletter_filter_uses_ignoring_case_block(self):
        """Newsletter detection must be inside an 'ignoring case' block so it
        handles uppercase sender addresses without per-message shell-outs."""
        script = self._build()
        self.assertIn("ignoring case", script)

    def test_script_is_complete_tell_block(self):
        script = self._build()
        stripped = script.strip()
        self.assertTrue(stripped.startswith('tell application "Mail"'), script[:60])
        self.assertTrue(stripped.endswith("end tell"), script[-60:])


# ---------------------------------------------------------------------------
# Group 2: _parse_inbox_replied_ids — edge cases
# ---------------------------------------------------------------------------


class ParseInboxRepliedIdsEdgeCasesTests(unittest.TestCase):
    """Edge cases for the ``_parse_inbox_replied_ids`` parser.

    This parser feeds directly into the replied-detection join.  A malformed
    row that silently yields a wrong message-ID would cause a false positive
    (email filtered as already-replied when it wasn't).
    """

    def test_extracts_angle_bracket_id_from_in_reply_to(self):
        raw = "INBOXHDR|||in-reply-to|||<abc@example.com>"
        ids = smart_inbox_tools._parse_inbox_replied_ids(raw)
        self.assertIn("<abc@example.com>", ids)

    def test_extracts_multiple_ids_from_references_header(self):
        raw = "INBOXHDR|||references|||<a@x.com> <b@x.com> <c@x.com>"
        ids = smart_inbox_tools._parse_inbox_replied_ids(raw)
        self.assertEqual(ids, {"<a@x.com>", "<b@x.com>", "<c@x.com>"})

    def test_skips_non_INBOXHDR_lines(self):
        """Lines that do not start with INBOXHDR||| must be completely
        ignored — they may be error rows or other protocol tokens."""
        raw = (
            "ERROR|||something went wrong\n"
            "INBOXHDR|||in-reply-to|||<real@example.com>\n"
            "random noise line"
        )
        ids = smart_inbox_tools._parse_inbox_replied_ids(raw)
        self.assertEqual(ids, {"<real@example.com>"})

    def test_skips_rows_with_fewer_than_three_fields(self):
        """Malformed INBOXHDR rows with no value field must not yield empty
        strings into the replied set (would cause false positives)."""
        raw = "INBOXHDR|||in-reply-to"  # only 2 fields
        ids = smart_inbox_tools._parse_inbox_replied_ids(raw)
        self.assertEqual(ids, set())

    def test_skips_empty_value_field(self):
        raw = "INBOXHDR|||in-reply-to|||   "  # whitespace only
        ids = smart_inbox_tools._parse_inbox_replied_ids(raw)
        self.assertEqual(ids, set())

    def test_normalizes_id_without_angle_brackets(self):
        """An ID lacking angle brackets should be wrapped so set-lookups work
        correctly against the normalized sent-row IDs."""
        raw = "INBOXHDR|||in-reply-to|||abc@example.com"
        ids = smart_inbox_tools._parse_inbox_replied_ids(raw)
        self.assertIn("<abc@example.com>", ids)

    def test_empty_input_returns_empty_set(self):
        self.assertEqual(smart_inbox_tools._parse_inbox_replied_ids(""), set())

    def test_handles_trailing_whitespace_on_id(self):
        raw = "INBOXHDR|||in-reply-to|||<abc@example.com>   "
        ids = smart_inbox_tools._parse_inbox_replied_ids(raw)
        self.assertIn("<abc@example.com>", ids)

    def test_multiple_inbox_messages_accumulate_ids(self):
        raw = (
            "INBOXHDR|||in-reply-to|||<msg1@example.com>\n"
            "INBOXHDR|||references|||<msg2@example.com> <msg3@example.com>\n"
            "INBOXHDR|||in-reply-to|||<msg4@example.com>"
        )
        ids = smart_inbox_tools._parse_inbox_replied_ids(raw)
        self.assertEqual(
            ids,
            {"<msg1@example.com>", "<msg2@example.com>", "<msg3@example.com>", "<msg4@example.com>"},
        )


# ---------------------------------------------------------------------------
# Group 3: _parse_awaiting_reply_sent_rows — edge cases
# ---------------------------------------------------------------------------


class ParseAwaitingReplySentRowsEdgeCasesTests(unittest.TestCase):
    """Edge cases for ``_parse_awaiting_reply_sent_rows``."""

    def test_parses_complete_sent_row(self):
        raw = "SENT|||101|||<abc@example.com>|||Subject here|||bob@example.com|||Mon Jan 6 2026"
        rows = smart_inbox_tools._parse_awaiting_reply_sent_rows(raw)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.mail_app_id, "101")
        self.assertEqual(row.internet_message_id, "<abc@example.com>")
        self.assertEqual(row.subject, "Subject here")
        self.assertEqual(row.recipient_address, "bob@example.com")
        self.assertEqual(row.sent_at, "Mon Jan 6 2026")

    def test_skips_rows_with_fewer_than_six_fields(self):
        """A row missing the date field must be silently skipped — not
        partially parsed — to avoid IndexError or a wrongly-dated entry."""
        raw = "SENT|||1|||<id@x.com>|||Subject|||bob@example.com"  # 5 fields
        rows = smart_inbox_tools._parse_awaiting_reply_sent_rows(raw)
        self.assertEqual(rows, [])

    def test_skips_non_SENT_prefixed_lines(self):
        raw = (
            "ERROR|||some error\n"
            "INBOXHDR|||in-reply-to|||<x@x.com>\n"
            "SENT|||1|||<ok@x.com>|||Subj|||to@x.com|||Date"
        )
        rows = smart_inbox_tools._parse_awaiting_reply_sent_rows(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].mail_app_id, "1")

    def test_parses_multiple_rows(self):
        raw = (
            "SENT|||1|||<a@x.com>|||Alpha|||a@x.com|||Jan 1\n"
            "SENT|||2|||<b@x.com>|||Beta|||b@x.com|||Jan 2"
        )
        rows = smart_inbox_tools._parse_awaiting_reply_sent_rows(raw)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].subject, "Alpha")
        self.assertEqual(rows[1].subject, "Beta")

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(smart_inbox_tools._parse_awaiting_reply_sent_rows(""), [])

    def test_subject_with_pipe_characters_preserved(self):
        """A subject containing '|||' must not break field splitting because
        the split uses maxsplit=5."""
        raw = "SENT|||1|||<a@x.com>|||Sub|||ject|||to@x.com|||Jan 1"
        rows = smart_inbox_tools._parse_awaiting_reply_sent_rows(raw)
        # 7 fields when subject contains a delimiter — parser should still
        # produce one row with the first-field values intact
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].mail_app_id, "1")

    def test_handles_trailing_whitespace_on_lines(self):
        raw = "SENT|||1|||<a@x.com>|||Subj|||to@x.com|||Jan 1   \n"
        rows = smart_inbox_tools._parse_awaiting_reply_sent_rows(raw)
        self.assertEqual(len(rows), 1)


# ---------------------------------------------------------------------------
# Group 4: _parse_needs_response_inbox_rows — additional edge cases
# ---------------------------------------------------------------------------


class ParseNeedsResponseRowsAdditionalEdgeCasesTests(unittest.TestCase):
    """Additional edge cases not covered by test_smart_inbox_json.py."""

    def test_boolean_fields_true_parsed_correctly(self):
        raw = "MSG|||<x@x.com>|||S|||u@x.com|||2026-05-20|||true|||true"
        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].is_flagged)
        self.assertTrue(rows[0].has_question)

    def test_boolean_fields_false_parsed_correctly(self):
        raw = "MSG|||<x@x.com>|||S|||u@x.com|||2026-05-20|||false|||false"
        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)
        self.assertFalse(rows[0].is_flagged)
        self.assertFalse(rows[0].has_question)

    def test_boolean_field_with_surrounding_whitespace(self):
        """AppleScript may emit ' true' or 'true '; strip() is applied."""
        raw = "MSG|||<x@x.com>|||S|||u@x.com|||2026-05-20||| true ||| false "
        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].is_flagged)
        self.assertFalse(rows[0].has_question)

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(smart_inbox_tools._parse_needs_response_inbox_rows(""), [])

    def test_rows_with_only_non_MSG_lines_skipped(self):
        raw = "TOTAL|||0\nERROR|||something\nrandom noise"
        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)
        self.assertEqual(rows, [])

    def test_empty_message_id_field_is_preserved(self):
        """When Mail.app cannot produce an internet message ID, the AppleScript
        emits an empty string.  The parser must pass that through so Python
        skips replied-detection for this message (not crash).

        MSG row schema: MSG|||message_id|||subject|||sender|||date|||flagged|||question
        An empty message_id produces: MSG|||||subject|||sender|||date|||f|||f
        """
        raw = "MSG||||||No-ID subject|||sender@x.com|||2026-05-20|||false|||false"
        rows = smart_inbox_tools._parse_needs_response_inbox_rows(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].message_id, "")
        self.assertEqual(rows[0].subject, "No-ID subject")


# ---------------------------------------------------------------------------
# Group 5: inbox builders — structural shape assertions
# ---------------------------------------------------------------------------


class InboxJsonScriptBuilderTests(unittest.TestCase):
    """Assert that ``_build_list_inbox_json_script`` generates structurally
    valid scripts that include the key idioms the parser expects."""

    def test_script_contains_account_reference(self):
        script = inbox_tools._build_list_inbox_json_script(
            "Work", max_emails=10, read_filter="all"
        )
        self.assertIn('account "Work"', script)

    def test_script_caps_message_count(self):
        """The script must limit message reads via an upper-bound index
        so it never tries to fetch an unbounded list."""
        script = inbox_tools._build_list_inbox_json_script(
            "Work", max_emails=25, read_filter="all"
        )
        self.assertIn("25", script)

    def test_script_emits_pipe_delimited_row_format(self):
        """The JSON script builds rows with '|||' delimiters that
        ``_parse_pipe_delimited_emails`` expects."""
        script = inbox_tools._build_list_inbox_json_script(
            "Work", max_emails=5, read_filter="all"
        )
        self.assertIn('"|||"', script)

    def test_script_includes_mail_app_id_read(self):
        """The mail_app_id (integer id property) must be fetched so the
        parsed dict can carry 'message_id' for targeted operations."""
        script = inbox_tools._build_list_inbox_json_script(
            "Work", max_emails=5, read_filter="all"
        )
        self.assertIn("id of aMessage", script)

    def test_script_with_content_includes_content_block(self):
        script = inbox_tools._build_list_inbox_json_script(
            "Work", max_emails=5, read_filter="all", include_content=True
        )
        self.assertIn("content of aMessage", script)

    def test_script_without_content_excludes_content_block(self):
        script = inbox_tools._build_list_inbox_json_script(
            "Work", max_emails=5, read_filter="all", include_content=False
        )
        self.assertNotIn("content of aMessage", script)

    def test_script_with_message_id_includes_internet_message_id_read(self):
        script = inbox_tools._build_list_inbox_json_script(
            "Work", max_emails=5, read_filter="all", include_message_id=True
        )
        self.assertIn("message id of aMessage", script)

    def test_script_without_message_id_excludes_internet_message_id_read(self):
        script = inbox_tools._build_list_inbox_json_script(
            "Work", max_emails=5, read_filter="all", include_message_id=False
        )
        # The plain "id of aMessage" (integer) is always present;
        # we're guarding against the RFC-2822 message-id probe being included
        # when not requested.
        self.assertNotIn("message id of aMessage", script)

    def test_script_is_complete_tell_block(self):
        script = inbox_tools._build_list_inbox_json_script(
            "Work", max_emails=5, read_filter="all"
        )
        stripped = script.strip()
        self.assertTrue(stripped.startswith('tell application "Mail"'), stripped[:60])
        self.assertTrue(stripped.endswith("end tell"), stripped[-60:])


class InboxTextScriptBuilderTests(unittest.TestCase):
    """Assert that ``_build_list_inbox_text_script`` generates structurally
    valid scripts."""

    def test_script_contains_account_reference(self):
        script = inbox_tools._build_list_inbox_text_script(
            "Work", max_emails=10, read_filter="all", include_content=False
        )
        self.assertIn('account "Work"', script)

    def test_script_is_complete_tell_block(self):
        script = inbox_tools._build_list_inbox_text_script(
            "Work", max_emails=5, read_filter="all", include_content=False
        )
        stripped = script.strip()
        self.assertTrue(stripped.startswith('tell application "Mail"'), stripped[:60])
        self.assertTrue(stripped.endswith("end tell"), stripped[-60:])

    def test_count_marker_emitted_in_script(self):
        """The text script must emit a __COUNT__|||N marker so
        _strip_count_marker can extract the total."""
        script = inbox_tools._build_list_inbox_text_script(
            "Work", max_emails=5, read_filter="all", include_content=False
        )
        self.assertIn('"__COUNT__|||"', script)


# ---------------------------------------------------------------------------
# Group 6: inbox parsers — edge cases
# ---------------------------------------------------------------------------


class ParsePipeDelimitedEmailsEdgeCasesTests(unittest.TestCase):
    """Edge cases for ``inbox._parse_pipe_delimited_emails``."""

    def test_parses_minimal_six_field_row(self):
        raw = "Subject A|||sender@x.com|||2026-01-01|||false|||Work|||42"
        emails = inbox_tools._parse_pipe_delimited_emails(raw)
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]["message_id"], "42")
        self.assertEqual(emails[0]["subject"], "Subject A")

    def test_skips_rows_with_fewer_than_six_fields(self):
        raw = "Subject|||sender|||date|||false|||Work"  # 5 fields
        emails = inbox_tools._parse_pipe_delimited_emails(raw)
        self.assertEqual(emails, [])

    def test_skips_rows_without_pipe_delimiter(self):
        raw = "this is not a pipe-delimited row"
        emails = inbox_tools._parse_pipe_delimited_emails(raw)
        self.assertEqual(emails, [])

    def test_skips_rows_with_empty_mail_app_id(self):
        """A row with an empty mail_app_id field is useless for targeted
        operations and must be dropped — prevents ghost entries in results."""
        raw = "Subject|||sender@x.com|||2026-01-01|||false|||Work|||"
        emails = inbox_tools._parse_pipe_delimited_emails(raw)
        self.assertEqual(emails, [])

    def test_is_read_parsed_correctly_for_true(self):
        raw = "Subject|||sender@x.com|||2026-01-01|||true|||Work|||1"
        emails = inbox_tools._parse_pipe_delimited_emails(raw)
        self.assertTrue(emails[0]["is_read"])

    def test_is_read_parsed_correctly_for_false(self):
        raw = "Subject|||sender@x.com|||2026-01-01|||false|||Work|||1"
        emails = inbox_tools._parse_pipe_delimited_emails(raw)
        self.assertFalse(emails[0]["is_read"])

    def test_empty_string_returns_empty_list(self):
        self.assertEqual(inbox_tools._parse_pipe_delimited_emails(""), [])

    def test_multiple_rows_all_parsed(self):
        raw = (
            "Subj 1|||a@x.com|||Date|||false|||Work|||1\n"
            "Subj 2|||b@x.com|||Date|||true|||Work|||2"
        )
        emails = inbox_tools._parse_pipe_delimited_emails(raw)
        self.assertEqual(len(emails), 2)

    def test_content_preview_parsed_when_has_message_id_false(self):
        """When has_message_id=False, field 6 (index 6) is the content preview."""
        raw = "Subject|||sender@x.com|||Date|||false|||Work|||1|||preview text here"
        emails = inbox_tools._parse_pipe_delimited_emails(raw, has_message_id=False)
        self.assertEqual(emails[0]["content_preview"], "preview text here")

    def test_internet_message_id_parsed_when_has_message_id_true(self):
        raw = "Subject|||sender@x.com|||Date|||false|||Work|||1|||<msg@x.com>"
        emails = inbox_tools._parse_pipe_delimited_emails(raw, has_message_id=True)
        self.assertEqual(emails[0]["internet_message_id"], "<msg@x.com>")


class StripCountMarkerTests(unittest.TestCase):
    """Edge cases for ``inbox._strip_count_marker``."""

    def test_strips_count_line_and_returns_count(self):
        raw = "Line 1\nLine 2\n__COUNT__|||7\nLine 3"
        clean, count = inbox_tools._strip_count_marker(raw)
        self.assertEqual(count, 7)
        self.assertNotIn("__COUNT__", clean)

    def test_returns_zero_count_when_no_marker_present(self):
        raw = "Line 1\nLine 2"
        clean, count = inbox_tools._strip_count_marker(raw)
        self.assertEqual(count, 0)
        self.assertIn("Line 1", clean)

    def test_empty_input_returns_empty_string_zero(self):
        clean, count = inbox_tools._strip_count_marker("")
        self.assertEqual(clean, "")
        self.assertEqual(count, 0)

    def test_non_numeric_count_value_returns_zero(self):
        raw = "Line 1\n__COUNT__|||notanumber"
        clean, count = inbox_tools._strip_count_marker(raw)
        self.assertEqual(count, 0)


# ---------------------------------------------------------------------------
# Group 7: analytics builders — structural shape assertions
# ---------------------------------------------------------------------------


class AnalyticsStatisticsScriptBuilderTests(unittest.TestCase):
    """Assert structural shape of the analytics get_statistics script.

    We call the tool with mocked run_applescript to capture the script, then
    assert key structural properties that protect against AppleScript
    API-misuse regressions.
    """

    def _capture_script(self, **kwargs):
        captured = {}

        def fake_run(script, timeout=120):
            captured["script"] = script
            return ""

        defaults = dict(account="Work", scope="account_overview", days_back=7)
        defaults.update(kwargs)
        with patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=fake_run):
            analytics_tools.get_statistics(**defaults)
        return captured.get("script", "")

    def test_script_contains_account_reference(self):
        script = self._capture_script()
        self.assertIn('account "Work"', script)

    def test_script_contains_inbox_mailbox_ref(self):
        """The account_overview script must include an INBOX guarantee block
        so Exchange accounts where alphabetical order pushes INBOX past the
        mailbox cap are still represented."""
        script = self._capture_script()
        self.assertIn("inboxMailboxRef", script)

    def test_script_contains_mbox_protocol_row(self):
        """MBOX|||name|||total|||unread rows must appear in the script to feed
        the Python aggregation loop."""
        script = self._capture_script()
        self.assertIn('"MBOX|||"', script)

    def test_script_contains_row_protocol_row(self):
        """ROW|||mailbox|||flagged|||hasAttach|||sender rows drive the sample-
        based stats."""
        script = self._capture_script()
        self.assertIn('"ROW|||"', script)

    def test_script_uses_bounded_slice_for_messages(self):
        """The script must cap per-mailbox message reads with a 'messages 1 thru
        N' slice — never 'every message'."""
        script = self._capture_script()
        self.assertIn("messages 1 thru mailboxUpperBound of aMailbox", script)

    def test_windows_use_bounded_per_mailbox_cap(self):
        """Both windows hard-cap per-mailbox reads at 50 (bounded for large
        Exchange accounts); the longer window fans across more mailboxes (20)
        than the short window (10) rather than reading deeper into each one."""
        short_script = self._capture_script(days_back=7)
        long_script = self._capture_script(days_back=30)
        self.assertIn("set mailboxUpperBound to 50", short_script)
        self.assertIn("set mailboxUpperBound to 50", long_script)
        self.assertIn("1 thru 10", short_script)
        self.assertNotIn("1 thru 20", short_script)
        self.assertIn("1 thru 20", long_script)

    def test_does_not_use_whose_for_message_filtering(self):
        """AppleScript 'whose' clauses on remote mailboxes can materialize
        full mailbox downloads.  The stats script must not use them."""
        script = self._capture_script()
        self.assertNotIn("whose date received", script)

    def test_script_is_complete_tell_block(self):
        script = self._capture_script()
        stripped = script.strip()
        self.assertTrue(stripped.startswith('tell application "Mail"'), stripped[:60])
        self.assertTrue(stripped.endswith("end tell"), stripped[-60:])


# ---------------------------------------------------------------------------
# Group 8: analytics parsers — edge cases
# ---------------------------------------------------------------------------


class ParseStatisticsErrorsTests(unittest.TestCase):
    """Edge cases for ``analytics._parse_statistics_errors``."""

    def test_extracts_error_lines_with_prefix(self):
        raw = "__APPLE_MAIL_MCP_ERROR__|||INBOX|||5 message(s) skipped due to read errors"
        errors = analytics_tools._parse_statistics_errors(raw)
        self.assertEqual(len(errors), 1)
        self.assertIn("INBOX", errors[0])
        self.assertIn("5 message(s)", errors[0])

    def test_ignores_non_error_lines(self):
        raw = "MBOX|||Inbox|||42|||5\nROW|||Inbox|||0|||1|||alice@x.com"
        errors = analytics_tools._parse_statistics_errors(raw)
        self.assertEqual(errors, [])

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(analytics_tools._parse_statistics_errors(""), [])

    def test_multiple_errors_all_collected(self):
        raw = (
            "__APPLE_MAIL_MCP_ERROR__|||Archive|||2 message(s) skipped due to read errors\n"
            "__APPLE_MAIL_MCP_ERROR__|||Sent|||network error"
        )
        errors = analytics_tools._parse_statistics_errors(raw)
        self.assertEqual(len(errors), 2)


class ParseAccountOverviewStatisticsTests(unittest.TestCase):
    """Edge cases for ``analytics._parse_account_overview_statistics``."""

    def test_parses_total_emails(self):
        text = "Total Emails: 123\nUnread: 10 (8%)\nRead: 113 (92%)\nFlagged: 2\nWith Attachments: 5 (4%)"
        stats = analytics_tools._parse_account_overview_statistics(text)
        self.assertEqual(stats["total_emails"], 123)

    def test_parses_unread_and_read(self):
        text = "Total Emails: 50\nUnread: 20 (40%)\nRead: 30 (60%)"
        stats = analytics_tools._parse_account_overview_statistics(text)
        self.assertEqual(stats["unread"], 20)
        self.assertEqual(stats["read"], 30)

    def test_zero_totals_parse_cleanly(self):
        """An empty account produces zero values — guard against zero-division
        or missing-key crashes in downstream formatters."""
        text = "Total Emails: 0\nUnread: 0\nRead: 0\nFlagged: 0\nWith Attachments: 0"
        stats = analytics_tools._parse_account_overview_statistics(text)
        self.assertEqual(stats["total_emails"], 0)
        self.assertEqual(stats["unread"], 0)

    def test_missing_fields_return_zero_defaults(self):
        """Output that omits some metrics must produce zero defaults — not
        KeyError — so callers can always access the full stats dict."""
        stats = analytics_tools._parse_account_overview_statistics("")
        self.assertIn("total_emails", stats)
        self.assertEqual(stats["total_emails"], 0)
        self.assertIn("top_senders", stats)
        self.assertIsInstance(stats["top_senders"], list)


# ---------------------------------------------------------------------------
# Group 9: live tool round-trip — empty inbox edge case
# ---------------------------------------------------------------------------


class EmptyMailboxEdgeCasesTests(unittest.TestCase):
    """Verify that tools handle empty AppleScript returns gracefully.

    These test the integration between script dispatch and Python-side
    aggregation when Mail returns no rows.
    """

    def test_get_awaiting_reply_with_empty_inbox_and_sent(self):
        """An account with no inbox/sent messages should return a graceful
        'found 0 sent email(s)' message rather than crashing."""
        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript", return_value=""
        ):
            result = smart_inbox_tools.get_awaiting_reply(
                account="Work", days_back=7, max_results=5
            )
        self.assertIn("0 sent email(s) awaiting reply", result)

    def test_get_needs_response_with_empty_inbox_returns_zero_found(self):
        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript", return_value=""
        ):
            result = smart_inbox_tools.get_needs_response(
                account="Work", days_back=7, max_results=5
            )
        self.assertIn("Found 0 email(s) needing response.", result)

    def test_get_top_senders_with_empty_mailbox_returns_graceful_result(self):
        with patch(
            "apple_mail_mcp.tools.smart_inbox.run_applescript",
            return_value="TOTAL|||0\nMAILBOX_COUNT|||0",
        ):
            result = smart_inbox_tools.get_top_senders(
                account="Work", days_back=7, top_n=5
            )
        self.assertIn("Total emails analysed: 0", result)


if __name__ == "__main__":
    unittest.main()
