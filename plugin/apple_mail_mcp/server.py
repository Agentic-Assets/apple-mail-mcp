"""FastMCP server instance and user preferences."""

import os
from collections.abc import Callable
from typing import Any, ParamSpec, Protocol, TypeVar, cast

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

P = ParamSpec("P")
R = TypeVar("R")


class _AppleMailMCP(Protocol):
    """Typed subset of FastMCP used by this package.

    The installed FastMCP runtime has a typed ``tool`` method, but mypy treats
    it as untyped through the dependency boundary in strict mode. This protocol
    keeps the package strict without changing the runtime object or the
    ``@mcp.tool`` source pattern that manifest validators inspect.
    """

    def tool(
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Any] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = None,
    ) -> Callable[[Callable[P, R]], Callable[P, R]]: ...

    def remove_tool(self, name: str) -> None: ...

    def run(self) -> None: ...


# Initialize FastMCP server
mcp = cast(
    _AppleMailMCP,
    FastMCP(
        "Apple Mail MCP",
        instructions=(
            "Mail.app automation is single-threaded. This server serializes every "
            "AppleScript call behind a lock, so invoking multiple Apple Mail tools "
            "at once does not run them in parallel; the calls queue and can time "
            "out waiting their turn. Call one Apple Mail tool at a time and wait "
            "for its result before issuing the next. On large Exchange or Gmail "
            "mailboxes, prefer small bounded calls (low max_emails, small "
            "recent_days, offset paging) over large ones."
        ),
    ),
)

# Shared MCP tool annotations (see tasks/reference/phase-3-annotation-matrix.md).
READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

WRITE_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

IDEMPOTENT_WRITE_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

DESTRUCTIVE_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)

SEND_TOOLS = ("compose_email", "reply_to_email", "forward_email")

# Load user preferences from environment
USER_PREFERENCES = os.environ.get("USER_EMAIL_PREFERENCES", "")

# Default Mail account name. When set, search/list tools default to this
# account instead of fanning out across every configured account. Tests
# monkeypatch ``apple_mail_mcp.server.DEFAULT_MAIL_ACCOUNT`` directly, so
# tools should read this lazily (e.g. ``from apple_mail_mcp import server;
# server.DEFAULT_MAIL_ACCOUNT``) rather than importing the constant once.
DEFAULT_MAIL_ACCOUNT = os.environ.get("DEFAULT_MAIL_ACCOUNT", "").strip() or None
DEFAULT_MAIL_SIGNATURE = os.environ.get("DEFAULT_MAIL_SIGNATURE", "").strip() or None

# Read-only mode flag — set via --read-only CLI argument.
# When enabled, tools that send email are disabled. Drafts remain available.
READ_ONLY = False

# Draft-safe mode flag — set via --draft-safe CLI argument.
# When enabled, sending is disabled but draft/open workflows remain available.
DRAFT_SAFE = False
