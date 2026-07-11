# Draft Verification Simplification Recommendations

Date: 2026-06-26
Status: proposed
Context: follow-up after implementing `verify_draft`, `reply_to_email` draft verification metadata, `manage_drafts(action="find")`, 29-tool manifest sync, artifact rebuilds, and a first code-simplifier pass on `compose.py`.

Research update: six focused subagent research passes were completed on 2026-06-26. Each report evaluates one recommendation with line-level evidence, implementation guidance, risks, and verification commands.

Research reports:

- [01-helper-module-extraction.md](draft-verification-simplification-research-2026-06-26/01-helper-module-extraction.md)
- [02-test-split.md](draft-verification-simplification-research-2026-06-26/02-test-split.md)
- [03-manage-drafts-builders.md](draft-verification-simplification-research-2026-06-26/03-manage-drafts-builders.md)
- [04-applescript-snippets.md](draft-verification-simplification-research-2026-06-26/04-applescript-snippets.md)
- [05-persisted-drafts-live-smoke.md](draft-verification-simplification-research-2026-06-26/05-persisted-drafts-live-smoke.md)
- [06-active-doc-tool-count-validation.md](draft-verification-simplification-research-2026-06-26/06-active-doc-tool-count-validation.md)

## Why this note exists

The draft verification work made the plugin materially safer for agent-driven Apple Mail drafting, but it also added meaningful weight to `plugin/apple_mail_mcp/tools/compose.py` and `tests/test_compose_tools.py`. The next improvements should preserve the public tool surface and behavior while making the implementation easier to audit, test, and extend.

The goal is not a broad redesign. The goal is to reduce local complexity around draft verification, make live verification easier to repeat, and prevent future doc or manifest drift.

## Research-Backed Decision Matrix

| Recommendation | Verdict | Why |
| --- | --- | --- |
| 1. Extract draft verification helpers | Do now | Pure Python payload helpers can move without changing tool registration, read-only registry behavior, manifests, or artifacts. |
| 2. Move verification tests | Do later | Highest-value direct helper tests depend on Recommendation 1. Move public `verify_draft` tests after the helper boundary exists. |
| 3. Extract `manage_drafts` builders | Do now, staged | Extract `list` and `find` first. Defer mutating branches until the read-only builder extraction is green. |
| 4. Centralize AppleScript snippets | Do now, narrow | Centralize small handlers and single-message blocks. Do not introduce a template system or use recipient helpers in bulk search paths. |
| 5. Add persisted-Drafts live smoke | Do now | Use a CLI-only maintainer command with explicit `--account`, bounded polling, exact-id verification, and exact-id cleanup. |
| 6. Extend active-doc tool-count validation | Do now | Add an allowlisted active-doc check to `tools/validate_manifests.py`, not a separate script. |

## Recommended Sequence

1. Extract draft verification helpers into a private module.
2. Add direct helper tests and then move the public verification-focused tests into a narrower test file.
3. Extract `manage_drafts` list and find script builders only.
4. Centralize small AppleScript snippets used by exact or bounded single-message paths.
5. Add active-doc tool-count validation to the manifest validator.
6. Add a CLI-only persisted-Drafts live smoke command.
7. Revisit mutating `manage_drafts` builders only after the first staged refactors are green.

This order keeps risk low: first isolate pure Python helpers and tests, then simplify AppleScript builders, then add stronger gates.

## Recommendation 1: Extract Draft Verification Helpers

Create a private helper module, likely:

```text
plugin/apple_mail_mcp/tools/draft_verification.py
```

Keep the public MCP tool registration in `compose.py` unless there is a deliberate tool-module reorganization. That avoids manifest and registration churn while still moving most of the verification logic out of the compose tool body.

Move or consider moving:

- `_build_verify_draft_payload`
- `_normalize_attachment_rows`
- `_parse_expected_attachments`
- `_split_csv_addresses`
- `_csv_contains_all`
- expectation warning construction for body, subject, recipients, attachments, signatures, and quoted-original checks

Benefits:

- Shrinks `compose.py`.
- Makes the JSON contract easier to review independently.
- Keeps public `verify_draft` behavior stable.
- Gives tests a smaller target for pure-Python expectation logic.

Research update:

- Verdict: Do now.
- Report: [01-helper-module-extraction.md](draft-verification-simplification-research-2026-06-26/01-helper-module-extraction.md).
- Move only pure helpers in the first pass. Keep `verify_draft`, its AppleScript, `@mcp.tool`, and `@inject_preferences` in `compose.py`.
- Import direction should be one way: `compose.py` imports `draft_verification.py`; the helper module imports only standard library symbols.
- No manifest, skill, artifact, MCPB, or `plugin/apple_mail_mcp/__init__.py` changes should be needed if the helper module has no `@mcp.tool`.
- Do not move saved reply verifier helpers in this pass. `_verify_saved_reply_draft` is reply-specific and has direct tests.

Implementation notes:

- Do not move `@mcp.tool` registration in the first pass.
- Avoid changing response shape.
- Add direct unit tests for the pure helper payload builder before editing AppleScript again.

Still needs research before implementation:

- Whether `verify_draft` should eventually move to its own `drafts.py` tool module, which would require import, docs, and manifest count checks.
- Whether saved reply verification should eventually move with draft verification helpers, because the reply verifier is reply-specific and currently has direct private-function tests.

## Recommendation 2: Move Verification Tests Out Of `test_compose_tools.py`

Create:

```text
tests/test_draft_verification.py
```

Move the new tests for:

- `verify_draft`
- `_build_verify_draft_payload`
- exact recipient matching
- attachment expectation warnings
- signature expectation warnings
- quoted-original expectation warnings
- reply verification success metadata, if it can be cleanly isolated

Keep compose-only tests in `test_compose_tools.py`, especially tests that assert generated reply AppleScript behavior.

Benefits:

- Makes failures easier to interpret.
- Keeps `test_compose_tools.py` from becoming a catch-all.
- Encourages more precise tests around the new JSON contract.

Research update:

- Verdict: Do later, after Recommendation 1 or as the second commit in the same branch.
- Report: [02-test-split.md](draft-verification-simplification-research-2026-06-26/02-test-split.md).
- Move existing public `verify_draft` tests first:
  - `test_verify_draft_returns_snapshot_json_with_expectation_warnings`
  - `test_verify_draft_rejects_non_numeric_draft_id`
  - `test_verify_draft_recipient_expectation_requires_exact_address`
- Add direct helper tests after the helper module exists.
- Consider moving reply verification status tests into `ReplyDraftVerificationTests`, but keep native reply script-generation, sender override, signature-order, and compose-mode tests in `test_compose_tools.py`.
- Keep `manage_drafts(action="find")` tests with compose until Recommendation 3 extracts builders.

Still needs research before implementation:

- How much of `_verify_saved_reply_draft` belongs in compose tests because it is reply-specific versus draft-verification tests because it now shares status metadata with `verify_draft`.

## Recommendation 3: Extract `manage_drafts` Script Builders

`manage_drafts` now covers many distinct behaviors:

- `list`
- `find`
- `create`
- `send`
- `open`
- `delete`
- `cleanup_empty`

Keep the public function signature unchanged, but move action-specific AppleScript generation into private helpers:

```python
_build_manage_drafts_list_script(...)
_build_manage_drafts_find_script(...)
_build_manage_drafts_create_script(...)
_build_manage_drafts_send_open_delete_script(...)
_build_manage_drafts_cleanup_empty_script(...)
```

Benefits:

- Makes `manage_drafts` a dispatcher plus validation layer.
- Reduces merge-conflict risk when changing one action.
- Makes it easier to snapshot-test `list` and `find` script generation.

Research update:

- Verdict: Do now, but extract only `list` and `find` first.
- Report: [03-manage-drafts-builders.md](draft-verification-simplification-research-2026-06-26/03-manage-drafts-builders.md).
- First helper set:
  - `_build_manage_drafts_subject_filter_script(subject_contains: str | None, *, indent: int) -> str`
  - `_build_manage_drafts_list_script(...) -> str`
  - `_build_manage_drafts_find_script(...) -> str`
- Builders should receive already-clamped `list_limit`, not raw `limit`.
- Leave `_server.READ_ONLY` and `_server.DRAFT_SAFE` send gates in the public dispatcher.
- Leave exact `draft_id` validation before AppleScript execution.
- Defer `create`, `send`, `open`, `delete`, and `cleanup_empty` extraction. Extract `send`, `open`, and `delete` together later only after `_draft_action_lookup()` is module-scoped and directly tested.

Implementation notes:

- Preserve all current text output.
- Preserve all safety gates, especially draft-safe send blocking and exact `draft_id` preference.
- Keep the `limit` cap and no unbounded `every message` behavior intact.

Resolved by research:

- Do not split `create`, `send`, `open`, `delete`, and `cleanup_empty` in the first pass. Extract only `list` and `find`, then revisit mutating branches after the first extraction is tested.

## Recommendation 4: Centralize Repeated AppleScript Snippets

There are repeated AppleScript fragments across `verify_draft`, `_verify_saved_reply_draft`, `manage_drafts(action="find")`, and existing search helpers.

Candidate snippet builders:

- `sanitize_field`
- `textOffset`
- Drafts bounded newest-window setup
- `In-Reply-To` / `References` header parsing
- recipient address collection

Benefits:

- Reduces drift across related tools.
- Makes Mail dictionary assumptions easier to audit.
- Helps future fixes land in one place.

Research update:

- Verdict: Do now, narrowly.
- Report: [04-applescript-snippets.md](draft-verification-simplification-research-2026-06-26/04-applescript-snippets.md).
- Add small generic helpers in `plugin/apple_mail_mcp/applescript_snippets.py`:
  - `sanitize_field_handler(include_attachment_row_delimiter: bool = False, name: str = "sanitize_field") -> str`
  - `text_offset_handler(name: str = "textOffset") -> str`
  - `thread_headers_block(message_var: str, in_reply_to_var: str, references_var: str, sanitize_fn: str | None = "sanitize_field") -> str`
  - `recipient_addresses_block(...) -> str` for exact or bounded single-message contexts only
- Keep Drafts head-window builders compose-local because they are compose-domain behavior.
- Do not add a template framework, DSL, class-based renderer, or Jinja dependency.
- Do not use shared recipient collection in bulk `search_emails`; recipient reads can hang large remote mailboxes.

Implementation notes:

- Prefer small string-builder helpers over a general AppleScript templating system.
- Keep generated AppleScript readable in tests.
- Add assertions that generated scripts still avoid unbounded Drafts scans.

Resolved by research:

- Generic snippets shared across tool modules belong in `plugin/apple_mail_mcp/applescript_snippets.py`.
- Drafts head-window snippets should stay compose-local.
- Exact-message search helpers can reuse generic header and recipient blocks, but bulk `search_emails` must not use shared recipient collection.

## Recommendation 5: Add A Persisted-Drafts Live Smoke

The live test exposed an important Mail behavior: an outgoing-message id returned immediately after standalone draft creation may not always be the same id that resolves later through Drafts mailbox lookup.

Add a maintainer-only live smoke command that verifies the persisted path:

1. Create a harmless standalone draft with a unique subject.
2. Poll `manage_drafts(action="list", subject_contains=..., limit=...)` until the persisted Drafts id appears.
3. Run `verify_draft(draft_id=..., expected_subject=..., expected_body_contains=...)`.
4. Delete only that exact persisted Drafts id.
5. Fail if cleanup cannot confirm the exact id.

Benefits:

- Tests the operational path agents actually need.
- Catches transient outgoing-message-id assumptions.
- Gives maintainers a repeatable live proof instead of ad hoc one-off commands.

Research update:

- Verdict: Do now.
- Report: [05-persisted-drafts-live-smoke.md](draft-verification-simplification-research-2026-06-26/05-persisted-drafts-live-smoke.md).
- Add a CLI-only maintainer command, not a new MCP tool:

```bash
.venv/bin/apple-mail draft-verify-smoke --account "ACCOUNT NAME" --cleanup --json
```

- Require `--account`. Do not fall back to `DEFAULT_MAIL_ACCOUNT` for a command that creates and deletes drafts.
- Require either `--cleanup` or `--leave-draft` before creating anything.
- Treat the immediate `Draft ID` from creation as provisional. Poll `manage_drafts(action="list", subject_contains=..., limit=...)`, parse persisted `Id: N`, and verify the persisted id with `verify_draft`.
- Delete only with exact persisted `draft_id`; never delete by subject.
- Confirm cleanup by calling `verify_draft` again and expecting not found.
- Do not add this command to `quick-check`, `release`, or the existing `live` dev-check tier. Consider a future explicit `live-draft` tier only if maintainers want it.

Possible command shape:

```bash
.venv/bin/apple-mail draft-verify-smoke --account iCloud --cleanup
```

Resolved by research:

- There should be no default account for this smoke. Require `--account`.
- The smoke should be CLI-only in `plugin/apple_mail_mcp/cli.py`, not a new MCP tool and not a `tools/` shell wrapper in the first pass.
- Keep it out of `quick-check`, `release`, and the existing `live` dev-check tier.

## Recommendation 6: Extend Active-Doc Tool-Count Validation

The 29-tool change left stale 28-tool claims in active docs until the validator-style review caught them. Add a narrow active-doc validator for current guidance files.

Candidate allowlist:

- `AGENTS.md`
- `CLAUDE.md`
- `README.md`
- `docs/CLAUDE.md`
- `tools/CLAUDE.md`
- `plugin/apple_mail_mcp/CLAUDE.md`
- `plugin/apple_mail_mcp/tools/CLAUDE.md`
- `plugin/docs/CLAUDE.md`
- `.claude-plugin/CLAUDE.md`
- `apple-mail-mcpb/CLAUDE.md`
- `apple-mail-mcpb/build-mcpb.sh`

Exclude:

- historical task docs
- archived plans
- changelog-like records
- prior incident reports

Benefits:

- Prevents active guidance drift after future tool additions.
- Keeps historical records untouched.
- Makes plugin-validator output more complete.

Research update:

- Verdict: Do now.
- Report: [06-active-doc-tool-count-validation.md](draft-verification-simplification-research-2026-06-26/06-active-doc-tool-count-validation.md).
- Implement in `tools/validate_manifests.py`, not as a separate docs validator.
- Keep `tools/validate_manifests.sh` as the wrapper that delegates to Python.
- Use an explicit allowlist, never a recursive scan over `docs/`, `tasks/`, or the repo root.
- For required active docs, require at least one tool-count claim and require every captured count to equal the registered `@mcp.tool` count.
- For scan-only active policy docs, fail stale numeric claims but allow no claim.
- Add a module-count table sum check for `plugin/apple_mail_mcp/tools/CLAUDE.md`.

Resolved by research:

- Put this in `tools/validate_manifests.py`.
- Check exact active-doc count claims first. Do not require new tool names in README or MCPB prose in the first pass.

## Lower-Priority Ideas

### Add A Small Draft Verification Result Type

Introduce a typed internal dataclass for the `verify_draft` payload before JSON serialization. This could improve mypy coverage and reduce accidental response drift.

Research first:

- Whether the project wants typed response builders for MCP tools, or whether simple dictionaries match existing style better.

### Make Attachment Verification More Explicit

Current draft verification reports names and sizes from `mail attachments`. Future work could add:

- `expected_attachment_count`
- per-attachment `matched_expected`
- size nonzero checks when expected filenames are present

Research first:

- How reliably Mail exposes `mail attachments` on outgoing Drafts across iCloud, Gmail, Exchange, and local accounts.

### Header Lookup Output As JSON

`manage_drafts(action="find")` currently returns text, consistent with `manage_drafts`. A future `find_draft_for_message` read-only tool could return structured JSON.

Research first:

- Whether a new tool is worth the additional manifest surface, or whether `manage_drafts(action="find")` is sufficient.

## Suggested Verification For Any Follow-Up PR

Minimum focused gate:

```bash
.venv/bin/ruff check plugin/apple_mail_mcp/tools/compose.py tests/test_compose_tools.py
.venv/bin/ruff format --check plugin/apple_mail_mcp/tools/compose.py tests/test_compose_tools.py
.venv/bin/mypy --strict plugin/apple_mail_mcp/
.venv/bin/pytest tests/test_compose_tools.py tests/test_read_only_registry.py tests/test_validate_manifests.py tests/test_no_unbounded_whose.py -q
bash tools/validate_manifests.sh
```

If public tools, manifests, skills, or artifacts change:

```bash
bash tools/dev-check.sh release
bash tools/validate-codex-plugin.sh
```

If draft behavior changes:

```bash
.venv/bin/apple-mail quick-check --json
```

Then run a live persisted-Drafts verification smoke once that command exists.
