"""Command-line interface for Apple Mail MCP tools.

This CLI intentionally wraps the same Python tool functions used by the MCP
server. It is a portable, repo-owned alternative to generated local wrappers.

Linker/facade for the ``apple_mail_mcp.cli`` package. The submodules re-export
their symbols here so the historical ``apple_mail_mcp.cli.<name>`` attribute
surface (console entry point + tests) is preserved unchanged. The four perf
seams (``_mailbox_count``, ``_timed_call``, ``_resolve_test_account``,
``_evaluate_perf_case``) and ``run_perf_battery`` are read through this package
namespace at call time inside ``perf``/``commands`` so ``patch.object(cli, ...)``
keeps taking effect.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from apple_mail_mcp.cli.commands import (
    _cmd_accounts,
    _cmd_addresses,
    _cmd_awaiting_reply,
    _cmd_draft,
    _cmd_drafts,
    _cmd_inbox,
    _cmd_mailboxes,
    _cmd_mcp_config,
    _cmd_move_dry_run,
    _cmd_needs_response,
    _cmd_overview,
    _cmd_perf_test,
    _cmd_quick_check,
    _cmd_search,
    _cmd_show,
    _cmd_smoke_test,
    _cmd_statistics,
    _cmd_top_senders,
    _cmd_trash_dry_run,
    _cmd_unread,
)
from apple_mail_mcp.cli.constants import (
    DEFAULT_PERF_PROFILE,
    INVALID_ACCOUNT,
    NO_HIT_SUBJECT,
    PERF_PROFILES,
    PERF_THRESHOLDS_MS,
)
from apple_mail_mcp.cli.draft_smoke import (
    _append_stage_error,
    _cleanup_smoke_draft,
    _cmd_draft_verify_smoke,
    _create_smoke_draft,
    _draft_cleanup_confirmed,
    _draft_verification_passed,
    _extract_draft_ids,
    _poll_for_verified_smoke_draft,
    _resolve_draft_smoke_from_address,
    _verify_smoke_candidates,
)
from apple_mail_mcp.cli.formatting import (
    _await_if_coro,
    _is_expected_account_not_found,
    _parse_csv_arg,
    _parse_tool_result,
    _print_result,
    _read_text_arg,
    _redact,
    _result_is_error,
    _run_tool,
    _version,
)
from apple_mail_mcp.cli.parser import _add_account_flag, _add_json_flag, _build_parser
from apple_mail_mcp.cli.perf import (
    PerfCase,
    _evaluate_perf_case,
    _mailbox_count,
    _print_perf_report,
    _resolve_test_account,
    _timed_call,
    build_perf_cases,
    metadata_threshold_ms,
    resolve_perf_thresholds,
    run_perf_battery,
)

COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "accounts": _cmd_accounts,
    "addresses": _cmd_addresses,
    "inbox": _cmd_inbox,
    "search": _cmd_search,
    "show": _cmd_show,
    "mailboxes": _cmd_mailboxes,
    "unread": _cmd_unread,
    "overview": _cmd_overview,
    "needs-response": _cmd_needs_response,
    "awaiting-reply": _cmd_awaiting_reply,
    "top-senders": _cmd_top_senders,
    "statistics": _cmd_statistics,
    "move-dry-run": _cmd_move_dry_run,
    "trash-dry-run": _cmd_trash_dry_run,
    "drafts": _cmd_drafts,
    "draft": _cmd_draft,
    "mcp-config": _cmd_mcp_config,
    "smoke-test": _cmd_smoke_test,
    "draft-verify-smoke": _cmd_draft_verify_smoke,
    "perf-test": _cmd_perf_test,
    "quick-check": _cmd_quick_check,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return COMMANDS[args.command](args)


__all__ = [
    "COMMANDS",
    "DEFAULT_PERF_PROFILE",
    "INVALID_ACCOUNT",
    "NO_HIT_SUBJECT",
    "PERF_PROFILES",
    "PERF_THRESHOLDS_MS",
    "PerfCase",
    "_add_account_flag",
    "_add_json_flag",
    "_append_stage_error",
    "_await_if_coro",
    "_build_parser",
    "_cleanup_smoke_draft",
    "_cmd_accounts",
    "_cmd_addresses",
    "_cmd_awaiting_reply",
    "_cmd_draft",
    "_cmd_draft_verify_smoke",
    "_cmd_drafts",
    "_cmd_inbox",
    "_cmd_mailboxes",
    "_cmd_mcp_config",
    "_cmd_move_dry_run",
    "_cmd_needs_response",
    "_cmd_overview",
    "_cmd_perf_test",
    "_cmd_quick_check",
    "_cmd_search",
    "_cmd_show",
    "_cmd_smoke_test",
    "_cmd_statistics",
    "_cmd_top_senders",
    "_cmd_trash_dry_run",
    "_cmd_unread",
    "_create_smoke_draft",
    "_draft_cleanup_confirmed",
    "_draft_verification_passed",
    "_evaluate_perf_case",
    "_extract_draft_ids",
    "_is_expected_account_not_found",
    "_mailbox_count",
    "_parse_csv_arg",
    "_parse_tool_result",
    "_poll_for_verified_smoke_draft",
    "_print_perf_report",
    "_print_result",
    "_read_text_arg",
    "_redact",
    "_resolve_draft_smoke_from_address",
    "_resolve_test_account",
    "_result_is_error",
    "_run_tool",
    "_timed_call",
    "_verify_smoke_candidates",
    "_version",
    "build_perf_cases",
    "main",
    "metadata_threshold_ms",
    "resolve_perf_thresholds",
    "run_perf_battery",
]


if __name__ == "__main__":
    raise SystemExit(main())
