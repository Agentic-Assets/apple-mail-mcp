# Active Pointer — apple-mail-mcp

**Branch:** `feat/codex-claude-plugin-setup` (created 2026-06-07 from current `main` checkout).

**Active workstream:** Codex + Claude plugin setup hardening. Goal: keep existing Claude Code/Cowork/MCPB paths working while adding a real Codex plugin surface (`plugin/.codex-plugin/plugin.json`, `plugin/.mcp.json`, `.agents/plugins/marketplace.json`), easy Codex install commands, and validator coverage.

**Plan:** [`tasks/codex-claude-plugin-setup-2026-06-07/phase-plan.md`](codex-claude-plugin-setup-2026-06-07/phase-plan.md)

**Next action:** commit and push.

**Latest verification (2026-06-07):** `bash tools/dev-check.sh release` passed at version `3.6.1`; `.venv/bin/pytest tests/test_validate_manifests.py -q` passed with 32 tests; `bash tools/validate-codex-plugin.sh` installed `apple-mail@apple-mail-mcp` version `3.6.1` in a temporary `CODEX_HOME`; `cmp apple-mail-plugin.zip apple-mail.plugin` passed; collected test count is 798 tests + 30 subtests.

**Blockers / caveats:** `mcpb` CLI is not installed locally, so the release script skipped optional `mcpb unpack + validate`; structural MCPB checks still passed through `tools/validate_manifests.py`. No Mail.app live smoke is required because runtime tool code did not change.
