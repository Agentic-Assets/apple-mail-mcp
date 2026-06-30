# Codex and Claude Plugin Setup Progress Log

## 2026-06-07

- Created feature branch `feat/codex-claude-plugin-setup` from `main`.
- Loaded `plugin-creator`, `plugin-dev:plugin-structure`, `plugin-dev:mcp-integration`, `superpowers:writing-plans`, and repo task/finalization guidance.
- Spawned three discovery subagents before editing plugin files:
  - Corbis reference deep dive: inspected `/Users/caymanseagraves/Documents/GitHub/agentic-assets/Corbis-Plugin`; found `.agents/plugins/marketplace.json`, generated `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, `.mcp.json`, installer scripts, and verification scripts as the useful reference pattern.
  - Apple Mail current package audit: confirmed Claude Code, Cowork `.plugin`, and MCPB surfaces exist; confirmed Codex `.codex-plugin`, `.mcp.json`, and `.agents/plugins/marketplace.json` are missing.
  - Plugin-validator/skill-reviewer style pass: reproduced the current Codex install blocker in a temp `CODEX_HOME`; temp addition of `plugin/.codex-plugin/plugin.json` and `plugin/.mcp.json` made Codex install succeed.
- Local inspection confirmed:
  - `plugin/.claude-plugin/plugin.json` launches `/bin/bash ${CLAUDE_PLUGIN_ROOT}/start_mcp.sh --draft-safe`.
  - `.claude-plugin/marketplace.json` points Claude to `./plugin`.
  - `tools/validate_manifests.py` validates Claude marketplace, plugin runtime, MCPB runtime, package deps, artifacts, and tool count but has no Codex contract check yet.
  - `README.md` started with stale test-count text and was missing Codex setup commands.
  - `AGENTS.md` started with stale Codex-path guidance from a mechanical migration.

## Verification Evidence

- `bash tools/dev-check.sh manifest` passed before release rebuild after Codex manifest validation was added.
- `.venv/bin/pytest tests/test_validate_manifests.py -q` initially failed because `apple-mail-plugin.zip` was stale and did not contain `.codex-plugin/plugin.json`; this was the expected artifact drift signal.
- `bash tools/validate-codex-plugin.sh` passed and installed `apple-mail@apple-mail-mcp` in a temporary `CODEX_HOME`.
- `bash tools/dev-check.sh release` passed after adding a narrow `.gitignore` exception for `plugin/.mcp.json` and rebuilding artifacts:
  - `ruff check plugin/apple_mail_mcp/` passed.
  - `ruff format --check plugin/apple_mail_mcp/` passed.
  - `mypy --strict plugin/apple_mail_mcp/` passed.
  - `validate_manifests.sh: OK (version=3.6.0, tools=28)`.
  - `claude plugin validate --strict OK`.
  - `mcpb` CLI was not installed, so the optional `mcpb unpack + validate` smoke was skipped by the release script.
  - Full pytest passed; warnings were existing deprecation warnings around read-status aliases.
- Final `3.6.1` close-out release gate passed after the Codex install-smoke regression test and coordinated version bump:
  - `ruff check plugin/apple_mail_mcp/` passed.
  - `ruff format --check plugin/apple_mail_mcp/` passed.
  - `mypy --strict plugin/apple_mail_mcp/` passed.
  - `validate_manifests.sh: OK (version=3.6.1, tools=28)`.
  - Rebuilt `apple-mail-plugin.zip`, ignored local `apple-mail.plugin`, and ignored local `apple-mail-mcp-v3.6.1.mcpb`.
  - `apple-mail.plugin` is byte-identical to `apple-mail-plugin.zip`.
  - `claude plugin validate --strict OK`.
  - `mcpb` CLI was not installed, so the optional `mcpb unpack + validate` smoke was skipped by the release script.
  - Full pytest passed; warnings were existing deprecation warnings around read-status aliases.
- Post-release focused checks passed:
  - `.venv/bin/pytest tests/test_validate_manifests.py -q` passed with 32 tests.
  - `bash tools/dev-check.sh manifest` passed before the final close-out.
  - `bash tools/validate-codex-plugin.sh` passed and installed `apple-mail@apple-mail-mcp` version `3.6.1` in a temporary `CODEX_HOME`.
  - `git diff --check` passed.
- Latest collected unit-test count in the current tree: 798 tests + 30 subtests.

## Caveats

- This work starts from `main`; `tasks/todo.md` previously referenced an older branch (`fix/v3.6.0-compose-race-and-draft-lookup`) that is not the current checkout.
- Codex plugin CLI behavior depends on the installed Codex CLI. The validator should skip the install smoke when `codex plugin` is unavailable, but repo manifests should still validate structurally.
