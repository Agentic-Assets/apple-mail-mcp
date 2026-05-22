# Apple Mail Plugin Robustness Completion Audit — 2026-05-22

Branch: `feat/apple-mail-plugin-robustness`

Status: implementation and validation evidence gathered; completion remains blocked on unavailable named `plugin-dev:*` agents and an uncommitted/unpushed branch.

## Improvements Made

- Permanent delete safety: `manage_trash(action="delete_permanent")` now honors the default `dry_run=True` in filtered, `message_ids`, and `apply_to_all` paths.
- Search error visibility: multi-account `search_emails(..., output_format="json")` keeps the compatible `errors` account list and adds `error_details` with timeout vs non-timeout failure context.
- Release artifact validation: `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh` requires both `apple-mail-plugin.zip` and `apple-mail-mcp-v3.1.8.mcpb` to exist and match current source bytes.
- Agent guidance cleanup: stale `.claude-plugin` links, stale test counts, unsafe skill examples, incorrect `move_email` caps, and incorrect `confirm_empty` examples were corrected.
- Task hygiene: `tasks/todo.md` is now a tiny active pointer; detailed checklist content moved to `tasks/robustness-backlog-2026-05-22.md`; `tasks/INDEX.md` added.
- Distributables: `apple-mail-plugin.zip` rebuilt; local ignored `apple-mail-mcp-v3.1.8.mcpb` rebuilt.

## Validation Evidence

### Mocked and Distribution Gates

```text
$ bash tools/validate_manifests.sh
validate_manifests.sh: OK (version=3.1.8, tools=27)
```

```text
$ APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh
validate_manifests.sh: OK (version=3.1.8, tools=27)
```

```text
$ .venv/bin/pytest tests/ -q
276 passed, 29 subtests passed in 0.88s
```

```text
$ python3 tools/check_wrapper_surface.py
wrapper: /Users/cayman-mac-mini/.local/bin/apple-mail
  ok   get-email-by-id
  ok   search-emails
  ok   get-email-thread
  ok   list-inbox-emails
  ok   get-inbox-overview
wrapper surface: OK
```

```text
$ claude plugin validate ./plugin
Validating plugin manifest: /Users/cayman-mac-mini/Documents/GitHub/apple-mail-mcp/plugin/.claude-plugin/plugin.json
Validation passed
```

```text
$ claude plugin validate .
Validating marketplace manifest: /Users/cayman-mac-mini/Documents/GitHub/apple-mail-mcp/.claude-plugin/marketplace.json
Validation passed
```

```text
$ git diff --check
<no output>
```

### Live Mail Gates

Account: `cayman@agenticassets.ai` (194 mailboxes)

```text
$ .venv/bin/apple-mail quick-check --account cayman@agenticassets.ai --json
ok: true
total_duration_ms: 9491.7
metadata: 5732.5ms / 8090ms pass
no_hit_search: 1882.8ms / 4500ms pass
inbox: 1876.4ms / 5000ms pass
```

```text
$ .venv/bin/apple-mail perf-test --profile production --account cayman@agenticassets.ai --json
ok: true
total_duration_ms: 15445.6
metadata: 5718.2ms / 8090ms pass
no_hit_search: 1866.8ms / 4500ms pass
inbox: 1966.2ms / 5000ms pass
dry_run_move: 1895.0ms / 5000ms pass
dry_run_trash: 1298.2ms / 5000ms pass
overview: 509.1ms / 15000ms pass
bad_account: 437.0ms / 2000ms pass
dashboard_metadata: 1755.1ms / 5000ms pass
```

```text
$ .venv/bin/apple-mail perf-test --include-analysis --allow-heavy-mail-scan --profile production --account cayman@agenticassets.ai --json
ok: true
total_duration_ms: 30933.6
metadata: 5729.7ms / 8090ms pass
no_hit_search: 1882.0ms / 4500ms pass
inbox: 1951.8ms / 5000ms pass
dry_run_move: 1913.9ms / 5000ms pass
dry_run_trash: 1284.8ms / 5000ms pass
overview: 677.5ms / 15000ms pass
bad_account: 304.0ms / 2000ms pass
dashboard_metadata: 1736.4ms / 5000ms pass
needs_response: 1902.0ms / 8000ms pass
awaiting_reply: 1719.1ms / 5000ms pass
top_senders: 1680.1ms / 5000ms pass
statistics_overview: 10152.3ms / 12000ms pass
```

## Current Diff Surface

Current uncommitted diff includes:

- code: `plugin/apple_mail_mcp/tools/search.py`
- tests: `tests/test_mail_search_tools.py`, `tests/test_validate_manifests.py`
- validation tooling/docs: `tools/validate_manifests.py`, `tools/CLAUDE.md`
- package/docs/tasks: `README.md`, `CLAUDE.md`, `docs/CLAUDE-conventions.md`, `apple-mail-mcpb/CLAUDE.md`, `tasks/*`, `tests/CLAUDE.md`
- artifact: `apple-mail-plugin.zip`

The local `apple-mail-mcp-v3.1.8.mcpb` exists and validated but remains ignored by git via `*.mcpb`.

## Residual Risks and Blockers

- `plugin-dev:plugin-validator`, `plugin-dev:plugin-architect`, and `plugin-dev:skill-reviewer` are referenced by repo instructions but are not callable in this environment. Local validators, requested plugin skills, and general subagents were used instead.
- The branch has uncommitted changes and has not been pushed in this continuation pass.
- Wrapper surface validation still checks critical read commands only. It documents wrapper parity for agent read workflows, but does not enforce a write/destructive-command exposure policy.
- Historical files such as `tasks/phase-plan-3.1.7.md` and archived reports intentionally retain historical version/test-count context; current navigation points to the active goal and backlog sidecar.

