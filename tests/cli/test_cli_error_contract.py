"""The CLI's structured-error contract: one JSON envelope, one non-zero exit.

Before this contract landed, ``apple-mail search --json`` answered with three
different shapes — ``items``/``returned`` on success, ``results``/``total``
plus a *string* ``error`` for an unknown account, and a bare
``{"result": "Error: Mailbox not found: ..."}`` with no code at all for an
unknown mailbox — and every one of them exited **0**, so a shell caller
checking ``$?`` was told the run succeeded.

These tests pin both halves of the fix:

* every structured error renders the canonical ``ToolError.to_dict()``
  envelope from ``backend/base.py`` (``error``/``code``/``message``/
  ``remediation``), the same shape agents already receive at the MCP boundary;
* every structured error exits non-zero, while success and *partial* failure
  (results plus a plural ``errors`` list) still exit 0.

The last group is the safety net for the gates: ``quick-check``, ``perf-test``,
and ``smoke-test`` compute their own exit codes and must not inherit the
tool-error exit.
"""

import json
import unittest
from unittest.mock import patch

from apple_mail_mcp import cli
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error

# The shape a --json consumer is allowed to depend on.
ENVELOPE_KEYS = {"error", "code", "message", "remediation"}

ACCOUNT_NOT_FOUND_PAYLOAD = json.dumps(
    {
        "results": [],
        "total": 0,
        "error": "account_not_found",
        "account": "Nope",
        "available_accounts": ["Work", "Personal"],
    }
)

MAILBOX_NOT_FOUND_PAYLOAD = "Error: Mailbox not found: __NO_SUCH_MAILBOX__"

UNBOUNDED_SCAN_PAYLOAD = serialize_tool_error(
    ToolError(
        code="UNBOUNDED_SCAN_REQUIRED",
        message="search_emails refuses to scan without a date window",
        remediation={"preferred": "Pass recent_days=7"},
    )
)

SUCCESS_PAYLOAD = json.dumps({"items": [], "returned": 0, "has_more": False})

# A search that reached Mail, returned rows, and lost one mailbox on the way.
# Results plus a plural `errors` list is a success with caveats, not a failed
# call, and `_result_is_error` (which the perf battery reads) keeps owning it.
PARTIAL_FAILURE_PAYLOAD = json.dumps(
    {
        "items": [{"id": "1"}],
        "returned": 1,
        "errors": ["Work"],
        "error_details": [{"account": "Work", "type": "timeout", "message": "timed out"}],
    }
)


def _run_search(payload, *, json_mode=True):
    """Run ``apple-mail search`` against a stubbed tool; return (exit code, printed)."""
    argv = ["search", "--account", "Work", "--query", "x"]
    if json_mode:
        argv.append("--json")
    with (
        patch("apple_mail_mcp.tools.search.search_emails", return_value=payload),
        patch("builtins.print") as mock_print,
    ):
        code = cli.main(argv)
    printed = mock_print.call_args.args[0] if mock_print.call_args else ""
    return code, printed


class SingleErrorEnvelopeTests(unittest.TestCase):
    """Defect A: every error class renders one envelope with a code."""

    def test_account_error_renders_canonical_envelope(self):
        code, printed = _run_search(ACCOUNT_NOT_FOUND_PAYLOAD)
        payload = json.loads(printed)

        self.assertEqual(code, 1)
        self.assertIs(payload["error"], True)
        self.assertEqual(payload["code"], "ACCOUNT_NOT_FOUND")
        self.assertEqual(payload["remediation"], {})
        # The success-shaped keys no longer masquerade as a result set, but the
        # recovery information the tool sent is not thrown away.
        self.assertNotIn("results", payload)
        self.assertNotIn("total", payload)
        self.assertEqual(payload["details"]["available_accounts"], ["Work", "Personal"])
        self.assertEqual(payload["details"]["account"], "Nope")

    def test_mailbox_error_string_gains_an_envelope_and_a_code(self):
        code, printed = _run_search(MAILBOX_NOT_FOUND_PAYLOAD)
        payload = json.loads(printed)

        self.assertEqual(code, 1)
        self.assertIs(payload["error"], True)
        # The tool layer emits no code for this one, so the CLI reports the
        # generic code rather than minting vocabulary the catalogue lacks.
        self.assertEqual(payload["code"], "TOOL_ERROR")
        self.assertEqual(payload["message"], "Mailbox not found: __NO_SUCH_MAILBOX__")
        # The old `{"result": "Error: ..."}` wrapper is gone.
        self.assertNotIn("result", payload)

    def test_canonical_tool_error_passes_through_unchanged(self):
        code, printed = _run_search(UNBOUNDED_SCAN_PAYLOAD)
        payload = json.loads(printed)

        self.assertEqual(code, 1)
        self.assertEqual(payload, json.loads(UNBOUNDED_SCAN_PAYLOAD))
        self.assertNotIn("details", payload)

    def test_every_error_class_shares_one_parse_path(self):
        for label, payload_text in (
            ("account", ACCOUNT_NOT_FOUND_PAYLOAD),
            ("mailbox", MAILBOX_NOT_FOUND_PAYLOAD),
            ("bounded-scan", UNBOUNDED_SCAN_PAYLOAD),
        ):
            with self.subTest(error_class=label):
                _, printed = _run_search(payload_text)
                payload = json.loads(printed)
                self.assertTrue(set(payload) >= ENVELOPE_KEYS)
                self.assertIs(payload["error"], True)
                self.assertIsInstance(payload["code"], str)
                self.assertTrue(payload["code"])
                self.assertIsInstance(payload["message"], str)
                self.assertIsInstance(payload["remediation"], dict)

    def test_embedded_upper_snake_code_is_lifted_out_of_error_text(self):
        code, printed = _run_search("Error: FORWARD_DRAFT_ID_MISMATCH\nthe saved draft id moved")
        payload = json.loads(printed)

        self.assertEqual(code, 1)
        self.assertEqual(payload["code"], "FORWARD_DRAFT_ID_MISMATCH")

    def test_english_first_word_is_not_mistaken_for_a_code(self):
        code, printed = _run_search("Error: offset must be >= 0")
        payload = json.loads(printed)

        self.assertEqual(code, 1)
        self.assertEqual(payload["code"], "TOOL_ERROR")
        self.assertEqual(payload["message"], "offset must be >= 0")


class ErrorExitStatusTests(unittest.TestCase):
    """Defect B: a structured error must be visible in ``$?``."""

    def test_structured_error_exits_non_zero(self):
        for label, payload_text in (
            ("account", ACCOUNT_NOT_FOUND_PAYLOAD),
            ("mailbox", MAILBOX_NOT_FOUND_PAYLOAD),
            ("bounded-scan", UNBOUNDED_SCAN_PAYLOAD),
        ):
            with self.subTest(error_class=label):
                code, _ = _run_search(payload_text)
                self.assertEqual(code, cli.TOOL_ERROR_EXIT_CODE)
                self.assertNotEqual(code, 0)

    def test_text_mode_error_exits_non_zero_and_keeps_human_wording(self):
        code, printed = _run_search(MAILBOX_NOT_FOUND_PAYLOAD, json_mode=False)

        self.assertEqual(code, 1)
        self.assertEqual(printed, MAILBOX_NOT_FOUND_PAYLOAD)

    def test_dict_returning_tool_error_also_exits_non_zero(self):
        # `get_mailbox_unread_counts` returns a dict, not a JSON string.
        with (
            patch(
                "apple_mail_mcp.tools.inbox.get_mailbox_unread_counts",
                return_value={"error": "timed_out", "message": "AppleScript timed out"},
            ),
            patch("builtins.print") as mock_print,
        ):
            code = cli.main(["unread", "--account", "Work", "--json"])

        payload = json.loads(mock_print.call_args.args[0])
        self.assertEqual(code, 1)
        self.assertEqual(payload["code"], "TIMED_OUT")
        self.assertEqual(payload["message"], "AppleScript timed out")


class SuccessStillExitsZeroTests(unittest.TestCase):
    """The other half of the contract: nothing healthy started failing."""

    def test_success_payload_is_untouched_and_exits_zero(self):
        code, printed = _run_search(SUCCESS_PAYLOAD)

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed), json.loads(SUCCESS_PAYLOAD))

    def test_partial_failure_with_results_is_not_a_failed_call(self):
        code, printed = _run_search(PARTIAL_FAILURE_PAYLOAD)
        payload = json.loads(printed)

        self.assertEqual(code, 0)
        self.assertEqual(payload["returned"], 1)
        self.assertEqual(payload["errors"], ["Work"])

    def test_falsy_error_key_is_not_an_error(self):
        # `get_inbox_overview` carries a top-level `error: None` on success.
        with (
            patch(
                "apple_mail_mcp.tools.inbox.get_inbox_overview",
                return_value={"error": None, "accounts": [], "total_unread": 0},
            ),
            patch("builtins.print"),
        ):
            code = cli.main(["overview", "--account", "Work", "--json"])

        self.assertEqual(code, 0)


class GateExitCodesUnchangedTests(unittest.TestCase):
    """`quick-check` / `perf-test` / `smoke-test` own their exit codes.

    `tools/gates/dev-check.sh live` runs `.venv/bin/apple-mail quick-check
    --json` under `set -e`, so a passing battery leaking the tool-error exit
    would turn a green gate red.
    """

    def test_passing_quick_check_still_exits_zero(self):
        with (
            patch.object(cli, "run_perf_battery", return_value={"ok": True, "cases": []}),
            patch("builtins.print"),
        ):
            self.assertEqual(cli.main(["quick-check", "--json"]), 0)

    def test_failing_quick_check_still_exits_one(self):
        with (
            patch.object(cli, "run_perf_battery", return_value={"ok": False, "cases": []}),
            patch("builtins.print"),
        ):
            self.assertEqual(cli.main(["quick-check", "--json"]), 1)

    def test_perf_report_carrying_an_error_key_is_not_rewritten(self):
        # The perf payload legitimately carries a top-level `error` on account
        # resolution failure; `_print_result` must keep printing it verbatim.
        payload = {"ok": False, "error": "No Mail accounts configured", "cases": []}
        with (
            patch.object(cli, "run_perf_battery", return_value=payload),
            patch("builtins.print") as mock_print,
        ):
            code = cli.main(["perf-test", "--json"])

        self.assertEqual(code, 1)
        self.assertEqual(json.loads(mock_print.call_args.args[0]), payload)


if __name__ == "__main__":
    unittest.main()
