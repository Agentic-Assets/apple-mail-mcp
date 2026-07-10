"""Subcommand handlers for the Apple Mail CLI.

Tool imports stay lazy inside each handler (the tests' source-patch seams).
``_cmd_smoke_test``, ``_cmd_perf_test``, and ``_cmd_quick_check`` route
``run_perf_battery`` and ``_resolve_test_account`` through ``cli.<name>`` so
``patch.object(cli, ...)`` keeps taking effect.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from apple_mail_mcp import cli
from apple_mail_mcp.cli.constants import DEFAULT_PERF_PROFILE, INVALID_ACCOUNT, NO_HIT_SUBJECT
from apple_mail_mcp.cli.formatting import (
    _await_if_coro,
    _is_expected_account_not_found,
    _parse_csv_arg,
    _parse_tool_result,
    _print_result,
    _read_text_arg,
    _redact,
    _run_tool,
)
from apple_mail_mcp.cli.perf import _print_perf_report


def _cmd_accounts(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.inbox import list_accounts

    return _run_tool(list_accounts, args.json)


def _cmd_addresses(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.inbox import list_account_addresses

    return _run_tool(list_account_addresses, args.json)


def _cmd_inbox(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.inbox import list_inbox_emails

    max_emails = args.max_emails if args.max_emails is not None else args.limit
    return _run_tool(
        list_inbox_emails,
        args.json,
        account=args.account,
        max_emails=max_emails,
        read_status="unread" if args.unread_only else "all",
        include_content=args.content,
        output_format="json" if args.json else "text",
    )


def _cmd_search(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.search import search_emails

    subject = args.subject or args.query
    mailboxes = [mb.strip() for mb in args.mailboxes.split(",") if mb.strip()] if args.mailboxes else None
    return _run_tool(
        search_emails,
        args.json,
        account=args.account,
        mailbox=args.mailbox,
        mailboxes=mailboxes,
        subject_keyword=subject,
        sender=args.sender,
        sender_exact=args.sender_exact,
        sender_domain=args.sender_domain,
        internet_message_id=args.internet_message_id,
        body_text=args.body,
        allow_body_scan=args.allow_body_scan,
        date_from=args.date_from,
        date_to=args.date_to,
        include_content=args.content,
        limit=args.limit,
        offset=args.offset,
        output_format="json" if args.json else "text",
    )


def _cmd_show(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.search import get_email_by_id

    return _run_tool(
        get_email_by_id,
        args.json,
        account=args.account,
        message_id=args.message_id,
        mailbox=args.mailbox,
        include_content=not args.no_content,
        max_content_length=args.max_content_length,
        output_format="json" if args.json else "text",
    )


def _cmd_mailboxes(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.inbox import list_mailboxes

    return _run_tool(
        list_mailboxes,
        args.json,
        account=args.account,
        include_counts=args.counts and not args.no_counts,
        output_format="json" if args.json else "text",
    )


def _cmd_unread(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.inbox import get_mailbox_unread_counts

    return _run_tool(
        get_mailbox_unread_counts,
        args.json,
        account=args.account,
        include_zero=args.include_zero,
        summary_only=args.summary,
    )


def _cmd_overview(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.inbox import get_inbox_overview

    output_format = args.output_format
    if args.json and output_format == "text":
        output_format = "json"

    return _run_tool(
        get_inbox_overview,
        args.json,
        account=args.account,
        output_format=output_format,
        include_mailboxes=not args.no_mailboxes,
        include_recent=not args.no_recent,
        include_suggestions=not args.no_suggestions,
    )


def _cmd_needs_response(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.smart_inbox import get_needs_response

    return _run_tool(
        get_needs_response,
        args.json,
        account=args.account,
        mailbox=args.mailbox,
        days_back=args.days_back,
        max_results=args.max_results,
    )


def _cmd_awaiting_reply(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.smart_inbox import get_awaiting_reply

    return _run_tool(
        get_awaiting_reply,
        args.json,
        account=args.account,
        days_back=args.days_back,
        max_results=args.max_results,
    )


def _cmd_top_senders(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.smart_inbox import get_top_senders

    return _run_tool(
        get_top_senders,
        args.json,
        account=args.account,
        mailbox=args.mailbox,
        days_back=args.days_back,
        top_n=args.top_n,
        group_by_domain=args.group_by_domain,
    )


def _cmd_statistics(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.analytics import get_statistics

    return _run_tool(
        get_statistics,
        args.json,
        account=args.account,
        scope=args.scope,
        sender=args.sender,
        mailbox=args.mailbox,
        days_back=args.days_back,
        output_format="json" if args.json else "text",
    )


def _cmd_move_dry_run(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.manage import move_email

    return _run_tool(
        move_email,
        args.json,
        account=args.account,
        to_mailbox=args.to_mailbox,
        from_mailbox=args.from_mailbox,
        message_ids=_parse_csv_arg(args.message_ids),
        subject_keyword=args.subject,
        sender=args.sender,
        allow_filter_scan=args.allow_filter_scan,
        max_moves=args.max_moves,
        dry_run=True,
    )


def _cmd_trash_dry_run(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.manage import manage_trash

    return _run_tool(
        manage_trash,
        args.json,
        account=args.account,
        action="move_to_trash",
        mailbox=args.mailbox,
        message_ids=_parse_csv_arg(args.message_ids),
        subject_keyword=args.subject,
        sender=args.sender,
        allow_filter_scan=args.allow_filter_scan,
        max_deletes=args.max_deletes,
        dry_run=True,
    )


def _cmd_drafts(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.compose import manage_drafts

    if args.drafts_action == "list":
        return _run_tool(
            manage_drafts,
            args.json,
            account=args.account,
            action="list",
            hide_empty=args.hide_empty,
        )
    if args.drafts_action == "cleanup-empty":
        return _run_tool(
            manage_drafts,
            args.json,
            account=args.account,
            action="cleanup_empty",
            dry_run=not args.execute,
            max_deletes=args.max_deletes,
        )
    print(f"Unsupported drafts action: {args.drafts_action}", file=sys.stderr)
    return 2


def _cmd_draft(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.compose import compose_email

    try:
        body = _read_text_arg(args.body, args.body_file)
        body_html = Path(args.html_file).expanduser().read_text() if args.html_file else None
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    return _run_tool(
        compose_email,
        args.json,
        account=args.account,
        to=args.to,
        subject=args.subject,
        body=body,
        cc=args.cc,
        bcc=args.bcc,
        mode="open" if args.open else "draft",
        body_html=body_html,
        from_address=args.from_address,
        include_signature=not args.no_signature,
        signature_name=args.signature_name,
        standalone_confirmed=args.standalone_confirmed,
    )


def _cmd_calendars(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.calendar import list_calendars

    return _run_tool(list_calendars, args.json, output_format="json" if args.json else "text")


def _cmd_calendar_events(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.calendar import list_events

    return _run_tool(
        list_events,
        args.json,
        calendar=args.calendar,
        days_back=args.days_back,
        days_ahead=args.days_ahead,
        query=args.query,
        timezone=args.timezone,
        limit=args.limit,
        output_format="json" if args.json else "text",
    )


def _cmd_calendar_grant(args: argparse.Namespace) -> int:
    """Request EventKit Calendars full access with a bounded run-loop pump.

    This is the ONLY code path in the package that may trigger the EventKit
    consent prompt, and it is reachable exclusively by a human running the
    CLI from a terminal. Exit codes: 0 granted, 2 denied/restricted,
    3 unavailable or timed out (permission-specific codes so scripts can
    branch without parsing text).
    """
    import time

    from apple_mail_mcp.calendar_core.eventkit import eventkit_status, load_frameworks

    available, reason = eventkit_status()
    if available:
        print("Calendars full access is already granted for this process ancestry.")
        return 0
    if reason.startswith("dependency_missing"):
        print(f"EventKit fast path unavailable: {reason}", file=sys.stderr)
        return 3
    if reason in ("denied", "restricted"):
        print(
            f"Calendars access is {reason}. Enable it under System Settings > Privacy & Security > "
            "Calendars for the app that launches this process, or run: tccutil reset Calendar",
            file=sys.stderr,
        )
        return 2

    frameworks = load_frameworks()
    if frameworks is None:  # pragma: no cover - eventkit_status covered this
        print("EventKit frameworks are not importable.", file=sys.stderr)
        return 3
    eventkit_mod, foundation_mod = frameworks
    store = eventkit_mod.EKEventStore.alloc().init()
    outcome: dict[str, Any] = {}

    def _completion(granted: bool, error: Any) -> None:
        outcome["granted"] = bool(granted)
        outcome["error"] = str(error) if error else None

    request = getattr(store, "requestFullAccessToEventsWithCompletion_", None)
    if request is not None:
        request(_completion)
    else:  # pragma: no cover - pre-macOS-14 fallback
        entity = getattr(eventkit_mod, "EKEntityTypeEvent", 0)
        store.requestAccessToEntityType_completion_(entity, _completion)

    print("Requested Calendars full access; answer the macOS prompt if one appears...")
    run_loop = foundation_mod.NSRunLoop.currentRunLoop()
    deadline = time.monotonic() + args.wait_seconds
    while "granted" not in outcome and time.monotonic() < deadline:
        run_loop.runMode_beforeDate_(
            getattr(foundation_mod, "NSDefaultRunLoopMode", "kCFRunLoopDefaultMode"),
            foundation_mod.NSDate.dateWithTimeIntervalSinceNow_(0.25),
        )

    if outcome.get("granted"):
        print("Granted: the EventKit read fast path is now available.")
        return 0
    if "granted" in outcome:
        print(f"Denied by the user or the system. {outcome.get('error') or ''}".strip(), file=sys.stderr)
        return 2
    print(
        f"No response within {args.wait_seconds:.0f}s. If no prompt appeared, the launching app "
        "may lack a Calendars usage description (known limitation inside Claude Desktop and "
        "Codex Desktop); run this command from Terminal instead.",
        file=sys.stderr,
    )
    return 3


def _cmd_mcp_config(args: argparse.Namespace) -> int:
    start_script = Path(args.repo).expanduser() / "plugin" / "start_mcp.sh"
    tool_args = [str(start_script)]
    if not args.unsafe_send:
        tool_args.append("--draft-safe")
    payload = {
        "mcpServers": {
            "apple-mail": {
                "command": "/bin/bash",
                "args": tool_args,
            }
        }
    }
    return _print_result(payload, json_mode=True)


def _cmd_smoke_test(args: argparse.Namespace) -> int:
    from apple_mail_mcp import server as _server
    from apple_mail_mcp.tools.compose import _send_blocked
    from apple_mail_mcp.tools.inbox import list_accounts, list_inbox_emails
    from apple_mail_mcp.tools.search import search_emails

    checks: list[dict[str, Any]] = []

    def record(name: str, fn: Callable[[], Any]) -> None:
        try:
            value = _await_if_coro(fn())
            checks.append(
                {
                    "name": name,
                    "ok": True,
                    "detail": _redact(_parse_tool_result(value)),
                }
            )
        except Exception as exc:  # pragma: no cover - live safety path
            checks.append({"name": name, "ok": False, "error": str(exc)})

    def record_expect(name: str, fn: Callable[[], Any], predicate: Callable[[Any], bool]) -> None:
        try:
            value = _await_if_coro(fn())
            parsed = _parse_tool_result(value)
            ok = predicate(parsed)
            entry: dict[str, Any] = {
                "name": name,
                "ok": ok,
                "detail": _redact(parsed),
            }
            if not ok:
                entry["error"] = "unexpected_result"
            checks.append(entry)
        except Exception as exc:  # pragma: no cover - live safety path
            checks.append({"name": name, "ok": False, "error": str(exc)})

    accounts = list_accounts()
    selected_account, _ = cli._resolve_test_account(args.account)
    record("accounts", lambda: {"count": len(accounts)})

    if selected_account:
        record(
            "inbox_json",
            lambda: list_inbox_emails(
                account=selected_account,
                max_emails=1,
                include_read=True,
                include_content=False,
                output_format="json",
            ),
        )
        record(
            "no_hit_search",
            lambda: search_emails(
                account=selected_account,
                subject_keyword=NO_HIT_SUBJECT,
                output_format="json",
                limit=1,
            ),
        )
    else:
        checks.append({"name": "mail_account_required", "ok": False})

    record_expect(
        "invalid_account",
        lambda: list_inbox_emails(
            account=INVALID_ACCOUNT,
            max_emails=1,
            output_format="json",
        ),
        _is_expected_account_not_found,
    )

    def _draft_safe_blocked() -> str:
        previous = _server.DRAFT_SAFE
        _server.DRAFT_SAFE = True
        try:
            return _send_blocked("send") or ""
        finally:
            _server.DRAFT_SAFE = previous

    record_expect(
        "draft_safe_send_block",
        _draft_safe_blocked,
        lambda value: isinstance(value, str) and "draft-safe" in value.lower(),
    )

    ok = all(item["ok"] for item in checks)
    payload = {"ok": ok, "account": selected_account, "checks": checks}
    if args.json:
        _print_result(payload, json_mode=True)
    else:
        for item in checks:
            status = "ok" if item["ok"] else "failed"
            print(f"{status} {item['name']}")
    return 0 if ok else 1


def _cmd_perf_test(args: argparse.Namespace) -> int:
    payload = cli.run_perf_battery(
        args.account,
        quick=args.quick,
        include_analysis=args.include_analysis,
        allow_heavy_mail_scan=args.allow_heavy_mail_scan,
        profile=args.profile,
        verbose_sensitive=args.verbose_sensitive,
    )
    _print_perf_report(payload, json_mode=args.json)
    return 0 if payload.get("ok") else 1


def _cmd_quick_check(args: argparse.Namespace) -> int:
    payload = cli.run_perf_battery(
        args.account,
        quick=True,
        include_analysis=False,
        allow_heavy_mail_scan=False,
        profile=DEFAULT_PERF_PROFILE,
        verbose_sensitive=args.verbose_sensitive,
    )
    _print_perf_report(payload, json_mode=args.json)
    return 0 if payload.get("ok") else 1
