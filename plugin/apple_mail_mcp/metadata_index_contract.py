"""Metadata-index guardrails for future ID-first discovery work.

This module is intentionally not wired into runtime search paths yet. It
captures the privacy, freshness, and hydration rules that a future local cache
must satisfy before it can answer discovery requests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

DEFAULT_CACHE_TTL_SECONDS = 15 * 60
DEFAULT_CACHE_RELATIVE_PATH = Path("Library") / "Caches" / "AgenticAssets" / "apple-mail-mcp" / "metadata-index"


class CoverageTier(str, Enum):
    """How much source data a metadata-index row contains."""

    BULK_METADATA = "bulk_metadata"
    EXACT_HYDRATED = "exact_hydrated"


class MetadataCapability(str, Enum):
    """Question classes a metadata-index row may or may not answer."""

    METADATA = "metadata"
    RECIPIENTS = "recipients"
    HEADERS = "headers"
    THREAD = "thread"
    ATTACHMENTS = "attachments"
    BODY = "body"


HYDRATED_ONLY_CAPABILITIES = frozenset(
    {
        MetadataCapability.RECIPIENTS,
        MetadataCapability.HEADERS,
        MetadataCapability.THREAD,
        MetadataCapability.ATTACHMENTS,
        MetadataCapability.BODY,
    }
)


def default_cache_root(home: Path | None = None) -> Path:
    """Return the default cache root outside repo/package artifacts."""
    base = home or Path.home()
    return base / DEFAULT_CACHE_RELATIVE_PATH


def is_within_path(child: Path, parent: Path) -> bool:
    """Return True when ``child`` resolves under ``parent`` without requiring existence."""
    child_parts = child.expanduser().resolve(strict=False).parts
    parent_parts = parent.expanduser().resolve(strict=False).parts
    return child_parts[: len(parent_parts)] == parent_parts


@dataclass(frozen=True)
class MetadataIndexPolicy:
    """Runtime policy gate for a future local metadata index."""

    cache_root: Path = field(default_factory=default_cache_root)
    opt_in: bool = False
    ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS

    def runtime_reads_enabled(self) -> bool:
        """Return whether callers may consult the cache for runtime discovery."""
        return self.opt_in

    def cache_root_allowed(self, repo_root: Path, package_roots: tuple[Path, ...] = ()) -> bool:
        """Return False when the cache root would live inside repo or package artifacts."""
        blocked_roots = (repo_root, *package_roots)
        return not any(is_within_path(self.cache_root, blocked_root) for blocked_root in blocked_roots)


@dataclass(frozen=True)
class MetadataIndexRow:
    """One local metadata-index row, keyed by account/mailbox/message id."""

    account: str
    mailbox: str
    message_id: str
    captured_at: datetime
    source_tool: str
    coverage_tier: CoverageTier = CoverageTier.BULK_METADATA
    internet_message_id: str | None = None
    date_received: str | None = None
    sender: str | None = None
    subject: str | None = None
    flags: frozenset[str] = frozenset()
    hydrated_capabilities: frozenset[MetadataCapability] = frozenset()
    mailbox_total_at_capture: int | None = None
    watermark: str | None = None

    def is_complete_key(self) -> bool:
        """Return whether the row has the exact handle required for ID-first use."""
        return bool(self.account and self.mailbox and self.message_id.isdecimal())

    def is_fresh(self, *, now: datetime | None = None, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> bool:
        """Return whether the row is within the configured freshness window."""
        effective_now = now or datetime.now(timezone.utc)
        return effective_now - self.captured_at <= timedelta(seconds=ttl_seconds)

    def can_answer(
        self,
        capability: MetadataCapability,
        *,
        now: datetime | None = None,
        ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    ) -> bool:
        """Return whether this row can answer a question class without hydration."""
        if not self.is_complete_key() or not self.is_fresh(now=now, ttl_seconds=ttl_seconds):
            return False
        if capability == MetadataCapability.METADATA:
            return True
        if capability in HYDRATED_ONLY_CAPABILITIES:
            return self.coverage_tier == CoverageTier.EXACT_HYDRATED and capability in self.hydrated_capabilities
        return False
