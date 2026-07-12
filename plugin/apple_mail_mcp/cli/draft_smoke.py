"""draft-verify-smoke pipeline: pure helpers plus the command handler.

Lazy tool imports (``manage_drafts``, ``verify_draft``,
``delete_draft_if_identity_matches``, ``list_account_addresses``) stay inside
the functions so the tests' source-patch seams keep working.
"""

from __future__ import annotations

import argparse
import re
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from apple_mail_mcp.cli.formatting import _parse_tool_result, _print_result, _result_is_error
from apple_mail_mcp.tools.draft_verification import _split_csv_addresses


def _extract_draft_ids(text: str) -> list[str]:
    """Extract Drafts ids from manage_drafts(action='list') text output."""
    seen: set[str] = set()
    ids: list[str] = []
    for match in re.finditer(r"\b(?:Draft ID|Id):\s*(\d+)\b", text):
        draft_id = match.group(1)
        if draft_id not in seen:
            seen.add(draft_id)
            ids.append(draft_id)
    return ids


def _normalized_recipient_set(value: str) -> set[str]:
    """Normalize a comma-delimited recipient field for smoke identity checks."""
    return set(_split_csv_addresses(value))


def _draft_verification_passed(value: Any, *, expected_to: str) -> bool:
    parsed = _parse_tool_result(value)
    if not isinstance(parsed, dict):
        return False
    if parsed.get("found") is not True:
        return False
    warnings = set(parsed.get("warnings") or [])
    recipients = parsed.get("recipients")
    if not isinstance(recipients, dict) or not isinstance(recipients.get("to"), str):
        return False
    # Exact-set equality is the recipient gate. It is strictly stronger than
    # verify_draft's own subset-only ``to_matches_expected`` / ``to_mismatch``
    # signals (a missing OR an extra recipient makes the sets differ), so those
    # do not need a redundant branch here.
    if _normalized_recipient_set(recipients["to"]) != _normalized_recipient_set(expected_to):
        return False
    return not {"subject_mismatch", "expected_body_missing"} & warnings and not parsed.get("error")


def _smoke_verification_evidence(value: Any) -> dict[str, Any]:
    """Return the relevant verification status without draft-content disclosure."""
    parsed = _parse_tool_result(value)
    if not isinstance(parsed, dict):
        return {"result_parseable": False}

    evidence: dict[str, Any] = {
        "result_parseable": True,
        "found": parsed.get("found") is True,
        "warnings": list(parsed.get("warnings") or []),
    }
    checks = parsed.get("checks")
    if isinstance(checks, dict) and "to_matches_expected" in checks:
        evidence["to_matches_expected"] = checks["to_matches_expected"]
    return evidence


def _draft_cleanup_confirmed(value: Any) -> bool:
    parsed = _parse_tool_result(value)
    return isinstance(parsed, dict) and parsed.get("deleted") is True


def _resolve_draft_smoke_from_address(
    *,
    account: str,
    explicit_from_address: str | None,
    timeout: int,
) -> tuple[str | None, str | None]:
    """Return a sender address that pins the smoke draft to the requested account."""
    if explicit_from_address:
        return explicit_from_address, None

    from apple_mail_mcp.tools.inbox import list_account_addresses

    account_addresses = list_account_addresses(timeout=timeout)
    addresses = account_addresses.get(account) or []
    if len(addresses) == 1:
        return addresses[0], None
    if not addresses:
        return None, f"Account {account!r} has no configured sender address; pass --from-address"
    return None, f"Account {account!r} has multiple sender addresses; pass --from-address"


def _append_stage_error(payload: dict[str, Any], stage: str, detail: Any, **extra: Any) -> None:
    error = {"stage": stage, "detail": detail}
    error.update(extra)
    payload["errors"].append(error)


def _verify_smoke_candidates(
    *,
    account: str,
    subject: str,
    expected_to: str,
    body_sentinel: str,
    candidate_ids: list[str],
    tool_timeout: int,
    verify_draft: Callable[..., Any],
) -> tuple[list[str], Any]:
    verified_ids: list[str] = []
    last_verify_result: Any = None
    for draft_id in candidate_ids:
        verify_result = verify_draft(
            account=account,
            draft_id=draft_id,
            expected_to=expected_to,
            expected_subject=subject,
            expected_body_contains=body_sentinel,
            timeout=tool_timeout,
        )
        last_verify_result = _parse_tool_result(verify_result)
        if _draft_verification_passed(verify_result, expected_to=expected_to):
            verified_ids.append(draft_id)
    return verified_ids, last_verify_result


def _create_smoke_draft(
    *,
    account: str,
    subject: str,
    to: str,
    body: str,
    from_address: str | None,
    tool_timeout: int,
    manage_drafts: Callable[..., Any],
) -> tuple[Any, str | None]:
    create_result = manage_drafts(
        account=account,
        action="create",
        subject=subject,
        to=to,
        body=body,
        from_address=from_address,
        timeout=tool_timeout,
        standalone_confirmed=True,
    )
    draft_id_match = re.search(r"\bDraft ID:\s*(\d+)\b", str(create_result))
    provisional_id = draft_id_match.group(1) if draft_id_match else None
    return create_result, provisional_id


def _poll_for_verified_smoke_draft(
    *,
    account: str,
    subject: str,
    expected_to: str,
    body_sentinel: str,
    list_limit: int,
    tool_timeout: int,
    poll_timeout: float,
    poll_interval: float,
    manage_drafts: Callable[..., Any],
    verify_draft: Callable[..., Any],
    payload: dict[str, Any],
) -> tuple[str | None, list[str], Any]:
    deadline = time.monotonic() + poll_timeout
    candidate_ids: list[str] = []
    last_verify_result: Any = None
    while True:
        payload["poll_attempts"] = int(payload["poll_attempts"]) + 1
        list_result = manage_drafts(
            account=account,
            action="list",
            subject_contains=subject,
            limit=list_limit,
            timeout=tool_timeout,
        )
        if _result_is_error(list_result):
            _append_stage_error(payload, "list", _parse_tool_result(list_result))
        else:
            candidate_ids = _extract_draft_ids(str(list_result))
            verified_ids, last_verify_result = _verify_smoke_candidates(
                account=account,
                subject=subject,
                expected_to=expected_to,
                body_sentinel=body_sentinel,
                candidate_ids=candidate_ids,
                tool_timeout=tool_timeout,
                verify_draft=verify_draft,
            )
            if len(verified_ids) == 1:
                return verified_ids[0], candidate_ids, last_verify_result
            if len(verified_ids) > 1:
                _append_stage_error(payload, "verify", "multiple_verified_candidates")
                return None, [], last_verify_result

        if time.monotonic() >= deadline:
            return None, candidate_ids, last_verify_result
        time.sleep(poll_interval)


def _cleanup_smoke_draft(
    *,
    account: str,
    draft_id: str,
    subject: str,
    expected_to: str,
    body_sentinel: str,
    tool_timeout: int,
    delete_draft_if_identity_matches: Callable[..., Any],
    payload: dict[str, Any],
) -> None:
    delete_result = delete_draft_if_identity_matches(
        account=account,
        draft_id=draft_id,
        expected_subject=subject,
        expected_to=expected_to,
        expected_body_sentinel=body_sentinel,
        timeout=tool_timeout,
    )
    payload["cleanup"]["delete_result"] = _parse_tool_result(delete_result)
    if _result_is_error(delete_result):
        _append_stage_error(payload, "cleanup_delete", _parse_tool_result(delete_result))
    payload["cleanup"]["confirmed"] = _draft_cleanup_confirmed(delete_result)
    if not payload["cleanup"]["confirmed"]:
        _append_stage_error(payload, "cleanup_confirm", _parse_tool_result(delete_result))


def _cmd_draft_verify_smoke(args: argparse.Namespace) -> int:
    from apple_mail_mcp.tools.compose import delete_draft_if_identity_matches, manage_drafts, verify_draft

    token = uuid4().hex[:8]
    subject = f"APPLE_MAIL_MCP_DRAFT_VERIFY_SMOKE_{int(time.time())}_{token}"
    body_sentinel = f"APPLE_MAIL_MCP_BODY_SENTINEL_{token}"
    body = f"Apple Mail MCP draft verification smoke.\nBody sentinel: {body_sentinel}"
    poll_timeout = max(1.0, float(args.poll_timeout))
    poll_interval = max(0.1, float(args.poll_interval))
    list_limit = max(1, int(args.list_limit))
    tool_timeout = max(1, int(args.tool_timeout))

    payload: dict[str, Any] = {
        "ok": False,
        "account": args.account,
        "from_address": None,
        "expected_to": args.to,
        "subject": subject,
        "created_draft_id_provisional": None,
        "persisted_draft_id": None,
        "poll_attempts": 0,
        "verified": False,
        "cleanup": {"requested": bool(args.cleanup), "confirmed": False, "skipped": bool(args.leave_draft)},
        "errors": [],
    }
    from_address, from_error = _resolve_draft_smoke_from_address(
        account=args.account,
        explicit_from_address=args.from_address,
        timeout=tool_timeout,
    )
    payload["from_address"] = from_address
    if from_error:
        _append_stage_error(payload, "sender", from_error)
        _print_result(payload, json_mode=args.json)
        return 2

    create_result, provisional_id = _create_smoke_draft(
        account=args.account,
        subject=subject,
        to=args.to,
        body=body,
        from_address=from_address,
        tool_timeout=tool_timeout,
        manage_drafts=manage_drafts,
    )
    payload["created_draft_id_provisional"] = provisional_id
    if _result_is_error(create_result):
        _append_stage_error(payload, "create", _parse_tool_result(create_result))
        _print_result(payload, json_mode=args.json)
        return 1

    persisted_id, candidate_ids, last_verify_result = _poll_for_verified_smoke_draft(
        account=args.account,
        subject=subject,
        expected_to=args.to,
        body_sentinel=body_sentinel,
        list_limit=list_limit,
        tool_timeout=tool_timeout,
        poll_timeout=poll_timeout,
        poll_interval=poll_interval,
        manage_drafts=manage_drafts,
        verify_draft=verify_draft,
        payload=payload,
    )

    if persisted_id is not None:
        payload["persisted_draft_id"] = persisted_id
        payload["verified"] = True
    if not payload["verified"]:
        _append_stage_error(
            payload,
            "verify",
            "no_verified_persisted_draft",
            candidate_ids=candidate_ids,
            last_verification=_smoke_verification_evidence(last_verify_result),
        )

    cleanup_draft_id = payload["persisted_draft_id"]
    if args.cleanup and cleanup_draft_id:
        _cleanup_smoke_draft(
            account=args.account,
            draft_id=str(cleanup_draft_id),
            subject=subject,
            expected_to=args.to,
            body_sentinel=body_sentinel,
            tool_timeout=tool_timeout,
            delete_draft_if_identity_matches=delete_draft_if_identity_matches,
            payload=payload,
        )

    payload["ok"] = bool(payload["verified"]) and (not args.cleanup or bool(payload["cleanup"]["confirmed"]))
    _print_result(payload, json_mode=args.json)
    return 0 if payload["ok"] else 1
