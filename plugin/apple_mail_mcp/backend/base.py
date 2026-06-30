"""Backend Protocol types and value objects for the Mail backend seam.

This module defines the *dormant* interface that wave-2 tool migrations will
route through. Phase A (whose-elimination) introduces only the types and a
runtime capability check; existing tools still call ``core.run_applescript``
directly. See ``tasks/whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md``
"Architecture: the seam".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanWindow:
    """A capability-token describing a bounded Mail.app scan.

    Only ``bounded_inbox_scan`` may construct instances with
    ``_issued_by="core.bounded_inbox_scan"``. Backends MUST refuse any
    ``ScanWindow`` whose token does not match — this prevents tools from
    smuggling in unbounded scans that re-enable the slow ``whose`` paths
    we are removing.
    """

    mailbox: str
    recent_days: float | None = None
    limit: int | None = None
    since: float | None = None
    _issued_by: str = ""


@dataclass(frozen=True)
class InvalidationScope:
    """Describes which backend caches/state to invalidate after a write."""

    mailbox: str | None = None
    account: str | None = None
    all: bool = False


@dataclass(frozen=True)
class WriteResult:
    """Structured result for write operations across the backend protocol."""

    ok: bool
    message_ids: tuple[str, ...] = ()
    invalidates: tuple[InvalidationScope, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Structured error
# ---------------------------------------------------------------------------


class ToolError(Exception):
    """Structured error carrying an error code and remediation guidance.

    Tools and backends raise ``ToolError`` to surface a machine-readable
    code (e.g. ``UNBOUNDED_SCAN_REQUIRED``) plus a remediation payload
    that downstream agent skills can act on. ``to_dict()`` returns the
    JSON-friendly shape used at the MCP boundary.
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        remediation: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": True,
            "code": self.code,
            "message": self.message,
            "remediation": self.remediation or {},
        }


def serialize_tool_error(error: ToolError) -> str:
    """Serialize a ``ToolError`` to the standard MCP-boundary JSON envelope.

    All tools wrap structured errors as ``json.dumps(err.to_dict(), indent=2)``
    so agents receive identical, parseable envelopes regardless of which tool
    emitted them. Centralizing the shape here keeps the envelope consistent
    if it ever needs to grow (e.g. trace ids).
    """
    return json.dumps(error.to_dict(), indent=2)


def target_selector_deprecated_error(
    tool_name: str,
    selectors: tuple[str, ...],
    *,
    preferred: str,
    discovery: str,
    exact_selector: str,
) -> str:
    """Return a standard deprecation error for legacy target selectors."""
    selector_text = ", ".join(selectors)
    return serialize_tool_error(
        ToolError(
            code="TARGET_SELECTOR_DEPRECATED",
            message=(
                f"{tool_name} no longer selects target messages by {selector_text}. "
                "Use discovery tools to collect exact ids, then call the action tool by id."
            ),
            remediation={
                "preferred": preferred,
                "discovery": discovery,
                "exact_selector": exact_selector,
                "deprecated_selectors": list(selectors),
            },
        )
    )


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class MailReadBackend(Protocol):
    """Read-only operations against the underlying Mail store."""

    def list_messages(
        self,
        window: ScanWindow,
        *,
        fields: tuple[str, ...] = (
            "id",
            "subject",
            "sender",
            "date",
            "read",
        ),
        include_read: bool = True,
    ) -> list[dict[str, Any]]: ...

    def count_messages(
        self,
        window: ScanWindow,
        *,
        include_read: bool = True,
    ) -> int: ...

    def search_messages(
        self,
        window: ScanWindow,
        *,
        query: str | None = None,
        sender: str | None = None,
        subject: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_message_by_id(
        self,
        *,
        mailbox: str,
        message_id: str,
    ) -> dict[str, Any] | None: ...

    def list_mailboxes(
        self,
        *,
        account: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def list_accounts(self) -> list[dict[str, Any]]: ...


class MailWriteBackend(Protocol):
    """Mutating operations against the underlying Mail store."""

    def move_messages(
        self,
        *,
        source_mailbox: str,
        target_mailbox: str,
        message_ids: list[str],
    ) -> WriteResult: ...

    def update_status(
        self,
        *,
        mailbox: str,
        message_ids: list[str],
        read: bool | None = None,
        flagged: bool | None = None,
    ) -> WriteResult: ...

    def empty_trash(
        self,
        *,
        account: str,
        older_than_days: int | None = None,
    ) -> WriteResult:
        """The only legitimate full-scan write path."""
        ...

    def invalidate(self, scope: InvalidationScope) -> None: ...


class MailBackend(MailReadBackend, MailWriteBackend, Protocol):
    """Combined read+write backend protocol."""

    ...
