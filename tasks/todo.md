# Active Pointer — apple-mail-mcp

**Branch:** `feat/apple-mail-plugin-robustness`

**Active workstream:** [`scalability-24k-hardening-2026-05-22.md`](scalability-24k-hardening-2026-05-22.md) (v3.1.9 + v3.1.10 24K-mailbox safety)

**Prior:** [`apple-mail-plugin-robustness-goal-2026-05-22.md`](apple-mail-plugin-robustness-goal-2026-05-22.md)

**Backlog sidecar:** [`robustness-backlog-2026-05-22.md`](robustness-backlog-2026-05-22.md)

**Latest verification (2026-05-22, v3.1.10):** `validate_manifests.sh` OK (3.1.10, 27 tools); `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 validate_manifests.sh` OK; `pytest tests/ -q` **303 passed + 29 subtests** (290 baseline + 11 new for v3.1.10 + 2 updated for new scan_cap contract); wrapper surface OK; `claude plugin validate ./plugin` and `claude plugin validate .` passed; rebuilt `apple-mail-plugin.zip` and `apple-mail-mcp-v3.1.10.mcpb` byte-fresh.

## Next Action

v3.1.10 hardening (pass-2 review fixes): `list_inbox_emails(include_read=False)` now binds a bounded newest-first slice (scan_cap = min(max(max_emails*10, 100), 1000)) BEFORE `whose read status is false`, so a 24K Exchange inbox is never materialized to evaluate the filter — closes the loop on the `unread_only=True` alias path that the v3.1.9 audit flagged as MAJOR. `search_emails` scan_cap now scales with `recent_days` (floor=limit+offset+1, ceiling=500) so narrow-filter queries over a 7-day window inspect ~350 messages instead of 21 — eliminates the silent "no matches" failure mode that mimicked read-only behavior. Open PR when ready.

## Blockers / Caveats

- `plugin-dev:plugin-architect` was referenced by historical repo guidance but is not in the current agent registry; structure work uses `plugin-dev:plugin-structure` and `plugin-dev:mcp-integration` skills instead.
- `apple-mail-mcp-v3.1.10.mcpb` is rebuilt and validated locally but ignored by git via `*.mcpb` — keep it alongside the branch for Claude Desktop handoff.
