# Active Pointer — apple-mail-mcp

**Branch:** `feat/apple-mail-plugin-robustness`

**Active workstream:** [`apple-mail-plugin-robustness-goal-2026-05-22.md`](apple-mail-plugin-robustness-goal-2026-05-22.md)

**Backlog sidecar:** [`robustness-backlog-2026-05-22.md`](robustness-backlog-2026-05-22.md)

**Latest verification (2026-05-22):** `validate_manifests.sh` OK (3.1.8, 27 tools); `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 validate_manifests.sh` OK; `pytest tests/ -q` 276 passed + 29 subtests; wrapper surface OK; `claude plugin validate ./plugin` and `claude plugin validate .` passed; rebuilt `apple-mail-plugin.zip` and local `apple-mail-mcp-v3.1.8.mcpb`; live production quick/perf/heavy gates passed against `cayman@agenticassets.ai`.

## Next Action

Prepare the branch for PR/merge review using [`robustness-completion-audit-2026-05-22.md`](robustness-completion-audit-2026-05-22.md), decide whether the unavailable `plugin-dev:*` agent caveat is acceptable, then commit/push only when explicitly requested.

## Blockers / Caveats

- Named `plugin-dev:plugin-validator`, `plugin-dev:plugin-architect`, and `plugin-dev:skill-reviewer` agents are not callable in this environment; local validators and plugin skills were used instead.
- `apple-mail-mcp-v3.1.8.mcpb` is rebuilt and validated locally but ignored by git via `*.mcpb`.
- No commit or push has been made in this continuation pass.
