"""Allowlisted RRULE parsing and bounded occurrence expansion.

Calendar.app AppleScript stores a recurring series as a single event whose
``start date`` is the first occurrence, so date-window predicates miss
occurrences of series that started before the window. The AppleScript engine
therefore fetches recurring masters in a bounded lookback pass and this module
projects their occurrences into the requested window, capped by
``CALENDAR_BOUNDS["OCCURRENCE_SCAN_CEILING"]`` at the call site.

Only the allowlisted grammar expands: FREQ (DAILY/WEEKLY/MONTHLY/YEARLY),
INTERVAL, COUNT, UNTIL, BYDAY (plain weekdays), BYMONTHDAY, BYMONTH. Rules
outside the grammar are invalid to write; rules inside the grammar but outside
the supported expansion combinations (for example MONTHLY with BYDAY) are
returned flagged as ``unsupported_rrule`` rather than silently dropped.
"""

from __future__ import annotations

import calendar as _stdlib_calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from apple_mail_mcp.backend.base import ToolError

_ALLOWED_KEYS = ("FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH")
_ALLOWED_FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
_WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")

# Hard iteration stop for the expansion loops, independent of the caller's
# occurrence ceiling, so a pathological rule can never spin.
_MAX_EXPANSION_STEPS = 20_000


def _invalid(rule: str, why: str) -> ToolError:
    return ToolError(
        code="INVALID_RECURRENCE_RULE",
        message=f"Recurrence rule {rule!r} is outside the supported grammar: {why}",
        remediation={
            "allowed_keys": list(_ALLOWED_KEYS),
            "allowed_freq": list(_ALLOWED_FREQS),
            "example": "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE,FR",
        },
    )


@dataclass(frozen=True)
class RecurrenceRule:
    """Parsed, validated representation of an allowlisted RRULE."""

    freq: str
    interval: int = 1
    count: int | None = None
    until: date | None = None
    bydays: tuple[str, ...] = field(default_factory=tuple)
    bymonthdays: tuple[int, ...] = field(default_factory=tuple)
    bymonths: tuple[int, ...] = field(default_factory=tuple)

    def canonical(self) -> str:
        parts = [f"FREQ={self.freq}"]
        if self.interval != 1:
            parts.append(f"INTERVAL={self.interval}")
        if self.count is not None:
            parts.append(f"COUNT={self.count}")
        if self.until is not None:
            parts.append(f"UNTIL={self.until.strftime('%Y%m%d')}")
        if self.bydays:
            parts.append("BYDAY=" + ",".join(self.bydays))
        if self.bymonthdays:
            parts.append("BYMONTHDAY=" + ",".join(str(d) for d in self.bymonthdays))
        if self.bymonths:
            parts.append("BYMONTH=" + ",".join(str(m) for m in self.bymonths))
        return ";".join(parts)


def parse_rrule(rule: str) -> RecurrenceRule:
    """Parse an RRULE string against the allowlist grammar.

    Raises ``ToolError(code="INVALID_RECURRENCE_RULE")`` for anything outside
    the grammar. Accepts an optional leading ``RRULE:`` prefix.
    """
    text = rule.strip()
    if text.upper().startswith("RRULE:"):
        text = text[len("RRULE:") :]
    if not text:
        raise _invalid(rule, "empty rule")

    seen: dict[str, str] = {}
    for chunk in text.split(";"):
        if not chunk.strip():
            continue
        if "=" not in chunk:
            raise _invalid(rule, f"malformed component {chunk!r}")
        key, value = chunk.split("=", 1)
        key = key.strip().upper()
        if key not in _ALLOWED_KEYS:
            raise _invalid(rule, f"key {key!r} is not allowlisted")
        if key in seen:
            raise _invalid(rule, f"duplicate key {key!r}")
        seen[key] = value.strip()

    freq = seen.get("FREQ", "").upper()
    if freq not in _ALLOWED_FREQS:
        raise _invalid(rule, f"FREQ must be one of {_ALLOWED_FREQS}")

    interval = 1
    if "INTERVAL" in seen:
        if not seen["INTERVAL"].isdigit() or not 1 <= int(seen["INTERVAL"]) <= 999:
            raise _invalid(rule, "INTERVAL must be an integer between 1 and 999")
        interval = int(seen["INTERVAL"])

    count: int | None = None
    if "COUNT" in seen:
        if not seen["COUNT"].isdigit() or not 1 <= int(seen["COUNT"]) <= 10_000:
            raise _invalid(rule, "COUNT must be an integer between 1 and 10000")
        count = int(seen["COUNT"])

    until: date | None = None
    if "UNTIL" in seen:
        raw = seen["UNTIL"].upper().rstrip("Z")
        raw = raw.split("T", 1)[0]
        try:
            until = datetime.strptime(raw, "%Y%m%d").date()
        except ValueError as exc:
            raise _invalid(rule, "UNTIL must be YYYYMMDD or YYYYMMDDTHHMMSSZ") from exc

    if count is not None and until is not None:
        raise _invalid(rule, "COUNT and UNTIL are mutually exclusive")

    bydays: tuple[str, ...] = ()
    if "BYDAY" in seen:
        parts = tuple(p.strip().upper() for p in seen["BYDAY"].split(",") if p.strip())
        for part in parts:
            if part not in _WEEKDAYS:
                raise _invalid(rule, f"BYDAY entry {part!r} must be a plain weekday (no numeric prefixes)")
        if not parts:
            raise _invalid(rule, "BYDAY is empty")
        bydays = parts

    bymonthdays: tuple[int, ...] = ()
    if "BYMONTHDAY" in seen:
        try:
            days = tuple(int(p) for p in seen["BYMONTHDAY"].split(",") if p.strip())
        except ValueError as exc:
            raise _invalid(rule, "BYMONTHDAY entries must be integers") from exc
        if not days or any(not 1 <= d <= 31 for d in days):
            raise _invalid(rule, "BYMONTHDAY entries must be between 1 and 31")
        bymonthdays = days

    bymonths: tuple[int, ...] = ()
    if "BYMONTH" in seen:
        try:
            months = tuple(int(p) for p in seen["BYMONTH"].split(",") if p.strip())
        except ValueError as exc:
            raise _invalid(rule, "BYMONTH entries must be integers") from exc
        if not months or any(not 1 <= m <= 12 for m in months):
            raise _invalid(rule, "BYMONTH entries must be between 1 and 12")
        bymonths = months

    return RecurrenceRule(
        freq=freq,
        interval=interval,
        count=count,
        until=until,
        bydays=bydays,
        bymonthdays=bymonthdays,
        bymonths=bymonths,
    )


def expansion_supported(rule: RecurrenceRule) -> bool:
    """Whether ``expand_occurrences`` can project this rule.

    Everything in the write allowlist is expandable except the combinations
    where iCalendar semantics need positional BYDAY handling: MONTHLY or
    YEARLY together with BYDAY.
    """
    return not (rule.freq in ("MONTHLY", "YEARLY") and rule.bydays)


def _weekday_code(d: date) -> str:
    return _WEEKDAYS[d.weekday()]


def _add_months(year: int, month: int, months: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + months
    return index // 12, (index % 12) + 1


def expand_occurrences(
    *,
    master_start: datetime,
    rule: RecurrenceRule,
    window_start: datetime,
    window_end: datetime,
    ceiling: int,
) -> list[datetime] | None:
    """Project occurrence start datetimes for *rule* into the window.

    Returns ``None`` when the rule is valid but not expandable
    (``expansion_supported`` is False). Raises
    ``ToolError(code="CALENDAR_WINDOW_TOO_DENSE")`` when more than *ceiling*
    in-window occurrences accumulate. Arithmetic runs on the master's local
    wall clock (naive components) and re-attaches the master's tzinfo, which
    keeps recurring times stable across DST transitions.
    """
    if not expansion_supported(rule):
        return None

    tz = master_start.tzinfo
    naive_master = master_start.replace(tzinfo=None)
    results: list[datetime] = []
    produced = 0
    steps = 0

    def _emit(candidate_naive: datetime) -> bool:
        """Track COUNT/UNTIL, collect in-window hits; True means stop."""
        nonlocal produced
        candidate = candidate_naive.replace(tzinfo=tz)
        if rule.until is not None and candidate_naive.date() > rule.until:
            return True
        produced += 1
        if rule.count is not None and produced > rule.count:
            return True
        if candidate > window_end:
            return True
        if candidate >= window_start:
            results.append(candidate)
            if len(results) > ceiling:
                raise ToolError(
                    code="CALENDAR_WINDOW_TOO_DENSE",
                    message=(f"Recurring expansion exceeded {ceiling} occurrences in this window; narrow the window."),
                    remediation={"preferred": "Reduce days_ahead/days_back or scope to one calendar."},
                )
        return False

    if rule.freq == "DAILY":
        current = naive_master
        while steps < _MAX_EXPANSION_STEPS:
            steps += 1
            if _emit(current):
                break
            current = current + timedelta(days=rule.interval)
    elif rule.freq == "WEEKLY":
        bydays = rule.bydays or (_weekday_code(naive_master.date()),)
        week_anchor = naive_master - timedelta(days=naive_master.weekday())
        while steps < _MAX_EXPANSION_STEPS:
            stop = False
            for code in sorted(bydays, key=_WEEKDAYS.index):
                steps += 1
                candidate = week_anchor + timedelta(days=_WEEKDAYS.index(code))
                if candidate < naive_master:
                    continue
                if _emit(candidate):
                    stop = True
                    break
            if stop or steps >= _MAX_EXPANSION_STEPS:
                break
            week_anchor = week_anchor + timedelta(weeks=rule.interval)
    elif rule.freq == "MONTHLY":
        monthdays = rule.bymonthdays or (naive_master.day,)
        year, month = naive_master.year, naive_master.month
        while steps < _MAX_EXPANSION_STEPS:
            stop = False
            days_in_month = _stdlib_calendar.monthrange(year, month)[1]
            for day in sorted(monthdays):
                steps += 1
                if day > days_in_month:
                    continue
                candidate = naive_master.replace(year=year, month=month, day=day)
                if candidate < naive_master:
                    continue
                if _emit(candidate):
                    stop = True
                    break
            if stop or steps >= _MAX_EXPANSION_STEPS:
                break
            year, month = _add_months(year, month, rule.interval)
    else:  # YEARLY
        months = rule.bymonths or (naive_master.month,)
        monthdays = rule.bymonthdays or (naive_master.day,)
        year = naive_master.year
        while steps < _MAX_EXPANSION_STEPS:
            stop = False
            for month in sorted(months):
                days_in_month = _stdlib_calendar.monthrange(year, month)[1]
                for day in sorted(monthdays):
                    steps += 1
                    if day > days_in_month:
                        continue
                    candidate = naive_master.replace(year=year, month=month, day=day)
                    if candidate < naive_master:
                        continue
                    if _emit(candidate):
                        stop = True
                        break
                if stop:
                    break
            if stop or steps >= _MAX_EXPANSION_STEPS:
                break
            year += rule.interval

    return results


__all__ = [
    "RecurrenceRule",
    "expand_occurrences",
    "expansion_supported",
    "parse_rrule",
]
