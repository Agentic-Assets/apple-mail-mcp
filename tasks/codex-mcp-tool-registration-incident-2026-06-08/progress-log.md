# Progress Log

## 2026-06-08

- Reproduced the failing boundary on Codex CLI `0.133.0`: temporary `CODEX_HOME` plugin install reported `apple-mail@apple-mail-mcp installed, enabled`, while `codex mcp get apple-mail` showed `/bin/bash` with literal `${CLAUDE_PLUGIN_ROOT}/start_mcp.sh --draft-safe`.
- Tested Codex launcher alternatives. `cwd: "."` plus `args: ["./start_mcp.sh", "--draft-safe"]` resolved to the installed plugin root; `cwd: "${CLAUDE_PLUGIN_ROOT}"` resolved to an installed-plugin path with a literal suffix and was rejected.
- Changed Codex `.mcp.json` to the working `cwd: "."` contract while keeping Claude Code on `${CLAUDE_PLUGIN_ROOT}` in `plugin/.claude-plugin/plugin.json`.
- Added `tools/mcp_tool_smoke.py`, an MCP stdio handshake that initializes the server, calls `list_tools`, and requires `reply_to_email`, `compose_email`, `manage_drafts`, `list_accounts`, and `get_inbox_overview`.
- Upgraded `tools/validate-codex-plugin.sh` from install-only to install plus runtime smoke: direct checkout launch, temporary Codex marketplace install, `codex mcp get apple-mail --json`, registered-command launch, and required-tool assertions.
- Updated manifest validation/tests to split Claude and Codex launcher contracts and reject the known-bad literal `${CLAUDE_PLUGIN_ROOT}` Codex argv shape.
- Added operator/drafting/README safety notes: if `mcp__apple-mail__*` tools are absent, do not create reply drafts with generic AppleScript, UI scripting, `osascript`, or standalone compose fallback; fix MCP registration or use the MCP-only absolute-path fallback first.
- Rebuilt tracked plugin artifacts after payload changes.

Verification:

- `.venv/bin/python tools/mcp_tool_smoke.py --command /bin/bash --arg "$PWD/plugin/start_mcp.sh" --arg=--draft-safe --cwd "$PWD" --expect-count 28 --required-tool reply_to_email --required-tool compose_email --required-tool manage_drafts --required-tool list_accounts --required-tool get_inbox_overview` passed.
- `.venv/bin/python -m pytest tests/test_validate_manifests.py -q -k 'not passes_on_current_repo'` passed: `32 passed`.
- `bash tools/validate-codex-plugin.sh` passed before and after artifact rebuild; the final run installed into a temporary `CODEX_HOME`, created a fresh installed-plugin venv, and returned `mcp_tool_smoke: OK (28 tools; required: reply_to_email, compose_email, manage_drafts, list_accounts, get_inbox_overview)`.
- `bash tools/build-artifacts.sh` passed and rebuilt `apple-mail-plugin.zip`, `apple-mail.plugin`, and `apple-mail-mcp-v3.6.1.mcpb`; local `mcpb` CLI remains unavailable, so optional unpack validation was skipped.
- `bash tools/dev-check.sh release` passed: ruff, ruff format check, mypy strict, artifact build/validation, full pytest suite, and wrapper check skip because no generated wrapper is on PATH.
