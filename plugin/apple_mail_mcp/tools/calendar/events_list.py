"""``list_events`` tool: bounded list/today/upcoming/search in one call."""

import asyncio
from typing import Any

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.calendar_core import bounded_calendar_window, window_payload
from apple_mail_mcp.constants import CALENDAR_BOUNDS
from apple_mail_mcp.core import AppleScriptTimeout, inject_preferences
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import calendar as calendar_tools
from apple_mail_mcp.tools.calendar.helpers import (
    collect_window_events,
    error_json,
    finish,
    output_format_error,
    recurring_lookback_disclosure,
    render_events_text,
    resolve_read_calendars,
    timeout_error,
)


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
async def list_events(
    calendar: str | None = None,
    calendars: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    days_back: float = 0.0,
    days_ahead: float = 7.0,
    timezone: str | None = None,
    query: str | None = None,
    include_all_day: bool = True,
    expand_recurring: bool = True,
    output_format: str = "json",
    offset: int = 0,
    limit: int | None = None,
    timeout: int | None = None,
) -> str:
    """
    List events in a bounded window, optionally filtered by a text query.

    Covers "what is on my calendar", today, upcoming, and search in one tool.
    The window is mandatory and capped: relative ``days_back``/``days_ahead``
    (default: the next 7 days) or an absolute ``start``/``end`` pair, never
    wider than 370 days. Events are matched by their start date inside the
    window; recurring series are expanded into occurrences (AppleScript
    engine: a bounded lookback pass plus Python RRULE expansion, flagged
    ``expansion``; EventKit engine: native).

    Recurring coverage limit (AppleScript engine only): recurring occurrences
    are projected from masters whose start date falls within the last 400 days
    (``recurring_lookback_days`` in the response), so a standing series created
    earlier may be missing from the window with no per-row error. The EventKit
    fast path expands natively and carries no such horizon.

    Scoping note (differs from mail's account scoping): with no ``calendar``
    or ``calendars`` argument this tool fans out across every calendar, capped
    at 20 calendars and an aggregate wall-clock budget; skipped calendars are
    reported in ``calendar_errors`` and ``budget_exhausted``. ``query``
    matches title, location, and the first 280 characters of notes
    (``notes_preview``, not the full note) case-insensitively in Python.

    Args:
        calendar: One calendar name (fuzzy-resolved). Mutually exclusive with
            ``calendars``.
        calendars: Explicit list of calendar names to scan.
        start: Absolute window start, ISO 8601 (requires ``end``).
        end: Absolute window end, ISO 8601.
        days_back: Relative window days before now (default 0).
        days_ahead: Relative window days after now (default 7).
        timezone: IANA zone for interpretation and output (default host zone).
        query: Case-insensitive substring over title/location/notes.
        include_all_day: Include all-day events (default True).
        expand_recurring: Expand recurring series into occurrences.
        output_format: "json" (default) or "text".
        offset: Paging offset into the sorted result list.
        limit: Maximum events returned (capped at 200 per call).
        timeout: Optional AppleScript timeout in seconds per engine call.

    Returns:
        JSON payload with ``events``, paging fields, ``engine``,
        ``resolved_timezone``, ``calendar_errors``, and ``budget_exhausted``.
    """
    fmt_error = output_format_error(output_format)
    if fmt_error:
        return fmt_error
    if offset < 0:
        return "Error: offset must be zero or positive."
    return_cap = int(CALENDAR_BOUNDS["EVENT_RETURN_CAP"])
    if limit is not None and limit <= 0:
        return "Error: limit must be a positive integer."
    effective_limit = min(limit, return_cap) if limit is not None else return_cap

    try:
        window = bounded_calendar_window(
            start=start,
            end=end,
            days_back=days_back,
            days_ahead=days_ahead,
            timezone_name=timezone,
        )
        names, fan_out_capped = resolve_read_calendars(calendar, calendars, timeout=timeout)
        engine = calendar_tools.get_engine()
        events, calendar_errors, budget_exhausted = await asyncio.to_thread(
            collect_window_events,
            engine=engine,
            window=window,
            calendar_names=names,
            expand_recurring=expand_recurring,
            query=query,
            include_all_day=include_all_day,
            timeout=timeout,
        )
    except AppleScriptTimeout:
        return timeout_error("list_events", timeout)
    except ToolError as exc:
        return error_json(exc)

    total = len(events)
    page = events[offset : offset + effective_limit]
    truncated = offset + effective_limit < total
    payload: dict[str, Any] = {
        "events": page,
        "total_matched": total,
        "offset": offset,
        "limit": effective_limit,
        "truncated": truncated,
        "next_offset": offset + effective_limit if truncated else None,
        "engine": engine.name,
        "resolved_timezone": window.timezone_name,
        "window": window_payload(window),
        "calendars_scanned": names,
        "fan_out_capped": fan_out_capped,
        "calendar_errors": calendar_errors,
        "budget_exhausted": budget_exhausted,
    }
    disclosure = recurring_lookback_disclosure(engine, expand_recurring)
    if disclosure:
        payload.update(disclosure)
    return finish(payload, output_format, render_events_text)
