"""Performance battery and report for the Apple Mail CLI.

The four perf seams patched by the tests as module attributes of
``apple_mail_mcp.cli`` (``_mailbox_count``, ``_timed_call``,
``_resolve_test_account``, ``_evaluate_perf_case``) are routed through
``cli.<name>`` at call time so ``patch.object(cli, ...)`` keeps taking effect.
Tool imports stay lazy inside ``build_perf_cases``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from apple_mail_mcp import cli
from apple_mail_mcp.cli.constants import (
    DEFAULT_PERF_PROFILE,
    INVALID_ACCOUNT,
    NO_HIT_SUBJECT,
    PERF_PROFILES,
    PERF_THRESHOLDS_MS,
)
from apple_mail_mcp.cli.formatting import (
    _await_if_coro,
    _is_expected_account_not_found,
    _parse_tool_result,
    _print_result,
    _redact,
    _result_is_error,
)


def metadata_threshold_ms(mailbox_count: int) -> int:
    return 2000 + max(0, mailbox_count - 20) * 35


def resolve_perf_thresholds(profile: str = DEFAULT_PERF_PROFILE) -> dict[str, int]:
    thresholds = dict(PERF_THRESHOLDS_MS)
    overrides = PERF_PROFILES.get(profile, PERF_PROFILES[DEFAULT_PERF_PROFILE])
    thresholds.update(overrides)
    return thresholds


def _mailbox_count(account: str) -> int:
    from apple_mail_mcp.tools.inbox import list_mailboxes

    raw = list_mailboxes(account=account, include_counts=False, output_format="json")
    parsed = _parse_tool_result(_await_if_coro(raw))
    return len(parsed) if isinstance(parsed, list) else 0


def _resolve_test_account(explicit: str | None) -> tuple[str | None, str | None]:
    from apple_mail_mcp import server as _server
    from apple_mail_mcp.tools.inbox import list_accounts

    if explicit:
        return explicit, None
    if _server.DEFAULT_MAIL_ACCOUNT:
        return _server.DEFAULT_MAIL_ACCOUNT, None
    accounts = list_accounts()
    if accounts:
        return accounts[0], None
    return None, "No Mail accounts configured"


@dataclass(frozen=True)
class PerfCase:
    name: str
    category: str
    threshold_ms: int
    runner: Callable[[], Any]
    expect_error: bool = False


def _timed_call(fn: Callable[[], Any]) -> tuple[Any, float]:
    start = time.perf_counter()
    result = _await_if_coro(fn())
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return result, elapsed_ms


def _evaluate_perf_case(case: PerfCase, *, verbose_sensitive: bool = False) -> dict[str, Any]:
    try:
        result, duration_ms = cli._timed_call(case.runner)
        parsed = _parse_tool_result(result)
        error = _result_is_error(parsed)
        if case.expect_error:
            passed = duration_ms < case.threshold_ms and _is_expected_account_not_found(parsed)
        else:
            passed = duration_ms < case.threshold_ms and not error
        entry: dict[str, Any] = {
            "name": case.name,
            "category": case.category,
            "duration_ms": round(duration_ms, 1),
            "threshold_ms": case.threshold_ms,
            "pass": passed,
            "sample": _redact(parsed, verbose_sensitive=verbose_sensitive),
        }
        if error or case.expect_error:
            entry["error"] = parsed if isinstance(parsed, str) else parsed.get("error", "tool_error")
        return entry
    except Exception as exc:  # pragma: no cover - live safety path
        return {
            "name": case.name,
            "category": case.category,
            "duration_ms": None,
            "threshold_ms": case.threshold_ms,
            "pass": False,
            "error": str(exc),
        }


def build_perf_cases(
    account: str,
    *,
    quick: bool = False,
    include_analysis: bool = False,
    profile: str = DEFAULT_PERF_PROFILE,
    mailbox_count: int | None = None,
) -> list[PerfCase]:
    from apple_mail_mcp.tools.analytics import (
        _get_recent_emails_structured_async,
        get_statistics,
    )
    from apple_mail_mcp.tools.inbox import (
        get_inbox_overview,
        get_mailbox_unread_counts,
        list_account_addresses,
        list_accounts,
        list_inbox_emails,
        list_mailboxes,
    )
    from apple_mail_mcp.tools.manage import manage_trash, move_email
    from apple_mail_mcp.tools.search import search_emails
    from apple_mail_mcp.tools.smart_inbox import (
        get_awaiting_reply,
        get_needs_response,
        get_top_senders,
    )

    thresholds = resolve_perf_thresholds(profile)
    if mailbox_count is None:
        mailbox_count = cli._mailbox_count(account)
    metadata_threshold = metadata_threshold_ms(mailbox_count)

    async def _dashboard_metadata_probe() -> dict[str, Any]:
        unread = await asyncio.to_thread(
            get_mailbox_unread_counts,
            account=account,
            summary_only=True,
            timeout=30,
        )
        recent = await _get_recent_emails_structured_async(
            account=account,
            max_total=5,
            max_per_account=3,
            include_preview=False,
            timeout=30,
        )
        return {"unread": unread, "recent": recent}

    cases: list[PerfCase] = [
        PerfCase(
            name="metadata",
            category="metadata",
            threshold_ms=metadata_threshold,
            runner=lambda: {
                "accounts": list_accounts(),
                "addresses": list_account_addresses(),
                "mailboxes": _parse_tool_result(
                    _await_if_coro(
                        list_mailboxes(
                            account=account,
                            include_counts=False,
                            output_format="json",
                        )
                    )
                ),
            },
        ),
        PerfCase(
            name="no_hit_search",
            category="no_hit_search",
            threshold_ms=thresholds["no_hit_search"],
            runner=lambda: search_emails(
                account=account,
                subject_keyword=NO_HIT_SUBJECT,
                output_format="json",
                limit=1,
            ),
        ),
        PerfCase(
            name="inbox",
            category="inbox",
            threshold_ms=thresholds["inbox"],
            runner=lambda: list_inbox_emails(
                account=account,
                max_emails=1 if quick else 2,
                include_read=True,
                include_content=False,
                output_format="json",
            ),
        ),
    ]

    if quick:
        return cases

    cases.extend(
        [
            PerfCase(
                name="dry_run_move",
                category="dry_run",
                threshold_ms=thresholds["dry_run"],
                runner=lambda: move_email(
                    account=account,
                    to_mailbox="Archive",
                    message_ids=["0"],
                    dry_run=True,
                    max_moves=1,
                ),
            ),
            PerfCase(
                name="dry_run_trash",
                category="dry_run",
                threshold_ms=thresholds["dry_run"],
                runner=lambda: manage_trash(
                    account=account,
                    action="move_to_trash",
                    message_ids=["0"],
                    dry_run=True,
                    max_deletes=1,
                ),
            ),
            PerfCase(
                name="overview",
                category="overview",
                threshold_ms=thresholds["overview"],
                runner=lambda: get_inbox_overview(
                    account=account,
                    output_format="compact",
                    include_mailboxes=False,
                    include_recent=False,
                    include_suggestions=False,
                ),
            ),
            PerfCase(
                name="bad_account",
                category="bad_account",
                threshold_ms=thresholds["bad_account"],
                expect_error=True,
                runner=lambda: list_inbox_emails(
                    account=INVALID_ACCOUNT,
                    max_emails=1,
                    output_format="json",
                ),
            ),
            PerfCase(
                name="dashboard_metadata",
                category="dashboard",
                threshold_ms=thresholds["dashboard"],
                runner=lambda: _await_if_coro(_dashboard_metadata_probe()),
            ),
        ]
    )

    if include_analysis and not quick:
        cases.extend(
            [
                PerfCase(
                    name="needs_response",
                    category="needs_response",
                    threshold_ms=thresholds["needs_response"],
                    runner=lambda: get_needs_response(
                        account=account,
                        days_back=2,
                        max_results=5,
                        check_already_replied=False,
                    ),
                ),
                PerfCase(
                    name="awaiting_reply",
                    category="awaiting_reply",
                    threshold_ms=thresholds["awaiting_reply"],
                    runner=lambda: get_awaiting_reply(
                        account=account,
                        days_back=7,
                        max_results=3,
                    ),
                ),
                PerfCase(
                    name="top_senders",
                    category="top_senders",
                    threshold_ms=thresholds["top_senders"],
                    runner=lambda: get_top_senders(
                        account=account,
                        days_back=30,
                        mailbox="INBOX",
                        top_n=5,
                    ),
                ),
                PerfCase(
                    name="statistics_overview",
                    category="statistics",
                    threshold_ms=thresholds["statistics_overview"],
                    runner=lambda: get_statistics(
                        account=account,
                        scope="account_overview",
                        days_back=2,
                        output_format="json",
                    ),
                ),
            ]
        )

    return cases


def run_perf_battery(
    account: str | None = None,
    *,
    quick: bool = False,
    include_analysis: bool = False,
    allow_heavy_mail_scan: bool = False,
    profile: str = DEFAULT_PERF_PROFILE,
    verbose_sensitive: bool = False,
) -> dict[str, Any]:
    thresholds = resolve_perf_thresholds(profile)
    if include_analysis and not allow_heavy_mail_scan:
        return {
            "ok": False,
            "account": account,
            "quick": quick,
            "include_analysis": include_analysis,
            "profile": profile,
            "thresholds_ms": thresholds,
            "cases": [],
            "error": (
                "--include-analysis requires --allow-heavy-mail-scan. "
                "Analysis probes can touch many message headers and may cause "
                "Mail.app to fetch remote messages on large accounts."
            ),
        }
    selected_account, account_error = cli._resolve_test_account(account)
    if not selected_account:
        return {
            "ok": False,
            "account": None,
            "quick": quick,
            "include_analysis": include_analysis,
            "profile": profile,
            "thresholds_ms": thresholds,
            "cases": [],
            "error": account_error,
        }

    mailbox_count = cli._mailbox_count(selected_account)
    cases = build_perf_cases(
        selected_account,
        quick=quick,
        include_analysis=include_analysis,
        profile=profile,
        mailbox_count=mailbox_count,
    )
    results = [cli._evaluate_perf_case(case, verbose_sensitive=verbose_sensitive) for case in cases]
    ok = all(item["pass"] for item in results)
    total_ms = sum(item["duration_ms"] or 0 for item in results)
    return {
        "ok": ok,
        "account": selected_account,
        "quick": quick,
        "include_analysis": include_analysis,
        "profile": profile,
        "mailbox_count": mailbox_count,
        "metadata_threshold_ms": metadata_threshold_ms(mailbox_count),
        "thresholds_ms": thresholds,
        "total_duration_ms": round(total_ms, 1),
        "cases": results,
    }


def _print_perf_report(payload: dict[str, Any], *, json_mode: bool) -> None:
    if json_mode:
        _print_result(payload, json_mode=True)
        return

    if payload.get("error") and not payload.get("cases"):
        print(f"failed account resolution: {payload['error']}")
        return

    mode = "quick" if payload.get("quick") else "full"
    analysis = " +analysis" if payload.get("include_analysis") else ""
    profile = payload.get("profile", DEFAULT_PERF_PROFILE)
    mailbox_count = payload.get("mailbox_count")
    mailbox_note = f" mailboxes={mailbox_count}" if mailbox_count is not None else ""
    print(f"perf-test ({mode}{analysis}, profile={profile}) account={payload.get('account')}{mailbox_note}")
    for item in payload.get("cases", []):
        status = "pass" if item.get("pass") else "FAIL"
        duration = item.get("duration_ms")
        threshold = item.get("threshold_ms")
        duration_text = f"{duration:.1f}ms" if duration is not None else "n/a"
        print(f"  {status} {item['name']} ({item['category']}): {duration_text} / {threshold}ms")
        if item.get("error"):
            print(f"         error: {item['error']}")
    total = payload.get("total_duration_ms")
    if total is not None:
        print(f"total: {total:.1f}ms")
