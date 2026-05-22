# Whose-Elimination & Envelope Index Migration — Final Synthesis

**Date:** 2026-05-22
**Branch:** `feat/apple-mail-plugin-robustness` (v3.1.10)
**Reports synthesized:**
- [`01-envelope-index-research.md`](01-envelope-index-research.md)
- [`02-mcp-architecture-research.md`](02-mcp-architecture-research.md)
- [`03-skill-impact-review.md`](03-skill-impact-review.md)
- [`04-agent-strategy.md`](04-agent-strategy.md)
- [`05-codebase-whose-map.md`](05-codebase-whose-map.md)

---

## Decision Record (locked 2026-05-22 by user)

| # | Decision | Choice | Notes |
|---|---|---|---|
| 1 | Branch strategy | **Stack on `feat/apple-mail-plugin-robustness`** (current branch) | User override. Phase A commits land on top of the v3.1.10 work already on this branch. |
| 2 | Phase A version target | **v3.2.0** (minor bump) | Retiring `allow_full_scan=True` is a breaking change to any script that passes it; minor bump signals that. |
| 3 | Phase B kickoff timing | **Wait 1-2 weeks after Phase A merges** | Lets the structured-error UX get real-agent exercise before layering Envelope Index on top. |
| 4 | `full_inbox_export` tool | **Ship in Phase A** | The structured `UNBOUNDED_SCAN_REQUIRED` error must point at a real `fallback_tool` — without it, the `remediation` field is aspirational. |
| 5 | Workstream pipeline | **Update `tasks/INDEX.md` + `tasks/todo.md`** | Points at this synthesis as the active workstream; keeps the project's `todo.md` → workstream folder → archive pipeline clean. |

After this commit and push, work pauses pending user kickoff of Phase A. No code changes will be made by Claude until the user explicitly says "start Phase A" or equivalent.

---

## TL;DR

**The codebase is in much better shape than the v3.1.10 commit message implied** — 36/36 direct mailbox enumerations are already bounded, 12/15 `whose` clauses are id-filtered or pre-sliced, and only **one** genuinely unbounded `whose` remains (`manage.py:431`, `manage_trash` older-than branch). The real problem isn't fixing live bugs — it's that **the safety pattern lives in the heads of contributors**, copy-pasted across five files. Next regression is one PR away.

**The fix is structural, not surgical:** introduce a `ScanWindow` capability token that the backend refuses to act without, abstract the AppleScript layer behind a `MailBackend` Protocol, and retire `allow_full_scan=True` in favor of structured errors that name a cheaper alternative tool. This makes the unsafe path unrepresentable at the type level — not "discouraged by comments," not "caught by lint sometimes," but *impossible without an explicit detour through the only token-issuing function.*

**Two phases, both meaningful:**
- **Phase A (v3.2.0, ~1 PR, ~1 week):** Capability-token + `MailBackend` Protocol refactor + retire `allow_full_scan` + fix the one unbounded `whose` + dedupe skill pre-flight blocks. Sets the seam for Phase B.
- **Phase B (v4.0.0, ~1 sprint, 8-12 PRs):** Swap the read-side backend to direct SQLite reads of Mail's `Envelope Index`. All discovery tools (list, search, statistics, top-senders, awaiting-reply, needs-response, inbox-overview, dashboard, mailbox-counts) become sub-millisecond. Mutations stay on AppleScript.

**Phase C (optional polish):** `outputSchema` / `structuredContent`, MCP Resources for static-ish data, progress notifications for the one legitimate full-scan tool.

---

## What we know now that we didn't yesterday

| Question | Yesterday's answer | Today's answer |
|---|---|---|
| Is direct SQLite reading feasible on Sequoia? | Maybe — needs reverse-engineering | **Yes, verified locally.** 104MB WAL DB at `~/Library/Mail/V10/MailData/Envelope Index`, `properties.version=4`, 44,812 messages across 180 mailboxes including the user's EWS Exchange inbox. Indexed reads are millisecond-fast. |
| What's the FDA story? | Probably gnarly | **Per-host grant.** Terminal, Claude Desktop, Claude Code each need their own Full Disk Access. No API to test — must trap `OperationalError`. Surfaceable as a structured tool error with a System Settings deep link. |
| How many `whose` regressions are live? | "Pattern is one PR away" | **One.** `manage_trash:431` (older-than branch). Everything else is already bounded by `messages 1 thru N`. |
| What's the right enforcement pattern? | Lint or test | **Capability token.** Frozen `ScanWindow` dataclass that only `core.bounded_inbox_scan()` can produce; backend rejects forged tokens at runtime, mypy/pyright reject wrong types statically. Lint + AST test as belt-and-braces. |
| Should we retire `allow_full_scan=True`? | Unclear | **Yes.** `mcp-builder` confirms boolean cost-escapes are an anti-pattern — agents flip them blindly. Replace with structured `UNBOUNDED_SCAN_REQUIRED` error + dedicated `full_inbox_export` tool. |
| How many new agents to create? | Unknown | **Two, both deferred to Phase B.** `envelope-index-validator` (reviewer) and `mail-tool-migration-engineer` (implementer). Skip `bounded-scan-enforcer` — CI lint + pytest cover it. |
| How much skill rewrite is needed? | Lots | **13 files, mostly concentrated.** Same 9-line "Large-inbox pre-flight" block is duplicated verbatim across 4 SKILL.md files — single-source it in Phase A. `email-management:152-174` Tool Selection table inverts under Phase B. |

---

## Architecture: the seam

```
┌──────────────────────────────────────────────────────────────────────┐
│  @mcp.tool surface (27 tools — unchanged signatures)                 │
│  — Always calls core.bounded_inbox_scan() to get a ScanWindow         │
│  — Always calls backend().read.* or backend().write.*                 │
│  — Never imports run_applescript, never emits raw AppleScript         │
└─────────────────────────────────┬────────────────────────────────────┘
                                  │
            ┌─────────────────────┴─────────────────────┐
            │                                           │
            ▼                                           ▼
┌─────────────────────────┐               ┌─────────────────────────────┐
│  core.bounded_inbox_scan │               │     MailBackend Protocol     │
│  -> ScanWindow (token)   │               │  read:  MailReadBackend     │
│  ONLY producer of valid  │               │  write: MailWriteBackend    │
│  ScanWindow instances    │               │  invalidate(scope)          │
└─────────────────────────┘               └──────────────┬──────────────┘
                                                         │
                                ┌────────────────────────┴────────────────────┐
                                │                                             │
                                ▼ (v1, today)                                 ▼ (v2, Phase B)
                ┌─────────────────────────────┐         ┌─────────────────────────────────┐
                │  AppleScriptBackend          │         │  HybridBackend                  │
                │  read  = AppleScript+slice   │         │  read  = EnvelopeIndexBackend   │
                │  write = AppleScript         │         │  write = AppleScriptBackend     │
                │  Builds "messages 1 thru N"  │         │  Reads SQLite (mode=ro WAL)     │
                │  AppleScript, applies post-  │         │  Writes via AppleScript,        │
                │  filter where needed         │         │  invalidates read cache after.  │
                └─────────────────────────────┘         └─────────────────────────────────┘
```

**Key invariants:**
1. Tools never see `run_applescript` or SQLite. The seam is the `MailBackend` Protocol.
2. Backend never accepts a read call without a `ScanWindow` token. The token carries `mailbox`, `since`, `limit`, and a `_issued_by` stamp.
3. Writes are *always* AppleScript-authoritative. SQLite is read-only, full stop.
4. Every `write.*` call returns a `WriteResult` carrying touched scopes; an `@invalidates` decorator on each write method calls `backend.invalidate(scope)` automatically.
5. Read backend is allowed to return stale-by-N-seconds; explicit `synchronize_account` is a barrier that forces SQLite handle re-open.

---

## Phase A — Capability-token refactor (target: v3.2.0)

### Scope (one PR, ~600-900 LOC net, mostly moves not adds)

**A1. Introduce the seam — `plugin/apple_mail_mcp/backend/`**
- `backend/base.py` — `ScanWindow` dataclass (frozen), `MailReadBackend` Protocol, `MailWriteBackend` Protocol, `MailBackend` aggregate Protocol, `WriteResult`, `InvalidationScope`, `ToolError(code=..., remediation=...)`.
- `backend/applescript.py` — `AppleScriptBackend` implementing both read and write Protocols. Internally uses the existing `core.run_applescript`, `escape_applescript`, etc.
- `backend/__init__.py` — module-level `backend()` accessor (lazy singleton; testable via `set_backend()`).

**A2. Centralize the bounded-scan helper — `core/bounded_scan.py`**
- `bounded_inbox_scan(*, mailbox, recent_days=None, limit=None, since=None) -> ScanWindow`
- Validates window is bounded (`recent_days <= MAX_SCAN_DAYS`, `limit <= MAX_SCAN_LIMIT`)
- Stamps token with `_issued_by="core.bounded_inbox_scan"`; backend checks the stamp at runtime
- Three internal AppleScript builders move here (per `05-codebase-whose-map.md` §7 "Missing Helpers"):
  - `build_bounded_message_scan(mailbox_var, limit, whose_condition=None) -> str`
  - `compute_scan_upper_bound(recent_days, base_cap=200, window_cap=500) -> int`
  - `build_whose_id_list(message_ids) -> str`
- Constants consolidated into `core/constants.py::SCAN_BOUNDS` (today's `DRAFT_LIST_CAP=100`, `MESSAGE_LOOKUP_CAP=100`, `SCAN_CAP=200`, `mailboxUpperBound`).

**A3. Refactor the 5 hot tool files**
Per `05-codebase-whose-map.md` §8 (impact estimate: ~90-120 LOC across the 5 files):
- `tools/inbox.py` — `_build_list_inbox_text_script`, `_build_list_inbox_json_script` (the two duplicated branches collapse into one through the helper)
- `tools/search.py` — `_build_search_script`, `_get_email_thread_search_impl`
- `tools/compose.py` — `_forward_email_by_subject_lookup`, `_manage_drafts_list`, `_build_found_message_lookup`
- `tools/smart_inbox.py` — `get_needs_response`, `get_awaiting_reply`, `get_top_senders`
- `tools/manage.py` — `move_email` body scan; **fix the unbounded `manage_trash:431` whose** by routing through `bounded_inbox_scan` and adding a `--allow-full-scan` opt-in only on that branch (the legitimate older-than-N case).

**A4. Retire `allow_full_scan=True`**
Remove the boolean param from 8 sites (per `05-codebase-whose-map.md` §5). Replace with structured `ToolError(code="UNBOUNDED_SCAN_REQUIRED", message=..., remediation={"preferred": "Pass recent_days=7 or 30...", "fallback_tool": "full_inbox_export", "fallback_tool_args": {...}})`.

**A5. Add the `full_inbox_export` tool**
New 28th tool. Explicit naming, `readOnlyHint=true`, `openWorldHint=true`. Streams `notifications/progress`. Description explicitly names cost ("walks every message; may take minutes"). This is the *only* tool that may walk the whole inbox.

**A6. Belt-and-braces enforcement**
- `tests/test_no_whose.py` — greps `tools/*.py` for `whose ` and `run_applescript(`; failure to be empty fails CI. Allowlist via `# noqa: bounded` comment + entry in a manifest list.
- `tests/test_bounded_scan_contract.py` — for each read tool, mock the backend and assert that the emitted AppleScript (when the AppleScript backend is active) contains `messages 1 thru` before any `whose`.
- Update `plugin/apple_mail_mcp/tools/CLAUDE.md` line 23: "Never write raw `whose` against `messages of mailbox` — use `core.bounded_inbox_scan()` + `backend().read.*`."

**A7. Skill sync (same PR per `03-skill-impact-review.md` recommendation)**
- Dedupe the 4× verbatim pre-flight block → single `plugin/skills/references/large-inbox-rules.md`, included by reference from `apple-mail-operator`, `email-management`, `email-archive-cleanup`, `inbox-triage`.
- Rewrite `allow_full_scan` mentions across 5 SKILL.md files → "if you need the full inbox, the tool will return `UNBOUNDED_SCAN_REQUIRED`; call `full_inbox_export` instead."
- Fix `mailbox-taxonomy:23` and `mail-rules-advisor:18` "v3.1.9" tags → "v3.2.0".
- Align `get_needs_response(max_results=...)` default citations across `email-management:101`, `inbox-triage:71`, `apple-mail-operator:17` (pre-existing PE-3).

**A8. Manifest + version bump**
- All 5 version files → `3.2.0` (per root CLAUDE.md § Version bump).
- MCPB `tools[]` adds `full_inbox_export`; tool count 27 → 28.
- Rebuild `apple-mail-plugin.zip` + `apple-mail-mcp-v3.2.0.mcpb`.

### Phase A agent orchestration

| Step | Subagent | Skills | Why |
|---|---|---|---|
| Build the seam (A1, A2) | `generalPurpose` | `plugin-dev:plugin-structure`, `mcp-builder` | Touches package layout; structure skill keeps `plugin/apple_mail_mcp/` canonical |
| Refactor 5 tool files (A3) | 5× `generalPurpose` **in parallel**, one per file | None needed — pattern is mechanical | Independent files, no shared state once A1/A2 land |
| Retire `allow_full_scan` (A4) | `generalPurpose` | `mcp-builder` | Structured-error contract is mcp-builder's anti-anti-pattern guidance |
| Add `full_inbox_export` (A5) | `generalPurpose` | `mcp-builder` | Tool design + outputSchema + progress notifications |
| Enforcement tests (A6) | `generalPurpose` | none | Mechanical |
| Skill sync (A7) | `generalPurpose` | none | Already scoped in `03-skill-impact-review.md` |
| Manifest + zip + mcpb (A8) | `shell` | none | Bash-heavy |
| Pre-merge validation | `plugin-dev:plugin-validator` | (skill) | Manifest parity gate; catches the 27→28 tool-count change everywhere |
| Skill quality pass | `plugin-dev:skill-reviewer` | (skill) | Run after A7 to catch wording regressions |
| Live smoke | `shell` | none | `.venv/bin/apple-mail quick-check --json` + `pytest tests/ -q` |
| Doc sync + commit | `finalize-apple-mail-mcp` | (skill) | Closes the loop; honors the commit gating rule |

### Phase A acceptance criteria

- [ ] `tests/test_no_whose.py` passes (zero raw `whose ` in tools/, zero `run_applescript(` in tools/)
- [ ] `tests/test_bounded_scan_contract.py` passes for every read tool
- [ ] No `allow_full_scan=True` parameter anywhere; structured `UNBOUNDED_SCAN_REQUIRED` errors round-trip with `remediation` field
- [ ] `full_inbox_export` tool present, registered, emits progress
- [ ] `manage_trash:431` unbounded `whose` is gone
- [ ] Pre-flight block exists in exactly one place (`plugin/skills/references/large-inbox-rules.md`) with includes from 4 skills
- [ ] All 5 version files at `3.2.0`; MCPB `tools[]` has 28 entries
- [ ] `pytest tests/ -q` green (existing 308 + ~10 new); live `apple-mail quick-check --json` green
- [ ] Both `claude plugin validate` runs PASS

---

## Phase B — Envelope Index SQLite read backend (target: v4.0.0)

### Why a major bump
Tool surface stays at 28 and signatures don't change, but `Envelope Index` access introduces a new system requirement (FDA grant) and a new failure mode (schema-version mismatch). Major-version signals "your install needs new permissions."

### Scope (one sprint, 8-12 per-tool PRs, plus 3 infra PRs)

**B0. Pre-work (one PR before any tool migrates)**
- Build `envelope-index-validator` agent (`04-agent-strategy.md` §1). Project-local in `.claude/agents/`. Reviewer only — `Read`/`Glob`/`Grep` toolset.
- Build `mail-tool-migration-engineer` agent (`04-agent-strategy.md` §2). Project-local. Implementer with `Read`/`Write`/`Edit`/`Bash` toolset. System prompt carries Envelope Index column reference + structured error contract + 6-step locked checklist.
- Add `backend/envelope_index.py` skeleton with `probe()`, schema-version allowlist, FDA-aware `_open_ro()` helper.
- Add `core/envelope_index_schema.py` — canonical column reference table (per `01-envelope-index-research.md` §"Core tables"): `messages`, `mailboxes`, `subjects`, `summaries`, `addresses`, `recipients`, `senders`/`sender_addresses`, `attachments`, `conversations`/`conversation_id_message_id`, `labels`, `properties`.
- Add `APPLE_MAIL_USE_ENVELOPE_INDEX=1` feature flag (opt-in default-off for the first 3.x.y releases on this branch; flips to default-on at v4.0.0).
- Wire `HybridBackend` (`read = EnvelopeIndexBackend`, `write = AppleScriptBackend`); selected at startup based on probe + flag.

**B1-Bn. Per-tool migration PRs (one per tool, parallelizable)**
Recommended order (cheapest first, highest-value first):

| Order | Tool | Why | New backend method |
|---|---|---|---|
| 1 | `list_mailboxes` | Cheap; single SQL on `mailboxes` table; warm-up | `read.list_mailboxes()` |
| 2 | `list_accounts` | Cheap; from `mailboxes.url` prefix grouping | `read.list_accounts()` |
| 3 | `list_account_addresses` | Cheap; addresses joined via sender_addresses | `read.list_account_addresses(account)` |
| 4 | `get_mailbox_unread_counts` | High-value; replaces a slow per-mailbox AppleScript walk | `read.unread_counts()` |
| 5 | `list_inbox_emails` | Highest-value; the original 24k pain point | `read.list_messages(window, fields)` |
| 6 | `get_inbox_overview` | High-value | `read.inbox_overview(window)` |
| 7 | `inbox_dashboard` | High-value; current "rescue path" becomes the default | `read.dashboard(account)` |
| 8 | `search_emails` | High-value; header/sender/date filters become SQL `WHERE`; full-text body stays on AppleScript fallback | `read.search(query, window)` |
| 9 | `get_top_senders` | Analytics; `GROUP BY addresses.address` | `read.top_senders(window)` |
| 10 | `get_statistics` | Analytics; multi-scope aggregations | `read.statistics(scope, window)` |
| 11 | `get_needs_response` | Cross-walks Sent — composite read | `read.needs_response(window)` |
| 12 | `get_awaiting_reply` | Same cross-walk pattern | `read.awaiting_reply(window)` |
| 13 (optional) | `get_email_thread` metadata | Uses `conversation_id_message_id`; body fetch falls back to AppleScript | `read.thread_metadata(thread_id)` |

**Stays on AppleScript for v4.0 (per `01-envelope-index-research.md` §"What stays on AppleScript"):**
- All mutations (compose, reply, forward, move, draft, send, mark-read, manage_trash, save_email_attachment, create_mailbox, synchronize_account, create_rich_email_draft) — 9 tools
- `get_email_by_id` body fetch and `list_email_attachments` content (Envelope Index has no full RFC822 — `.emlx` parsing is a Phase C+ project)
- Very-recent message race fallback in any read tool (if message_id requested but not in SQLite yet, fall back to AppleScript)

**B-final. Cutover PR**
- Flip `APPLE_MAIL_USE_ENVELOPE_INDEX` default to `True`
- Bump all 5 version files → `4.0.0`
- README + skill PR (per `03-skill-impact-review.md` recommendation — skills lag code by one PR):
  - `apple-mail-operator`: `inbox_dashboard` reframed from "rescue path" to "one-shot summary"
  - `email-management:152-174`: Tool Selection Guidelines table flattened (no more "fast/slow/costly" hierarchy)
  - `email-style-profile:27`: ladder ("try `recent_days=7` first; drop to 3...") deleted
  - Pre-flight block in `references/large-inbox-rules.md` trimmed to mutation-only

### Phase B agent orchestration (per-tool PR template)

| Step | Subagent | Skills | Why |
|---|---|---|---|
| Schema mapping | `Explore` | none | Find the Envelope Index columns this tool needs |
| Implementation | `mail-tool-migration-engineer` (project-local, built in B0) | none — agent carries the checklist | Locked 6-step migration: read existing AppleScript shape → map fields → write SQL → wire fallback → write pytest → verify parity |
| Pytest gates | `shell` | none | Both `EnvelopeIndexBackend` and `AppleScriptBackend` paths must pass; monkeypatched `sqlite3.connect` raise must trigger fallback cleanly |
| Live smoke | `shell` | none | `apple-mail quick-check --json` |
| Schema-version / FDA / fallback review | `envelope-index-validator` (project-local, built in B0) | none — agent carries the checklist | Confirms `?mode=ro` URI, schema-version probe, FDA error surface, no `SELECT *`, fallback test exists |
| Manifest parity | `plugin-dev:plugin-validator` | (skill) | Tool count stays at 28; descriptions stable |

After all tools migrated:

| Step | Subagent | Skills |
|---|---|---|
| Full suite | `shell` | none |
| Skill sweep | `plugin-dev:skill-reviewer` | (skill) — `03-skill-impact-review.md` Phase B punch list |
| Doc sync + v4.0 bump | `finalize-apple-mail-mcp` | (skill) |

### Phase B acceptance criteria

- [ ] `envelope-index-validator` + `mail-tool-migration-engineer` agents committed and invocable
- [ ] All 12 read-side tools have a SQLite-backed implementation + AppleScript fallback + dual-path pytest
- [ ] Schema-version probe runs at startup; mismatch falls back to AppleScript with a logged warning (not a crash)
- [ ] FDA-denied error surfaces as `ENV_FDA_DENIED` with System Settings deep link in `remediation`
- [ ] `mailboxes.total_count` vs `COUNT(*) FROM messages WHERE mailbox=?` sanity check runs on cold start; >5% divergence falls back
- [ ] Live perf gate on the 24k EWS inbox: `list_inbox_emails(max_emails=50)` returns in <500ms (was multi-second to timeout)
- [ ] All 5 version files at `4.0.0`; `APPLE_MAIL_USE_ENVELOPE_INDEX` default flipped to `True`
- [ ] Skill PR follows code PR by one merge; all `03-skill-impact-review.md` Phase B items addressed

---

## Phase C — MCP-level polish (target: v4.1.0, optional)

Per `02-mcp-architecture-research.md` §"MCP-level wins":

- **C1. `outputSchema` + `structuredContent` on all read tools.** Pydantic output models so Claude Desktop / Claude Code clients can render and filter without re-parsing. Highest-ROI ergonomics improvement.
- **C2. Reclassify `list_accounts`, `list_mailboxes`, `list_account_addresses` as MCP Resources.** URI-addressable (`mail://accounts`, `mail://accounts/{acct}/mailboxes`). Frees tool budget. Cacheable client-side. After Phase B these are sub-millisecond anyway, so they're textbook resources.
- **C3. Session-scoped result caching** for `list_mailboxes` and `list_account_addresses`. Pairs naturally with Resources.
- **C4. `notifications/progress` already proposed in A5** for `full_inbox_export`.
- **C5. Flip `idempotentHint=true`** on all SQLite-backed reads (truly idempotent now).

**Phase C agent orchestration:** one `generalPurpose` subagent per item with `mcp-builder` skill loaded; `plugin-dev:plugin-validator` after.

---

## Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Apple changes Envelope Index schema in a macOS point release | **Medium** | High — would break all Phase B tools | Schema-version probe + allowlist + AppleScript fallback. Any mismatch = transparent degrade. |
| FDA grant friction for users (per-host) | **High** | Medium — Phase B value is gated on permission | Surface as a structured error with a clear System Settings deep link; ship the AppleScript fallback so the plugin still works. Document in README + skills. |
| SQLite read sees stale state during EWS sync | **Low** | Low — same staleness AppleScript provides | `WAL` mode means we see committed-to-WAL state; `mailboxes.total_count` divergence check catches gross drift. |
| Capability token pattern is unfamiliar to contributors | **Low** | Low | One-line CLAUDE.md update + the type system enforces it automatically; bad code won't compile. |
| `full_inbox_export` becomes the new escape hatch (agent calls it reflexively) | **Medium** | Medium — same anti-pattern, new shape | Tool description names the cost explicitly; `mcp-builder` advice + plugin-validator description-content gate. |
| Phase B per-tool PRs accumulate divergence before cutover | **Medium** | Medium | Feature flag (`APPLE_MAIL_USE_ENVELOPE_INDEX=1`) lets us ship intermediate state safely; per-tool migration is independently revertable. |
| `manage_trash` `older_than` unbounded-whose fix breaks an existing user workflow | **Low** | Low | The fix is "now requires `--allow-full-scan` on that specific branch"; structured error tells the user how to opt in. Tested under live perf gate. |
| Migration engineer agent over-migrates and breaks parity | **Medium** | Medium | Agent's locked checklist forces parity-verification step before reporting done; `envelope-index-validator` reviews every PR. |

---

## What to NOT do

Per `02-mcp-architecture-research.md` anti-patterns and the "demand elegance, balanced" principle:

- **Don't split `list_inbox_emails` into `list_inbox_emails_fast` + `list_inbox_emails_slow`.** One tool, route inside the backend based on the window. `mcp-builder` favors fewer, clearer tools.
- **Don't write to SQLite directly.** Ever. Even for "fast" status flips. Mail will overwrite on sync and corrupt its caches.
- **Don't leak backend kwargs through tool signatures** (`timeout`, `osascript_path`, `sqlite_pragma`). They become a forever-API.
- **Don't build the `.emlx` parser in Phase B.** Full RFC822 body reading is a Phase C+ project; for v4.0 those reads stay on AppleScript. Scope discipline.
- **Don't build `bounded-scan-enforcer`, `live-mail-test-runner`, or `tool-contract-auditor` agents.** Per `04-agent-strategy.md`: redundant with CI lint, existing CLI, and `plugin-validator`.
- **Don't ship Phase B without the feature flag.** First Envelope Index PR ships opt-in. Default flips at v4.0.0 cutover only.
- **Don't dedupe the skill pre-flight blocks until Phase A.** They're not broken today; Phase A is the natural moment because all five files are being touched anyway.

---

## Decision points for the user

Before kicking Phase A:

1. **Branch strategy.** Land Phase A on top of `feat/apple-mail-plugin-robustness` (current branch), or open a fresh `feat/bounded-scan-refactor` branch off `main` and rebase? Recommend the fresh branch — clean diff, easier to review, current branch already has v3.1.10 as a clean ship point.
2. **Phase A version target.** `3.2.0` (minor bump — new tool, removed parameter is technically breaking) vs `3.1.11` (patch — argue the structured error makes it forward-compatible). Recommend **`3.2.0`** because retiring `allow_full_scan=True` *is* a breaking change for any script that passes it.
3. **Phase B kickoff timing.** Immediately after Phase A merges, or wait for live-user feedback on the structured-error UX first? Recommend **wait 1-2 weeks** between Phase A merge and Phase B kickoff. Lets the agent-side UX of the new errors get exercised before we layer another big refactor on top.
4. **`full_inbox_export` scope.** Ship in Phase A as proposed, or defer to Phase B (where SQLite makes it cheap)? Recommend **ship in Phase A** — the structured `UNBOUNDED_SCAN_REQUIRED` error needs a real `fallback_tool` to point at, otherwise the remediation field is aspirational.
5. **Workstream conventions.** Add this synthesis to `tasks/INDEX.md` as the active workstream pointer? Update `tasks/todo.md` next-action line to "Phase A kickoff"? Recommend **yes to both** — keeps the project-convention pipeline (`todo.md` → workstream folder → archive) clean.

---

## Cross-reference to the source reports

- **For the SQLite schema, FDA mechanics, and feasibility:** [`01-envelope-index-research.md`](01-envelope-index-research.md)
- **For the ScanWindow token, MailBackend Protocol, and contract anti-patterns:** [`02-mcp-architecture-research.md`](02-mcp-architecture-research.md)
- **For the per-skill edit list and pre-flight dedupe target:** [`03-skill-impact-review.md`](03-skill-impact-review.md)
- **For the two new agent specs and orchestration sketches:** [`04-agent-strategy.md`](04-agent-strategy.md)
- **For the file-by-file refactor inventory and the one remaining unbounded `whose`:** [`05-codebase-whose-map.md`](05-codebase-whose-map.md)
