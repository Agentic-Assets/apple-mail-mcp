# Active Pointer — apple-mail-mcp

**Branch:** `feat/apple-mail-plugin-robustness`

**Active workstream:** [`scalability-24k-hardening-2026-05-22.md`](scalability-24k-hardening-2026-05-22.md) (v3.1.9 24K-mailbox safety)

**Prior:** [`apple-mail-plugin-robustness-goal-2026-05-22.md`](apple-mail-plugin-robustness-goal-2026-05-22.md)

**Backlog sidecar:** [`robustness-backlog-2026-05-22.md`](robustness-backlog-2026-05-22.md)

**Latest verification (2026-05-22):** `validate_manifests.sh` OK (3.1.9, 27 tools); `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 validate_manifests.sh` OK; `pytest tests/ -q` 290 passed + 29 subtests (276 baseline + 14 new scalability tests); wrapper surface OK; `claude plugin validate ./plugin` and `claude plugin validate .` passed; rebuilt `apple-mail-plugin.zip` and `apple-mail-mcp-v3.1.9.mcpb`; final `plugin-dev:plugin-validator` and `plugin-dev:skill-reviewer` passes both ship-ready.

## Next Action

v3.1.9 hardening pushed: compose subject-fallback bounded, `get_statistics`/`get_top_senders` gated on `days_back=0`, `list_inbox_emails` accepts `limit`/`unread_only` aliases with warning, 4 skills got the shared "Large-inbox pre-flight" block. Open PR when ready.

## Blockers / Caveats

- `plugin-dev:plugin-architect` was referenced by historical repo guidance but is not in the current agent registry; structure work uses `plugin-dev:plugin-structure` and `plugin-dev:mcp-integration` skills instead.
- `apple-mail-mcp-v3.1.8.mcpb` is rebuilt and validated locally but ignored by git via `*.mcpb` — keep it alongside the branch for Claude Desktop handoff.
