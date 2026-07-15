"""Tests for the repo-owned apple-mail CLI."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp import cli


class AppleMailCliTests(unittest.TestCase):
    def _draft_verify_smoke_args(self, *extra: str) -> list[str]:
        return [
            "draft-verify-smoke",
            "--account",
            "Work",
            "--from-address",
            "work@example.com",
            *extra,
            "--json",
        ]

    def _printed_json_payload(self, mock_print):
        return json.loads(mock_print.call_args.args[0])

    def test_accounts_json_prints_structured_output(self):
        with (
            patch(
                "apple_mail_mcp.tools.inbox.list_accounts",
                return_value=["Work", "Personal"],
            ),
            patch("builtins.print") as mock_print,
        ):
            code = cli.main(["accounts", "--json"])

        self.assertEqual(code, 0)
        payload = json.loads(mock_print.call_args.args[0])
        self.assertEqual(payload, ["Work", "Personal"])

    def test_search_query_maps_to_subject_keyword(self):
        captured = {}

        def fake_search(**kwargs):
            captured.update(kwargs)
            return '{"items":[]}'

        with (
            patch("apple_mail_mcp.tools.search.search_emails", side_effect=fake_search),
            patch("builtins.print"),
        ):
            code = cli.main(
                [
                    "search",
                    "--account",
                    "Work",
                    "--query",
                    "invoice",
                    "--limit",
                    "3",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(captured["account"], "Work")
        self.assertEqual(captured["subject_keyword"], "invoice")
        self.assertEqual(captured["limit"], 3)
        self.assertEqual(captured["output_format"], "json")

    def test_search_mailboxes_splits_into_list(self):
        captured = {}

        def fake_search(**kwargs):
            captured.update(kwargs)
            return '{"items":[]}'

        with (
            patch("apple_mail_mcp.tools.search.search_emails", side_effect=fake_search),
            patch("builtins.print"),
        ):
            code = cli.main(
                [
                    "search",
                    "--account",
                    "Work",
                    "--query",
                    "invoice",
                    "--mailboxes",
                    "INBOX, Sent ,Archive",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(captured["mailboxes"], ["INBOX", "Sent", "Archive"])

    def test_search_without_mailboxes_passes_none(self):
        captured = {}

        def fake_search(**kwargs):
            captured.update(kwargs)
            return '{"items":[]}'

        with (
            patch("apple_mail_mcp.tools.search.search_emails", side_effect=fake_search),
            patch("builtins.print"),
        ):
            code = cli.main(["search", "--account", "Work", "--query", "x", "--json"])

        self.assertEqual(code, 0)
        self.assertIsNone(captured["mailboxes"])

    def test_search_body_requires_explicit_allow_body_scan(self):
        captured = {}

        def fake_search(**kwargs):
            captured.update(kwargs)
            return '{"items":[]}'

        with (
            patch("apple_mail_mcp.tools.search.search_emails", side_effect=fake_search),
            patch("builtins.print"),
        ):
            code = cli.main(
                [
                    "search",
                    "--account",
                    "Work",
                    "--body",
                    "invoice terms",
                    "--allow-body-scan",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(captured["body_text"], "invoice terms")
        self.assertTrue(captured["allow_body_scan"])

    def test_search_exact_sender_domain_and_message_id_forward_to_tool(self):
        captured = {}

        def fake_search(**kwargs):
            captured.update(kwargs)
            return '{"items":[]}'

        with (
            patch("apple_mail_mcp.tools.search.search_emails", side_effect=fake_search),
            patch("builtins.print"),
        ):
            code = cli.main(
                [
                    "search",
                    "--account",
                    "Work",
                    "--sender-exact",
                    "person@example.com",
                    "--sender-domain",
                    "example.com",
                    "--internet-message-id",
                    "<reply@example.com>",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(captured["sender_exact"], "person@example.com")
        self.assertEqual(captured["sender_domain"], "example.com")
        self.assertEqual(captured["internet_message_id"], "<reply@example.com>")

    def test_drafts_list_forwards_hide_empty(self):
        captured = {}

        def fake_drafts(**kwargs):
            captured.update(kwargs)
            return "DRAFT EMAILS"

        with (
            patch("apple_mail_mcp.tools.compose.manage_drafts", side_effect=fake_drafts),
            patch("builtins.print"),
        ):
            code = cli.main(["drafts", "list", "--account", "Work", "--hide-empty"])

        self.assertEqual(code, 0)
        self.assertEqual(captured["action"], "list")
        self.assertTrue(captured["hide_empty"])

    def test_drafts_cleanup_empty_defaults_to_dry_run(self):
        captured = {}

        def fake_drafts(**kwargs):
            captured.update(kwargs)
            return "DRAFT CLEANUP"

        with (
            patch("apple_mail_mcp.tools.compose.manage_drafts", side_effect=fake_drafts),
            patch("builtins.print"),
        ):
            code = cli.main(["drafts", "cleanup-empty", "--account", "Work"])

        self.assertEqual(code, 0)
        self.assertEqual(captured["action"], "cleanup_empty")
        self.assertTrue(captured["dry_run"])
        self.assertEqual(captured["max_deletes"], 20)

    def test_drafts_cleanup_empty_execute_clears_dry_run(self):
        captured = {}

        def fake_drafts(**kwargs):
            captured.update(kwargs)
            return "DRAFT CLEANUP"

        with (
            patch("apple_mail_mcp.tools.compose.manage_drafts", side_effect=fake_drafts),
            patch("builtins.print"),
        ):
            code = cli.main(["drafts", "cleanup-empty", "--account", "Work", "--execute", "--limit", "5"])

        self.assertEqual(code, 0)
        self.assertFalse(captured["dry_run"])
        self.assertEqual(captured["max_deletes"], 5)

    def test_inbox_accepts_max_emails_alias(self):
        captured = {}

        def fake_inbox(**kwargs):
            captured.update(kwargs)
            return '{"emails":[]}'

        with (
            patch("apple_mail_mcp.tools.inbox.list_inbox_emails", side_effect=fake_inbox),
            patch("builtins.print"),
        ):
            code = cli.main(
                [
                    "inbox",
                    "--account",
                    "Work",
                    "--max-emails",
                    "3",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(captured["account"], "Work")
        self.assertEqual(captured["max_emails"], 3)
        self.assertEqual(captured["output_format"], "json")

    def test_show_calls_exact_id_tool(self):
        captured = {}

        def fake_show(**kwargs):
            captured.update(kwargs)
            return '{"item":null}'

        with (
            patch("apple_mail_mcp.tools.search.get_email_by_id", side_effect=fake_show),
            patch("builtins.print"),
        ):
            code = cli.main(
                [
                    "show",
                    "--account",
                    "Work",
                    "--id",
                    "123",
                    "--no-content",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(captured["message_id"], "123")
        self.assertFalse(captured["include_content"])

    def test_draft_reads_body_file_and_defaults_to_draft_mode(self):
        captured = {}

        def fake_compose(**kwargs):
            captured.update(kwargs)
            return "drafted"

        with tempfile.TemporaryDirectory() as tmpdir:
            body_file = Path(tmpdir) / "body.txt"
            body_file.write_text("Hello from file")
            with (
                patch(
                    "apple_mail_mcp.tools.compose.compose_email",
                    side_effect=fake_compose,
                ),
                patch("builtins.print"),
            ):
                code = cli.main(
                    [
                        "draft",
                        "--account",
                        "Work",
                        "--to",
                        "person@example.com",
                        "--subject",
                        "Subject",
                        "--body-file",
                        str(body_file),
                    ]
                )

        self.assertEqual(code, 0)
        self.assertEqual(captured["body"], "Hello from file")
        self.assertEqual(captured["mode"], "draft")

    def test_draft_forwards_signature_name_to_compose_email(self):
        captured = {}

        def fake_compose(**kwargs):
            captured.update(kwargs)
            return "drafted"

        with (
            patch("apple_mail_mcp.tools.compose.compose_email", side_effect=fake_compose),
            patch("builtins.print"),
        ):
            code = cli.main(
                [
                    "draft",
                    "--account",
                    "Work",
                    "--to",
                    "person@example.com",
                    "--subject",
                    "Subject",
                    "--body",
                    "Hello",
                    "--signature-name",
                    "TU",
                ]
            )

        self.assertEqual(code, 0)
        self.assertTrue(captured["include_signature"])
        self.assertEqual(captured["signature_name"], "TU")

    def test_draft_forwards_no_signature_to_compose_email(self):
        captured = {}

        def fake_compose(**kwargs):
            captured.update(kwargs)
            return "drafted"

        with (
            patch("apple_mail_mcp.tools.compose.compose_email", side_effect=fake_compose),
            patch("builtins.print"),
        ):
            code = cli.main(
                [
                    "draft",
                    "--account",
                    "Work",
                    "--to",
                    "person@example.com",
                    "--subject",
                    "Subject",
                    "--body",
                    "Hello",
                    "--no-signature",
                ]
            )

        self.assertEqual(code, 0)
        self.assertFalse(captured["include_signature"])
        self.assertIsNone(captured["signature_name"])

    def test_draft_forwards_standalone_confirmed_to_compose_email(self):
        captured = {}

        def fake_compose(**kwargs):
            captured.update(kwargs)
            return "drafted"

        with (
            patch("apple_mail_mcp.tools.compose.compose_email", side_effect=fake_compose),
            patch("builtins.print"),
        ):
            code = cli.main(
                [
                    "draft",
                    "--account",
                    "Work",
                    "--to",
                    "person@example.com",
                    "--subject",
                    "Re: standalone project name",
                    "--body",
                    "Hello",
                    "--standalone-confirmed",
                ]
            )

        self.assertEqual(code, 0)
        self.assertTrue(captured["standalone_confirmed"])

    def test_extract_draft_ids_handles_list_and_create_output(self):
        text = "Draft ID: 111\n   Id: 222   To: test@example.com\nnoise\n   Id: bad\n   Id: 222"

        self.assertEqual(cli._extract_draft_ids(text), ["111", "222"])

    def test_draft_verify_smoke_requires_cleanup_or_leave_draft_before_tool_calls(self):
        with (
            patch("apple_mail_mcp.tools.compose.manage_drafts") as mock_drafts,
            patch("apple_mail_mcp.tools.compose.verify_draft") as mock_verify,
            patch("sys.stderr"),
            self.assertRaises(SystemExit),
        ):
            cli.main(["draft-verify-smoke", "--account", "Work"])

        mock_drafts.assert_not_called()
        mock_verify.assert_not_called()

    def test_draft_verify_smoke_success_uses_persisted_id_and_atomic_identity_cleanup(self):
        manage_calls = []
        verify_calls = []

        def fake_manage(**kwargs):
            manage_calls.append(kwargs)
            if kwargs["action"] == "create":
                return "Draft created\nDraft ID: 111\n"
            if kwargs["action"] == "list":
                return "DRAFT EMAILS\n   Id: 222   To: apple-mail-mcp-smoke@example.invalid\n"
            raise AssertionError(kwargs)

        def fake_verify(**kwargs):
            verify_calls.append(kwargs)
            if kwargs["draft_id"] == "222" and "expected_subject" in kwargs:
                return json.dumps(
                    {"found": True, "recipients": {"to": "apple-mail-mcp-smoke@example.invalid"}, "warnings": []}
                )
            return json.dumps({"found": False})

        with (
            patch("apple_mail_mcp.tools.compose.manage_drafts", side_effect=fake_manage),
            patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify),
            patch(
                "apple_mail_mcp.tools.compose.delete_draft_if_identity_matches",
                return_value=json.dumps({"deleted": True, "draft_id": "333"}),
            ) as mock_delete,
            patch("time.sleep"),
            patch("builtins.print") as mock_print,
        ):
            code = cli.main(
                self._draft_verify_smoke_args(
                    "--cleanup",
                    "--poll-timeout",
                    "0.01",
                    "--poll-interval",
                    "0.01",
                )
            )

        self.assertEqual(code, 0)
        payload = self._printed_json_payload(mock_print)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["created_draft_id_provisional"], "111")
        self.assertEqual(payload["persisted_draft_id"], "222")
        self.assertEqual(manage_calls[0]["from_address"], "work@example.com")
        self.assertEqual([call["action"] for call in manage_calls], ["create", "list"])
        self.assertEqual(mock_delete.call_args.kwargs["draft_id"], "222")
        self.assertEqual(mock_delete.call_args.kwargs["expected_to"], "apple-mail-mcp-smoke@example.invalid")
        self.assertEqual(mock_delete.call_args.kwargs["expected_subject"], payload["subject"])
        self.assertNotIn("draft_subject", mock_delete.call_args.kwargs)
        self.assertEqual(verify_calls[0]["draft_id"], "222")
        self.assertEqual(verify_calls[0]["expected_to"], "apple-mail-mcp-smoke@example.invalid")
        self.assertEqual(len(verify_calls), 1)

    def test_draft_verify_smoke_recipient_mismatch_fails_with_safe_evidence(self):
        def fake_manage(**kwargs):
            if kwargs["action"] == "create":
                return "Draft created"
            if kwargs["action"] == "list":
                return "Id: 888"
            raise AssertionError(kwargs)

        with (
            patch("apple_mail_mcp.tools.compose.manage_drafts", side_effect=fake_manage),
            patch(
                "apple_mail_mcp.tools.compose.verify_draft",
                return_value=json.dumps(
                    {
                        "found": True,
                        "recipients": {"to": "unexpected@example.com"},
                        "body_preview": "private draft body",
                        "checks": {"to_matches_expected": False},
                        "warnings": ["to_mismatch"],
                    }
                ),
            ) as mock_verify,
            patch("builtins.print") as mock_print,
        ):
            code = cli.main(
                self._draft_verify_smoke_args("--leave-draft", "--poll-timeout", "0.01", "--poll-interval", "0.01")
            )

        self.assertEqual(code, 1)
        self.assertGreaterEqual(mock_verify.call_count, 1)
        self.assertEqual(mock_verify.call_args.kwargs["expected_to"], "apple-mail-mcp-smoke@example.invalid")
        payload = self._printed_json_payload(mock_print)
        self.assertEqual(payload["expected_to"], "apple-mail-mcp-smoke@example.invalid")
        evidence = payload["errors"][-1]["last_verification"]
        self.assertEqual(evidence["to_matches_expected"], False)
        self.assertEqual(evidence["warnings"], ["to_mismatch"])
        self.assertNotIn("unexpected@example.com", json.dumps(payload))
        self.assertNotIn("private draft body", json.dumps(payload))

    def test_draft_verify_smoke_polls_until_list_returns_candidate(self):
        manage_calls = []

        def fake_manage(**kwargs):
            manage_calls.append(kwargs)
            if kwargs["action"] == "create":
                return "Draft created"
            if kwargs["action"] == "list" and len([c for c in manage_calls if c["action"] == "list"]) == 1:
                return "Found 0 draft(s)"
            if kwargs["action"] == "list":
                return "Id: 333   To: smoke@example.invalid"
            raise AssertionError(kwargs)

        def fake_verify(**kwargs):
            if "expected_subject" in kwargs:
                return json.dumps(
                    {"found": True, "recipients": {"to": "apple-mail-mcp-smoke@example.invalid"}, "warnings": []}
                )
            return json.dumps({"found": False})

        with (
            patch("apple_mail_mcp.tools.compose.manage_drafts", side_effect=fake_manage),
            patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify),
            patch(
                "apple_mail_mcp.tools.compose.delete_draft_if_identity_matches",
                return_value=json.dumps({"deleted": True, "draft_id": "333"}),
            ),
            patch("time.sleep"),
            patch("builtins.print") as mock_print,
        ):
            code = cli.main(
                self._draft_verify_smoke_args(
                    "--cleanup",
                    "--poll-timeout",
                    "5",
                    "--poll-interval",
                    "0.1",
                )
            )

        self.assertEqual(code, 0)
        payload = self._printed_json_payload(mock_print)
        self.assertEqual(payload["poll_attempts"], 2)

    def test_draft_verify_smoke_timeout_never_deletes_without_candidate(self):
        manage_calls = []

        def fake_manage(**kwargs):
            manage_calls.append(kwargs)
            if kwargs["action"] == "create":
                return "Draft created"
            if kwargs["action"] == "list":
                return "Found 0 draft(s)"
            raise AssertionError(kwargs)

        with (
            patch("apple_mail_mcp.tools.compose.manage_drafts", side_effect=fake_manage),
            patch("apple_mail_mcp.tools.compose.verify_draft") as mock_verify,
            patch("time.sleep"),
            patch("builtins.print") as mock_print,
        ):
            code = cli.main(
                self._draft_verify_smoke_args(
                    "--cleanup",
                    "--poll-timeout",
                    "0.01",
                    "--poll-interval",
                    "0.01",
                )
            )

        self.assertEqual(code, 1)
        payload = self._printed_json_payload(mock_print)
        self.assertFalse(payload["ok"])
        self.assertFalse(any(call["action"] == "delete" for call in manage_calls))
        mock_verify.assert_not_called()

    def test_draft_verify_smoke_retains_single_unverified_candidate_without_delete(self):
        manage_calls = []
        verify_calls = []

        def fake_manage(**kwargs):
            manage_calls.append(kwargs)
            if kwargs["action"] == "create":
                return "Draft created"
            if kwargs["action"] == "list":
                return "Id: 777   To: smoke@example.invalid"
            raise AssertionError(kwargs)

        def fake_verify(**kwargs):
            verify_calls.append(kwargs)
            if "expected_subject" in kwargs:
                return json.dumps({"found": True, "warnings": ["expected_body_missing"]})
            return json.dumps({"found": False, "warnings": ["draft_not_found"]})

        with (
            patch("apple_mail_mcp.tools.compose.manage_drafts", side_effect=fake_manage),
            patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify),
            patch("builtins.print") as mock_print,
        ):
            code = cli.main(
                self._draft_verify_smoke_args("--cleanup", "--poll-timeout", "0.01", "--poll-interval", "0.01")
            )

        self.assertEqual(code, 1)
        payload = self._printed_json_payload(mock_print)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["verified"])
        self.assertIsNone(payload["persisted_draft_id"])
        self.assertFalse(payload["cleanup"]["confirmed"])
        self.assertEqual(payload["errors"][-1]["candidate_ids"], ["777"])
        delete_calls = [call for call in manage_calls if call["action"] == "delete"]
        self.assertEqual(delete_calls, [])
        self.assertEqual(verify_calls[0]["draft_id"], "777")

    def test_draft_verify_smoke_extra_recipient_with_cleanup_never_deletes(self):
        manage_calls = []

        def fake_manage(**kwargs):
            manage_calls.append(kwargs)
            if kwargs["action"] == "create":
                return "Draft created"
            if kwargs["action"] == "list":
                return "Id: 777"
            raise AssertionError(kwargs)

        with (
            patch("apple_mail_mcp.tools.compose.manage_drafts", side_effect=fake_manage),
            patch(
                "apple_mail_mcp.tools.compose.verify_draft",
                return_value=json.dumps(
                    {
                        "found": True,
                        "recipients": {"to": "apple-mail-mcp-smoke@example.invalid, unexpected@example.com"},
                        "checks": {"to_matches_expected": True},
                        "warnings": [],
                    }
                ),
            ) as mock_verify,
            patch("apple_mail_mcp.tools.compose.delete_draft_if_identity_matches") as mock_delete,
            patch("builtins.print") as mock_print,
        ):
            code = cli.main(
                self._draft_verify_smoke_args("--cleanup", "--poll-timeout", "0.01", "--poll-interval", "0.01")
            )

        self.assertEqual(code, 1)
        self.assertGreaterEqual(mock_verify.call_count, 1)
        mock_delete.assert_not_called()
        self.assertFalse(any(call["action"] == "delete" for call in manage_calls))
        payload = self._printed_json_payload(mock_print)
        self.assertEqual(payload["errors"][-1]["candidate_ids"], ["777"])
        self.assertIsNone(payload["persisted_draft_id"])

    def test_draft_verify_smoke_leave_draft_skips_delete(self):
        manage_calls = []

        def fake_manage(**kwargs):
            manage_calls.append(kwargs)
            if kwargs["action"] == "create":
                return "Draft created"
            if kwargs["action"] == "list":
                return "Id: 444   To: smoke@example.invalid"
            raise AssertionError(kwargs)

        with (
            patch("apple_mail_mcp.tools.compose.manage_drafts", side_effect=fake_manage),
            patch(
                "apple_mail_mcp.tools.compose.verify_draft",
                return_value=json.dumps(
                    {"found": True, "recipients": {"to": "apple-mail-mcp-smoke@example.invalid"}, "warnings": []}
                ),
            ),
            patch("builtins.print") as mock_print,
        ):
            code = cli.main(self._draft_verify_smoke_args("--leave-draft"))

        self.assertEqual(code, 0)
        payload = self._printed_json_payload(mock_print)
        self.assertTrue(payload["cleanup"]["skipped"])
        self.assertFalse(any(call["action"] == "delete" for call in manage_calls))

    def test_draft_verify_smoke_cleanup_identity_failure_returns_retained_id(self):
        def fake_manage(**kwargs):
            if kwargs["action"] == "create":
                return "Draft created"
            if kwargs["action"] == "list":
                return "Id: 555   To: smoke@example.invalid"
            raise AssertionError(kwargs)

        with (
            patch("apple_mail_mcp.tools.compose.manage_drafts", side_effect=fake_manage),
            patch(
                "apple_mail_mcp.tools.compose.verify_draft",
                return_value=json.dumps(
                    {"found": True, "recipients": {"to": "apple-mail-mcp-smoke@example.invalid"}, "warnings": []}
                ),
            ),
            patch(
                "apple_mail_mcp.tools.compose.delete_draft_if_identity_matches",
                return_value=json.dumps(
                    {"deleted": False, "draft_id": "999", "error": "smoke_draft_identity_mismatch"}
                ),
            ),
            patch("builtins.print") as mock_print,
        ):
            code = cli.main(self._draft_verify_smoke_args("--cleanup"))

        self.assertEqual(code, 1)
        payload = self._printed_json_payload(mock_print)
        self.assertEqual(payload["persisted_draft_id"], "555")
        self.assertFalse(payload["cleanup"]["confirmed"])

    def test_draft_verify_smoke_requires_from_address_for_multi_alias_account(self):
        with (
            patch(
                "apple_mail_mcp.tools.inbox.list_account_addresses",
                return_value={"Work": ["one@example.com", "two@example.com"]},
            ),
            patch("apple_mail_mcp.tools.compose.manage_drafts") as mock_drafts,
            patch("builtins.print") as mock_print,
        ):
            code = cli.main(["draft-verify-smoke", "--account", "Work", "--cleanup", "--json"])

        self.assertEqual(code, 2)
        mock_drafts.assert_not_called()
        payload = json.loads(mock_print.call_args.args[0])
        self.assertEqual(payload["errors"][0]["stage"], "sender")

    def test_draft_verify_smoke_derives_single_account_sender_address(self):
        manage_calls = []

        def fake_manage(**kwargs):
            manage_calls.append(kwargs)
            if kwargs["action"] == "create":
                return "Draft created"
            if kwargs["action"] == "list":
                return "Id: 666   To: smoke@example.invalid"
            raise AssertionError(kwargs)

        def fake_verify(**kwargs):
            if "expected_subject" in kwargs:
                return json.dumps(
                    {"found": True, "recipients": {"to": "apple-mail-mcp-smoke@example.invalid"}, "warnings": []}
                )
            return json.dumps({"found": False})

        with (
            patch("apple_mail_mcp.tools.inbox.list_account_addresses", return_value={"Work": ["one@example.com"]}),
            patch("apple_mail_mcp.tools.compose.manage_drafts", side_effect=fake_manage),
            patch("apple_mail_mcp.tools.compose.verify_draft", side_effect=fake_verify),
            patch(
                "apple_mail_mcp.tools.compose.delete_draft_if_identity_matches",
                return_value=json.dumps({"deleted": True, "draft_id": "333"}),
            ),
            patch("builtins.print"),
        ):
            code = cli.main(["draft-verify-smoke", "--account", "Work", "--cleanup", "--json"])

        self.assertEqual(code, 0)
        self.assertEqual(manage_calls[0]["from_address"], "one@example.com")

    def test_mcp_config_defaults_to_draft_safe(self):
        with patch("builtins.print") as mock_print:
            code = cli.main(["mcp-config", "--repo", "/tmp/apple-mail-mcp"])

        self.assertEqual(code, 0)
        payload = json.loads(mock_print.call_args.args[0])
        args = payload["mcpServers"]["apple-mail"]["args"]
        self.assertEqual(args[0], "/tmp/apple-mail-mcp/plugin/start_mcp.sh")
        self.assertIn("--draft-safe", args)

    def test_unread_summary_calls_tool(self):
        captured = {}

        def fake_unread(**kwargs):
            captured.update(kwargs)
            return {"Work": 3}

        with (
            patch(
                "apple_mail_mcp.tools.inbox.get_mailbox_unread_counts",
                side_effect=fake_unread,
            ),
            patch("builtins.print"),
        ):
            code = cli.main(["unread", "--account", "Work", "--summary", "--json"])

        self.assertEqual(code, 0)
        self.assertTrue(captured["summary_only"])
        self.assertEqual(captured["account"], "Work")

    def test_move_dry_run_forwards_dry_run_flag(self):
        captured = {}

        def fake_move(**kwargs):
            captured.update(kwargs)
            return "preview"

        with (
            patch("apple_mail_mcp.tools.manage.move_email", side_effect=fake_move),
            patch("builtins.print"),
        ):
            code = cli.main(
                [
                    "move-dry-run",
                    "--account",
                    "Work",
                    "--to",
                    "Archive",
                    "--subject",
                    cli.NO_HIT_SUBJECT,
                ]
            )

        self.assertEqual(code, 0)
        self.assertTrue(captured["dry_run"])

    def test_move_dry_run_prefers_message_ids(self):
        captured = {}

        def fake_move(**kwargs):
            captured.update(kwargs)
            return "preview"

        with (
            patch("apple_mail_mcp.tools.manage.move_email", side_effect=fake_move),
            patch("builtins.print"),
        ):
            code = cli.main(
                [
                    "move-dry-run",
                    "--account",
                    "Work",
                    "--to",
                    "Archive",
                    "--message-ids",
                    "101, 202",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(captured["message_ids"], ["101", "202"])
        self.assertIsNone(captured["subject_keyword"])
        self.assertFalse(captured["allow_filter_scan"])

    def test_trash_dry_run_prefers_message_ids(self):
        captured = {}

        def fake_trash(**kwargs):
            captured.update(kwargs)
            return "preview"

        with (
            patch("apple_mail_mcp.tools.manage.manage_trash", side_effect=fake_trash),
            patch("builtins.print"),
        ):
            code = cli.main(
                [
                    "trash-dry-run",
                    "--account",
                    "Work",
                    "--message-ids",
                    "303,404",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(captured["message_ids"], ["303", "404"])
        self.assertIsNone(captured["subject_keyword"])
        self.assertFalse(captured["allow_filter_scan"])

    def test_smoke_test_checks_invalid_account_and_draft_safe(self):
        with (
            patch(
                "apple_mail_mcp.tools.inbox.list_accounts",
                return_value=["Work"],
            ),
            patch(
                "apple_mail_mcp.tools.inbox.list_inbox_emails",
                side_effect=lambda **kwargs: (
                    '{"error":"account_not_found","account":"' + kwargs["account"] + '"}'
                    if kwargs["account"] == cli.INVALID_ACCOUNT
                    else '{"emails":[]}'
                ),
            ),
            patch(
                "apple_mail_mcp.tools.search.search_emails",
                return_value='{"items":[]}',
            ),
            patch(
                "apple_mail_mcp.tools.compose._send_blocked",
                return_value="Error: Sending is disabled in draft-safe mode.",
            ),
            patch("builtins.print"),
        ):
            code = cli.main(["smoke-test", "--account", "Work", "--json"])

        self.assertEqual(code, 0)
