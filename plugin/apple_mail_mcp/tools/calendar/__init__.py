"""Calendar tools: bounded reads, gated writes, and calendar management.

Linker/facade for the ``calendar`` tool package. Engine seams
(``get_engine``, ``get_write_engine``, ``eventkit_status``,
``run_applescript``) and the ``list_calendar_names`` resolution helper are
imported first so tests can patch them as
``apple_mail_mcp.tools.calendar.<name>`` attributes; the tool submodules are
then imported (registering the ten ``@mcp.tool`` tools exactly once); and
``__all__`` re-exports every public symbol so mypy --strict
no-implicit-reexport stays clean.

Naming caution: never ``import calendar`` (the stdlib module) inside this
package; use absolute imports and ``datetime``/``zoneinfo`` only. The engine
package is deliberately named ``calendar_core`` for the same reason.
"""

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.calendar_core import (
    bounded_calendar_window,
    eventkit_status,
    get_engine,
    get_write_engine,
)
from apple_mail_mcp.calendar_core.engine import run_applescript
from apple_mail_mcp.core import AppleScriptTimeout, inject_preferences
from apple_mail_mcp.server import mcp
from apple_mail_mcp.tools.calendar.availability import check_availability
from apple_mail_mcp.tools.calendar.calendars_list import list_calendars
from apple_mail_mcp.tools.calendar.calendars_manage import manage_calendars
from apple_mail_mcp.tools.calendar.events_batch import batch_create_events
from apple_mail_mcp.tools.calendar.events_create import create_event, resolve_event_times
from apple_mail_mcp.tools.calendar.events_delete import delete_events
from apple_mail_mcp.tools.calendar.events_get import get_events_by_id
from apple_mail_mcp.tools.calendar.events_list import list_events
from apple_mail_mcp.tools.calendar.events_update import update_event

# helpers must import before the tool submodules (they all use it), and it
# defines list_calendar_names which the tool modules reach through this
# facade at call time (the conftest autouse fixture patches it here).
from apple_mail_mcp.tools.calendar.helpers import (
    AUTOMATION_PANE_NOTE,
    CallBudget,
    attendee_gate,
    calendar_delete_blocked,
    calendar_write_blocked,
    collect_window_events,
    error_json,
    find_conflicts,
    finish,
    list_calendar_names,
    output_format_error,
    render_events_text,
    resolve_create_target,
    resolve_read_calendars,
    timeout_error,
    tz_for_window,
    validate_on_conflict,
)
from apple_mail_mcp.tools.calendar.rsvp import respond_to_invitation

__all__ = [
    "AUTOMATION_PANE_NOTE",
    "AppleScriptTimeout",
    "CallBudget",
    "ToolError",
    "_server",
    "attendee_gate",
    "batch_create_events",
    "bounded_calendar_window",
    "calendar_delete_blocked",
    "calendar_write_blocked",
    "check_availability",
    "collect_window_events",
    "create_event",
    "delete_events",
    "error_json",
    "eventkit_status",
    "find_conflicts",
    "finish",
    "get_engine",
    "get_events_by_id",
    "get_write_engine",
    "inject_preferences",
    "list_calendar_names",
    "list_calendars",
    "list_events",
    "manage_calendars",
    "mcp",
    "output_format_error",
    "render_events_text",
    "resolve_create_target",
    "resolve_event_times",
    "resolve_read_calendars",
    "respond_to_invitation",
    "run_applescript",
    "serialize_tool_error",
    "timeout_error",
    "tz_for_window",
    "update_event",
    "validate_on_conflict",
]
