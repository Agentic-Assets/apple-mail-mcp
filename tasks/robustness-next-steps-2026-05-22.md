# Apple Mail Plugin Robustness Next Steps — 2026-05-22

Branch: `feat/apple-mail-plugin-robustness`

Goal file: [`apple-mail-plugin-robustness-goal-2026-05-22.md`](apple-mail-plugin-robustness-goal-2026-05-22.md)

Completion audit: [`robustness-completion-audit-2026-05-22.md`](robustness-completion-audit-2026-05-22.md)

## Goal Summary

Make `apple-mail-mcp` feel like a robust, polished, production-ready Apple Mail plugin rather than only a working MCP server. The target package includes the Python MCP tools, CLI/wrapper surface, Claude Code plugin, workflow skills, validation scripts, Claude Desktop MCPB bundle, marketplace metadata, tests, docs, and regenerated distributable artifacts.

The goal is evidence-driven: preserve public tool names and documented behavior unless repo evidence requires a change, keep Mail operations draft-safe by default, gate expensive scans behind explicit opt-ins, and prove the final state with mocked, packaging, wrapper, plugin, MCPB, and bounded live Mail checks.

## Current Status

The implementation work and validation evidence are in place, but the completion contract is not fully closed because the named `plugin-dev:*` agents required by repo guidance are not callable in this environment.

Current branch state:

- `apple-mail-plugin.zip` has been rebuilt.
- Local ignored `apple-mail-mcp-v3.1.8.mcpb` has been rebuilt and validated.
- The working tree has uncommitted changes.
- No commit or push has been made in this continuation pass.

## Completed Improvements

- Permanent delete safety: `manage_trash(action="delete_permanent")` honors default `dry_run=True` across filtered, `message_ids`, and `apply_to_all` paths.
- Search error visibility: multi-account `search_emails(..., output_format="json")` keeps the compatible `errors` account list and adds structured `error_details`.
- Release artifact validation: `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh` requires both distributable artifacts to exist and match source bytes.
- Agent guidance cleanup: stale links, stale test counts, unsafe examples, incorrect caps, and incorrect empty-subject examples were corrected.
- Task hygiene: `tasks/todo.md` is a tiny active pointer; detailed work moved to sidecar reports.

## Verification Already Captured

Use [`robustness-completion-audit-2026-05-22.md`](robustness-completion-audit-2026-05-22.md) as the source of truth for exact command output.

Most recent passing gates:

```bash
bash tools/validate_manifests.sh
APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh
.venv/bin/pytest tests/ -q
python3 tools/check_wrapper_surface.py
git diff --check
```

Previously captured on the same code/artifact state:

```bash
claude plugin validate ./plugin
claude plugin validate .
.venv/bin/apple-mail quick-check --account cayman@agenticassets.ai --json
.venv/bin/apple-mail perf-test --profile production --account cayman@agenticassets.ai --json
.venv/bin/apple-mail perf-test --include-analysis --allow-heavy-mail-scan --profile production --account cayman@agenticassets.ai --json
```

## Next Steps

1. Re-run a final local status check:

```bash
git status --short --branch
git diff --stat
```

2. Re-run the smallest sufficient gates before PR review:

```bash
bash tools/validate_manifests.sh
APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh
.venv/bin/pytest tests/ -q
python3 tools/check_wrapper_surface.py
git diff --check
```

3. Re-run plugin validators if the local Claude plugin tooling is available:

```bash
claude plugin validate ./plugin
claude plugin validate .
```

4. Decide whether the `plugin-dev:*` agent caveat is acceptable for this branch. If those agents become available, run:

- `plugin-dev:plugin-validator` for manifest, MCPB, plugin structure, version, artifact, and tool-count parity.
- `plugin-dev:skill-reviewer` if any bundled skill text changes again.
- `plugin-dev:plugin-architect` before any further package-structure changes.

5. If the caveat is accepted, make a focused commit. Stage only the robustness work and artifact changes:

```bash
git add CLAUDE.md README.md docs/CLAUDE-conventions.md \
  plugin/apple_mail_mcp/tools/search.py \
  tests/test_mail_search_tools.py tests/test_validate_manifests.py tests/CLAUDE.md \
  tools/validate_manifests.py tools/CLAUDE.md \
  apple-mail-mcpb/CLAUDE.md apple-mail-plugin.zip \
  tasks/CLAUDE.md tasks/INDEX.md tasks/todo.md \
  tasks/live-test-baseline-2026-05-21.md \
  tasks/robustness-backlog-2026-05-22.md \
  tasks/robustness-completion-audit-2026-05-22.md \
  tasks/robustness-next-steps-2026-05-22.md
```

Suggested commit message:

```text
harden Apple Mail plugin packaging and agent guidance
```

6. Push or open a PR only after explicit user approval:

```bash
git push -u origin HEAD
```

## Residual Caveats

- `plugin-dev:plugin-validator`, `plugin-dev:plugin-architect`, and `plugin-dev:skill-reviewer` are referenced by repo guidance but were unavailable here.
- `apple-mail-mcp-v3.1.8.mcpb` is intentionally ignored by git via `*.mcpb`; keep the local artifact alongside the branch if needed for handoff.
- Wrapper validation currently covers critical read workflows; it does not enforce a complete destructive-command exposure policy.
- Historical task files may retain old version or test-count context by design. Current navigation points to the active goal, backlog, audit, and this handoff file.
