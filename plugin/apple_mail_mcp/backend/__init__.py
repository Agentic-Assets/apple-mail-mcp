"""Mail backend seam — Protocol and error type re-exports.

``backend/base.py`` is the shared contract layer: tools import ``ToolError``,
``ScanWindow``, and related types from here. The ``AppleScriptBackend``
Phase-A scaffolding has been removed; production tools drive Mail.app directly
via ``core.run_applescript``.
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
    "InvalidationScope",
    "MailBackend",
    "MailReadBackend",
    "MailWriteBackend",
    "ScanWindow",
    "ToolError",
    "WriteResult",
]
