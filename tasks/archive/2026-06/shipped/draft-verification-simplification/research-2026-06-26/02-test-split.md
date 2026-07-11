Do later.

Create `tests/test_draft_verification.py`, but do it after Recommendation 1 extracts the draft verification helpers, or as the second commit in the same follow-up branch. Moving the public `verify_draft` tests can happen before helper extraction, but the highest-value new tests are direct helper-contract tests for `_build_verify_draft_payload`, and those should not lock in `compose.py` as the long-term helper home.

## Evidence

- The recommendation sequence puts helper extraction before the test split: `tasks/draft-verification-simplification-recommendations-2026-06-26.md:13` to `tasks/draft-verification-simplification-recommendations-2026-06-26.md:22`.
- Recommendation 2 explicitly proposes `tests/test_draft_verification.py` and names the candidate moved surfaces: `tasks/draft-verification-simplification-recommendations-2026-06-26.md:61` to `tasks/draft-verification-simplification-recommendations-2026-06-26.md:90`.
- Tests are expected to mock AppleScript and keep CI independent of Mail.app: `tests/CLAUDE.md:1` to `tests/CLAUDE.md:18`, and CI runs manifest validation plus pytest: `tests/CLAUDE.md:47`.
- Compose owns six tools, including `verify_draft`, and guidance identifies `verify_draft` as read-only while `reply_to_email` verifies saved drafts before success: `plugin/apple_mail_mcp/tools/CLAUDE.md:3` to `plugin/apple_mail_mcp/tools/CLAUDE.md:16`, `plugin/apple_mail_mcp/tools/CLAUDE.md:60` to `plugin/apple_mail_mcp/tools/CLAUDE.md:68`.
- The registry already covers `verify_draft` as read-only and keeps `reply_to_email` and `manage_drafts` in the destructive group: `tests/test_read_only_registry.py:17` to `tests/test_read_only_registry.py:56`, with annotation coverage at `tests/test_read_only_registry.py:81` to `tests/test_read_only_registry.py:108`.
- Manifest or Codex smoke tests only need attention as verification gates, not as moved tests. The Codex runtime smoke expects `reply_to_email`, `compose_email`, `manage_drafts`, `list_accounts`, and `get_inbox_overview`, not `verify_draft`: `tests/test_validate_manifests.py:571` to `tests/test_validate_manifests.py:592`.

## Move To `tests/test_draft_verification.py`

Move these exact existing tests first:

- `tests/test_compose_tools.py:1814` `test_verify_draft_returns_snapshot_json_with_expectation_warnings`
- `tests/test_compose_tools.py:1856` `test_verify_draft_rejects_non_numeric_draft_id`
- `tests/test_compose_tools.py:1863` `test_verify_draft_recipient_expectation_requires_exact_address`

These are direct `verify_draft` public-tool tests. They currently sit under `ManageDraftsListTests`, but they do not test `manage_drafts` list behavior.

Move these reply draft verification tests if the new file is allowed to include reply-verification integration tests that still call `compose_tools.reply_to_email`:

- `tests/test_compose_tools.py:908` `test_reply_draft_success_outputs_artifact_id_for_exact_verification`
- `tests/test_compose_tools.py:937` `test_reply_draft_success_outputs_attachment_and_signature_verification`
- `tests/test_compose_tools.py:1019` `test_reply_draft_success_runs_bounded_saved_draft_verifier`
- `tests/test_compose_tools.py:1049` `test_reply_draft_verifier_falls_back_when_exact_id_is_not_yet_resolvable`
- `tests/test_compose_tools.py:1076` `test_reply_signature_verification_only_runs_for_resolved_signature`
- `tests/test_compose_tools.py:1101` `test_reply_draft_success_reports_structured_artifact_error_when_body_missing`
- `tests/test_compose_tools.py:1127` `test_reply_draft_verifier_rejects_body_after_quoted_original`
- `tests/test_compose_tools.py:1149` `test_reply_draft_reports_structured_error_when_body_saved_after_quote`
- `tests/test_compose_tools.py:1175` `test_reply_draft_success_reports_error_when_saved_draft_not_verified`
- `tests/test_compose_tools.py:1224` `test_reply_open_success_outputs_verification_status`

These all assert verifier status, artifact ids, bounded saved-draft verification, body-missing or body-after-quote structured errors, attachment verification status, or signature verification status. A class name like `ReplyDraftVerificationTests` would make the file read as a verification suite rather than a general reply suite.

## Keep In `tests/test_compose_tools.py`

Keep compose-mode, native-reply script-generation, sender, signature-order, and manage-drafts action tests in the compose file:

- Native reply body and quote construction: `tests/test_compose_tools.py:844`, `tests/test_compose_tools.py:885`, `tests/test_compose_tools.py:977`.
- Open-mode compose behavior that saves before review: `tests/test_compose_tools.py:1196`.
- Reply recipient mode and native Mail reply selection: `tests/test_compose_tools.py:1288`, `tests/test_compose_tools.py:1320`.
- Reply signature insertion order and `include_signature=False` body insertion: `tests/test_compose_tools.py:1350`, `tests/test_compose_tools.py:1385`.
- Reply sender override and invalid sender guardrails: `tests/test_compose_tools.py:1445`, `tests/test_compose_tools.py:1471`.
- `manage_drafts` create, send, open, delete, and exact id action behavior: `tests/test_compose_tools.py:1627` to `tests/test_compose_tools.py:1810`.
- `manage_drafts` list and find bounded script behavior: `tests/test_compose_tools.py:1881` to `tests/test_compose_tools.py:1987`.

The `manage_drafts(action="find")` header scan is related to reply draft discovery, but it is still a `manage_drafts` action script contract. Keep it in compose until Recommendation 3 extracts manage-drafts builders.

## Fixture And Import Implications

- A new test module can import `from apple_mail_mcp.tools import compose as compose_tools`, just like `tests/test_compose_tools.py:10` to `tests/test_compose_tools.py:11`.
- The autouse account fixture will still patch `apple_mail_mcp.tools.compose.validate_account_name`, so no new fixture is required for `account="Work"`: `tests/conftest.py:6` to `tests/conftest.py:24`.
- Keep imports at module level. The project test skill says module-level imports are required, and `tests/*` currently gets only unused-import and fixture-redefinition lint ignores: `pyproject.toml:97` to `pyproject.toml:103`.
- Move or duplicate the small `_saved_reply_draft_output` helper from `tests/test_compose_tools.py:40` to `tests/test_compose_tools.py:58` into the new file. Do not create a shared helper module just for one 19-line factory unless another test file needs it.
- Do not move `_main_reply_script` with the verification tests unless a moved test still needs to find the main reply script. Most recommended moved tests inspect verifier scripts or result payloads directly.
- If the reply error tests move, `json` may no longer be needed in `tests/test_compose_tools.py:4`; clean that import in the implementation PR. `tempfile` and `Path` remain used by rich draft and attachment tests.

## Helper Extraction Order

Helper extraction should happen first for direct helper tests. Today `_parse_expected_attachments`, `_split_csv_addresses`, `_csv_contains_all`, `_normalize_attachment_rows`, and `_build_verify_draft_payload` all live inside `compose.py`: `plugin/apple_mail_mcp/tools/compose.py:1461` to `plugin/apple_mail_mcp/tools/compose.py:1588`. Testing them directly from a new file before extraction would bake in `compose.py` as the helper boundary.

After Recommendation 1, add direct tests against the extracted private module while leaving the MCP registration in `compose.py`. The public `verify_draft` function is registered at `plugin/apple_mail_mcp/tools/compose.py:1591` to `plugin/apple_mail_mcp/tools/compose.py:1604`, and the package imports `compose` for registration at `plugin/apple_mail_mcp/__init__.py:13` to `plugin/apple_mail_mcp/__init__.py:21`, so a private helper module should not affect tool count or manifests.

## Risks And Mitigations

- Risk: over-moving reply tests makes `test_compose_tools.py` stop protecting native reply script generation. Mitigation: keep script construction tests in compose and move only verifier status, artifact, and structured-error tests.
- Risk: helper import churn if tests are split before helper extraction. Mitigation: move only existing public-tool tests now, or wait until helper extraction and then add direct helper tests against the extracted module.
- Risk: duplicate test helper drift. Mitigation: keep `_saved_reply_draft_output` local to the new file and do not add a shared test helper unless reuse expands.
- Risk: registry or manifest assumptions drift after moving files. Mitigation: run registry and manifest tests even though no tool registration should change.
- Risk: hidden edits from other worktree users. Mitigation: before implementation, check `git status --short`; this worktree already has modified `apple-mail-plugin.zip`, `plugin/apple_mail_mcp/tools/compose.py`, and `tests/test_compose_tools.py`, plus the recommendation file is untracked.

## Verification Commands

Focused split gate:

```bash
.venv/bin/ruff check tests/test_draft_verification.py tests/test_compose_tools.py
.venv/bin/ruff format --check tests/test_draft_verification.py tests/test_compose_tools.py
.venv/bin/pytest tests/test_draft_verification.py tests/test_compose_tools.py -q
```

Contract guardrails:

```bash
.venv/bin/pytest tests/test_read_only_registry.py tests/test_validate_manifests.py -q
bash tools/validate_manifests.sh
```

Before shipping a PR that also extracts helpers from production code:

```bash
.venv/bin/ruff check plugin/apple_mail_mcp/tools/compose.py plugin/apple_mail_mcp/tools/draft_verification.py tests/test_draft_verification.py tests/test_compose_tools.py
.venv/bin/ruff format --check plugin/apple_mail_mcp/tools/compose.py plugin/apple_mail_mcp/tools/draft_verification.py tests/test_draft_verification.py tests/test_compose_tools.py
.venv/bin/mypy --strict plugin/apple_mail_mcp/
.venv/bin/pytest tests/ -q
```

## Coverage Gaps Worth Adding

- Direct `_build_verify_draft_payload` tests for each warning path: body missing, subject mismatch, `to_mismatch`, `cc_mismatch`, expected attachments missing, signature missing, signature unexpected, quoted original missing, and quoted original unexpected.
- Attachment parser edge cases: path-like expected attachment values should compare by basename, empty comma segments should be ignored, and malformed `name::size` rows should produce `size=None`.
- `verify_draft` failure outputs for `NOT_FOUND`, `ERROR|||...`, unexpected verifier output, and `AppleScriptTimeout`.
- Multi-recipient exact matching beyond the current substring guard at `tests/test_compose_tools.py:1863` to `tests/test_compose_tools.py:1879`.
- A direct test that `expected_attachments` accepts both comma strings and `list[str]`.
