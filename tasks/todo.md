# Active Pointer — apple-mail-mcp

**Branch:** `feat/apple-mail-plugin-robustness`

**Active workstream:** [`whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md`](whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md) — **Phase A shipped 2026-05-22 (v3.2.0).** Phase B (Envelope Index SQLite, v4.0.0) intentionally deferred 1-2 weeks per Decision Record.

**Prior workstream (shipped):** [`scalability-24k-hardening-2026-05-22.md`](scalability-24k-hardening-2026-05-22.md) — v3.1.9 + v3.1.10 24K-mailbox safety.

**Parent goal:** [`apple-mail-plugin-robustness-goal-2026-05-22.md`](apple-mail-plugin-robustness-goal-2026-05-22.md)

**Backlog sidecar:** [`robustness-backlog-2026-05-22.md`](robustness-backlog-2026-05-22.md)

**Latest verification (2026-05-22, v3.2.0):** `validate_manifests.sh` OK (3.2.0, 28 tools); `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 validate_manifests.sh` OK; `pytest tests/ -q` **337 passed + 30 subtests**; `claude plugin validate ./plugin` passed; `apple-mail-plugin.zip` + `apple-mail-mcp-v3.2.0.mcpb` byte-fresh; plugin-validator + skill-reviewer PASS.

## Next Action

**Phase A complete.** Possible next moves (pick one, or pause):

1. **Live smoke verification** — run `.venv/bin/apple-mail quick-check --json --account "cayman@agenticassets.ai"` against production Mail to confirm the `UNBOUNDED_SCAN_REQUIRED` envelope round-trips correctly through the MCP transport. Defer 1-2 days until structured-error UX has been exercised by an agent.
2. **Open PR** to `main` for v3.2.0 release.
3. **Wait 1-2 weeks** then kick off Phase B (Envelope Index SQLite read backend, v4.0.0) per the synthesis Decision Record. Phase B starts with B0 pre-work: build `envelope-index-validator` + `mail-tool-migration-engineer` project-local agents.

## Blockers / Caveats

- **Phase A is shipped on this branch but not yet merged to `main`.** Open PR when ready.
- **Phase B deliberately deferred 1-2 weeks** to let the new structured-error UX get real-agent exercise before layering another big refactor on top.
- One **known dangerous-`whose` quarantine** in `tools/compose.py:122` (`_manage_drafts_list` — `items 1 thru N of (every message of draftsMailbox whose subject contains)`). Drafts mailboxes are local and tiny so impact is low; tracked via `KNOWN_DANGEROUS_WHOSE` allowlist in `tests/test_no_unbounded_whose.py`. Remove allowlist entry to force a fix.
- `apple-mail-mcp-v3.2.0.mcpb` is rebuilt and validated locally but ignored by git via `*.mcpb` — keep it alongside the branch for Claude Desktop handoff.
