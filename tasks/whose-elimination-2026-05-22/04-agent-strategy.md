# Agent Strategy — `whose` Elimination + SQLite Migration

**Date:** 2026-05-22  
**Branch:** `feat/apple-mail-plugin-robustness`  
**Phases in scope:** A (bounded_inbox_scan centralization) · B (Envelope Index SQLite read path)

---

## TL;DR

| Candidate | Verdict |
|-----------|---------|
| `bounded-scan-enforcer` (reviewer) | **Skip** — CI lint + existing test suite cover this; agent adds friction, not coverage |
| `envelope-index-validator` (reviewer) | **Build in Phase B** — genuinely novel checklist with no existing equivalent |
| `mail-tool-migration-engineer` (implementer) | **Build in Phase B** — per-tool scope + dual-path parity testing exceeds what `general-purpose` handles cleanly |
| `live-mail-test-runner` | **Skip** — `apple-mail quick-check` + `perf-test` CLI already exist; this duplicates them |
| `tool-contract-auditor` | **Skip** — `plugin-dev:plugin-validator` + the proposed CI lint cover structured error shape and manifest contracts |

**Net new agents to build: 2, both deferred to Phase B.**

---

## Recommended New Agents

### 1. `envelope-index-validator` — build at Phase B kickoff

**Type:** Reviewer (read-only analysis)  
**Model:** sonnet — checklist is mechanical but the judgment calls (is a fallback path real or cosmetic? is schema version detection actually tested?) benefit from more reasoning capacity than haiku provides.  
**Trigger conditions:** Invoke on any PR that introduces or modifies a tool claiming to use the SQLite `Envelope Index` backend. Also invoke at Phase B completion before merging to `main`.  
**Tool allowlist:** `Read`, `Glob`, `Grep` — no writes, no shell execution. It reads Python source and test files; it does not run tests.  
**Location:** `.claude/agents/envelope-index-validator.md` (project-local; this concern is specific to apple-mail-mcp's Envelope Index schema, not generically reusable across other projects).  
**Phase:** Build at Phase B kickoff, not before. Before Phase B exists there is nothing meaningful to validate against.

**Frontmatter description (5-line):**
```
Use this agent when a PR adds or modifies a tool that reads Apple Mail's
Envelope Index SQLite database. Reviews the tool implementation for: (1)
schema-version detection before any query, (2) read-only URI connection
mode, (3) FDA/TCC error handling with a user-facing message, (4) full
AppleScript fallback on any SQLite failure, and (5) a pytest coverage path
that exercises the fallback. Do NOT use for AppleScript-only tools.
```

**What it checks (in order):**

1. Connection string uses `?mode=ro` URI — confirms the backend never opens the database writable.
2. Schema-version probe executes before any data query — guards against Apple silently changing column layout between macOS releases.
3. `OperationalError` / `DatabaseError` catch block exists and returns the same structured error envelope shape as the AppleScript path (`{"ok": false, "error": "...", ...}`).
4. FDA/TCC permission error is surfaced as a named error key, not a bare exception string.
5. `pytest` coverage: at least one test that monkeypatches `sqlite3.connect` to raise and verifies the tool falls back to AppleScript (not that it crashes).
6. No `SELECT *` — column names must be explicit so a schema change fails loudly rather than silently returning wrong fields.

---

### 2. `mail-tool-migration-engineer` — build at Phase B kickoff

**Type:** Implementer (code generation + verification)  
**Model:** sonnet — tasks involve reading existing AppleScript, writing equivalent SQL, writing a pytest, and doing behavioral parity checks. Haiku will miss subtle field-mapping differences.  
**Trigger conditions:** Invoke once per tool being migrated from AppleScript to Envelope Index in Phase B. Each invocation receives: the tool file path, the target column list from the Envelope Index schema, and the fallback contract spec.  
**Tool allowlist:** `Read`, `Write`, `Edit`, `Bash` (for running `pytest` on the specific test file). No `mcp__*` tools — this agent edits source, it does not call the MCP server.  
**Location:** `.claude/agents/mail-tool-migration-engineer.md` (project-local; the Envelope Index schema knowledge is apple-mail-mcp specific).  
**Phase:** Build at Phase B kickoff.

**Frontmatter description (5-line):**
```
Use this agent when migrating a specific read-only apple-mail-mcp tool from
AppleScript to the Envelope Index SQLite backend. Given a tool file and the
target schema columns, writes the SQLite query path, wires the AppleScript
fallback, writes or updates the pytest covering both paths, and verifies
behavioral parity between the old and new output shapes. One agent
invocation per tool — do not attempt multi-tool migrations in a single run.
```

**Why this warrants its own agent rather than `general-purpose`:**

- The migration has a locked 6-step checklist (read existing AppleScript output → map fields → write SQL → wire fallback → write pytest → verify parity). `general-purpose` will improvise the checklist every time and skip steps under token pressure.
- The agent can hold the Envelope Index column reference and the structured error envelope contract in its system prompt, so each individual tool migration starts with full context rather than requiring the orchestrator to re-paste those contracts.
- Phase B likely involves migrating 8–12 tools. Repeatable per-tool invocations with a locked checklist are exactly the pattern purpose-built agents are good at.

**System prompt structure (abbreviated):** Role → Envelope Index column reference table → 6-step migration process → structured error envelope spec → parity verification protocol → explicit "stop and report" rules if the AppleScript output shape is ambiguous.

---

## Rejected Candidates

### `bounded-scan-enforcer`

The proposed CI lint (a `grep`/`rg` rule banning `whose ` outside the designated helper) is the right tool here. A lint runs on every push automatically, produces a line-level failure with no agent startup cost, and catches regressions before a human ever reviews the PR. An agent reviewer adds several seconds of invocation overhead and requires a human to remember to run it; the lint does not. The only work an agent could add beyond the lint is checking the `allow_full_scan` removal and structured error envelope shape — but those are already covered by the existing `pytest` suite (`tests/test_mail_search_tools.py` and related) and will break tests before they break the lint. Adding a reviewer agent here is redundant with two cheaper, always-on mechanisms.

### `live-mail-test-runner`

The repo already has `.venv/bin/apple-mail quick-check --json` (30s smoke) and `.venv/bin/apple-mail perf-test --include-analysis --allow-heavy-mail-scan` (full perf gate). These are documented in `docs/AGENT_LIVE_TESTING.md` and used in the existing verification workflow. A `live-mail-test-runner` agent would wrap CLI commands that already exist. The value would only materialize if the agent were also interpreting results — but `quick-check` already outputs structured JSON and `perf-test` already applies thresholds with pass/fail. Build a small shell script alias for the Phase B live gate if needed; do not build an agent.

### `tool-contract-auditor`

`plugin-dev:plugin-validator` already checks manifest drift, tool count, and MCPB parity. The structured error envelope shape is enforced by the existing pytest suite. If Phase A adds a CI lint for `whose` violations, that closes the remaining gap. A separate contract auditor would overlap with `plugin-validator` on manifest concerns and with pytest on behavioral contracts, without owning a distinct surface. The `envelope-index-validator` recommended above covers the new contract surface that Phase B introduces.

---

## Existing Agent Reuse Plan

### `plugin-dev:plugin-validator`

- **Phase A:** Run after adding the `bounded_inbox_scan` helper and refactoring the 5 tool files. Tool count stays at 27; validator confirms no manifest drift from the refactor. Also run after adding the CI lint script if it is wired into the plugin's `hooks` or `tools/`.
- **Phase B:** Run after each tool migration. Phase B does not add tools (it reimplements read paths), so the validator's main job is confirming that tool names, descriptions, and the mcpb `tools[]` array remain stable. If Phase B surfaces any schema-version or FDA error in the tool `description` fields, validator catches that drift.

### `plugin-dev:skill-reviewer`

- **Phase A:** No skill changes expected. Run only if the `bounded_inbox_scan` refactor changes the observable behavior of `search_emails` or `list_inbox_emails` in a way that affects the skill guidance in `plugin/skills/`.
- **Phase B:** If Phase B changes default performance characteristics visibly (e.g., `search_emails` becomes 10x faster), update relevant skills (`apple-mail-operator`, `inbox-triage`) and run `skill-reviewer` once before the Phase B merge.

### `finalize-apple-mail-mcp`

- **Phase A:** Invoke at PR-ready point to sync CLAUDE.md, manifests, and version bump (if 3.1.11 ships as the Phase A tag). Commit and push.
- **Phase B:** Invoke after the final tool migration lands and tests pass. Phase B is a minor version bump (v4.0); `finalize-apple-mail-mcp` handles the five-file version sync.

---

## Orchestration Sketch

### Phase A — PR review (bounded_inbox_scan centralization)

```
1. generalPurpose subagent: implement bounded_inbox_scan helper in core.py
2. 5x generalPurpose subagents (parallel): refactor one tool file each
3. shell subagent: pytest tests/ -q  →  must be green before proceeding
4. CI lint rule added (grep/rg in .github/workflows or tools/)
5. plugin-dev:plugin-validator  →  manifest parity gate
6. finalize-apple-mail-mcp  →  sync docs + commit
```

No new agents invoked. The CI lint replaces what `bounded-scan-enforcer` would have done.

### Phase B — per-tool migration PR

```
1. Explore subagent: map Envelope Index schema columns for this tool
2. mail-tool-migration-engineer: migrate tool, write SQLite path + fallback + pytest
3. shell subagent: pytest tests/test_<tool>.py -v  →  both paths green
4. shell subagent: apple-mail quick-check --json  →  smoke gate on live Mail
5. envelope-index-validator: verify schema-version detection, read-only URI,
   FDA error shape, fallback coverage
6. plugin-dev:plugin-validator  →  manifest stable
```

After all tools migrated:
```
7. shell subagent: pytest tests/ -q  →  full suite green
8. plugin-dev:skill-reviewer  →  if perf characteristics changed skill guidance
9. finalize-apple-mail-mcp  →  v4.0 version bump + commit + push
```

---

## Build Checklist

| Agent | Create in | When |
|-------|-----------|------|
| `envelope-index-validator` | `.claude/agents/` | Phase B kickoff |
| `mail-tool-migration-engineer` | `.claude/agents/` | Phase B kickoff |
| CI lint rule (not an agent) | `.github/workflows/` or `tools/` | Phase A |
