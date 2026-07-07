# Manifest release hardening closeout (2026-07-07)

**Branch**: `codex/verify-plugin-marketplace-parity`
**Base**: `main`
**State**: local verification green, draft PR pending
**Version**: `3.9.1`

## Goal

Make the plugin, marketplace, MCP registry, and MCPB version checks explicit and durable, then rebuild and verify release artifacts so `apple-mail-plugin.zip`, `apple-mail.plugin`, and `apple-mail-mcp-v3.9.1.mcpb` agree.

## What shipped

- Centralized public version validation in `tools/validators/validate_manifests.py` through `_public_version_checks()` and `_check_public_versions()`.
- Added direct coverage in `tests/infra/test_validate_manifests.py` for the complete version surface list:
  - `plugin/.claude-plugin/plugin.json`
  - `plugin/.codex-plugin/plugin.json`
  - `.claude-plugin/marketplace.json` `plugins[0].version`
  - `server.json` top-level and `packages[0].version`
  - `apple-mail-mcpb/manifest.json`
- Added a regression fixture that fails when the Codex plugin manifest version drifts from `pyproject.toml`.
- Updated `tools/expected_test_count.txt` from `1023` to `1025`.
- Rebuilt the tracked `apple-mail-plugin.zip`; release build also regenerated local ignored artifacts `apple-mail.plugin` and `apple-mail-mcp-v3.9.1.mcpb`.

## Verification

- `.venv/bin/pytest tests/infra/test_validate_manifests.py -q`: 46 passed.
- `python3 tools/validators/check_module_line_budget.py`: OK.
- `PYTEST_ADDOPTS='' .venv/bin/pytest --collect-only tests`: 1025 tests collected.
- `.venv/bin/ruff check tools/validators/validate_manifests.py tests/infra/test_validate_manifests.py`: passed.
- `.venv/bin/ruff format --check tools/validators/validate_manifests.py tests/infra/test_validate_manifests.py`: passed.
- `bash tools/gates/validate_manifests.sh`: OK, version `3.9.1`, 31 tools.
- `bash tools/gates/dev-check.sh release`: passed twice after the validator change. The final run rebuilt artifacts, passed strict plugin validation, passed MCPB unpack and validate, ran full pytest, and confirmed test-count parity.
- Manual parity check after rebuild: `cmp -s apple-mail-plugin.zip apple-mail.plugin` returned `0`.

## Decisions

- Kept `pyproject.toml` as the source of truth and made every other public release surface an explicit validator entry.
- Left `.claude-plugin/marketplace.json` `metadata.version` untouched at `1.0.0`; it is marketplace metadata, not the plugin release version.
- Did not commit `.codex/environments/environment.toml`; it is local generated environment configuration and remains untracked.
- Subagent and plugin-validator experts were not used because the current host tool policy only permits spawning subagents when the user explicitly asks for subagents. The local release validators and full release gate covered the plugin-validation path.

## Left to operator

- Review and merge the draft PR when ready. Do not merge without the required Cayman approval phrase.
