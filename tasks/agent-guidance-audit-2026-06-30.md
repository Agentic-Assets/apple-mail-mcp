# Agent Guidance Audit — Apple Mail MCP

**Date:** 2026-06-30 (verified same day by three codebase review passes)  
**Branch reviewed:** `codex/pr38-guidance-verifier-followup` (v3.8.0 native-reply release)  
**Scope:** Agent-facing training surface for coding agents (skills, MCP tool descriptions, README, templates, conventions)  
**Status:** Actionable fix plan; runtime behavior confirmed correct for deprecated paths  

---

## Executive overview

Apple Mail MCP is **safer at runtime than its documentation suggests**. Code guardrails from v3.6–3.8 (ID-first mutations, `TARGET_SELECTOR_DEPRECATED`, native reply default, compose race fixes) block the worst historical failure modes: clipboard reply races, keyword-targeted bulk deletes, and unbounded inbox scans.

The remaining risk is **agent confusion**, not silent data corruption. Agents that load `email-drafting` + `search-patterns.md` generally succeed. Agents that read **only MCP tool schemas**, **README tool tables**, or **`email-management/templates/common-workflows.md`** are likely to:

1. Hit structured-error loops (`TARGET_SELECTOR_DEPRECATED`, `FILTER_SCAN_DISABLED`) or parameter validation failures.
2. Mis-copy discovery param names (`sender_exact`, `sender_domain`) onto action tools that only accept `sender`.
3. Draft replies without native-format / Accessibility context and get `REPLY_WINDOW_FOCUS_FAILED` or flat, logo-less drafts via `native_format=False`.
4. Break threading after typos (`body=` vs `reply_body`, `message_id=` vs `message_ids`) by falling back to standalone compose tools.

**Bottom line:** Fix the **training surface** first. Do not remove v3.x schema params until v4; make every layer tell the same ID-first story.

---

## Verification summary (2026-06-30)

Three independent passes re-read the repo against this audit:

| Pass | Verdict |
|------|---------|
| P0 templates (`common-workflows.md`, `inbox-zero-workflow.md`) | Priority correct; **error taxonomy in original P0-1 was wrong** (see below) |
| P1–P2 skills / routing | Directionally correct; **umbrella skill frontmatter routes better than first draft implied** |
| Manifest / README / docstrings | P0-2/3/4 **verified**; `allow_filter_scan` never revives subject/sender; several **missed HIGH** README/manifest rows added below |

---

## Runtime error taxonomy (agents must understand four classes)

When templates or docs are wrong, agents see **different** failures. Fix docs to match this table:

| Class | Example call | Runtime outcome |
|-------|--------------|-----------------|
| **A. Unknown parameter** | `manage_trash(sender_exact=...)`, `update_email_status(sender_domain=...)`, `save_email_attachment(message_id=...)`, `reply_to_email(body=...)` | MCP / Python **validation error** before tool logic runs |
| **B. Bare move (no ids, no filters)** | `move_email(to_mailbox="Archive", from_mailbox="INBOX", max_moves=20)` | Plain text: **"At least one filter is required (subject_keyword, sender, or older_than_days), or pass message_ids=[...]"** |
| **C. Deprecated selector on action tool** | `reply_to_email(subject_keyword=...)`, `move_email(sender=...)` (no `message_ids`) | Structured **`TARGET_SELECTOR_DEPRECATED`** (fires before any scan) |
| **D. Filter scan without opt-in** | `move_email(older_than_days=30, only_read=True)` without `message_ids` or `allow_filter_scan=True` | Structured **`FILTER_SCAN_DISABLED`** |

**Critical nuance:** `allow_filter_scan=True` only helps **date/bulk paths** (`older_than_days`, `apply_to_all`). **Subject/sender selectors are deprecated on action tools even with `allow_filter_scan=True`** (`manage.py` checks `_deprecated_target_selectors` first).

### Parameter namespace: discovery vs action tools

| Tool family | Use for discovery | Use on action tools |
|-------------|-------------------|---------------------|
| `search_emails` | `sender_exact`, `sender_domain`, `subject_keyword` | N/A |
| `move_email`, `update_email_status`, `manage_trash` | — | `message_ids` (preferred); `sender` / `subject_keyword` → class C; date filters → class D without opt-in |
| `save_email_attachment` | — | `message_ids` (list); not `message_id` |
| `reply_to_email`, `forward_email` | — | `message_id`; `reply_body` (not `body`); `subject_keyword` → class C |
| `get_statistics` (`sender_stats`) | — | `sender` (not `sender_exact`) |

---

## What works well (keep as canonical)

| Asset | Role | Caveat |
|-------|------|--------|
| `docs/CLAUDE-conventions.md` | ID-first policy, `native_format`, deprecation contracts | **L156 overclaims** that `apple-mail-operator` documents native reply; it does not yet |
| `plugin/skills/email-drafting/SKILL.md` | Native reply, verification, standalone guards | Internal "degraded path" wording contradicts L45 (P1-1) |
| `plugin/skills/email-management/templates/search-patterns.md` | Gold copy-paste: discovery → ids → action | — |
| `plugin/skills/references/large-inbox-rules.md` | ID-first mutations, scan caps | — |
| `plugin/skills/email-archive-cleanup/SKILL.md` | Staged cleanup + deprecation warnings | — |
| `plugin/skills/inbox-triage/SKILL.md` | Read-first triage; correct archive handoff | Frontmatter already routes drafting to `email-drafting` |
| `plugin/skills/email-management/SKILL.md` | Umbrella program | Decision tree L60/L94 already routes "check my email" → `inbox-triage` |
| `list_email_attachments` docstring | Explicit `subject_keyword` deprecation block | Copy pattern to `save_email_attachment` |
| Runtime structured errors | `TARGET_SELECTOR_DEPRECATED`, `FILTER_SCAN_DISABLED`, `REPLY_WINDOW_FOCUS_FAILED` | — |

**Tool count unchanged (31).** No tools should be removed in v3.x.

**False security:** `tests/test_id_first_guidance.py` **passes today** while `common-workflows.md` is broken because it matches `sender=` but not `sender_exact=`, and does not flag filter-only `move_email` without deprecated selectors. Extend tests in step 5 (see implementation order).

---

## Priority-ranked issues

**P0** = copy-paste / MCP-schema traps · **P1** = workflow confusion · **P2** = routing / historical docs · **P3** = test / release hygiene

---

### P0 — Copy-paste and MCP-schema traps (fix first)

#### P0-1. `common-workflows.md` teaches broken patterns

**File:** `plugin/skills/email-management/templates/common-workflows.md`

| Location | Broken pattern | Error class | Why it matters |
|----------|--------------|-------------|----------------|
| L41, L846, L873 | `move_email(to_mailbox=..., from_mailbox=...)` only | **B** | "Archive inbox" one-liners fail before any scan |
| L217–224 | `move_email(..., older_than_days=30, only_read=True)` no `allow_filter_scan` | **D** | Pushes agents toward slow-scan escape hatch on 24k mailboxes |
| L457–478 | `manage_trash(..., sender_exact=...)` | **A** (then **C** if renamed `sender=`) | Discovery param on action tool; default `dry_run=True` → preview only even if fixed |
| L436–479 | Search `sender_exact` then `manage_trash(sender_exact=...)` | **A** + disconnected workflow | Search OK; never wires `message_ids` into trash step |
| L701–720 | Search `sender_domain` then `update_email_status(sender_domain=...)` | **A** | Step 1 correct on `search_emails`; step 2 wrong param |
| L723–746 | Search then `move_email(sender_exact=...)` | **A** + disconnected | Never passes collected ids |
| L617–622, L643–648 | `get_statistics(scope="sender_stats", sender_exact=...)` | **A** | Tool accepts `sender`, not `sender_exact` |
| L167, L795–800 | `save_email_attachment(message_id=...)` | **A** | Correct: `message_ids=[...]` |
| L591–596 | `reply_to_email(..., body=...)` | **A** | Correct: `reply_body=`; call never runs (not an empty-body draft) |

**Good contrast:** L305–360 Quick Reply / Deferred Response use `search_emails` → `message_id` → `reply_to_email(..., reply_body=...)`.

**Partial trap:** L355 mentions `native_format=False` for headless use but not default native path, Accessibility, or `REPLY_WINDOW_FOCUS_FAILED` recovery.

**Recommendations:**

1. Add file header: "Action-tool examples must follow `search-patterns.md`. `sender_exact` / `sender_domain` / `subject_keyword` belong on **`search_emails`** only."
2. Replace every mutation block with: bounded discovery → collect `message_ids` → `dry_run=True` preview → execute with `message_ids=[...]`.
3. Fix param names: `reply_body`, `message_ids`, `sender` on `get_statistics`.
4. For newsletter cleanup (L436–479): show `ids = [r["message_id"] for r in results["items"]]` then `manage_trash(message_ids=ids, dry_run=False, ...)`.
5. Rewrite Morning Inbox Check (L7–26) to mirror `inbox-triage` (`get_needs_response`, `exclude_replied=True`) or link to that skill.

---

#### P0-2. MCP manifest still advertises dead code paths

**File:** `apple-mail-mcpb/manifest.json` (rebuild all artifacts after edit)

| Tool / lines | Problem | Verified runtime |
|--------------|---------|------------------|
| `reply_to_email` L86 | "by message_id (preferred) **or subject keyword**" + full subject-scan semantics (`recent_days`, etc.) that **never execute** | **C** on `subject_keyword` only |
| `forward_email` L94 | Same subject-keyword fallback language | **C** |
| `list_email_attachments` L114 | "or subject keyword" | **C** |
| `manage_drafts` L102 | "prefer exact draft_id over **subject matching**" | `draft_subject` on send/open/delete → **C** |
| `move_email` L82, `update_email_status` L138, `manage_trash` L142 | Imply `allow_filter_scan` revives subject/sender filters | **C** before filter gate; only `older_than_days` paths reach **D** |
| `export_emails` L170 | Manifest mild ("single_email supports message_id") | **C** on subject-only in **docstring** (`analytics.py` scope Arg) |
| `save_email_attachment` L117–118 | No ID-first workflow (`message_ids` + `attachment_index`) | Agents get no targeting guidance from manifest alone |

**Recommendations:**

1. Action tools: "`message_id` / `message_ids` required. `subject_keyword` / `sender` / `draft_subject` are schema-compat only → `TARGET_SELECTOR_DEPRECATED`. Discover via `search_emails` or `list_inbox_emails`."
2. `reply_to_email`: remove subject-scan paragraph; keep `native_format=true` default + Accessibility note.
3. Filter escape hatch: "`allow_filter_scan=True` + `older_than_days` only; subject/sender never work on action tools."
4. `save_email_attachment`: "Requires `message_ids` from prior list/search; use `list_email_attachments` to pick `attachment_index`."
5. Run `bash tools/dev-check.sh release`.

---

#### P0-3. Tool docstrings invite deprecated paths

**Files:** `plugin/apple_mail_mcp/tools/compose.py`, `manage.py`, `analytics.py`

| Location | Issue | Fix |
|----------|-------|-----|
| `reply_to_email` summary L2198 | "or subject keyword" | Lead with `message_id` required + deprecation |
| `reply_to_email` validation L2236 | `'subject_keyword' or 'message_id' is required` | `message_id is required (discover via search_emails...)` |
| `forward_email` Args L2754, validation L2773 | Same parity-implying pattern | Same as reply (opening line L2750 is neutral; Args are the trap) |
| `manage_drafts` `draft_subject` Arg L3244 | Implies working fallback | Mark schema-compat + `TARGET_SELECTOR_DEPRECATED` |
| `move_email` / `update_email_status` / `manage_trash` Args | "requires `allow_filter_scan=True`" for subject/sender | Subject/sender deprecated **regardless** of opt-in |
| `save_email_attachment` Arg L505 | No deprecation paragraph | Copy `list_email_attachments` pattern (`analytics.py` L76–83) |
| `export_emails` scope Arg L1251 | "requires subject_keyword or message_id" | Subject path → **C** |

**Good pattern to copy:** `list_email_attachments` docstring (`analytics.py` L76–83).

---

#### P0-4. README contradicts shipped policy

**File:** `README.md`

| Lines | Stale content | Fix |
|-------|---------------|-----|
| L318–320 | Filter-based `move_email` / `update_email_status` / `manage_trash` with `allow_filter_scan=True` **without** noting subject/sender deprecation | Clarify: opt-in is for **date/bulk** paths only |
| L337 | `list_email_attachments` "or subject keyword" | **C** at runtime |
| L393–394 | "`subject_keyword` is fallback only" / "Cayman-approved-only degraded reply fallback" | Hard-fail **C**; no approval path in code |
| L327 area | `reply_to_email` native format | Mostly accurate; align full tool table |

**Recommendations:** One ID-first paragraph in draft-safe section: discover → `message_id` → `reply_to_email(message_id=..., reply_body=...)`. Remove all "fallback only" language for action-tool `subject_keyword`.

---

### P1 — Agent workflow confusion (fix second)

#### P1-1. `email-drafting` contradicts itself on `subject_keyword`

**File:** `plugin/skills/email-drafting/SKILL.md`

| Lines | Text | Problem |
|-------|------|---------|
| L45 | `subject_keyword` on `reply_to_email` → `TARGET_SELECTOR_DEPRECATED` | **Correct** |
| L44, L56 | "degraded path" / "subject lookup" without naming discovery tool | Ambiguous |
| L70–71 | `reply_to_email` / `forward_email` rows: "`subject_keyword` is a degraded path" | Reads like passing keyword to action tool |

**Recommendation:** Replace every "degraded path" with: "**Discovery-only:** pass `subject_keyword` to `search_emails` or `list_inbox_emails`, then pass returned `message_id` to `reply_to_email` / `forward_email`. Never pass `subject_keyword` to action tools."

---

#### P1-2. Native reply guidance missing outside `email-drafting`

Files mentioning `reply_to_email` **without** `native_format` / Accessibility / `REPLY_WINDOW_FOCUS_FAILED`:

- `apple-mail-operator/SKILL.md`
- `inbox-triage/SKILL.md`
- `email-management/SKILL.md`
- `email-style-profile/SKILL.md`
- `search-patterns.md`, `thread-management.md`, `inbox-zero-workflow.md`
- `plugin/skills/CLAUDE.md` (routing index; **missed in first audit draft**)
- `common-workflows.md` L355 (mentions flatten path only)

**Also fix:** `docs/CLAUDE-conventions.md` L156 claims operator documents native reply; it does not.

**Recommendations:**

1. **`apple-mail-operator/SKILL.md`:** Add "Reply drafting handoff" (default `native_format=True`, Accessibility, `REPLY_WINDOW_FOCUS_FAILED` → retry visible Mail or `native_format=False`; link `email-drafting`).
2. **`inbox-triage/SKILL.md`:** End daily loop: "To draft, load `email-drafting`."
3. **`plugin/skills/CLAUDE.md`:** Add triage/operator → `email-drafting` native-reply note in routing cheat sheet.
4. Fix conventions L156 to match reality.

---

#### P1-3. `inbox-zero-workflow.md` quick-reference errors

**File:** `plugin/skills/email-management/examples/inbox-zero-workflow.md`

| Lines | Issue | Recommendation |
|-------|-------|----------------|
| L277 | "Create draft" → `manage_drafts(action="create")` | Valid API but **misroutes thread replies**; use `reply_to_email(message_id=..., mode="draft", reply_body=...)` for in-thread defer |
| L76–77 | `save_email_attachment` without `message_ids` | Add `message_ids=[...]` from prior step |

---

#### P1-4. Competing triage workflows (templates, not umbrella routing)

**Corrected finding:** `email-management/SKILL.md` **already** routes "what came in today" → `inbox-triage` (L60, L94). Real conflicts:

| Doc | Problem |
|-----|---------|
| `common-workflows.md` Morning Inbox (L7–26) | Keyword `search_emails` only; no `get_needs_response`, no `exclude_replied=True` |
| `examples/email-triage.md` | Legacy 10–15 min keyword-sweep; superseded by `inbox-triage` skill |

**Recommendations:**

1. Banner atop `examples/email-triage.md`: "Superseded by `inbox-triage` for daily use."
2. Align or replace Morning Inbox block in `common-workflows.md`.
3. Optional: one line in `email-management` decision tree reinforcing templates are not the skill contract.

---

#### P1-5. "Compose stack" wording

**File:** `plugin/skills/email-management/SKILL.md` (~L100, L138)

"compose stack" / "compose MCP stack" may steer agents to `compose_email` for thread replies.

**Recommendation:** "use `email-drafting` → `reply_to_email(message_id=...)` for thread replies; `compose_email` only for new standalone mail."

---

### P2 — Lower traffic but worth addressing

#### P2-1. Skill routing overlap

- Duplicate "Already-replied safeguard" blocks in operator, triage, management, drafting (~25 lines each).
- `email-style-profile` → `email-drafting` handoff stated but easy to skip.
- Single-email lookup: both `email-management` (L42) and `apple-mail-operator` mention "find email"; management correctly defers to operator in decision tree L66.

**Recommendation:** Keep full safeguard in `email-drafting`; elsewhere shorten to "See `email-drafting` pre-draft verification." Consider `references/pre-draft-verification.md` if deduplicating.

---

#### P2-2. Historical `tasks/` docs

| File | Stale content | Note |
|------|---------------|------|
| `tasks/scalability-24k-hardening-2026-05-22.md` | `move_email(sender=...)` co-filter advice | Pre-3.7; `sender=` now **C** |
| `tasks/id-first-refactor-spec.md` | **Corrected:** not reply `subject_keyword` fallback; stale **`get_email_thread` Path 2** subject fallback (still valid for read path) + filter-path matrix without `TARGET_SELECTOR_DEPRECATED` emphasis |
| `LIVE_FIELD_REPORT_2026-06-04.md` | `search_emails` on Drafts for verify/find | Historical anti-pattern; operator now forbids |

**Recommendation:** One-line stale banner on pre-3.7 task docs → `docs/CLAUDE-conventions.md` + this audit.

---

#### P2-3. `get_email_thread` subject fallback

**File:** `email-drafting/SKILL.md` L24

"account + subject signature when no id" is a last resort; L59 warns on large inboxes.

**Recommendation:** "If no `message_id`, run bounded `search_emails` first."

---

### P3 — Test and release hygiene

#### P3-1. Extend `test_id_first_guidance.py`

**Current scope:** `docs/CLAUDE-conventions.md` + `plugin/skills/**/*.md` only.

**Add checks for:**

- `README.md`: no working subject-keyword fallback on reply/forward/attachments without deprecation note; L318–320 filter-scan wording
- `apple-mail-mcpb/manifest.json`: no "or subject keyword" on action tools without `TARGET_SELECTOR_DEPRECATED`
- `common-workflows.md`:
  - no `sender_exact` / `sender_domain` on action-tool call blocks
  - no bare `move_email(` without `message_ids` or explicit `allow_filter_scan` + `older_than_days`
  - no `reply_to_email(..., body=`
  - no `save_email_attachment(..., message_id=`

**Why tests pass today:** regex matches `sender=` but not `sender_exact=`; bare `move_email` has no deprecated selector token.

---

#### P3-2. Brand-voice manifest sweep (deferred)

Pre-existing em dashes in ~10 shipped descriptions (`tasks/todo.md`). Separate pass; rebuild artifacts after.

---

## Symptom → cause → fix

| Symptom | Likely cause | Agent fix | Doc fix |
|---------|--------------|-----------|---------|
| Validation / unknown argument | `sender_exact` on action tool, `body=`, `message_id=` on attachment | Use correct param names; search on `search_emails` | P0-1 |
| "At least one filter is required" | Bare `move_email` from template | Collect `message_ids` first | P0-1 |
| `TARGET_SELECTOR_DEPRECATED` loop | `subject_keyword` or `sender` on action tool | Search → `message_id` / `message_ids` | P0-2, P0-3, P0-4, P1-1 |
| `FILTER_SCAN_DISABLED` | Date filter without `allow_filter_scan` | Use ids, or approved date scan only | P0-1, P0-2 |
| `REPLY_WINDOW_FOCUS_FAILED` | Native path, no focus/Accessibility | Visible Mail + retry, or `native_format=False` | P1-2 |
| Flat draft, no logo | `native_format=False` or flatten path | Default native; flatten only headless/CI | P1-2 |
| Cannot find draft | `search_emails` on Drafts | `verify_draft`, `manage_drafts(action="list")` | operator L72 |
| Detached reply | `compose_email` / `manage_drafts(create)` for thread | `reply_to_email(message_id=...)` | P0-1, P1-3 |

**Old bugs (clipboard races, wrong-thread paste, keyword bulk delete) should not recur** if agents obey structured errors. Remaining pain is UX: error loops, flat drafts, focus failures.

---

## Recommended implementation order

| Step | Work | Primary files | Verification |
|------|------|---------------|--------------|
| **1** | Repair copy-paste templates | `common-workflows.md`, `inbox-zero-workflow.md`, banner on `examples/email-triage.md` | Manual review + extended guidance tests |
| **2** | Honest deprecation in docstrings | `compose.py`, `manage.py`, `analytics.py` | Existing pytest; `mypy` |
| **3** | Manifest + README alignment | `apple-mail-mcpb/manifest.json`, `README.md` | `bash tools/dev-check.sh release` |
| **4** | Skill wording + handoffs | `email-drafting`, `apple-mail-operator`, `inbox-triage`, `plugin/skills/CLAUDE.md`, `CLAUDE-conventions.md` L156 | `plugin-dev:skill-reviewer` |
| **5** | Guidance test extension | `tests/test_id_first_guidance.py` | `pytest tests/test_id_first_guidance.py` (should **fail** on current `common-workflows.md` until step 1 lands) |
| **6** | Stale banners on historical tasks | `scalability-24k-hardening`, `id-first-refactor-spec`, `LIVE_FIELD_REPORT` | Optional |

**v3.x rule:** Keep deprecated schema params; document them as hard-fail. **v4 target:** `tasks/id-first-search-retirement-recommendations-2026-06-29.md`.

**Do not implement code behavior changes** for this audit unless product intent shifts; this is a **documentation and agent-training** fix pass.

---

## Related documents

| Document | Relevance |
|----------|-----------|
| `tasks/id-first-search-retirement-recommendations-2026-06-29.md` | v4 schema removal plan |
| `tasks/native-reply-handoff-2026-06-30.md` | Native reply ship + live TO-TEST |
| `tasks/reply-draft-pr38-review-findings-2026-06-30.md` | PR #38 verification context |
| `docs/CLAUDE-conventions.md` | Canonical policy (fix L156 operator claim) |
| `plugin/skills/email-management/templates/search-patterns.md` | Template gold standard |
| `CHANGELOG.md` 3.8.0 | `native_format=True` default, `REPLY_WINDOW_FOCUS_FAILED` |

---

## Audit provenance

| Phase | Focus |
|-------|-------|
| Initial audit (2026-06-30) | Skills routing, deprecated patterns, tool-doc vs policy, branch vs `main` |
| Verification pass 1 | P0-1 templates + runtime taxonomy (`common-workflows.md`, `manage.py`, `compose.py`) |
| Verification pass 2 | P1–P2 skills, routing, native_format, historical docs |
| Verification pass 3 | P0-2/3/4 manifest, README, docstrings, `test_id_first_guidance.py` gaps |

**Branch context:** v3.8.0 adds `native_format=True` default on `reply_to_email`. Multiset attachment verification and `tools/expected_test_count.txt` gate are orthogonal to this audit.
