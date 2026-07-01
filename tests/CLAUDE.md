# tests/ — pytest suite

Mocked unit tests for the Apple Mail MCP server. The collected-test count is single-sourced in [`../tools/expected_test_count.txt`](../tools/expected_test_count.txt) (the dev-check/release gate fails on drift and prints the new number); recount with `PYTEST_ADDOPTS='' .venv/bin/pytest tests/ --collect-only`. CI runs on Ubuntu with no Mail.app; every test mocks AppleScript or tests pure Python.

New tests and perf gates: delegate to a **`shell`** or **`generalPurpose`** subagent when available and permitted; parent runs the relevant suite after merge. See root [`CLAUDE.md`](../CLAUDE.md), Agent orchestration section.

```bash
.venv/bin/pytest tests/
.venv/bin/pytest tests/cli/test_cli.py -q
```

Dev venv: root `.venv/` (editable install). See root [`CLAUDE.md`](../CLAUDE.md).

## conftest.py — validate_account_name

Autouse fixture `_pass_through_known_test_accounts` patches `validate_account_name` in `core` and every tool module. `account='Work'` passes without real Mail; `account='Missing'` returns structured `account_not_found`. Most tool tests depend on this.

## Mock patterns

- **AppleScript capture** — patch `subprocess.run` with `side_effect` reading script from `kwargs["input"]`. Templates: `cross_cutting/test_modernization_3_1_5.py` (`_ScriptCapture`), `search/test_mail_search_tools.py`, `compose/test_compose_tools.py`.
- **Pure helpers** — `core/test_bulk_helpers.py`: `escape_applescript`, filters, mailbox refs (no subprocess mock).
- **Registry / CLI** — `core/test_read_only_registry.py`, `cli/test_cli.py`, `cli/test_cli_perf.py` (perf thresholds, `--include-analysis`, profiles; no live Mail).
- **Wrapper surface** — `infra/test_wrapper_surface.py`: mocks `check_wrapper_surface.py` help parsing (no generated wrapper required).
- **Infra** — `core/test_orphan_watcher.py` (injectable seams); `infra/test_validate_manifests.py`; `infra/test_tasks_layout.py` (tasks/ bucket layout); `infra/test_module_line_budget.py` (600 LOC budget warn + baseline regression).

## Test files

**57 test modules** on disk across 10 domain subfolders plus `tests/property/` (each subfolder has `__init__.py`). `conftest.py`, `fixtures/`, and `property/` stay at `tests/` root. Discover with `find tests -maxdepth 3 -type f \( -name 'test_*.py' -o -name '*_test.py' \)`.

**Modules by subfolder:**

- **`cli/`**: `test_cli`, `test_cli_perf`, `test_cli_characterization`
- **`inbox/`**: `test_inbox_tools`, `test_inbox_typed_kwargs`, `test_inbox_pure_helpers`, `test_get_inbox_overview_json`, `test_contracts_inbox_tools`, `test_gmail_unread_crash_regression`
- **`compose/`**: `test_compose_tools`, `test_compose_security`, `test_compose_none_handling`, `test_draft_verification_helpers`
- **`manage/`**: `test_manage_create_mailbox`, `test_manage_bulk_action_errors`
- **`search/`**: `test_mail_search_tools`, `test_search_escaping`, `test_search_split_characterization`, `test_contracts_search_tools`
- **`analytics/`**: `test_get_statistics_json`, `test_analytics_resource_safety`, `test_full_inbox_export`, `test_dashboard_id_first`
- **`smart_inbox/`**: `test_smart_inbox_json`, `test_smart_inbox_top_senders_domain`, `test_contracts_smart_inbox`
- **`core/`**: `test_bulk_helpers`, `test_core_validators`, `test_core_fetch_replied_ids`, `test_core_helpers_characterization`, `test_bounded_scan_contract`, `test_no_unbounded_whose`, `test_applescript_snippets`, `test_read_only_registry`, `test_orphan_watcher`, `test_metadata_index_contract`
- **`infra/`**: `test_validate_manifests`, `test_module_line_budget`, `test_tasks_layout`, `test_wrapper_surface`, `test_compare_perf_results`, `test_perf_budget`, `test_packaged_skill_paths`, `test_inspect_envelope_index_schema`, `test_measure_metadata_hydration`
- **`cross_cutting/`**: `test_phase_a_fixes`, `test_phase_2_scan_hardening`, `test_tier1_hardening_regression`, `test_tier3_hardening`, `test_modernization_3_1_5`, `test_scalability_24k`, `test_replied_detection`, `test_applescript_script_idioms`, `test_applescript_builders_compile`, `test_id_first_guidance`
- **`property/`** (property-based): `test_escape_applescript_properties`, `test_validate_account_name_properties`

## v3.2.0 contract suite (capability-token + unbounded-scan refusal — keep green before any release)

`test_bounded_scan_contract`, `test_no_unbounded_whose`, `test_full_inbox_export`.

## Module line budget

**600 LOC** soft target on `plugin/apple_mail_mcp/` and `tools/` (test modules are not budgeted). Enforced by:

- `tests/infra/test_module_line_budget.py` — warn on oversize production modules; fail on baseline regression
- `tools/validators/check_module_line_budget.py` — standalone report (also invoked by `dev-check.sh` and CI)
- Baseline: `tests/fixtures/module_line_budget/baseline.json` (empty `modules` after v3.9.1 decomposition; regression gate still blocks growth if entries are reintroduced)

```bash
python3 tools/validators/check_module_line_budget.py
python3 tools/validators/check_module_line_budget.py --write-baseline tests/fixtures/module_line_budget/baseline.json
```

Full rules: [`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md) § Module line budget.

## CI vs live Mail

`.github/workflows/ci.yml`: `validate_manifests.sh` + `pytest tests/ -q`. Live verification: [`docs/AGENT_LIVE_TESTING.md`](../docs/AGENT_LIVE_TESTING.md). Local hook: [`tools/gates/pre-commit-validate.sh`](../tools/gates/pre-commit-validate.sh).

## Related

[`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md) · [`tools/CLAUDE.md`](../tools/CLAUDE.md) · [`plugin/apple_mail_mcp/`](../plugin/apple_mail_mcp/)
