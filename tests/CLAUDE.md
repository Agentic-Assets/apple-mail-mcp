# tests/ — pytest suite

Mocked unit tests for the Apple Mail MCP server. Discover the current count with `.venv/bin/pytest tests/ --collect-only -q` (do not hardcode totals in docs). CI runs on Ubuntu with no Mail.app — every test mocks AppleScript or tests pure Python.

New tests and perf gates: delegate to a **`shell`** or **`generalPurpose`** subagent; parent runs full suite after merge. See root [`CLAUDE.md`](../CLAUDE.md) § Agent orchestration.

```bash
.venv/bin/pytest tests/
.venv/bin/pytest tests/test_cli.py -q
```

Dev venv: root `.venv/` (editable install). See root [`CLAUDE.md`](../CLAUDE.md).

## conftest.py — validate_account_name

Autouse fixture `_pass_through_known_test_accounts` patches `validate_account_name` in `core` and every tool module. `account='Work'` passes without real Mail; `account='Missing'` returns structured `account_not_found`. Most tool tests depend on this.

## Mock patterns

- **AppleScript capture** — patch `subprocess.run` with `side_effect` reading script from `kwargs["input"]`. Templates: `test_modernization_3_1_5.py` (`_ScriptCapture`), `test_mail_search_tools.py`, `test_compose_tools.py`.
- **Pure helpers** — `test_bulk_helpers.py`: `escape_applescript`, filters, mailbox refs (no subprocess mock).
- **Registry / CLI** — `test_read_only_registry.py`, `test_cli.py`, `test_cli_perf.py` (perf thresholds, `--include-analysis`, profiles; no live Mail).
- **Wrapper surface** — `test_wrapper_surface.py`: mocks `check_wrapper_surface.py` help parsing (no generated wrapper required).
- **Infra** — `test_orphan_watcher.py` (injectable seams); `test_validate_manifests.py`.

## Test files

**40 test modules** on disk; discover via `pytest tests/ --collect-only -q`.

**Core suites by domain:**

- **Inbox tools**: `test_inbox_tools`, `test_inbox_typed_kwargs`, `test_get_inbox_overview_json`
- **Search**: `test_mail_search_tools`, `test_search_escaping`, `test_no_unbounded_whose`
- **Compose**: `test_compose_tools`, `test_compose_security`, `test_compose_none_handling`
- **Analytics**: `test_get_statistics_json`, `test_analytics_resource_safety`
- **Registry/CLI**: `test_read_only_registry`, `test_cli`, `test_cli_perf`
- **Contracts & hardening**: `test_bounded_scan_contract`, `test_contracts_*.py` (inbox, search, smart_inbox), `test_tier*_hardening_*.py`
- **Phase fixes**: `test_phase_a_fixes`, `test_phase_2_scan_hardening`, `test_phase_*_regression`
- **Infrastructure**: `test_orphan_watcher`, `test_validate_manifests`, `test_wrapper_surface`, `test_bulk_helpers`
- **Scale/regression**: `test_scalability_24k`, `test_gmail_unread_crash_regression`
- **Property-based (under `tests/property/`)**: `test_escape_applescript_properties`, `test_validate_account_name_properties`

**v3.2.0 contract suite** (capability-token + unbounded-scan refusal — keep green before any release): `test_bounded_scan_contract`, `test_no_unbounded_whose`, `test_full_inbox_export`.

## CI vs live Mail

`.github/workflows/ci.yml`: `validate_manifests.sh` + `pytest tests/ -q`. Live verification: [`docs/AGENT_LIVE_TESTING.md`](../docs/AGENT_LIVE_TESTING.md). Local hook: [`tools/pre-commit-validate.sh`](../tools/pre-commit-validate.sh).

## Related

[`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md) · [`tools/CLAUDE.md`](../tools/CLAUDE.md) · [`plugin/apple_mail_mcp/`](../plugin/apple_mail_mcp/)
