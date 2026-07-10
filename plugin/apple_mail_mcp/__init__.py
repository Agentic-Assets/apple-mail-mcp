"""Apple Mail MCP - Modular package."""

from apple_mail_mcp.server import mcp

# UI availability flag
try:
    from ui import create_inbox_dashboard_ui

    UI_AVAILABLE = True
except ImportError:
    UI_AVAILABLE = False

# Import all tool modules to register @mcp.tool() decorators
from apple_mail_mcp.tools import (
    analytics,  # noqa: F401  (4 tools)
    calendar,  # noqa: F401  (10 tools)
    compose,  # noqa: F401  (6 tools)
    inbox,  # noqa: F401  (6 tools)
    manage,  # noqa: F401  (6 tools)
    search,  # noqa: F401  (3 tools)
    smart_inbox,  # noqa: F401  (3 tools)
)

__all__ = ["UI_AVAILABLE", "mcp"]
