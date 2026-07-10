"""Shared helpers for the calendar tool surface: gates, resolution, fan-out.

The mode gates here are NEW safety plumbing (final plan F4/F7), not a port of
an existing mail helper: for mail tools the ``--read-only`` / ``--draft-safe``
flags block only the send paths, while these guards block every calendar
write under read-only and every calendar delete plus attendee send under
draft-safe. The CLI calls tool functions without registry removal, so these
internal guards are mandatory defense-in-depth.

Patched seams (via the ``apple_mail_mcp.tools.calendar`` facade):
``get_engine``, ``get_write_engine``, and ``list_calendar_names``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.calendar_core import (
    CalendarReadEngine,
    CalendarWindow,
    bounded_calendar_window,
    event_payload,
    expand_occurrences,
    parse_rrule,
    resolve_calendar_name,
    shifted_window,
    validate_attendee_emails,
)
from apple_mail_mcp.constants import CALENDAR_BOUNDS
from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import calendar as _calendar

AUTOMATION_PANE_NOTE = (
    "If this is the first calendar call from this app, macOS may be waiting on the "
    "Automation consent prompt (System Settings > Privacy & Security > Automation); "
    "answer it once and retry."
)


def error_json(exc: ToolError) -> str:
    """Serialize a ToolError to the standard JSON envelope."""
    return serialize_tool_error(exc)


def output_format_error(output_format: str) -> str | None:
    if output_format not in ("json", "text"):
        return f"Error: output_format must be 'json' or 'text'; got {output_format!r}."
    return None


def timeout_error(tool_name: str, timeout: int | None) -> str:
    seconds = 120 if timeout is None else timeout
    return f"Error: {tool_name} timed out after {seconds}s talking to Calendar.app. {AUTOMATION_PANE_NOTE}"


def calendar_write_blocked(tool_name: str) -> str | None:
    """Backstop for --read-only (registry removal covers the MCP path)."""
    if _server.READ_ONLY:
        return error_json(
            ToolError(
                code="CALENDAR_WRITE_BLOCKED",
                message=f"{tool_name} is disabled in read-only mode; calendar writes are blocked.",
                remediation={"preferred": "Relaunch the server without --read-only to enable calendar writes."},
            )
        )
    return None


def calendar_delete_blocked(tool_name: str) -> str | None:
    """Deletes are blocked under draft-safe unless the operator set the env unlock."""
    blocked = calendar_write_blocked(tool_name)
    if blocked:
        return blocked
    if _server.DRAFT_SAFE and not _server.CALENDAR_ALLOW_DESTRUCTIVE:
        return error_json(
            ToolError(
                code="CALENDAR_DELETE_BLOCKED",
                message=f"{tool_name} deletes are disabled in draft-safe mode.",
                remediation={
                    "operator_unlock": "Launch with CALENDAR_ALLOW_DESTRUCTIVE=1 to allow deletes under draft-safe.",
                    "note": "The unlock is environment-only by design; it cannot be passed as a tool argument.",
                },
            )
        )
    return None


def attendee_gate(
    attendees: list[str] | None,
    send_invitations: bool,
    tool_name: str,
) -> tuple[list[str] | None, str | None]:
    """Validate and mode-gate attendee use. Returns (validated, error_json).

    Attendee attachment is treated as outward-facing: platform APIs never
    guarantee invitation delivery, so the caller must confirm explicitly and
    draft-safe blocks it outright.
    """
    if not attendees:
        return None, None
    if _server.READ_ONLY or _server.DRAFT_SAFE:
        return None, error_json(
            ToolError(
                code="INVITE_SEND_BLOCKED",
                message=f"{tool_name} cannot attach attendees in draft-safe or read-only mode.",
                remediation={
                    "alternative": (
                        "Create the event without attendees, then draft (never auto-send) an "
                        "invitation email with an .ics attachment through the gated Mail compose path."
                    )
                },
            )
        )
    try:
        validated = validate_attendee_emails(attendees)
    except ToolError as exc:
        return None, error_json(exc)
    if not send_invitations:
        return None, error_json(
            ToolError(
                code="INVITE_SEND_REQUIRES_CONFIRM",
                message=(
                    f"{tool_name} received attendees without send_invitations=True. Attendee "
                    "attachment is potentially outward-facing, so it requires explicit confirmation."
                ),
                remediation={
                    "confirm": "Retry with send_invitations=True after the user confirms.",
                    "delivery_note": "macOS offers no public API that guarantees invitation transmission.",
                },
            )
        )
    return validated, None


def list_calendar_names(timeout: int | None = None) -> list[str]:
    """Live calendar names via the active read engine (conftest patches this)."""
    calendars, _errors = _calendar.get_engine().list_calendars(timeout=timeout)
    return [str(c["name"]) for c in calendars]


def resolve_read_calendars(
    calendar_name: str | None,
    calendar_names: list[str] | None,
    *,
    timeout: int | None = None,
) -> tuple[list[str], bool]:
    """Resolve the calendar scope for a read; returns (names, fan_out_capped).

    Unlike mail's account scoping (which errors without a configured
    default), an unscoped calendar read fans out across every calendar up to
    ``MAX_CALENDARS_PER_QUERY``; this divergence is deliberate and documented
    in each fan-out tool's docstring.
    """
    if calendar_name is not None and calendar_names:
        raise ToolError(
            code="AMBIGUOUS_CALENDAR_SELECTOR",
            message="Pass calendar or calendars, not both.",
        )
    known = _calendar.list_calendar_names(timeout=timeout)
    if calendar_name is not None:
        return [resolve_calendar_name(calendar_name, known)], False
    if calendar_names:
        resolved: dict[str, None] = {}
        for name in calendar_names:
            resolved.setdefault(resolve_calendar_name(name, known), None)
        return list(resolved), False
    cap = int(CALENDAR_BOUNDS["MAX_CALENDARS_PER_QUERY"])
    return known[:cap], len(known) > cap


def resolve_create_target(calendar_name: str | None, *, timeout: int | None = None) -> str:
    """Resolve the write target: explicit name, DEFAULT_CALENDAR, engine default."""
    known = _calendar.list_calendar_names(timeout=timeout)
    if calendar_name is not None:
        return resolve_calendar_name(calendar_name, known)
    if _server.DEFAULT_CALENDAR:
        return resolve_calendar_name(_server.DEFAULT_CALENDAR, known)
    engine_default = _calendar.get_engine().default_calendar_name()
    if engine_default and engine_default in known:
        return engine_default
    raise ToolError(
        code="CALENDAR_NOT_FOUND",
        message="No calendar specified and no DEFAULT_CALENDAR is configured.",
        remediation={
            "preferred": "Pass calendar=... or set the DEFAULT_CALENDAR environment variable.",
            "candidates": known[:5],
        },
    )


def tz_for_window(window: CalendarWindow) -> tzinfo:
    """tzinfo for output formatting: the window's IANA zone or host-local."""
    try:
        return ZoneInfo(window.timezone_name)
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


def widen_write_window_for_recurring(window: CalendarWindow, recurring: bool) -> CalendarWindow:
    """Widen the write-side uid lookup for recurring targets (F5).

    The write-side ``whose`` stays date-bounded (the calendar lint forbids an
    unbounded predicate, and Calendar.app ``whose`` cost tracks total store
    size), but a recurring master's own start date can sit far outside the read
    lookup window. Shift the window start back by the recurring lookback horizon
    so the master is located; a series whose master started earlier still
    returns ``EVENT_NOT_FOUND`` (documented in each write tool's docstring).
    """
    if not recurring:
        return window
    return shifted_window(window, start_delta_days=-float(CALENDAR_BOUNDS["RECURRING_LOOKBACK_DAYS"]))


class CallBudget:
    """Aggregate wall-clock budget across a multi-calendar fan-out (F2)."""

    def __init__(self, seconds: float | None = None) -> None:
        budget = float(CALENDAR_BOUNDS["CALL_BUDGET_SECONDS"]) if seconds is None else seconds
        self._deadline = time.monotonic() + budget

    def exhausted(self) -> bool:
        return time.monotonic() >= self._deadline


def _matches_query(payload: dict[str, Any], query: str) -> bool:
    needle = query.lower()
    for key in ("title", "location", "notes_preview", "notes"):
        value = payload.get(key)
        if isinstance(value, str) and needle in value.lower():
            return True
    return False


def collect_window_events(
    *,
    engine: CalendarReadEngine,
    window: CalendarWindow,
    calendar_names: list[str],
    expand_recurring: bool = True,
    include_detail: bool = False,
    event_ids: list[str] | None = None,
    query: str | None = None,
    include_all_day: bool = True,
    timeout: int | None = None,
    budget: CallBudget | None = None,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Fetch, expand, filter, and sort events across the calendar scope.

    Returns (event payloads sorted by start, per-calendar errors,
    budget_exhausted). Per-calendar failures produce partial results; only
    ``CALENDAR_WINDOW_TOO_DENSE`` aborts the whole call.
    """
    tz = tz_for_window(window)
    budget = budget or CallBudget()
    payloads: list[dict[str, Any]] = []
    calendar_errors: list[str] = []
    budget_exhausted = False
    occurrence_ceiling = int(CALENDAR_BOUNDS["OCCURRENCE_SCAN_CEILING"])
    occurrence_count = 0

    for index, name in enumerate(calendar_names):
        if budget.exhausted():
            budget_exhausted = True
            for skipped in calendar_names[index:]:
                calendar_errors.append(f"{skipped}: skipped, call budget exhausted")
            break
        try:
            raw_events, row_errors = engine.fetch_window(
                window,
                name,
                scan_cap=int(CALENDAR_BOUNDS["EVENT_SCAN_CAP"]),
                include_detail=include_detail,
                event_ids=event_ids,
                timeout=timeout,
            )
            calendar_errors.extend(f"{name}: {err}" for err in row_errors)
            recurring_masters: dict[str, dict[str, Any]] = {}
            for raw in raw_events:
                if raw.get("recurrence") and expand_recurring and not engine.expands_occurrences:
                    recurring_masters[str(raw["event_id"])] = raw
                    continue
                expansion = None
                if engine.expands_occurrences and raw.get("recurring_flag"):
                    expansion = "native"
                payloads.append(
                    event_payload(raw, tz=tz, engine=engine.name, include_detail=include_detail, expansion=expansion)
                )
            if expand_recurring and not engine.expands_occurrences and event_ids is None:
                masters, master_errors = engine.fetch_recurring_masters(window, name, timeout=timeout)
                calendar_errors.extend(f"{name}: {err}" for err in master_errors)
                for raw in masters:
                    recurring_masters.setdefault(str(raw["event_id"]), raw)
            for raw in recurring_masters.values():
                rule_text = str(raw.get("recurrence") or "")
                occurrences: list[datetime] | None
                try:
                    occurrences = expand_occurrences(
                        master_start=raw["start"],
                        rule=parse_rrule(rule_text),
                        window_start=window.start,
                        window_end=window.end,
                        ceiling=occurrence_ceiling,
                    )
                except ToolError as exc:
                    if exc.code == "CALENDAR_WINDOW_TOO_DENSE":
                        raise
                    occurrences = None
                if occurrences is None:
                    payloads.append(
                        event_payload(
                            raw,
                            tz=tz,
                            engine=engine.name,
                            include_detail=include_detail,
                            expansion="unsupported_rrule",
                        )
                    )
                    continue
                occurrence_count += len(occurrences)
                if occurrence_count > occurrence_ceiling:
                    raise ToolError(
                        code="CALENDAR_WINDOW_TOO_DENSE",
                        message=(
                            f"Recurring expansion exceeded {occurrence_ceiling} occurrences in this "
                            "window; narrow the window or scope to fewer calendars."
                        ),
                    )
                for occurrence in occurrences:
                    payloads.append(
                        event_payload(
                            raw,
                            tz=tz,
                            engine=engine.name,
                            include_detail=include_detail,
                            occurrence_start=occurrence,
                            expansion="python",
                        )
                    )
        except AppleScriptTimeout:
            calendar_errors.append(f"{name}: timed out. {AUTOMATION_PANE_NOTE}")
        except ToolError as exc:
            if exc.code == "CALENDAR_WINDOW_TOO_DENSE":
                raise
            calendar_errors.append(f"{name}: [{exc.code}] {exc.message}")

    if not include_all_day:
        payloads = [p for p in payloads if not p.get("all_day")]
    if query:
        payloads = [p for p in payloads if _matches_query(p, query)]
    payloads.sort(key=lambda p: str(p.get("start_utc") or ""))
    return payloads, calendar_errors, budget_exhausted


def find_conflicts(
    *,
    calendar_name: str,
    start: datetime,
    end: datetime,
    timezone_name: str | None,
    timeout: int | None = None,
    exclude_event_id: str | None = None,
) -> list[dict[str, Any]]:
    """Bounded overlap check over the new event's own span (one fetch)."""
    pad = timedelta(days=float(CALENDAR_BOUNDS["AVAILABILITY_FETCH_PAD_DAYS"]))
    window = bounded_calendar_window(
        start=(start - pad).isoformat(),
        end=end.isoformat(),
        timezone_name=timezone_name,
    )
    engine = _calendar.get_engine()
    payloads, _errors, _exhausted = collect_window_events(
        engine=engine,
        window=window,
        calendar_names=[calendar_name],
        expand_recurring=True,
        timeout=timeout,
    )
    conflicts: list[dict[str, Any]] = []
    for payload in payloads:
        if exclude_event_id and payload.get("event_id") == exclude_event_id:
            continue
        other_start = datetime.fromisoformat(str(payload["start_utc"]))
        other_end_text = payload.get("end_utc")
        other_end = datetime.fromisoformat(str(other_end_text)) if other_end_text else other_start
        if other_start < end.astimezone(timezone.utc) and other_end > start.astimezone(timezone.utc):
            conflicts.append(
                {
                    "event_id": payload.get("event_id"),
                    "title": payload.get("title"),
                    "start": payload.get("start"),
                    "end": payload.get("end"),
                    "calendar": payload.get("calendar"),
                }
            )
    return conflicts


def recurring_lookback_disclosure(engine: CalendarReadEngine, expand_recurring: bool) -> dict[str, Any] | None:
    """Disclosure fields when the AppleScript recurring-master lookback pass ran (F2).

    The AppleScript engine only finds recurring masters whose ``start date``
    falls inside a bounded lookback horizon (``RECURRING_LOOKBACK_DAYS``), then
    projects their occurrences forward, so a standing series whose master
    started before that horizon is silently absent from the window. EventKit
    expands occurrences natively and needs no disclosure. Returns None when the
    lookback pass did not run (EventKit engine, or ``expand_recurring=False``).
    """
    if not expand_recurring or engine.expands_occurrences:
        return None
    days = int(CALENDAR_BOUNDS["RECURRING_LOOKBACK_DAYS"])
    return {
        "recurring_lookback_days": days,
        "recurring_coverage_note": (
            f"Recurring series are matched from masters whose start date falls within the last "
            f"{days} days on the AppleScript engine; a standing series created earlier may be "
            "missing from this window. Install the EventKit fast path "
            "(pip install 'mcp-apple-mail[eventkit]') for native recurrence expansion."
        ),
    }


def validate_on_conflict(on_conflict: str) -> None:
    if on_conflict not in ("warn", "block", "allow"):
        raise ToolError(
            code="EVENT_CONFLICT",
            message=f"on_conflict must be 'warn', 'block', or 'allow'; got {on_conflict!r}.",
        )


def render_events_text(payload: dict[str, Any]) -> str:
    """Compact human-readable rendering for event-list payloads."""
    lines: list[str] = []
    events = payload.get("events", [])
    lines.append(f"{len(events)} event(s) [engine: {payload.get('engine', '?')}]")
    for event in events:
        marker = " (all-day)" if event.get("all_day") else ""
        recurring = " [recurring]" if event.get("recurring") else ""
        lines.append(f"- {event.get('start')}{marker}  {event.get('title')}{recurring}  [{event.get('calendar')}]")
        lines.append(f"    id: {event.get('event_id')}")
    for err in payload.get("calendar_errors", []):
        lines.append(f"! {err}")
    if payload.get("truncated"):
        lines.append(f"(truncated; next_offset={payload.get('next_offset')})")
    return "\n".join(lines)


def finish(
    payload: dict[str, Any],
    output_format: str,
    renderer: Callable[[dict[str, Any]], str] | None = None,
) -> str:
    """Serialize a success payload; text uses the renderer or formatted JSON."""
    if output_format == "text" and renderer is not None:
        return renderer(payload)
    return json.dumps(payload, indent=2)


__all__ = [
    "AUTOMATION_PANE_NOTE",
    "CallBudget",
    "attendee_gate",
    "calendar_delete_blocked",
    "calendar_write_blocked",
    "collect_window_events",
    "error_json",
    "find_conflicts",
    "finish",
    "list_calendar_names",
    "output_format_error",
    "render_events_text",
    "resolve_create_target",
    "resolve_read_calendars",
    "timeout_error",
    "tz_for_window",
    "validate_on_conflict",
    "widen_write_window_for_recurring",
]
