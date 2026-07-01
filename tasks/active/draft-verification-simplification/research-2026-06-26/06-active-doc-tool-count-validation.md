Do now.

## Verdict

Extend the existing manifest validator now. The stale 28-tool issue was not a runtime bug, but it affected active operator and install guidance, so it belongs in the same release gate that already computes the registered tool count and checks manifest descriptions.

Prefer `tools/validate_manifests.py`, not a separate docs validator. Add a small active-doc helper inside the Python validator and let `tools/validate_manifests.sh` keep delegating to it.

## Evidence

Recommendation 6 says the 29-tool change left stale 28-tool claims in active docs and proposes a narrow allowlist while excluding historical task docs, archived plans, changelog-like records, and incident reports: `tasks/draft-verification-simplification-recommendations-2026-06-26.md:188`, `tasks/draft-verification-simplification-recommendations-2026-06-26.md:190`, `tasks/draft-verification-simplification-recommendations-2026-06-26.md:192`, `tasks/draft-verification-simplification-recommendations-2026-06-26.md:206`.

The current Python validator already owns the authoritative count path. It extracts registered tool names from `@mcp.tool` decorators in `plugin/apple_mail_mcp/tools/*.py`: `tools/validate_manifests.py:98`. It has a reusable count-claim checker for `N tools` and `N MCP tools`: `tools/validate_manifests.py:120`. It applies that checker to Claude plugin, Claude marketplace, Codex plugin, and MCPB manifest descriptions: `tools/validate_manifests.py:944`, `tools/validate_manifests.py:951`, `tools/validate_manifests.py:486`, `tools/validate_manifests.py:961`. It also enforces MCPB `tools[]` count and name parity: `tools/validate_manifests.py:964`.

The Bash wrapper is already only the CI entry plus compatibility layer. `tools/CLAUDE.md` says CI calls `validate_manifests.sh` and that `validate_manifests.py` is covered by `tests/test_validate_manifests.py`: `tools/CLAUDE.md:9`, `tools/CLAUDE.md:10`. The Bash script delegates full Python validation at the end: `tools/validate_manifests.sh:195`, `tools/validate_manifests.sh:198`.

Current active docs contain duplicated numeric tool-count claims:

- `AGENTS.md:3` and `CLAUDE.md:3` advertise `29 tools`.
- `README.md:21`, `README.md:48`, and `README.md:610` advertise `29 tools`.
- `docs/CLAUDE.md:29` says skills teach how to call the `29 MCP tools`.
- `plugin/apple_mail_mcp/CLAUDE.md:27` gives `29 tools` plus the per-module split.
- `plugin/apple_mail_mcp/tools/CLAUDE.md:2` gives `29 tools`, and `plugin/apple_mail_mcp/tools/CLAUDE.md:8` through `plugin/apple_mail_mcp/tools/CLAUDE.md:13` give the module counts that sum to 29.
- `plugin/docs/CLAUDE.md:33` says the package is source of truth for all `29 MCP tools`.
- `.claude-plugin/CLAUDE.md:43` says the install exposes `29 tools`.
- `apple-mail-mcpb/CLAUDE.md:25` says the manifest description must claim the correct count, currently `29`.
- `apple-mail-mcpb/build-mcpb.sh:117` writes a generated MCPB README with `29 tools`.
- `tools/validate_manifests.py:794` also embeds `29 tools` in the expected generated MCPB README used for artifact freshness.

Marketplace and package manifests are already covered by the existing validator:

- `plugin/.claude-plugin/plugin.json:3`
- `plugin/.codex-plugin/plugin.json:4`
- `.claude-plugin/marketplace.json:17`
- `apple-mail-mcpb/manifest.json:5`

There are historical or report-like docs with old or conditional counts that should not be touched by this validator. Examples include `docs/findings-allow-full-scan-audit-2026-06-09.md:235` and `docs/live-testing-reports/LIVE_FIELD_REPORT_2026-06-04.md:183`.

## Placement

Put the check in `tools/validate_manifests.py` as `_check_active_doc_tool_count_claims(actual_count, errors)`. Call it from `main()` after `actual_count` is computed and after manifest description checks, before artifact freshness. This keeps one release gate for all tool-count drift and avoids another script that agents might forget.

Do not add new logic to `tools/validate_manifests.sh` beyond its current Python delegation. It already runs the Python validator, and duplicating doc parsing in Bash would create another drift surface.

## Active-Doc Allowlist

Use an explicit allowlist. Do not scan directories recursively.

Required numeric claim entries:

- `AGENTS.md`
- `CLAUDE.md`
- `README.md`
- `docs/CLAUDE.md`
- `plugin/apple_mail_mcp/CLAUDE.md`
- `plugin/apple_mail_mcp/tools/CLAUDE.md`
- `plugin/docs/CLAUDE.md`
- `.claude-plugin/CLAUDE.md`
- `apple-mail-mcpb/CLAUDE.md`
- `apple-mail-mcpb/build-mcpb.sh`
- `tools/validate_manifests.py`, limited to the generated MCPB README string

Scan-only active policy entries:

- `tools/CLAUDE.md`
- `docs/CLAUDE-conventions.md`

I would also add `plugin/skills/email-management/README.md` only if the team wants skill README files covered by manifest validation. It currently contains an active `29 tools` claim, but skill prose may be better handled with `plugin-dev:skill-reviewer` because the skill tree has different authoring rules.

## Exact Matching Policy

For every allowlisted file, read UTF-8 text and check each line independently. Report errors as `path:line: tool-count claim X, registry has Y`.

Use these claim matchers:

1. `(?i)\b(\d+)\s+(?:MCP\s+)?tools?\b`
2. `(?i)\btool-count claims\b.*?\(\*\*(\d+)\*\*\)`
3. `(?i)\bcorrect count\b.*?\(\*\*(\d+)\*\*\)`

Every captured integer from those patterns must equal `actual_count`. For files in the required set, at least one configured claim must be present. For scan-only files, stale numeric claims fail, but absence of a numeric claim is allowed.

For `plugin/apple_mail_mcp/tools/CLAUDE.md`, add one extra structured check: parse the module-count table rows at `plugin/apple_mail_mcp/tools/CLAUDE.md:8` through `plugin/apple_mail_mcp/tools/CLAUDE.md:13`, sum the `#` column, and require the sum to equal `actual_count`. This catches a future case where the headline says 30 tools but the module map still sums to 29, or the reverse.

Do not validate the general test count (`822 tests`) in this check. It is a separate drift problem and should not be coupled to tool additions.

## Avoiding Historical Docs

Never use `rg` over `docs/`, `tasks/`, or the repo root as the validator source of truth. The validator should open only the allowlisted active files above.

This avoids historical task docs, archived plans, changelog-like records, and incident reports by construction. It also avoids rewriting old evidence files that are supposed to preserve the count that was true at the time.

If a new active guidance file starts carrying tool-count claims, add it to the allowlist in the same PR that adds the claim. Do not promote historical files into the allowlist unless they become live operator guidance.

## Tests To Add

Add focused tests to `tests/test_validate_manifests.py`:

- `test_active_doc_tool_count_claims_pass_on_current_repo`: calls the helper with current `actual_count` and expects no errors.
- `test_active_doc_tool_count_claims_rejects_stale_required_doc`: temp root with `AGENTS.md` claiming `28 tools`, actual count 29, expects `AGENTS.md:line` in the error.
- `test_active_doc_tool_count_claims_requires_required_claim`: temp root with an allowlisted required file present but missing any count claim, expects a missing-claim error.
- `test_active_doc_tool_count_claims_allows_scan_only_without_claim`: temp root with `docs/CLAUDE-conventions.md` containing policy text but no numeric count, expects no error.
- `test_active_doc_tool_count_claims_ignores_historical_task_docs`: temp root with `tasks/old-plan.md` claiming `28 tools`, actual count 29, expects no error.
- `test_active_doc_tool_count_claims_checks_tools_module_sum`: temp root with `plugin/apple_mail_mcp/tools/CLAUDE.md` headline at 29 but table rows summing to 28, expects a module-sum error.
- `test_active_doc_tool_count_claims_reports_generated_mcpb_readme_source`: temp root with stale `apple-mail-mcpb/build-mcpb.sh` or stale `_generated_mcpb_readme` text, expects the exact file and line.

No new test file is necessary. These belong with the manifest validator tests because the helper is part of that release gate.

## Verification Commands

Focused implementation check:

```bash
.venv/bin/python -m pytest tests/test_validate_manifests.py -q
bash tools/validate_manifests.sh
```

Release-gate check after the validator change:

```bash
bash tools/dev-check.sh manifest
```

Optional manual stale-count sanity check against the allowlist:

```bash
rg -n "\b(28|29|30)\s+(MCP\s+)?tools?\b|tool-count claims|correct count" \
  AGENTS.md CLAUDE.md README.md docs/CLAUDE.md docs/CLAUDE-conventions.md \
  tools/CLAUDE.md plugin/apple_mail_mcp/CLAUDE.md \
  plugin/apple_mail_mcp/tools/CLAUDE.md plugin/docs/CLAUDE.md \
  .claude-plugin/CLAUDE.md apple-mail-mcpb/CLAUDE.md \
  apple-mail-mcpb/build-mcpb.sh tools/validate_manifests.py
```

Current baseline observed during research:

- `python3 tools/validate_manifests.py` passed with `version=3.7.1, tools=29`.
- `.venv/bin/python -m pytest tests/test_validate_manifests.py -q` passed, 37 tests.
- `python3 -m pytest tests/test_validate_manifests.py -q` failed because system Python could not import `apple_mail_mcp`; use the repo `.venv` command above.
