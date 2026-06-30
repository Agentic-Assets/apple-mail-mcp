"""Analytics tools: attachments, statistics, exports, and dashboard.

Package facade. The single-file ``analytics.py`` was split into domain
submodules; this ``__init__`` re-exports every moved symbol so the historical
``apple_mail_mcp.tools.analytics.<name>`` attribute surface (cli.py + tests)
keeps resolving and the ``@mcp.tool`` handlers register exactly once. IO/core
collaborator names live here too: submodules that a test patches as a module
attribute (``run_applescript``, ``validate_account_name``,
``list_mail_account_names``, ``_get_recent_emails_structured_async``) call them
through this package namespace so the patch still fires.
"""

import asyncio
import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.applescript_snippets import sanitize_field_handler
from apple_mail_mcp.backend.base import ToolError, target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import MAX_WHOSE_IDS, build_whose_id_list, iter_id_chunks
from apple_mail_mcp.constants import SCAN_BOUNDS, SKIP_FOLDERS
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
    list_mail_account_names,
    normalize_message_ids,
    run_applescript,
    validate_account_name,
    validate_save_path,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, WRITE_TOOL_ANNOTATIONS, mcp

logger = logging.getLogger(__name__)

# Re-export moved tools and helpers so the historical
# apple_mail_mcp.tools.analytics.<name> attribute surface (cli.py + tests) is
# preserved and @mcp.tool registration runs once.
from apple_mail_mcp.tools.analytics.attachments import (
    _parse_attachment_listing_rows,
    list_email_attachments,
)
from apple_mail_mcp.tools.analytics.dashboard import (
    _build_recent_one_account_script,
    _get_recent_emails_structured,
    _get_recent_emails_structured_async,
    _parse_recent_email_lines,
    inbox_dashboard,
)
from apple_mail_mcp.tools.analytics.export import (
    _EXPORT_ENTIRE_MAILBOX_DEFAULT,
    _EXPORT_ENTIRE_MAILBOX_WARN_THRESHOLD,
    _build_exact_message_export_script,
    export_emails,
)
from apple_mail_mcp.tools.analytics.full_export import (
    _FULL_EXPORT_ALLOWED_FIELDS,
    _FULL_EXPORT_DEFAULT_FIELDS,
    _FULL_EXPORT_ERROR_PREFIX,
    _FULL_EXPORT_FIELD_EXPRS,
    _FULL_EXPORT_FIELD_SEP,
    _FULL_EXPORT_ROW_SEP,
    _full_export_batch_script,
    _full_export_field_script,
    _full_export_parse_batch,
    _normalize_full_export_fields,
    full_inbox_export,
)
from apple_mail_mcp.tools.analytics.statistics import get_statistics
from apple_mail_mcp.tools.analytics.statistics_parsing import (
    _STATISTICS_ERROR_PREFIX,
    _build_account_overview_report,
    _format_statistics_json,
    _parse_account_overview_statistics,
    _parse_mailbox_breakdown_statistics,
    _parse_sender_stats_statistics,
    _parse_statistics_errors,
    _parse_statistics_text,
    _statistics_json_error,
    _statistics_recent_days_applied,
    _statistics_scan_caps,
    _strip_statistics_error_lines,
)

__all__ = [
    "Any",
    "AppleScriptTimeout",
    "Counter",
    "MAX_WHOSE_IDS",
    "Path",
    "READ_ONLY_TOOL_ANNOTATIONS",
    "SCAN_BOUNDS",
    "SKIP_FOLDERS",
    "ToolError",
    "WRITE_TOOL_ANNOTATIONS",
    "_EXPORT_ENTIRE_MAILBOX_DEFAULT",
    "_EXPORT_ENTIRE_MAILBOX_WARN_THRESHOLD",
    "_FULL_EXPORT_ALLOWED_FIELDS",
    "_FULL_EXPORT_DEFAULT_FIELDS",
    "_FULL_EXPORT_ERROR_PREFIX",
    "_FULL_EXPORT_FIELD_EXPRS",
    "_FULL_EXPORT_FIELD_SEP",
    "_FULL_EXPORT_ROW_SEP",
    "_STATISTICS_ERROR_PREFIX",
    "_build_account_overview_report",
    "_build_exact_message_export_script",
    "_build_recent_one_account_script",
    "_format_statistics_json",
    "_full_export_batch_script",
    "_full_export_field_script",
    "_full_export_parse_batch",
    "_get_recent_emails_structured",
    "_get_recent_emails_structured_async",
    "_normalize_full_export_fields",
    "_parse_account_overview_statistics",
    "_parse_attachment_listing_rows",
    "_parse_mailbox_breakdown_statistics",
    "_parse_recent_email_lines",
    "_parse_sender_stats_statistics",
    "_parse_statistics_errors",
    "_parse_statistics_text",
    "_server",
    "_statistics_json_error",
    "_statistics_recent_days_applied",
    "_statistics_scan_caps",
    "_strip_statistics_error_lines",
    "asyncio",
    "build_whose_id_list",
    "escape_applescript",
    "export_emails",
    "full_inbox_export",
    "get_statistics",
    "inbox_dashboard",
    "inbox_mailbox_script",
    "inject_preferences",
    "iter_id_chunks",
    "json",
    "list_email_attachments",
    "list_mail_account_names",
    "logger",
    "logging",
    "mcp",
    "normalize_message_ids",
    "re",
    "run_applescript",
    "sanitize_field_handler",
    "target_selector_deprecated_error",
    "validate_account_name",
    "validate_save_path",
]
