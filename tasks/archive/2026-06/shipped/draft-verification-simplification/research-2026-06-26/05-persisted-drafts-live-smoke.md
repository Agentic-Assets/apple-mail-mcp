Verdict: Do now.

Add a CLI-only maintainer smoke, not a new MCP tool. The current tool surface already has the required primitives: create a standalone draft, discover Drafts by bounded subject filtering, verify a single Drafts id, and delete by exact Drafts id. The missing piece is a safe orchestrator that proves the persisted Drafts id path after Mail has saved and indexed the draft.

## Evidence

- `tasks/draft-verification-simplification-recommendations-2026-06-26.md:160` records the live behavior to protect against: the immediate outgoing-message id can differ from the later Drafts mailbox id.
- `tasks/draft-verification-simplification-recommendations-2026-06-26.md:164` to `tasks/draft-verification-simplification-recommendations-2026-06-26.md:168` defines the intended persisted path: create, poll `manage_drafts(action="list", subject_contains=...)`, verify by exact id, delete only that id, and fail if cleanup cannot be confirmed.
- `plugin/apple_mail_mcp/cli.py:1` to `plugin/apple_mail_mcp/cli.py:4` says the repo CLI wraps the same Python tools as the MCP server, so a CLI smoke can exercise production tool logic without adding public tool or manifest surface.
- Existing CLI `drafts list` only exposes `--hide-empty` and `--json`; it does not expose `subject_contains` or `limit` even though the tool supports them (`plugin/apple_mail_mcp/cli.py:663` to `plugin/apple_mail_mcp/cli.py:690`).
- Existing CLI `draft` creates standalone drafts through `compose_email` and requires `--account`, `--to`, and `--subject` (`plugin/apple_mail_mcp/cli.py:692` to `plugin/apple_mail_mcp/cli.py:714`; `plugin/apple_mail_mcp/cli.py:1000` to `plugin/apple_mail_mcp/cli.py:1028`).
- `manage_drafts` resolves `DEFAULT_MAIL_ACCOUNT` when the account is omitted (`plugin/apple_mail_mcp/tools/compose.py:224` to `plugin/apple_mail_mcp/tools/compose.py:240`), but a write/delete smoke should require an explicit CLI `--account`.
- `manage_drafts(action="list")` already scans a bounded newest Drafts window, supports `subject_contains`, avoids date filters that would drop null-date new drafts, and emits `Id:` lines (`plugin/apple_mail_mcp/tools/compose.py:2560` to `plugin/apple_mail_mcp/tools/compose.py:2585`; `plugin/apple_mail_mcp/tools/compose.py:2629` to `plugin/apple_mail_mcp/tools/compose.py:2727`).
- `manage_drafts(action="create")` saves a new outgoing message and emits an immediate `Draft ID` when Mail exposes one (`plugin/apple_mail_mcp/tools/compose.py:2804` to `plugin/apple_mail_mcp/tools/compose.py:2887`). The smoke should record this id as provisional only.
- `verify_draft` is read-only, requires a numeric Drafts id, reads `mailbox "Drafts"` by exact id, returns JSON, and reports `draft_not_found` after deletion (`plugin/apple_mail_mcp/tools/compose.py:1591` to `plugin/apple_mail_mcp/tools/compose.py:1628`; `plugin/apple_mail_mcp/tools/compose.py:1671` to `plugin/apple_mail_mcp/tools/compose.py:1799`).
- Exact-id draft deletion is already supported and preferred over subject matching (`plugin/apple_mail_mcp/tools/compose.py:2604` to `plugin/apple_mail_mcp/tools/compose.py:2627`; `plugin/apple_mail_mcp/tools/compose.py:2976` to `plugin/apple_mail_mcp/tools/compose.py:3000`).
- Existing mocked CLI tests cover draft creation, draft cleanup dry-run behavior, and `smoke-test`, but no persisted Drafts smoke sequence (`tests/test_cli.py:100` to `tests/test_cli.py:152`; `tests/test_cli.py:208` to `tests/test_cli.py:331`; `tests/test_cli.py:389` to `tests/test_cli.py:417`).
- Current live guidance keeps `quick-check` read-only and fast (`docs/AGENT_LIVE_TESTING.md:48` to `docs/AGENT_LIVE_TESTING.md:59`; `docs/AGENT_LIVE_TESTING.md:232` to `docs/AGENT_LIVE_TESTING.md:244`), and CI remains mocked tests plus manifest validation (`docs/AGENT_LIVE_TESTING.md:294` to `docs/AGENT_LIVE_TESTING.md:317`).
- `tools/dev-check.sh live` currently runs default checks plus `.venv/bin/apple-mail quick-check --json` only (`tools/dev-check.sh:4` to `tools/dev-check.sh:10`; `tools/dev-check.sh:100` to `tools/dev-check.sh:107`).

## Recommended CLI Shape

Add `draft-verify-smoke` in `plugin/apple_mail_mcp/cli.py`.

```bash
.venv/bin/apple-mail draft-verify-smoke \
  --account "Cayman - Agentic Assets" \
  --cleanup \
  --json
```

Suggested options:

- `--account ACCOUNT`: required. Do not fall back to `DEFAULT_MAIL_ACCOUNT` or the first account for this command.
- `--cleanup`: required for the normal passing path. Reject before creating anything unless either `--cleanup` or `--leave-draft` is passed.
- `--leave-draft`: debugging escape hatch that verifies but does not delete, and prints the exact persisted id.
- `--to ADDRESS`: optional, default `apple-mail-mcp-smoke@example.invalid`.
- `--poll-timeout SECONDS`: default `45`.
- `--poll-interval SECONDS`: default `1.5`.
- `--list-limit N`: default `25`, capped at the existing Drafts cap.
- `--tool-timeout SECONDS`: default `30`.
- `--json`: same output convention as other CLI commands.

Use a generated subject and body sentinel, for example:

```text
APPLE_MAIL_MCP_DRAFT_VERIFY_SMOKE_20260626T165500Z_ab12cd34
Body sentinel: APPLE_MAIL_MCP_BODY_SENTINEL_ab12cd34
```

The implementation should call `manage_drafts(action="create")` or `compose_email(mode="draft")`, but the better first target is `manage_drafts(action="create")` because it exposes the provisional immediate `Draft ID`. The command should then ignore that id for proof, poll `manage_drafts(action="list", subject_contains=subject, limit=list_limit)`, parse `Id: N`, and verify the persisted id with `verify_draft(draft_id=N, expected_subject=subject, expected_body_contains=sentinel)`.

## Default Account Behavior

Require `--account` even though `quick-check`, `perf-test`, and `smoke-test` can fall back to `DEFAULT_MAIL_ACCOUNT` or the first configured account (`plugin/apple_mail_mcp/cli.py:181` to `plugin/apple_mail_mcp/cli.py:192`). This command creates and deletes a draft, so accidental account selection is a higher risk than a read-only battery. The underlying tools can still keep their existing default behavior for MCP use.

## Cleanup Semantics

- Never delete by subject.
- Never delete the provisional immediate id unless it is also rediscovered as the persisted Drafts id.
- Delete with `manage_drafts(action="delete", draft_id=persisted_id, timeout=tool_timeout)`.
- Confirm cleanup with `verify_draft(draft_id=persisted_id)`, expecting `found: false` or `warnings: ["draft_not_found"]`.
- If cleanup cannot be confirmed, exit non-zero and print the exact persisted id, subject, and delete command.
- If the command discovers no persisted id, do not attempt subject-based cleanup. Exit non-zero and print the unique subject for manual inspection.
- If verification fails after a persisted id is found, still run exact-id cleanup when `--cleanup` was requested, then exit non-zero with both verification and cleanup status.
- If multiple ids match the unique subject, verify candidates by exact id. If exactly one has the expected subject and body sentinel, use it. If more than one verifies, fail safe and delete none in the first implementation unless the code can prove every id is a current-run artifact.

## Timeout And Polling Design

Use one create call, then a bounded polling loop until either a verified persisted id is found or `--poll-timeout` expires.

- Each list poll should call `manage_drafts(action="list", subject_contains=subject, limit=list_limit, timeout=tool_timeout)`.
- The list poll must keep the current tool behavior: bounded newest-window scan, no date filter, and no full Drafts scan.
- Use monotonic time and sleep `--poll-interval` between failed polls.
- Parse ids from lines shaped like `Id: 12345`, then verify each candidate with `verify_draft`.
- Treat tool timeout strings as failed attempts during polling, but keep the last error in the final payload.
- Cap list limits to the same internal Drafts cap instead of adding an independent unbounded path.

## Failure Modes

Return exit code `0` only when create, persisted discovery, verification, exact-id delete, and cleanup confirmation all pass, unless `--leave-draft` was explicitly requested.

Exit non-zero for:

- Missing `--account`.
- Neither `--cleanup` nor `--leave-draft` supplied.
- Account validation failure from the tool layer.
- Draft create failure or missing required inputs.
- Poll timeout before a persisted id appears.
- Candidate id parse failure.
- `verify_draft` returns `found: false`, `error`, `subject_mismatch`, or `expected_body_missing`.
- Multiple verified candidates for the same unique subject in the first implementation.
- Exact-id delete reports not found or errors.
- Cleanup confirmation still finds the exact id.

The JSON payload should include `ok`, `account`, `subject`, `created_draft_id_provisional`, `persisted_draft_id`, `poll_attempts`, `verified`, `cleanup`, and `errors`.

## Relationship To Quick Check And Release Gates

Do not fold this into `quick-check`. `quick-check` is a fast, mostly read-only regression battery: metadata, no-hit search, and inbox (`plugin/apple_mail_mcp/cli.py:242` to `plugin/apple_mail_mcp/cli.py:336`; `plugin/apple_mail_mcp/cli.py:1187` to `plugin/apple_mail_mcp/cli.py:1197`). It is also the only live action in `tools/dev-check.sh live` today (`tools/dev-check.sh:100` to `tools/dev-check.sh:107`).

Keep release gates unchanged. `bash tools/dev-check.sh release` should not run a command that creates and deletes real Mail drafts. Instead:

1. Run mocked tests and release gates as today.
2. Run `.venv/bin/apple-mail quick-check --json` after draft-related code changes.
3. Run `draft-verify-smoke` manually on macOS when changes touch draft creation, Drafts discovery, exact-id verification, or cleanup.

If maintainers want a wrapper later, add an explicit `tools/dev-check.sh live-draft` tier rather than changing `live` or `release`.

## Tests To Add

- `tests/test_cli.py`: parser requires `--account` for `draft-verify-smoke`.
- `tests/test_cli.py`: parser rejects before calling tools when neither `--cleanup` nor `--leave-draft` is supplied.
- `tests/test_cli.py`: success sequence calls `manage_drafts(action="create")`, polls `manage_drafts(action="list", subject_contains=..., limit=...)`, calls `verify_draft` with expected subject and body sentinel, deletes by `draft_id`, and confirms deletion with `verify_draft`.
- `tests/test_cli.py`: command treats immediate `Draft ID` from create as provisional and uses the id parsed from `drafts list` for verify and delete.
- `tests/test_cli.py`: poll succeeds after one or more no-hit attempts without deleting by subject.
- `tests/test_cli.py`: poll timeout exits non-zero and never calls delete.
- `tests/test_cli.py`: verification warning or error exits non-zero but still attempts exact-id cleanup when `--cleanup` is set.
- `tests/test_cli.py`: cleanup confirmation failure exits non-zero and reports the retained exact id.
- `tests/test_cli.py`: `--leave-draft` verifies and reports the exact persisted id without deleting.
- `tests/test_cli.py` or a small helper test file: id parser handles `Id: 12345   To: ...` lines and rejects malformed ids.
- `tests/test_cli_perf.py`: no change expected unless a new dev-check tier or perf battery integration is added.
- `tests/test_compose_tools.py`: no new production tool behavior is required for the first CLI-only implementation.

## Verification Commands

Non-live:

```bash
.venv/bin/pytest tests/test_cli.py -q
.venv/bin/pytest tests/test_cli_perf.py tests/test_compose_tools.py -q
bash tools/dev-check.sh
```

Live, after implementation and only on a maintainer Mac with Mail permissions:

```bash
.venv/bin/apple-mail quick-check --json
.venv/bin/apple-mail draft-verify-smoke --account "Cayman - Agentic Assets" --cleanup --json
.venv/bin/apple-mail draft-verify-smoke --account "iCloud" --cleanup --json
```

Optional debugging:

```bash
.venv/bin/apple-mail draft-verify-smoke --account "iCloud" --leave-draft --json
.venv/bin/apple-mail drafts list --account "iCloud" --hide-empty
```

## Mail Account Behavior Needing Live Research

- Whether iCloud, Gmail, Exchange, and local accounts all surface the persisted Drafts id within the default 45 second poll window.
- Whether just-created drafts always appear in the newest Drafts window, or whether some providers need a larger `--list-limit` up to the existing cap.
- Whether a draft created with `apple-mail-mcp-smoke@example.invalid` is accepted consistently across account types.
- Whether the immediate `Draft ID` from `manage_drafts(action="create")` differs from the persisted id by provider.
- Whether exact-id delete from Drafts is immediately reflected by `verify_draft` on iCloud, Gmail, and Exchange.
- Whether provider sync can create duplicate persisted drafts for one saved outgoing message.
- Whether account-level sender aliases or signatures change the body sentinel placement when using `manage_drafts(action="create")` versus `compose_email(mode="draft")`.
