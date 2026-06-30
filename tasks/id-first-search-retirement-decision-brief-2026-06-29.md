# ID-First Search Retirement Decision Brief, 2026-06-29

## Purpose

This brief turns the remaining unchecked product decisions from the ID-first search retirement plan into explicit choices. The implementation branch has completed the v3.x hardening work and verification audit. These decisions determine whether to ship the current branch as a compatibility hardening release, start a breaking v4 cleanup, or approve live read-only measurements for metadata-index work.

Related files:

- `tasks/id-first-search-retirement-recommendations-2026-06-29.md`
- `tasks/id-first-search-retirement-todo-2026-06-29.md`
- `tasks/id-first-search-retirement-completion-audit-2026-06-29.md`
- `tasks/metadata-index-feasibility-spike-2026-06-30.md`

## Current Branch State

Branch: `codex/id-first-search-retirement-implementation`

Proven state:

- Legacy keyword target selectors on action tools now return structured deprecation errors before AppleScript runs.
- Exact-id action and batch APIs are implemented and tested.
- Discovery tools gained safer exact filters and warnings.
- Metadata-index runtime integration has not started.
- Live Mail reads, live Draft creation, and email sending were not performed.

## Decision 1: v3.x Legacy Selector Behavior

Question: should v3.x reject legacy selector usage immediately at runtime, or warn for one compatibility release?

Options:

1. **Reject now in v3.x, current branch behavior.**
   - Keeps schemas compatible but returns `TARGET_SELECTOR_DEPRECATED` before AppleScript runs.
   - Best protects users from keyword-selected mutations.
   - May break agents relying on subject or sender mutation shortcuts.
2. **Warn for one v3.x release, then reject.**
   - Softer migration.
   - Leaves risky target selection behavior alive longer.
3. **Keep legacy behavior indefinitely.**
   - Lowest compatibility risk.
   - Conflicts with the ID-first goal.

Recommended decision: **Option 1.** Ship schema-compatible runtime rejection in v3.x. It is the clearest safety improvement and the branch already has tests proving no AppleScript call occurs for deprecated target selectors.

## Decision 2: `allow_filter_scan=True`

Question: should `allow_filter_scan=True` remain as the bulk-action escape hatch, or move to separate `bulk_*` tools?

Options:

1. **Keep `allow_filter_scan=True` in v3.x only.**
   - Preserves a controlled compatibility path for bulk operations.
   - Must stay off by default and documented as advanced.
2. **Move bulk filtering to separate future `bulk_*` tools.**
   - Makes ordinary action tools purely exact-id.
   - Requires new product design and another implementation pass.
3. **Remove all filter-scan bulk paths immediately.**
   - Strongest safety posture.
   - Likely too disruptive for archive cleanup workflows.

Recommended decision: **Option 1 for v3.x, Option 2 for v4.** Keep the explicit escape hatch only for compatibility, then design separate bulk tools with dry-run, cap, and review contracts before v4 removal.

## Decision 3: `mailbox="All"` Opt-In

Question: should `mailbox="All"` require an explicit opt-in flag?

Options:

1. **Require explicit opt-in for `mailbox="All"` in discovery tools.**
   - Reduces accidental whole-account scans.
   - Requires schema and CLI updates.
2. **Keep warnings only.**
   - Current branch already warns on whole-account discovery patterns.
   - Lower disruption.
3. **Remove `mailbox="All"`.**
   - Too restrictive for legitimate discovery and audit workflows.

Recommended decision: **Option 2 for the current v3.x branch, Option 1 for a follow-up.** The current branch already adds warnings and ID-first guidance. A separate change can add an `allow_all_mailboxes` or `include_all_mailboxes` opt-in with focused tests.

## Decision 4: Fuzzy `sender`

Question: should fuzzy `sender` remain permanently as discovery, or become deprecated after `sender_exact` and `sender_domain` exist?

Options:

1. **Keep fuzzy `sender` as discovery-only with warnings.**
   - Useful for user-facing exploratory search.
   - Must never authorize mutation.
2. **Deprecate fuzzy `sender` after exact/domain fields ship.**
   - Cleaner long-term contract.
   - Removes a convenient human query style.
3. **Remove fuzzy `sender` now.**
   - Too disruptive for discovery.

Recommended decision: **Option 1.** Keep fuzzy sender as a discovery-only convenience, but keep warnings for sender-only and broad mailbox searches. Action tools should continue rejecting sender selectors unless exact message ids are provided.

## Decision 5: Release Boundary

Question: should the current branch ship as a v3.x hardening release, or wait for breaking v4 schema removal?

Options:

1. **Ship current branch as v3.x hardening.**
   - Delivers safety improvements immediately.
   - Keeps compatibility parameters in schemas.
2. **Wait for v4 schema removal.**
   - Cleaner final API.
   - Delays safety fixes and broadens review surface.
3. **Split into smaller PRs.**
   - Easier review per topic.
   - More branch coordination and artifact churn.

Recommended decision: **Option 1.** Review and ship the current branch as the compatibility hardening tranche. Start v4 removal only after this behavior has review and user-facing evidence.

## Decision 6: Live Read-Only Measurements

Question: should we run approved live read-only measurements for metadata-index feasibility?

Options:

1. **Run exact-id hydration measurements with approved dummy or selected ids.**
   - Uses `tools/measure_metadata_hydration.py`.
   - Requires account, mailbox, and exact numeric ids.
   - Prints aggregate timings and counts only.
2. **Run schema-only Envelope Index probe.**
   - Uses `tools/inspect_envelope_index_schema.py`.
   - Opens local SQLite index read-only, sets `PRAGMA query_only`, and prints schema metadata only.
3. **Do not run live probes yet.**
   - Keeps current proof fully mocked/local.
   - Leaves metadata-index feasibility incomplete.

Recommended decision: **Option 3 before branch review, then Option 1 after review if Phase 4b remains interesting.** The current branch is already reviewable without live proof. Hydration measurements matter before extending exporters or integrating a cache.

## Recommended Maintainer Action

Approve the current branch for review as a v3.x hardening tranche with these defaults:

- Reject legacy action target selectors at runtime while keeping v3.x schemas compatible.
- Keep `allow_filter_scan=True` only as an explicit compatibility escape hatch.
- Keep `mailbox="All"` warning-only for this branch.
- Keep fuzzy `sender` as discovery-only with warnings.
- Defer v4 schema removal and metadata-index runtime integration to follow-up branches.
- Do not run live read-only measurement until the current branch has maintainer review.

## Follow-Up Branches After Decision

1. `codex/id-first-all-mailbox-opt-in`
   - Add explicit opt-in for `mailbox="All"` discovery.
   - Update CLI, docs, and tests.
2. `codex/id-first-v4-schema-removal`
   - Remove legacy selector params from tool signatures.
   - Update schema snapshots, docs, manifests, and packaged skills.
3. `codex/metadata-index-live-measurement`
   - Run approved read-only helper commands with dummy or selected exact ids.
   - Record aggregate-only results in a new task report.
4. `codex/metadata-index-integration`
   - Start only after Phase 4a review and measurement evidence.
   - Keep cache opt-in, outside repo artifacts, with hit, miss, stale, and invalidation tests.
