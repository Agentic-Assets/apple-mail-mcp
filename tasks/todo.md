# Active Pointer — apple-mail-mcp

**Branch:** `feat/apple-mail-plugin-robustness`

**Active workstream:** [`whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md`](whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md) — **Phase A shipped 2026-05-22 (v3.2.0).** **Senior-review hardening pass shipped 2026-05-22 (v3.2.1).** Phase B (Envelope Index SQLite, v4.0.0) intentionally deferred 1-2 weeks per Decision Record.

**Prior workstream (shipped):** [`scalability-24k-hardening-2026-05-22.md`](scalability-24k-hardening-2026-05-22.md) — v3.1.9 + v3.1.10 24K-mailbox safety.

**Parent goal:** [`apple-mail-plugin-robustness-goal-2026-05-22.md`](apple-mail-plugin-robustness-goal-2026-05-22.md)

**Backlog sidecar:** [`robustness-backlog-2026-05-22.md`](robustness-backlog-2026-05-22.md)

**Latest verification (2026-05-22, v3.2.1):** `validate_manifests.sh` OK (3.2.1, 28 tools); `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 validate_manifests.sh` OK; `pytest tests/ -q` **352 passed + 30 subtests** (was 337+30 before review pass; +14 contract tests, +1 retried envelope test); `claude plugin validate ./plugin` passed; `apple-mail-plugin.zip` + `apple-mail-mcp-v3.2.1.mcpb` byte-fresh; plugin-validator + skill-reviewer PASS post-fix; live smoke `apple-mail quick-check --account "cayman@agenticassets.ai"` ran 2026-05-22 — metadata + inbox PASS; `no_hit_search` 27.9s vs 4.5s threshold (known Phase A behavior — `compute_scan_upper_bound(recent_days=2)` is 300 vs old 100; not a v3.2.1 regression).

## v3.2.1 hardening pass — what shipped

**Senior code review (4 parallel reviewers) found:**

P0 (runtime defects):
1. `analytics.py` `full_inbox_export` field script used invalid AppleScript inline `if-then-else` — every default-fields call failed at runtime, mocked tests could not catch. Fixed via `(read/flagged status of aMessage) as string`.
2. `compose.py` `_build_found_message_lookup` used dangerous `every message of {mailbox_var} whose subject contains` that the static guard regex skipped (f-string brace prefix not matched by `\w`). Fixed via bounded-slice-then-filter pattern AND tightened `tests/test_no_unbounded_whose.py` regex to normalize f-string placeholders.

P1 (correctness + UX):
3. ToolError envelope shape standardized: every `-> str` tool now returns `json.dumps(tool_error.to_dict(), indent=2)` via new `serialize_tool_error()` helper in `backend/base.py` (5 sites deduplicated).
4. `search.py` text-mode no longer drops structured remediation on `UNBOUNDED_SCAN_REQUIRED`.
5. `compose.py` invalid `message_id` now returns structured `ToolError(code="INVALID_MESSAGE_ID")` instead of `"Error: …"` string.
6. `inbox.py` dead `max_emails == 0` branch deleted; `assert max_emails > 0` added.
7. `backend/applescript.py` Phase B stubs raise structured `ToolError(code="BACKEND_NOT_IMPLEMENTED")` instead of `NotImplementedError`; new test asserts each stub.

**3 parallel code-simplifier passes (zero test delta):**
- `backend/applescript.py` got `_raise_not_implemented()` + `_parse_message_row()` helpers; `bounded_scan.py` got `_unbounded_remediation()` helper.
- `analytics.py` `_full_export_field_script` collapsed 9-branch if/return into `_FULL_EXPORT_FIELD_EXPRS` table; `inbox.py` shares `_build_inbox_collection_block()` helper.
- `backend/base.py` `serialize_tool_error()` shared helper; unused `import json` dropped from compose/smart_inbox.

**Docs sweep:**
- README: 27→28 tools header; added `full_inbox_export` row; added `UNBOUNDED_SCAN_REQUIRED` row in Performance Defaults.
- Root CLAUDE.md: test count 315+→**337+30 subtests**; new section instructing regular `code-simplifier:code-simplifier` use alongside plugin-dev experts.
- `.claude-plugin/CLAUDE.md`: example version 3.1.8→3.2.0.
- `tasks/CLAUDE.md`: test count + 3.1.8→3.2.0; new active-files row for whose-elimination workstream.
- `tasks/INDEX.md`: reference text 3.1.8→3.2.0.
- `tests/CLAUDE.md`: added 5 missing test files + v3.2.0 contract-suite callout.
- `plugin/skills/CLAUDE.md`: added Shared references note pointing at `references/large-inbox-rules.md`.
- `docs/CLAUDE-conventions.md`: new `### ScanWindow capability token (v3.2.0)` subsection.

**Skill sweep:** all 5 skills that inlined v3.2.0 notes (`mailbox-taxonomy`, `mail-rules-advisor`, `email-attachments`, `email-style-profile`, `email-management/references/analytics.md`) now reference canonical `large-inbox-rules.md`. `email-attachments` + `email-style-profile` also gained `full_inbox_export` escalation guidance.

## Next Action

**v3.2.1 complete.** Possible next moves:

1. **Open PR** to `main` for v3.2.0 + v3.2.1 release together (stacked commits on this branch).
2. **Investigate `no_hit_search` latency** — `compute_scan_upper_bound(recent_days=2)` returning 300 vs old 100 makes the default-search smoke ~6x slower on 24K-class accounts. Either retune the formula or bump the quick-check threshold to match the new Phase A reality.
3. **Wait 1-2 weeks** then kick off Phase B (Envelope Index SQLite read backend, v4.0.0) per the synthesis Decision Record. Phase B starts with B0 pre-work: build `envelope-index-validator` + `mail-tool-migration-engineer` project-local agents.

## Blockers / Caveats

- **v3.2.0 + v3.2.1 are shipped on this branch but not yet merged to `main`.** Open PR when ready.
- **Phase B deliberately deferred 1-2 weeks** to let the new structured-error UX get real-agent exercise before layering another big refactor on top.
- One **known dangerous-`whose` quarantine** in `tools/compose.py:142` (`_build_draft_lookup` — `items 1 thru N of (every message of draftsMailbox whose subject contains)`). Drafts mailboxes are local and tiny so impact is low; tracked via `KNOWN_DANGEROUS_WHOSE` allowlist in `tests/test_no_unbounded_whose.py`. Remove allowlist entry to force a fix.
- **`no_hit_search` perf threshold** (4.5s) was set against pre-Phase-A scan bounds. Real production accounts now sit around 25–30s on no-hit queries with `recent_days=2`. Threshold needs revisiting or formula needs retuning before this is a green CI gate.
- `apple-mail-mcp-v3.2.1.mcpb` is rebuilt and validated locally but ignored by git via `*.mcpb` — keep it alongside the branch for Claude Desktop handoff.
