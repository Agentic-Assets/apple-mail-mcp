# Reply artifact reporting closeout (2026-07-07)

**Branch**: `codex/agentic-946-preserve-reply-draft-id`
**Base**: `origin/main` at `b25d76e`
**Commits**: not committed when written
**Verification**: `bash tools/gates/dev-check.sh` passed after artifact rebuild and test-count update
**State**: local feature branch, main untouched

## Goal

Address the GitHub issue updated on 2026-07-07, #54, where `REPLY_WINDOW_FOCUS_FAILED` could claim no draft was saved even though Mail left signature-only Drafts artifacts. While investigating, preserve the exact reply Drafts id contract from the related Linear item AGENTIC-946 and older GitHub #32 style reports.

## What Shipped

- `plugin/apple_mail_mcp/tools/compose/reply_scripts.py`: `GUARD_ABORT` now returns the reply subject and detail on separate lines, so Python can run a bounded same-subject Drafts verifier after focus failure.
- `plugin/apple_mail_mcp/tools/compose/reply.py`: focus-failure errors now run `_verify_saved_reply_draft` before returning and include `draft_artifact_status`, `suspected_draft_id`, and exact cleanup guidance when a body-missing artifact is found.
- `plugin/apple_mail_mcp/tools/compose/verification.py`: reply success JSON now promotes a verifier-found Drafts artifact id into `draft_id` when Mail did not expose the id directly, while preserving `captured_draft_id`, `draft_id_source`, and `exact_id_verified=false`.
- `plugin/apple_mail_mcp/tools/search/emails.py`: missing-account JSON no longer crashes if the secondary available-accounts probe times out. It preserves the documented `account_not_found` shape with an empty `available_accounts` list.
- `tests/compose/test_compose_tools.py`: added regressions for verified fallback draft ids and focus-failure artifact reporting.
- `tools/expected_test_count.txt`: updated from 1021 to 1023 for the two added tests.
- `apple-mail-plugin.zip`, `apple-mail.plugin`, and `apple-mail-mcp-v3.9.1.mcpb`: rebuilt by `bash tools/gates/build-artifacts.sh`.

## Verification

- `rg` against `/System/Applications/Mail.app/Contents/Resources/Mail.sdef` confirmed the Mail `reply` command returns an `outgoing message` and supports optional `opening window` and `reply to all` booleans.
- `.venv/bin/pytest tests/compose/test_compose_tools.py -k 'reply_draft_success_json or guard_abort'`: 5 passed.
- `.venv/bin/pytest tests/compose/test_compose_tools.py tests/cross_cutting/test_applescript_builders_compile.py tests/cross_cutting/test_applescript_script_idioms.py`: 238 passed, 4 subtests passed.
- `.venv/bin/ruff check ...`: passed on touched Python files.
- `.venv/bin/ruff format --check ...`: passed on touched Python files.
- `.venv/bin/mypy --strict ...`: passed on touched source files.
- `python3 tools/validators/check_module_line_budget.py`: passed.
- `bash tools/gates/build-artifacts.sh`: passed, manifest validation OK, MCPB unpack and validate OK, Claude plugin strict validation OK.
- `bash tools/gates/dev-check.sh`: passed, including 1023 collected tests matching `tools/expected_test_count.txt`.

## Decisions

- The #54 fix reuses `_verify_saved_reply_draft` rather than adding a new Drafts scanner. That keeps artifact detection in one bounded verifier and preserves existing subject/body/quote checks.
- `draft_id` now means "best cleanup-safe Drafts artifact id in a successful reply JSON payload." `captured_draft_id` records the narrower Mail-returned id, and `draft_id_source` tells callers whether the id came from Mail or verifier fallback.
- `exact_id_verified` remains strict. It is true only when the id Mail returned was the id verified. A fallback-promoted id does not pretend Mail exposed it directly.
- The search fallback is intentionally narrow. It catches only `AppleScriptTimeout` while listing available accounts for an already-detected missing-account error, so normal account validation behavior is unchanged.

## Deferred

- No live focus-contention smoke was run. The deterministic regression proves the structured-error behavior, but an actual Mail focus race still belongs in the native-reply TO-TEST lane.
- No PR was opened when this log was written, because repo policy requires explicit PR authorization.
- Plugin-dev expert passes were not available in the exposed tool set, so local release gates are the verification source.
