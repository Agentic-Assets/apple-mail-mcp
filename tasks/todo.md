# Active Pointer — apple-mail-mcp

**Branch:** `feat/apple-mail-plugin-robustness`

**Active workstream:** [`apple-mail-plugin-robustness-goal-2026-05-22.md`](apple-mail-plugin-robustness-goal-2026-05-22.md)

**Backlog sidecar:** [`robustness-backlog-2026-05-22.md`](robustness-backlog-2026-05-22.md)

**Latest verification (2026-05-22):** `validate_manifests.sh` OK (3.1.8, 27 tools); `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 validate_manifests.sh` OK; `pytest tests/ -q` 276 passed + 29 subtests; wrapper surface OK; `claude plugin validate ./plugin` and `claude plugin validate .` passed; rebuilt `apple-mail-plugin.zip` and local `apple-mail-mcp-v3.1.8.mcpb`; live production quick/perf/heavy gates passed against `cayman@agenticassets.ai`.

## Next Action

Branch shipped: robustness commits pushed; v3.1.8 release candidate validated with `plugin-dev:plugin-validator` and `plugin-dev:skill-reviewer`. Open PR when ready, or schedule the v3.1.9 backlog from [`robustness-backlog-2026-05-22.md`](robustness-backlog-2026-05-22.md).

## Blockers / Caveats

- `plugin-dev:plugin-architect` was referenced by historical repo guidance but is not in the current agent registry; structure work uses `plugin-dev:plugin-structure` and `plugin-dev:mcp-integration` skills instead.
- `apple-mail-mcp-v3.1.8.mcpb` is rebuilt and validated locally but ignored by git via `*.mcpb` — keep it alongside the branch for Claude Desktop handoff.
