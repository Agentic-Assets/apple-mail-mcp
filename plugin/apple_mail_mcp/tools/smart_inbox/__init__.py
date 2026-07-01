"""Smart inbox tools: follow-up tracking, actionable email detection, and sender analytics.

Linker/facade preserving the ``apple_mail_mcp.tools.smart_inbox`` import path and
attribute surface (cli.py + tests). IO/core/server imports come first so
``run_applescript``, ``validate_account_name``, and ``_server`` stay patchable as
module attributes; the tool submodules are then imported (registering all three
``@mcp.tool`` tools exactly once) and every moved symbol is re-exported via the
explicit ``__all__`` (keeps mypy --strict no-implicit-reexport clean).
"""

from collections import Counter
from dataclasses import dataclass
from typing import Any

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.constants import (
    NEWSLETTER_KEYWORD_PATTERNS,
    NEWSLETTER_PLATFORM_PATTERNS,
    SCAN_BOUNDS,
)
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    date_cutoff_script,
    escape_applescript,
    fetch_replied_ids,
    inbox_mailbox_script,
    inject_preferences,
    run_applescript,
    sanitize_pipe_delimited_field,
    validate_account_name,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp

# Re-export every moved symbol so the historical attribute surface
# (``apple_mail_mcp.tools.smart_inbox.<name>`` for cli.py + tests) is preserved
# and importing the package registers all three @mcp.tool tools exactly once.
from apple_mail_mcp.tools.smart_inbox.awaiting_reply import (
    _awaiting_reply_error,
    _AwaitingReplySentRow,
    _build_awaiting_reply_inbox_script,
    _build_awaiting_reply_json,
    _build_awaiting_reply_sent_script,
    _filter_awaiting_reply,
    _format_awaiting_reply_results,
    _is_noreply_recipient,
    _parse_awaiting_reply_sent_rows,
    _parse_inbox_replied_ids,
    _sent_mailbox_script,
    get_awaiting_reply,
)
from apple_mail_mcp.tools.smart_inbox.helpers import _normalize_message_id
from apple_mail_mcp.tools.smart_inbox.needs_response import (
    _build_needs_response_inbox_script,
    _classify_needs_response_rows,
    _format_needs_response_text,
    _needs_response_error,
    _NeedsResponseRow,
    _newsletter_filter_condition,
    _parse_needs_response_inbox_rows,
    _priority_label,
    get_needs_response,
)
from apple_mail_mcp.tools.smart_inbox.top_senders import _top_senders_error, get_top_senders

__all__ = [
    "Any",
    "AppleScriptTimeout",
    "Counter",
    "NEWSLETTER_KEYWORD_PATTERNS",
    "NEWSLETTER_PLATFORM_PATTERNS",
    "READ_ONLY_TOOL_ANNOTATIONS",
    "SCAN_BOUNDS",
    "ToolError",
    "_AwaitingReplySentRow",
    "_NeedsResponseRow",
    "_awaiting_reply_error",
    "_build_awaiting_reply_inbox_script",
    "_build_awaiting_reply_json",
    "_build_awaiting_reply_sent_script",
    "_build_needs_response_inbox_script",
    "_classify_needs_response_rows",
    "_filter_awaiting_reply",
    "_format_awaiting_reply_results",
    "_format_needs_response_text",
    "_is_noreply_recipient",
    "_needs_response_error",
    "_newsletter_filter_condition",
    "_normalize_message_id",
    "_parse_awaiting_reply_sent_rows",
    "_parse_inbox_replied_ids",
    "_parse_needs_response_inbox_rows",
    "_priority_label",
    "_sent_mailbox_script",
    "_server",
    "_top_senders_error",
    "date_cutoff_script",
    "dataclass",
    "escape_applescript",
    "fetch_replied_ids",
    "get_awaiting_reply",
    "get_needs_response",
    "get_top_senders",
    "inbox_mailbox_script",
    "inject_preferences",
    "mcp",
    "run_applescript",
    "sanitize_pipe_delimited_field",
    "serialize_tool_error",
    "validate_account_name",
]
