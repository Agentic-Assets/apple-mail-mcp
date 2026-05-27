# V4 Performance Consolidation Progress Log

## 2026-05-27

- Created branch `feat/v4-performance-consolidation` from `3f6d3f1`.
- Confirmed `HEAD` is `3f6d3f1`, the v3.4.0 baseline commit named in the goal.
- Confirmed `git status --short --branch` showed only the branch line before workstream docs were added.
- Delegated read-only mapping to subagents for tool surface, release/perf gates, competitor prior art, and strict lint/type baseline.
- Confirmed current registered tool count is `28`.
- Confirmed current test collection is `763` tests across `39` files.
- Read-only recon reported `.venv/bin/pytest tests/ -q -p no:cacheprovider` passed with 4 deprecation warnings.
- Read-only recon reported `bash tools/dev-check.sh manifest` passed.
- Strict gate blocker observed on untouched baseline:
  - `.venv/bin/ruff check plugin/apple_mail_mcp/` -> `593` errors, `454` fixable
  - `.venv/bin/ruff format --check plugin/apple_mail_mcp/` -> `12` files would be reformatted
  - `.venv/bin/mypy --strict plugin/apple_mail_mcp/` -> `116` errors in `9` files
- Because the goal says to stop and report if strict ruff/mypy surfaces more than 50 issues needing triage, no MCP behavior changes have been made yet.
- Bootstrapped durable task sidecars:
  - `tasks/v4-performance-consolidation-2026-05-27/phase-plan.md`
  - `tasks/v4-performance-consolidation-2026-05-27/progress-log.md`
  - `tasks/v4-performance-consolidation-2026-05-27/learnings-and-parking-lot.md`
- Updated `tasks/todo.md` and `tasks/INDEX.md` so future agents land on the v4 workstream rather than stale v3.2.1 release notes.
- User confirmed the strict lint/type errors should be treated as real cleanup work and asked to continue with the strict version.
- Ran `.venv/bin/ruff check plugin/apple_mail_mcp/ --fix` and `.venv/bin/ruff format plugin/apple_mail_mcp/`, then completed semantic strict fixes for typed decorators, AppleScript runner injection, search/inbox tuple contracts, path handling, temp-file context managers, exception chaining, and unused variables.
- Updated `pyproject.toml` so package mypy is strict by default and Ruff ignores only `E501` for embedded AppleScript/docstring literals in addition to the existing `E402` escape hatch.
- Updated `tools/dev-check.sh lint` from advisory to fatal package gate: Ruff check, Ruff format check, and `mypy --strict` for `plugin/apple_mail_mcp/`.
- Verification passed:
  - `.venv/bin/ruff check plugin/apple_mail_mcp/`
  - `.venv/bin/ruff format --check plugin/apple_mail_mcp/`
  - `.venv/bin/mypy plugin/apple_mail_mcp/`
  - `bash tools/dev-check.sh lint`
  - `.venv/bin/pytest tests/test_orphan_watcher.py tests/test_core_fetch_replied_ids.py tests/test_bounded_scan_contract.py tests/test_mail_search_tools.py tests/test_inbox_tools.py tests/test_get_inbox_overview_json.py tests/test_gmail_unread_crash_regression.py -q`
  - `.venv/bin/pytest tests/test_compose_tools.py tests/test_compose_security.py tests/test_compose_none_handling.py tests/test_manage_bulk_action_errors.py tests/test_get_statistics_json.py tests/test_smart_inbox_json.py -q`
- Full-suite follow-up initially caught two non-behavioral drift issues:
  - `tests/test_no_unbounded_whose.py` rejected a reformatted id-only `whose` line because the condition was inlined instead of assigned to the canonical `{id_condition}` variable.
  - `tests/test_validate_manifests.py` correctly reported stale `apple-mail-plugin.zip` and `apple-mail-mcp-v3.4.0.mcpb` payloads after source rewrites.
- Fixed the id-only `whose` shape in `save_email_attachment` by assigning `id_condition = build_whose_id_list(normalized_ids)` before the AppleScript f-string, preserving the bounded/id-filtered behavior the scanner allows.
- Rebuilt release artifacts with `bash tools/build-artifacts.sh`; manifest validation and `claude plugin validate --strict` passed. Optional `mcpb unpack` smoke skipped because `mcpb` is not installed on PATH.
- Additional verification passed:
  - `.venv/bin/pytest tests/test_validate_manifests.py tests/test_no_unbounded_whose.py -q`
  - `.venv/bin/pytest tests/ -q` with existing deprecation warnings for `include_read`/`unread_only` aliases
  - `bash tools/dev-check.sh release`; wrapper check skipped because no generated `apple-mail` wrapper is on PATH
- Started the Phase 0 perf-comparison harness:
  - Added `tools/compare_perf_results.py`, a pure JSON comparator for `apple-mail perf-test --json` payloads.
  - Added `tests/test_compare_perf_results.py` covering case-name matching, delta reporting, missing cases, current `ok=false`, failed current cases, null/non-numeric durations, zero-baseline handling, regression budgets, and CLI exit codes.
  - Updated `docs/AGENT_LIVE_TESTING.md` with capture/compare commands and corrected the production perf gate account to `Cayman - Agentic Assets` (`cayman@agenticassets.ai`).
  - Focused verification passed: `.venv/bin/pytest tests/test_compare_perf_results.py -q`.
- Finished Phase 0 comparator verification:
  - `.venv/bin/pytest tests/test_compare_perf_results.py tests/test_cli_perf.py -q` -> passed
  - `.venv/bin/ruff check tools/compare_perf_results.py tests/test_compare_perf_results.py` -> passed
  - `.venv/bin/ruff format --check tools/compare_perf_results.py tests/test_compare_perf_results.py` -> passed
  - `bash tools/dev-check.sh lint` -> passed
  - `bash tools/dev-check.sh release` -> passed; optional `mcpb unpack` smoke skipped because `mcpb` is not installed on PATH, and wrapper check skipped because no generated `apple-mail` wrapper is on PATH

## Next Action

Add `tests/test_perf_budget.py` with recorded fixture assertions for top-10 p50/p95 budgets, then wire the comparator into the v4 hot-path workflow once baseline/current fixture capture files exist.
