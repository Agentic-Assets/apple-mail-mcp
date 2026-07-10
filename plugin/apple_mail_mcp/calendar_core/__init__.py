"""Calendar core: bounded windows, validation, engines, and script builders.

Facade for the ``apple_mail_mcp.calendar_core`` package, mirroring the mail
``core`` facade: every public symbol is re-exported here so it stays a valid
``apple_mail_mcp.calendar_core.<name>`` attribute (test patch surface) and
mypy --strict no-implicit-reexport stays clean.

Naming note: this package is ``calendar_core`` (never ``calendar``) so the
stdlib ``calendar`` module stays importable; recurrence expansion uses it.
"""

from apple_mail_mcp.calendar_core.engine import (
    AppleScriptCalendarEngine,
    CalendarReadEngine,
    get_engine,
    get_write_engine,
)
from apple_mail_mcp.calendar_core.eventkit import (
    EventKitCalendarEngine,
    eventkit_status,
    load_frameworks,
)
from apple_mail_mcp.calendar_core.records import (
    event_payload,
    parse_calendar_rows,
    parse_event_rows,
    parse_numeric_datetime,
)
from apple_mail_mcp.calendar_core.recurrence import (
    RecurrenceRule,
    expand_occurrences,
    expansion_supported,
    parse_rrule,
)
from apple_mail_mcp.calendar_core.scripts_read import (
    applescript_date_block,
    build_uid_condition,
    count_events_script,
    fetch_recurring_masters_script,
    fetch_window_events_script,
    list_calendars_script,
)
from apple_mail_mcp.calendar_core.scripts_write import (
    build_event_set_lines,
    create_calendar_script,
    create_event_script,
    delete_calendar_script,
    delete_events_script,
    rename_calendar_script,
    update_event_script,
)
from apple_mail_mcp.calendar_core.validation import (
    EVENT_ID_MAX_LEN,
    normalize_event_ids,
    resolve_calendar_name,
    validate_alarms,
    validate_attendee_emails,
    validate_event_id,
    validate_rrule,
    validate_slot_params,
)
from apple_mail_mcp.calendar_core.window import (
    CalendarWindow,
    bounded_calendar_window,
    isoformat_pair,
    parse_iso_datetime,
    require_issued_window,
    resolve_timezone,
    shifted_window,
    window_payload,
)

__all__ = [
    "EVENT_ID_MAX_LEN",
    "AppleScriptCalendarEngine",
    "CalendarReadEngine",
    "CalendarWindow",
    "EventKitCalendarEngine",
    "RecurrenceRule",
    "applescript_date_block",
    "bounded_calendar_window",
    "build_event_set_lines",
    "build_uid_condition",
    "count_events_script",
    "create_calendar_script",
    "create_event_script",
    "delete_calendar_script",
    "delete_events_script",
    "event_payload",
    "eventkit_status",
    "expand_occurrences",
    "expansion_supported",
    "fetch_recurring_masters_script",
    "fetch_window_events_script",
    "get_engine",
    "get_write_engine",
    "isoformat_pair",
    "list_calendars_script",
    "load_frameworks",
    "normalize_event_ids",
    "parse_calendar_rows",
    "parse_event_rows",
    "parse_iso_datetime",
    "parse_numeric_datetime",
    "parse_rrule",
    "rename_calendar_script",
    "require_issued_window",
    "resolve_calendar_name",
    "resolve_timezone",
    "shifted_window",
    "update_event_script",
    "validate_alarms",
    "validate_attendee_emails",
    "validate_event_id",
    "validate_rrule",
    "validate_slot_params",
    "window_payload",
]
