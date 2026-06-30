# ID-First Search Retirement Completion Audit, 2026-06-29

## Scope

This audit covers the `codex/id-first-search-retirement-implementation` branch and the plan files:

- `tasks/id-first-search-retirement-recommendations-2026-06-29.md`
- `tasks/id-first-search-retirement-todo-2026-06-29.md`
- `tasks/metadata-index-feasibility-spike-2026-06-30.md`

The branch advances the ID-first contract for action tools, discovery tools, batch exact-id APIs, metadata-index feasibility, and local verification helpers. It does not remove v3.x compatibility parameters, enable a runtime metadata index, inspect live Mail, create drafts, or send email.

## Proven Completed

- Action-tool guidance and packaged examples now steer mutation workflows through reviewed exact ids instead of keyword target selection.
- v3.x action surfaces keep legacy selector parameters for compatibility but return structured `TARGET_SELECTOR_DEPRECATED` errors before AppleScript runs.
- `search_emails` supports exact sender/domain and exact Internet Message-ID discovery.
- `get_email_thread` supports JSON, explicit mailbox sets, header-first matching, and no-preview handle collection.
- Batch exact-id APIs exist for email fetch, draft verification, attachment listing, and export.
- Forward draft save/open paths surface saved Draft ids and use exact Drafts verification where available.
- The metadata-index contract defines opt-in cache policy, freshness/provenance rules, coverage tiers, and strict hydration gates.
- Offline p50/p95 fixture tests exist for ID-first hot paths.
- Read-only helper tools exist for approved future measurement:
  - `tools/measure_metadata_hydration.py`
  - `tools/inspect_envelope_index_schema.py`

## Proof Commands

Latest explicit Phase 6 bundle:

```bash
.venv/bin/pytest \
  tests/test_phase_2_scan_hardening.py \
  tests/test_compose_tools.py \
  tests/test_compose_none_handling.py \
  tests/test_analytics_resource_safety.py \
  tests/test_mail_search_tools.py \
  tests/test_contracts_search_tools.py \
  tests/test_cli.py \
  tests/test_cli_perf.py \
  tests/test_dashboard_id_first.py \
  tests/test_id_first_guidance.py \
  tests/test_no_unbounded_whose.py \
  tests/test_bounded_scan_contract.py \
  tests/test_read_only_registry.py \
  tests/test_metadata_index_contract.py \
  tests/test_measure_metadata_hydration.py \
  tests/test_inspect_envelope_index_schema.py \
  tests/test_perf_budget.py \
  -q
```

Result: passed.

Latest branch-level gates run during this workstream:

```bash
bash tools/dev-check.sh default
bash tools/dev-check.sh release
python3 tools/validate_manifests.py
APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh
git diff --check
```

Result: passed. The release gate had only the existing generated-wrapper-on-PATH skip.

Focused helper gates run during this workstream:

```bash
.venv/bin/ruff format --check tools/measure_metadata_hydration.py tests/test_measure_metadata_hydration.py
.venv/bin/ruff check tools/measure_metadata_hydration.py tests/test_measure_metadata_hydration.py
.venv/bin/mypy --strict tools/measure_metadata_hydration.py
.venv/bin/ruff format --check tools/inspect_envelope_index_schema.py tests/test_inspect_envelope_index_schema.py
.venv/bin/ruff check tools/inspect_envelope_index_schema.py tests/test_inspect_envelope_index_schema.py
.venv/bin/mypy --strict tools/inspect_envelope_index_schema.py
```

Result: passed.

Safety probes:

```bash
python3 tools/measure_metadata_hydration.py --account "Dummy Account" --message-ids 101
python3 tools/inspect_envelope_index_schema.py
```

Result: both refused without explicit live-read confirmation flags and did not access Mail.

Package and sensitive-data scans were run over changed source, tests, docs, skills, UI, and package artifacts during the workstream. No private content or secret material was found. Archive scans confirmed the new helper scripts are not packaged in `apple-mail-plugin.zip` or `apple-mail-mcp-v3.7.1.mcpb`.

## Still Open

These items require maintainer decision or approved live read-only proof, so they are intentionally not marked complete:

- Decide whether v3.x should keep runtime deprecation errors or warn for one compatibility release.
- Decide whether `allow_filter_scan=True` remains as a bulk-campaign escape hatch or moves to separate `bulk_*` tools.
- Decide whether `mailbox="All"` requires an explicit opt-in flag.
- Decide whether fuzzy `sender` remains permanently as discovery or becomes deprecated after `sender_exact` and `sender_domain`.
- Decide release boundary for v3.x compatibility deprecation versus v4 schema removal.
- Run approved read-only metadata hydration measurement with known dummy or selected exact ids.
- Optionally run approved schema-only Envelope Index probe.
- Review Phase 4a before any Phase 4b runtime metadata-index integration.
- Implement v4 schema removal only after compatibility evidence and maintainer approval.

## Current Recommendation

Treat the branch as ready for maintainer review for the v3.x ID-first hardening tranche. Do not begin Phase 4b runtime metadata-index integration or v4 schema removal until the product decisions and approved live read-only measurements above are resolved.
