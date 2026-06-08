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

## Native Reply Follow-Up - 2026-06-08

- Reviewed the interrupted native-reply changes after Jason was stopped. The working tree was narrow and non-conflicted: `compose.py`, compose/bounded-scan tests, task notes, README/skill docs, and rebuilt plugin artifact.
- Changed `reply_to_email` from synthetic `make new outgoing message` reply bodies to Mail's native `reply foundMessage with opening window` path. The tool now lets Mail generate the quoted prior conversation automatically by default.
- Changed native reply-all to use Mail's native `reply to all` option instead of rebuilding recipient fan-out manually.
- Captured `replySubject` immediately after creating the native reply so `mode="draft"` does not read the subject from an invalid object after saving/closing the compose window.
- Changed `mode="draft"` to `save replyMessage` and `close front window saving yes`.
- Corrected Drafts lifecycle lookup after live Mail showed freshly created native reply drafts at positions 3 and 4 of a 975-message Drafts mailbox. `manage_drafts(action="list")` now reads only the bounded first Drafts window. Targeted send/open/delete lookup checks bounded head and bounded tail windows, never a full Drafts scan.
- Updated email-drafting skill and README wording so future agents know `reply_to_email` uses Mail-native quoted prior messages by default.
- Replaced the stale native-reply follow-up issue with a resolution/evidence note.

Verification:

- `.venv/bin/python -m pytest tests/test_compose_tools.py tests/test_phase_2_scan_hardening.py tests/test_scalability_24k.py tests/test_bounded_scan_contract.py tests/test_compose_security.py tests/test_compose_none_handling.py tests/test_tier3_hardening.py -q` passed.
- Live bounded Drafts check: `manage_drafts(action="list", subject_contains="Your monthly AI Companion Basic limit has been reset")` found the existing smoke drafts through the current working-tree code.
- Live native reply smoke: `reply_to_email(message_id="80833", mode="draft")` returned success and created draft id `80855`; bounded first-20 Drafts inspection confirmed `nativeQuote=yes` for Mail's generated quote header (`Zoom <billing@zoom.us> wrote:`).
- Cleanup: deleted only the three uniquely marked smoke drafts (`80855`, `80840`, `80838`) from the bounded first-20 Drafts window and verified zero remaining smoke-marker matches; `manage_drafts` then found zero matching Zoom reset drafts.
- `.venv/bin/python -m pytest tests/test_no_unbounded_whose.py tests/test_phase_2_scan_hardening.py -q` passed, covering the no-unbounded-folder-scan guardrails.
- `osacompile` syntax checks passed for `reply foundMessage with opening window` and `reply foundMessage with opening window and reply to all` without creating drafts.
- `bash tools/dev-check.sh release` passed: ruff, ruff format check, mypy strict, artifact build/validation, Claude plugin validation, full pytest suite. Local `mcpb` CLI remains unavailable, so optional unpack smoke was skipped.
- `bash tools/validate-codex-plugin.sh` passed: fresh temp Codex marketplace install, installed-plugin venv bootstrap, registered MCP launch, and MCP `list_tools` showing all 28 tools including `reply_to_email`, `compose_email`, `manage_drafts`, `list_accounts`, and `get_inbox_overview`.
