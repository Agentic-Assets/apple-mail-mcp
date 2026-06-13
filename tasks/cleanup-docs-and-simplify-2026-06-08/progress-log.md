# Progress Log

## 2026-06-08

- Created branch `codex/cleanup-docs-and-simplify` from `main` after the native reply and plugin install fixes were merged and pushed.
- Removed the retired legacy slash-command surface and its guide so hosts do not surface duplicate command/skill entries.
- Added a manifest validator guard that fails if `plugin/commands/` reappears.
- Simplified `reply_to_email` saved-draft verification by passing the already-known reply body into the verifier instead of re-reading the temp body file.
- Updated README, plugin docs, skill docs, MCPB manifest text, task navigation, and historical issue notes to reflect current behavior:
  - workflow entry points are skills-only;
  - `reply_to_email` uses Mail's native reply composer and verifies saved drafts;
  - Mail may apply account default signatures when no plugin signature is forced;
  - routine scans/searches stay bounded and do not use full-folder scans.

## Verification

- `.venv/bin/python -m pytest tests/test_validate_manifests.py -q -k 'not passes_on_current_repo'` passed before artifact rebuild, confirming validator logic independent of stale bundles.
- `.venv/bin/python -m pytest tests/test_compose_tools.py::ReplyToEmailSenderOverrideTests tests/test_compose_tools.py::DefaultMailSignatureSupportTests -q` passed.
- `bash tools/build-artifacts.sh` passed, rebuilt `apple-mail-plugin.zip`, `apple-mail.plugin`, and `apple-mail-mcp-v3.6.1.mcpb`, and ran `claude plugin validate --strict`. Local `mcpb` CLI remains unavailable, so optional unpack validation was skipped.
- `.venv/bin/python -m pytest tests/test_validate_manifests.py tests/test_compose_tools.py::ReplyToEmailSenderOverrideTests tests/test_compose_tools.py::DefaultMailSignatureSupportTests -q` passed after artifact rebuild.
- `bash tools/dev-check.sh release` passed: ruff, ruff format check, mypy strict, artifact build/validation, Claude plugin validation, full pytest suite, and wrapper check skip because no generated wrapper is on PATH.
- `bash tools/validate-codex-plugin.sh` passed: direct checkout MCP smoke, temporary Codex marketplace install, installed-plugin venv bootstrap, registered MCP launch, and MCP `list_tools` showing all 28 tools including `reply_to_email`, `compose_email`, `manage_drafts`, `list_accounts`, and `get_inbox_overview`.
- `unzip -l apple-mail-plugin.zip | rg "commands|docs/commands"` returned no matches, confirming the shipped plugin artifact no longer contains the retired command surface.
- `git diff --check` passed.
