"""Mail backend seam — singleton accessor and Protocol re-exports.

Phase A scaffolding: tools do NOT yet route through ``backend()``. The
active backend defaults to a lazily-constructed ``AppleScriptBackend``.
Tests can swap it with ``set_backend`` and restore the default via
``reset_backend``.
"""

from __future__ import annotations

from apple_mail_mcp.backend.base import (
    InvalidationScope,
    MailBackend,
    MailReadBackend,
    MailWriteBackend,
    ScanWindow,
    ToolError,
    WriteResult,
)

__all__ = [
    "backend",
    "set_backend",
    "reset_backend",
    "InvalidationScope",
    "MailBackend",
    "MailReadBackend",
    "MailWriteBackend",
    "ScanWindow",
    "ToolError",
    "WriteResult",
]


_backend: MailBackend | None = None


def backend() -> MailBackend:
    """Return the active backend, lazily constructing the default."""
    global _backend
    if _backend is None:
        # Imported lazily so test patches that swap the backend before
        # first use don't pay AppleScript-module import cost.
        from apple_mail_mcp.backend.applescript import AppleScriptBackend

        _backend = AppleScriptBackend()
    return _backend


def set_backend(b: MailBackend) -> None:
    """Install *b* as the active backend (intended for tests)."""
    global _backend
    _backend = b


def reset_backend() -> None:
    """Drop the active backend so the next ``backend()`` rebuilds the default."""
    global _backend
    _backend = None
