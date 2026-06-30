"""argparse construction for the Apple Mail CLI.

The full subparser tree is built lazily inside ``main()``; no tool calls run at
import time.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from apple_mail_mcp.cli.constants import DEFAULT_PERF_PROFILE, PERF_PROFILES
from apple_mail_mcp.cli.formatting import _version


def _add_account_flag(parser: argparse.ArgumentParser, required: bool = False) -> None:
    parser.add_argument("--account", required=required, help="Mail account name")


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print structured JSON")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apple-mail",
        description="Portable CLI for the Apple Mail MCP tools.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    accounts = subparsers.add_parser("accounts", help="List Mail accounts")
    _add_json_flag(accounts)

    addresses = subparsers.add_parser("addresses", help="List configured email addresses by account")
    _add_json_flag(addresses)

    inbox = subparsers.add_parser("inbox", help="List inbox emails")
    _add_account_flag(inbox)
    inbox.add_argument("--limit", type=int, default=10, help="Maximum emails per account")
    inbox.add_argument(
        "--max-emails",
        type=int,
        dest="max_emails",
        help="Maximum emails per account; alias for --limit",
    )
    inbox.add_argument("--unread-only", action="store_true", help="Only include unread emails")
    inbox.add_argument("--content", action="store_true", help="Include content preview")
    _add_json_flag(inbox)

    search = subparsers.add_parser("search", help="Discover candidate emails; use returned ids for actions")
    _add_account_flag(search)
    search.add_argument("--mailbox", default="INBOX", help="Mailbox path")
    search.add_argument(
        "--mailboxes",
        help="Comma-separated folder names to search instead of --mailbox "
        "(e.g. 'INBOX,Sent,Archive'); missing folders are reported and skipped",
    )
    search.add_argument("--query", help="Subject keyword alias")
    search.add_argument("--subject", help="Subject keyword")
    search.add_argument("--sender", help="Sender substring")
    search.add_argument("--sender-exact", help="Exact sender address discovery filter")
    search.add_argument("--sender-domain", help="Exact sender domain discovery filter")
    search.add_argument("--internet-message-id", help="Exact Internet Message-ID discovery filter")
    search.add_argument("--body", help="Body text search, slower; requires --allow-body-scan")
    search.add_argument(
        "--allow-body-scan",
        action="store_true",
        help="Opt in to slow body scanning when --body is set",
    )
    search.add_argument("--date-from", help="Start date YYYY-MM-DD")
    search.add_argument("--date-to", help="End date YYYY-MM-DD")
    search.add_argument("--limit", type=int, default=20, help="Maximum results")
    search.add_argument("--offset", type=int, default=0, help="Pagination offset")
    search.add_argument("--content", action="store_true", help="Include content preview")
    _add_json_flag(search)

    show = subparsers.add_parser("show", help="Fetch one email by exact message id")
    _add_account_flag(show, required=True)
    show.add_argument("--id", required=True, dest="message_id", help="Mail message id")
    show.add_argument("--mailbox", default="INBOX", help="Mailbox path")
    show.add_argument("--no-content", action="store_true", help="Do not include message content")
    show.add_argument(
        "--max-content-length",
        type=int,
        default=5000,
        help="Maximum content chars when content is enabled",
    )
    _add_json_flag(show)

    mailboxes = subparsers.add_parser("mailboxes", help="List mailboxes")
    _add_account_flag(mailboxes)
    mailboxes.add_argument(
        "--counts",
        action="store_true",
        help="Include message/unread counts (slower on large mailboxes)",
    )
    mailboxes.add_argument(
        "--no-counts",
        action="store_true",
        help="Deprecated compatibility flag; counts are skipped by default",
    )
    _add_json_flag(mailboxes)

    unread = subparsers.add_parser("unread", help="Unread counts by mailbox or account")
    _add_account_flag(unread)
    unread.add_argument(
        "--summary",
        action="store_true",
        help="Return per-account inbox unread totals only",
    )
    unread.add_argument(
        "--include-zero",
        action="store_true",
        help="Include mailboxes with zero unread messages",
    )
    _add_json_flag(unread)

    overview = subparsers.add_parser("overview", help="Inbox overview for one account")
    _add_account_flag(overview)
    overview.add_argument(
        "--format",
        dest="output_format",
        choices=["text", "json", "compact"],
        default="text",
        help="Output format (default: text)",
    )
    overview.add_argument("--no-mailboxes", action="store_true", help="Omit mailbox breakdown")
    overview.add_argument("--no-recent", action="store_true", help="Omit recent email preview")
    overview.add_argument("--no-suggestions", action="store_true", help="Omit suggested actions")
    _add_json_flag(overview)

    needs = subparsers.add_parser("needs-response", help="Unread emails that likely need a response")
    _add_account_flag(needs)
    needs.add_argument("--mailbox", default="INBOX", help="Mailbox to scan")
    needs.add_argument("--days", type=int, default=7, dest="days_back")
    needs.add_argument("--limit", type=int, default=20, dest="max_results")
    _add_json_flag(needs)

    awaiting = subparsers.add_parser("awaiting-reply", help="Sent emails still awaiting a reply")
    _add_account_flag(awaiting)
    awaiting.add_argument("--days", type=int, default=7, dest="days_back")
    awaiting.add_argument("--limit", type=int, default=20, dest="max_results")
    _add_json_flag(awaiting)

    top = subparsers.add_parser("top-senders", help="Most frequent senders in a mailbox")
    _add_account_flag(top)
    top.add_argument("--mailbox", default="INBOX")
    top.add_argument("--days", type=int, default=30, dest="days_back")
    top.add_argument("--limit", type=int, default=10, dest="top_n")
    top.add_argument("--by-domain", action="store_true", dest="group_by_domain", help="Group by domain")
    _add_json_flag(top)

    stats = subparsers.add_parser("statistics", help="Email statistics for an account")
    _add_account_flag(stats)
    stats.add_argument(
        "--scope",
        default="account_overview",
        choices=["account_overview", "sender_stats", "mailbox_breakdown"],
    )
    stats.add_argument("--sender", help="Sender filter for sender_stats scope")
    stats.add_argument("--mailbox", help="Mailbox for mailbox_breakdown scope")
    stats.add_argument("--days", type=int, default=30, dest="days_back")
    _add_json_flag(stats)

    move_dry = subparsers.add_parser("move-dry-run", help="Preview exact-id email moves (no changes)")
    _add_account_flag(move_dry, required=True)
    move_dry.add_argument("--to", required=True, dest="to_mailbox")
    move_dry.add_argument("--from", default="INBOX", dest="from_mailbox")
    move_dry.add_argument("--message-ids", help="Comma-separated exact Mail message ids from list/search")
    move_dry.add_argument("--subject", help="Deprecated selector; collect ids first")
    move_dry.add_argument("--sender", help="Deprecated selector; collect ids first")
    move_dry.add_argument(
        "--allow-filter-scan",
        action="store_true",
        help="Opt in to remaining date/bulk filter scans where supported",
    )
    move_dry.add_argument("--limit", type=int, default=10, dest="max_moves")
    _add_json_flag(move_dry)

    trash_dry = subparsers.add_parser("trash-dry-run", help="Preview exact-id trash moves (no changes)")
    _add_account_flag(trash_dry, required=True)
    trash_dry.add_argument("--mailbox", default="INBOX")
    trash_dry.add_argument("--message-ids", help="Comma-separated exact Mail message ids from list/search")
    trash_dry.add_argument("--subject", help="Deprecated selector; collect ids first")
    trash_dry.add_argument("--sender", help="Deprecated selector; collect ids first")
    trash_dry.add_argument(
        "--allow-filter-scan",
        action="store_true",
        help="Opt in to remaining date/bulk filter scans where supported",
    )
    trash_dry.add_argument("--limit", type=int, default=5, dest="max_deletes")
    _add_json_flag(trash_dry)

    drafts = subparsers.add_parser("drafts", help="Draft email operations")
    drafts_sub = drafts.add_subparsers(dest="drafts_action", required=True)
    drafts_list = drafts_sub.add_parser("list", help="List draft emails")
    _add_account_flag(drafts_list)
    drafts_list.add_argument(
        "--hide-empty",
        action="store_true",
        help="Skip orphaned drafts whose subject and body are both blank",
    )
    _add_json_flag(drafts_list)
    drafts_cleanup = drafts_sub.add_parser(
        "cleanup-empty",
        help="Remove orphaned blank drafts (preview-only unless --execute)",
    )
    _add_account_flag(drafts_cleanup)
    drafts_cleanup.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete (default is a dry-run preview)",
    )
    drafts_cleanup.add_argument(
        "--limit",
        type=int,
        default=20,
        dest="max_deletes",
        help="Maximum blank drafts to delete in one call (default 20)",
    )
    _add_json_flag(drafts_cleanup)

    draft = subparsers.add_parser("draft", help="Create a draft email")
    _add_account_flag(draft, required=True)
    draft.add_argument("--to", required=True, help="Recipient address(es)")
    draft.add_argument("--subject", required=True, help="Subject line")
    draft.add_argument("--body", help="Plain text body")
    draft.add_argument("--body-file", help="Read plain text body from file")
    draft.add_argument("--html-file", help="Read HTML body from file")
    draft.add_argument("--cc", help="CC address(es)")
    draft.add_argument("--bcc", help="BCC address(es)")
    draft.add_argument("--from-address", help="Sender alias on the account")
    draft.add_argument("--signature-name", help="Mail signature name to apply")
    draft.add_argument(
        "--no-signature",
        action="store_true",
        help="Do not apply the configured/default Mail signature",
    )
    draft.add_argument("--open", action="store_true", help="Open compose window")
    draft.add_argument(
        "--standalone-confirmed",
        action="store_true",
        help="Confirm this is a standalone new message (bypasses Re:/Fwd: safeguard)",
    )
    _add_json_flag(draft)

    config = subparsers.add_parser("mcp-config", help="Print Claude/OpenClaw MCP config JSON")
    config.add_argument(
        "--repo",
        default=str(Path(__file__).resolve().parents[3]),
        help="Path to the apple-mail-mcp repository checkout",
    )
    config.add_argument(
        "--unsafe-send",
        action="store_true",
        help="Omit --draft-safe from generated config",
    )

    smoke = subparsers.add_parser("smoke-test", help="Run privacy-safe live checks")
    _add_account_flag(smoke)
    _add_json_flag(smoke)

    draft_smoke = subparsers.add_parser(
        "draft-verify-smoke",
        help="Create, verify, and optionally clean up one persisted Drafts smoke artifact",
    )
    _add_account_flag(draft_smoke, required=True)
    cleanup_group = draft_smoke.add_mutually_exclusive_group(required=True)
    cleanup_group.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete the exact persisted Drafts id after verification",
    )
    cleanup_group.add_argument(
        "--leave-draft",
        action="store_true",
        help="Verify and report the exact persisted id without deleting it",
    )
    draft_smoke.add_argument(
        "--to",
        default="apple-mail-mcp-smoke@example.invalid",
        help="Smoke draft recipient address",
    )
    draft_smoke.add_argument(
        "--from-address",
        help="Sender address for the requested account. Required when the account has multiple aliases.",
    )
    draft_smoke.add_argument("--poll-timeout", type=float, default=45.0, help="Seconds to poll Drafts")
    draft_smoke.add_argument("--poll-interval", type=float, default=1.5, help="Seconds between polls")
    draft_smoke.add_argument("--list-limit", type=int, default=25, help="Newest Drafts window to inspect")
    draft_smoke.add_argument("--tool-timeout", type=int, default=30, help="Per-tool AppleScript timeout")
    _add_json_flag(draft_smoke)

    perf = subparsers.add_parser(
        "perf-test",
        help="Time safe read-only checks against Mail.app with pass/fail thresholds",
    )
    _add_account_flag(perf)
    _add_json_flag(perf)
    perf.add_argument(
        "--quick",
        action="store_true",
        help="Run a ~30s subset (metadata, no-hit search, inbox)",
    )
    perf.add_argument(
        "--verbose-sensitive",
        action="store_true",
        help="Include account names and other sensitive fields in perf samples",
    )
    perf.add_argument(
        "--include-analysis",
        action="store_true",
        help="Add analysis cases (needs-response, awaiting-reply, top-senders, statistics)",
    )
    perf.add_argument(
        "--allow-heavy-mail-scan",
        action="store_true",
        help=(
            "Required with --include-analysis. This opt-in acknowledges the "
            "analysis probes may touch many message headers on large accounts."
        ),
    )
    perf.add_argument(
        "--profile",
        choices=sorted(PERF_PROFILES),
        default=DEFAULT_PERF_PROFILE,
        help="Threshold profile: light (small account) vs production (large mailbox)",
    )

    quick = subparsers.add_parser(
        "quick-check",
        help="Fast post-edit battery (~30s); alias for perf-test --quick",
    )
    _add_account_flag(quick)
    _add_json_flag(quick)
    quick.add_argument(
        "--verbose-sensitive",
        action="store_true",
        help="Include account names and other sensitive fields in perf samples",
    )

    return parser
