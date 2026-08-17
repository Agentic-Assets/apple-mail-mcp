"""Apple Mail MCP - Modular package."""

from apple_mail_mcp.server import mcp

# UI availability flag
try:
    from ui import create_inbox_dashboard_ui

    UI_AVAILABLE = True
except ImportError:
    UI_AVAILABLE = False

# Import all tool modules to register @mcp.tool() decorators.
# Per-module tool counts deliberately live in tools/CLAUDE.md, not here:
# validate_manifests.py gates that table against the real @mcp.tool count, and
# nothing gates a comment. The counts that used to sit on these lines drifted to
# 38 against an actual 41 and stayed wrong for five weeks.
from apple_mail_mcp.tools import (
    analytics,  # noqa: F401
    calendar,  # noqa: F401
    compose,  # noqa: F401
    inbox,  # noqa: F401
    manage,  # noqa: F401
    search,  # noqa: F401
    smart_inbox,  # noqa: F401
)

__all__ = ["UI_AVAILABLE", "mcp"]
