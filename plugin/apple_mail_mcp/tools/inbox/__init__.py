"""Inbox tools: listing, counting, and overview.

Linker/facade for the ``inbox`` package. IO/core/server imports come first so the
patchable attribute surface (``apple_mail_mcp.tools.inbox.run_applescript`` etc.)
exists before the tool submodules resolve it via the ``inbox`` facade at call
time; then every moved symbol is re-imported from the leaf submodules and listed
in ``__all__`` so mypy --strict (no-implicit-reexport) stays clean and
``apple_mail_mcp.tools.inbox.<name>`` remains importable for cli.py and the test
suite. Importing the package imports the tool submodules, registering all six
inbox tools exactly once."""

import asyncio
import json
from typing import Any, cast

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.bounded_scan import (
    build_bounded_filtered_scan,
    build_bounded_message_scan,
)
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    account_not_found_json,
    content_preview_script,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
    run_applescript,
    sanitize_pipe_delimited_field,
    validate_account_name,
)
from apple_mail_mcp.core import (
    fetch_replied_ids as _core_fetch_replied_ids,
)
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp

# Re-export moved helpers and tools so the historical apple_mail_mcp.tools.inbox.<name>
# attribute surface (cli.py + tests) is preserved and @mcp.tool registration runs once.
from apple_mail_mcp.tools.inbox.accounts import (
    _list_accounts_script,
    _list_mail_accounts,
    list_account_addresses,
    list_accounts,
)
from apple_mail_mcp.tools.inbox.list_emails import (
    _attach_warnings_to_json,
    _list_inbox_emails_json,
    _list_inbox_emails_text,
    _run_json_one,
    _run_text_one,
    list_inbox_emails,
)
from apple_mail_mcp.tools.inbox.list_scripts import (
    _build_inbox_collection_block,
    _build_list_inbox_json_script,
    _build_list_inbox_text_script,
)
from apple_mail_mcp.tools.inbox.mailboxes import _list_mailboxes_json, list_mailboxes
from apple_mail_mcp.tools.inbox.overview import (
    _build_overview_one_account_script,
    _format_overview,
    _format_overview_json,
    _overview_json_error,
    _overview_suggestions,
    _parse_overview_account,
    _run_overview_one,
    get_inbox_overview,
)
from apple_mail_mcp.tools.inbox.parsing import (
    _VALID_READ_FILTERS,
    _parse_pipe_delimited_emails,
    _read_filter_condition,
    _resolve_read_filter,
    _strip_count_marker,
)
from apple_mail_mcp.tools.inbox.replied import (
    _apply_replied_to_emails,
    _filter_text_body_by_replied,
    _normalize_message_id_token,
    fetch_replied_ids,
)
from apple_mail_mcp.tools.inbox.unread_counts import get_mailbox_unread_counts

__all__ = [
    "Any",
    "AppleScriptTimeout",
    "READ_ONLY_TOOL_ANNOTATIONS",
    "SCAN_BOUNDS",
    "ToolError",
    "_VALID_READ_FILTERS",
    "_apply_replied_to_emails",
    "_attach_warnings_to_json",
    "_build_inbox_collection_block",
    "_build_list_inbox_json_script",
    "_build_list_inbox_text_script",
    "_build_overview_one_account_script",
    "_core_fetch_replied_ids",
    "_filter_text_body_by_replied",
    "_format_overview",
    "_format_overview_json",
    "_list_accounts_script",
    "_list_inbox_emails_json",
    "_list_inbox_emails_text",
    "_list_mail_accounts",
    "_list_mailboxes_json",
    "_normalize_message_id_token",
    "_overview_json_error",
    "_overview_suggestions",
    "_parse_overview_account",
    "_parse_pipe_delimited_emails",
    "_read_filter_condition",
    "_resolve_read_filter",
    "_run_json_one",
    "_run_overview_one",
    "_run_text_one",
    "_server",
    "_strip_count_marker",
    "account_not_found_json",
    "asyncio",
    "build_bounded_filtered_scan",
    "build_bounded_message_scan",
    "cast",
    "content_preview_script",
    "escape_applescript",
    "fetch_replied_ids",
    "get_inbox_overview",
    "get_mailbox_unread_counts",
    "inbox_mailbox_script",
    "inject_preferences",
    "json",
    "list_account_addresses",
    "list_accounts",
    "list_inbox_emails",
    "list_mailboxes",
    "mcp",
    "run_applescript",
    "sanitize_pipe_delimited_field",
    "validate_account_name",
]
