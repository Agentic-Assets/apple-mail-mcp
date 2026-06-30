# Metadata Index Feasibility Spike, 2026-06-30

## Scope

This spike defines the local metadata-index contract for the ID-first search retirement work. It does not wire a cache into `search_emails`, `full_inbox_export`, or any mutation tool. Runtime integration should wait for maintainer review.

## Dictionary Evidence

Local Mail dictionary inspected:

- `/System/Applications/Mail.app/Contents/Resources/Mail.sdef`
- `message.id` is a read-only integer.
- `message.message id` is the Internet Message-ID header string.
- `message.mailbox`, `message.date received`, `message.sender`, `message.subject`, and flag fields are read-only message metadata.
- `message.all headers` is read-only text.
- `message` has `header` elements and `mail attachment` elements.
- `mail attachment` exposes `name`, `MIME type`, `file size`, `downloaded`, and `id`.

This supports a two-tier cache contract:

- `bulk_metadata`: account, mailbox, numeric message id, Internet Message-ID when available, date, sender, subject, and flags.
- `exact_hydrated`: a row hydrated by exact id or a bounded explicit candidate set with recipients, selected headers, thread linkage, attachment metadata, or body snippets.

## Privacy And Storage Rules

- Cache storage must be outside the repo and outside package artifacts.
- Default root: `~/Library/Caches/AgenticAssets/apple-mail-mcp/metadata-index`.
- Runtime reads must be opt-in.
- Cache rows need a TTL and provenance, including source tool, capture time, mailbox scope, mailbox count where known, and resume or watermark metadata.
- Cache files must be refreshable and deletable by an explicit future command.
- No private body text, full headers, or attachment contents should be committed, packaged, or printed in reports.

## Hydration Rules

Bulk metadata rows may answer only metadata discovery questions. They must not answer:

- recipient queries
- header queries
- thread queries
- attachment queries
- body queries

Those require `coverage_tier="exact_hydrated"` plus the specific hydrated capability on the row.

The executable contract lives in:

- `plugin/apple_mail_mcp/metadata_index_contract.py`
- `tests/test_metadata_index_contract.py`

## Envelope Index Position

Direct Envelope Index reads remain a separate research lane. They may be useful for fast metadata-only queries, but they introduce Full Disk Access, schema drift, WAL consistency, and fallback behavior risks. This branch keeps direct Envelope Index work out of runtime code.

## Performance Measurement Boundary

Actual header and attachment-count cost measurement needs a privacy-safe live read-only protocol before it can be treated as proof. The current branch adds the contract and unit tests only. A future measurement pass should record p50 and p95 for:

- metadata-only export rows
- header hydration by exact id
- attachment-count hydration by exact id
- batch exact-id cache hydration at 50, 51, and 120 ids

No live Mail reads were performed for this spike.

An offline CI fixture gate now exists for ID-first hot paths:

- `tests/test_perf_budget.py`
- `tests/fixtures/perf_budget/id_first_baseline.json`
- `tests/fixtures/perf_budget/id_first_current.json`

These fixtures are synthetic and marked `live_mail=false`. They prove the p50/p95 budget format and regression assertions, not live Mail performance.

A privacy-safe measurement helper now exists:

- `tools/measure_metadata_hydration.py`
- `tests/test_measure_metadata_hydration.py`

The helper requires `--confirm-read-only-live-mail`, exact numeric ids, and emits only timing and aggregate count fields. It does not print raw ids or Mail content.

## Next Actions

1. Review the contract and decide whether this cache policy is acceptable.
2. Run `tools/measure_metadata_hydration.py` with approved dummy or selected exact ids before extending exporters.
3. Only after review, implement Phase 4b integration behind opt-in runtime flags.
4. Keep cache misses bounded: fall back only to bounded AppleScript paths or explicit `full_inbox_export`.
