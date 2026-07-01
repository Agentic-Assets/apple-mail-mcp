"""Tests for the future metadata-index contract."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from apple_mail_mcp.metadata_index_contract import (
    CoverageTier,
    MetadataCapability,
    MetadataIndexPolicy,
    MetadataIndexRow,
    default_cache_root,
)

NOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


def _row(**overrides):
    values = {
        "account": "Work",
        "mailbox": "INBOX",
        "message_id": "84053",
        "captured_at": NOW,
        "source_tool": "full_inbox_export",
        "internet_message_id": "<dummy@example.invalid>",
        "date_received": "2026-06-30 12:00",
        "sender": "Example Sender <sender@example.invalid>",
        "subject": "Dummy subject",
    }
    values.update(overrides)
    return MetadataIndexRow(**values)


def test_default_cache_root_is_outside_repo_style_paths():
    root = default_cache_root(Path("/Users/example"))

    assert root == Path("/Users/example/Library/Caches/AgenticAssets/apple-mail-mcp/metadata-index")


def test_policy_requires_opt_in_and_rejects_repo_or_package_cache_roots():
    repo_root = Path("/repo/apple-mail-mcp")
    package_root = repo_root / "plugin"

    disabled_policy = MetadataIndexPolicy(cache_root=Path("/Users/example/Library/Caches/AgenticAssets/cache"))
    repo_policy = MetadataIndexPolicy(cache_root=repo_root / ".cache" / "metadata-index", opt_in=True)
    package_policy = MetadataIndexPolicy(cache_root=package_root / ".cache" / "metadata-index", opt_in=True)
    allowed_policy = MetadataIndexPolicy(
        cache_root=Path("/Users/example/Library/Caches/AgenticAssets/apple-mail-mcp/metadata-index"),
        opt_in=True,
    )

    assert not disabled_policy.runtime_reads_enabled()
    assert not repo_policy.cache_root_allowed(repo_root, (package_root,))
    assert not package_policy.cache_root_allowed(repo_root, (package_root,))
    assert allowed_policy.cache_root_allowed(repo_root, (package_root,))


def test_bulk_metadata_row_can_answer_only_metadata_questions():
    row = _row(coverage_tier=CoverageTier.BULK_METADATA)

    assert row.can_answer(MetadataCapability.METADATA, now=NOW)
    assert not row.can_answer(MetadataCapability.RECIPIENTS, now=NOW)
    assert not row.can_answer(MetadataCapability.HEADERS, now=NOW)
    assert not row.can_answer(MetadataCapability.THREAD, now=NOW)
    assert not row.can_answer(MetadataCapability.ATTACHMENTS, now=NOW)
    assert not row.can_answer(MetadataCapability.BODY, now=NOW)


def test_exact_hydrated_row_answers_only_explicit_hydrated_capabilities():
    row = _row(
        coverage_tier=CoverageTier.EXACT_HYDRATED,
        hydrated_capabilities=frozenset({MetadataCapability.HEADERS, MetadataCapability.ATTACHMENTS}),
    )

    assert row.can_answer(MetadataCapability.METADATA, now=NOW)
    assert row.can_answer(MetadataCapability.HEADERS, now=NOW)
    assert row.can_answer(MetadataCapability.ATTACHMENTS, now=NOW)
    assert not row.can_answer(MetadataCapability.RECIPIENTS, now=NOW)
    assert not row.can_answer(MetadataCapability.THREAD, now=NOW)
    assert not row.can_answer(MetadataCapability.BODY, now=NOW)


def test_stale_or_incomplete_rows_cannot_answer_cache_queries():
    stale_row = _row(captured_at=NOW - timedelta(seconds=901))
    missing_scope = _row(mailbox="")
    invalid_id = _row(message_id="not-numeric")

    assert not stale_row.can_answer(MetadataCapability.METADATA, now=NOW, ttl_seconds=900)
    assert not missing_scope.can_answer(MetadataCapability.METADATA, now=NOW)
    assert not invalid_id.can_answer(MetadataCapability.METADATA, now=NOW)
