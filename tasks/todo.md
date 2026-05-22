# Active Pointer — apple-mail-mcp

**Branch:** `feat/apple-mail-plugin-robustness`

**Active workstream:** [`whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md`](whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md) — capability-token refactor + Envelope Index SQLite migration (Phase A v3.2.0, Phase B v4.0.0). Decisions locked 2026-05-22; awaiting user kickoff.

**Prior workstream (shipped):** [`scalability-24k-hardening-2026-05-22.md`](scalability-24k-hardening-2026-05-22.md) — v3.1.9 + v3.1.10 24K-mailbox safety.

**Parent goal:** [`apple-mail-plugin-robustness-goal-2026-05-22.md`](apple-mail-plugin-robustness-goal-2026-05-22.md)

**Backlog sidecar:** [`robustness-backlog-2026-05-22.md`](robustness-backlog-2026-05-22.md)

**Latest verification (2026-05-22, v3.1.10):** `validate_manifests.sh` OK (3.1.10, 27 tools); `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 validate_manifests.sh` OK; `pytest tests/ -q` **308 passed + 29 subtests**; wrapper surface OK; both `claude plugin validate` runs passed; `apple-mail-plugin.zip` + `apple-mail-mcp-v3.1.10.mcpb` byte-fresh.

## Next Action

**Phase A — capability-token refactor (v3.2.0).** Stack on this branch. Per [`whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md`](whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md) §"Phase A":
1. Build `plugin/apple_mail_mcp/backend/` (`MailBackend` Protocol + `AppleScriptBackend` + `ScanWindow` token)
2. Centralize 3 AppleScript builders into `core/bounded_scan.py` + consolidate caps into `core/constants.py::SCAN_BOUNDS`
3. Refactor 5 tool files (`inbox.py`, `search.py`, `compose.py`, `smart_inbox.py`, `manage.py`) through the helper; fix the one unbounded `whose` at `manage.py:431`
4. Retire `allow_full_scan=True` from 8 sites; replace with structured `UNBOUNDED_SCAN_REQUIRED` errors
5. Add 28th tool: `full_inbox_export` (the only tool allowed to walk the whole inbox)
6. Add `tests/test_no_whose.py` + `tests/test_bounded_scan_contract.py` (CI-blocking)
7. Skill sync in same PR (dedupe pre-flight block, rewrite `allow_full_scan` mentions, fix v3.1.9→v3.2.0 tags)
8. Bump all 5 version files to 3.2.0; rebuild zip + mcpb

Pause and wait for user kickoff before starting.

## Blockers / Caveats

- Awaiting explicit user kickoff for Phase A.
- Phase B (Envelope Index SQLite reads, v4.0.0) deliberately deferred 1-2 weeks after Phase A merges for structured-error UX feedback.
- `plugin-dev:plugin-architect` was referenced by historical repo guidance but is not in the current agent registry; structure work uses `plugin-dev:plugin-structure` and `plugin-dev:mcp-integration` skills instead.
- `apple-mail-mcp-v3.1.10.mcpb` is rebuilt and validated locally but ignored by git via `*.mcpb` — keep it alongside the branch for Claude Desktop handoff.
