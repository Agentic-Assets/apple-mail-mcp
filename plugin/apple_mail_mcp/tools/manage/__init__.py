"""Management tools: moving, status updates, trash, attachments, mailbox creation, and sync.

Linker/facade for the ``apple_mail_mcp.tools.manage`` package. IO/core/server/search
imports come FIRST so the module-attribute patch seams
(``run_applescript``, ``_search_mail_records``, ``validate_account_name``,
``list_mail_account_names``) resolve on this module; the tool submodules then
re-import them through ``from apple_mail_mcp.tools import manage`` and call
``manage.<name>(...)`` at call time. Importing the package imports each tool
submodule once, registering all six ``@mcp.tool`` tools."""

import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error, target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS, build_whose_id_list
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    build_mailbox_ref,
    contains_any_condition,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
    list_mail_account_names,
    normalize_message_ids,
    normalize_search_terms,
    run_applescript,
    validate_account_name,
    validate_save_path,
)
from apple_mail_mcp.server import (
    DESTRUCTIVE_TOOL_ANNOTATIONS,
    IDEMPOTENT_WRITE_TOOL_ANNOTATIONS,
    WRITE_TOOL_ANNOTATIONS,
    mcp,
)
from apple_mail_mcp.tools.manage.attachments import save_email_attachment

# Re-export moved helpers and tools so the historical apple_mail_mcp.tools.manage.<name>
# attribute surface (cli.py + tests) is preserved and @mcp.tool registration runs once.
from apple_mail_mcp.tools.manage.helpers import (
    FILTER_SCAN_WARNING,
    _check_message_ids_cap,
    _date_from_for_recent_days,
    _date_to_for_older_than,
    _deprecated_target_selectors,
    _filter_scan_disabled_error,
    _format_dry_run_records,
    _search_message_ids,
    _with_filter_scan_warning,
)
from apple_mail_mcp.tools.manage.mailbox import _INVALID_MAILBOX_CHARS, create_mailbox
from apple_mail_mcp.tools.manage.move import _move_email_by_message_ids, move_email
from apple_mail_mcp.tools.manage.status import update_email_status
from apple_mail_mcp.tools.manage.sync import synchronize_account
from apple_mail_mcp.tools.manage.trash import manage_trash
from apple_mail_mcp.tools.search import _search_mail_records_sync as _search_mail_records

__all__ = [
    "Any",
    "AppleScriptTimeout",
    "DESTRUCTIVE_TOOL_ANNOTATIONS",
    "FILTER_SCAN_WARNING",
    "IDEMPOTENT_WRITE_TOOL_ANNOTATIONS",
    "MAX_WHOSE_IDS",
    "Path",
    "ToolError",
    "WRITE_TOOL_ANNOTATIONS",
    "_INVALID_MAILBOX_CHARS",
    "_check_message_ids_cap",
    "_date_from_for_recent_days",
    "_date_to_for_older_than",
    "_deprecated_target_selectors",
    "_filter_scan_disabled_error",
    "_format_dry_run_records",
    "_move_email_by_message_ids",
    "_search_mail_records",
    "_search_message_ids",
    "_server",
    "_with_filter_scan_warning",
    "build_mailbox_ref",
    "build_whose_id_list",
    "contains_any_condition",
    "create_mailbox",
    "datetime",
    "escape_applescript",
    "inbox_mailbox_script",
    "inject_preferences",
    "list_mail_account_names",
    "manage_trash",
    "mcp",
    "move_email",
    "normalize_message_ids",
    "normalize_search_terms",
    "re",
    "run_applescript",
    "save_email_attachment",
    "serialize_tool_error",
    "shutil",
    "synchronize_account",
    "target_selector_deprecated_error",
    "timedelta",
    "update_email_status",
    "validate_account_name",
    "validate_save_path",
]
