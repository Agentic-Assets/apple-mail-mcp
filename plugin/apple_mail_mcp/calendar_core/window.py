"""Bounded calendar windows: the only sanctioned producer of ``CalendarWindow`` tokens.

``bounded_calendar_window`` is the calendar analogue of ``bounded_inbox_scan``:
every event-returning read must pass a ``CalendarWindow`` issued here, and
engines refuse foreign tokens. All timezone interpretation happens in this
module with ``zoneinfo``; AppleScript never sees a datetime string, only
validated integer components (see ``scripts_read.applescript_date_block``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.constants import CALENDAR_BOUNDS

_ISSUER = "calendar_core.bounded_calendar_window"


@dataclass(frozen=True)
class CalendarWindow:
    """A capability token describing a bounded calendar read window.

    Only ``bounded_calendar_window`` may construct instances with
    ``_issued_by=_ISSUER``. Engines MUST call ``require_issued_window``
    before running any AppleScript so no tool can smuggle in an unbounded
    or unvalidated scan.
    """

    start: datetime
    end: datetime
    timezone_name: str
    _issued_by: str = ""


def resolve_timezone(name: str | None) -> tuple[tzinfo, str]:
    """Resolve an IANA zone name (or None for host-local) to a tzinfo + label.

    Raises ``ToolError(code="INVALID_TIMEZONE")`` on unknown names.
    """
    if name is None or not name.strip():
        local = datetime.now().astimezone().tzinfo
        if local is None:  # pragma: no cover - astimezone always sets tzinfo
            local = timezone.utc
        return local, str(local)
    candidate = name.strip()
    try:
        zone = ZoneInfo(candidate)
    except Exception as exc:
        raise ToolError(
            code="INVALID_TIMEZONE",
            message=f"Unknown timezone {candidate!r}. Pass an IANA zone name such as 'America/Chicago'.",
            remediation={
                "examples": ["America/Chicago", "America/New_York", "UTC"],
                "note": "Offset-aware ISO 8601 datetimes also work without a timezone parameter.",
            },
        ) from exc
    return zone, candidate


def parse_iso_datetime(value: str, tz: tzinfo, param: str) -> datetime:
    """Parse an ISO 8601 string into an aware datetime.

    Naive values are interpreted in *tz*. A trailing ``Z`` is accepted on
    Python 3.10 by normalizing it to ``+00:00``. Raises
    ``ToolError(code="INVALID_EVENT_WINDOW")`` on malformed input.
    """
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ToolError(
            code="INVALID_EVENT_WINDOW",
            message=f"{param} is not valid ISO 8601: {value!r}.",
            remediation={"examples": ["2026-07-10", "2026-07-10T09:00:00", "2026-07-10T09:00:00-05:00"]},
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def bounded_calendar_window(
    *,
    start: str | None = None,
    end: str | None = None,
    days_back: float = 0.0,
    days_ahead: float = 0.0,
    timezone_name: str | None = None,
    max_window_days: int | None = None,
    now: datetime | None = None,
) -> CalendarWindow:
    """Issue a validated ``CalendarWindow`` token.

    Absolute ``start``/``end`` (both required together) win over the relative
    ``days_back``/``days_ahead`` pair. A zero-width or absent window refuses
    with ``UNBOUNDED_CALENDAR_SCAN`` before any engine call; windows wider
    than *max_window_days* refuse with ``CALENDAR_WINDOW_TOO_WIDE``.
    """
    tz, resolved_name = resolve_timezone(timezone_name)
    cap_days = int(max_window_days if max_window_days is not None else CALENDAR_BOUNDS["MAX_WINDOW_DAYS"])

    if (start is None) != (end is None):
        raise ToolError(
            code="INVALID_EVENT_WINDOW",
            message="Pass both start and end for an absolute window, or neither to use days_back/days_ahead.",
            remediation={"relative": "days_back / days_ahead", "absolute": "start + end (ISO 8601)"},
        )

    if start is not None and end is not None:
        start_dt = parse_iso_datetime(start, tz, "start")
        end_dt = parse_iso_datetime(end, tz, "end")
    else:
        if days_back < 0 or days_ahead < 0:
            raise ToolError(
                code="INVALID_EVENT_WINDOW",
                message="days_back and days_ahead must be zero or positive.",
            )
        if days_back == 0 and days_ahead == 0:
            raise ToolError(
                code="UNBOUNDED_CALENDAR_SCAN",
                message=(
                    "Calendar reads require a bounded window. Pass days_back/days_ahead "
                    "or an absolute start/end pair; zero-width windows are refused."
                ),
                remediation={
                    "preferred": "days_ahead=7 for the upcoming week",
                    "absolute": "start='2026-07-10' end='2026-07-17'",
                },
            )
        anchor = now.astimezone(tz) if now is not None else datetime.now(tz)
        start_dt = anchor - timedelta(days=float(days_back))
        end_dt = anchor + timedelta(days=float(days_ahead))

    if start_dt >= end_dt:
        raise ToolError(
            code="INVALID_EVENT_WINDOW",
            message="Window start must be before window end.",
        )

    width_days = (end_dt - start_dt).total_seconds() / 86_400.0
    if width_days > cap_days:
        raise ToolError(
            code="CALENDAR_WINDOW_TOO_WIDE",
            message=(f"Window spans {width_days:.1f} days; the cap for this call is {cap_days} days."),
            remediation={
                "preferred": "Narrow the window and page through results with offset.",
                "cap_days": cap_days,
            },
        )

    return CalendarWindow(start=start_dt, end=end_dt, timezone_name=resolved_name, _issued_by=_ISSUER)


def require_issued_window(window: CalendarWindow) -> None:
    """Refuse ``CalendarWindow`` tokens not issued by ``bounded_calendar_window``."""
    if window._issued_by != _ISSUER:
        raise ToolError(
            code="INVALID_EVENT_WINDOW",
            message=("CalendarWindow token was not issued by bounded_calendar_window; refusing the scan."),
            remediation={"preferred": "Call calendar_core.bounded_calendar_window(...)"},
        )


def shifted_window(
    window: CalendarWindow, *, start_delta_days: float = 0.0, end_delta_days: float = 0.0
) -> CalendarWindow:
    """Return a new issued window shifted by whole-day deltas.

    Used internally for the recurring-master lookback pass and the
    availability fetch pad. Callers cannot forge arbitrary windows with this:
    it only operates on an already-issued token.
    """
    require_issued_window(window)
    return CalendarWindow(
        start=window.start + timedelta(days=start_delta_days),
        end=window.end + timedelta(days=end_delta_days),
        timezone_name=window.timezone_name,
        _issued_by=_ISSUER,
    )


def isoformat_pair(dt: datetime, tz: tzinfo) -> tuple[str, str]:
    """Return (requested-zone ISO 8601, UTC ISO 8601) for an aware datetime."""
    return dt.astimezone(tz).isoformat(), dt.astimezone(timezone.utc).isoformat()


def window_payload(window: CalendarWindow) -> dict[str, Any]:
    """JSON-friendly description of an issued window for tool responses."""
    tz, _ = resolve_timezone(window.timezone_name if _is_iana(window.timezone_name) else None)
    start_local, start_utc = isoformat_pair(window.start, tz)
    end_local, end_utc = isoformat_pair(window.end, tz)
    return {
        "start": start_local,
        "end": end_local,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "timezone": window.timezone_name,
    }


def _is_iana(name: str) -> bool:
    try:
        ZoneInfo(name)
    except Exception:
        return False
    return True


__all__ = [
    "CalendarWindow",
    "bounded_calendar_window",
    "isoformat_pair",
    "parse_iso_datetime",
    "require_issued_window",
    "resolve_timezone",
    "shifted_window",
    "window_payload",
]
