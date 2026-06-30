# Agent Guidance Audit — Apple Mail MCP

**Date:** 2026-06-30  
**Branch reviewed:** `codex/pr38-guidance-verifier-followup` (v3.8.0 native-reply release)  
**Scope:** Agent-facing training surface for coding agents using the plugin (skills, MCP tool descriptions, README, templates, conventions)  
**Sources:** Three read-only audits (skills routing, deprecated-pattern scan, tool-docstring vs policy gaps) plus branch-vs-`main` review  

---

## Executive overview

Apple Mail MCP is **safer at runtime than its documentation suggests**. Code guardrails from v3.6–3.8 (ID-first mutations, `TARGET_SELECTOR_DEPRECATED`, native reply default, compose race fixes) block the worst historical failure modes: clipboard reply races, keyword-targeted bulk deletes, and unbounded inbox scans.

The remaining risk is **agent confusion**, not silent data corruption. Coding agents that load bundled skills and follow `email-drafting` + `search-patterns.md` generally get the right workflow. Agents that read **only MCP tool schemas**, **README tool tables**, or **copy-paste templates** under `email-management/templates/` are likely to:

1. Call action tools with deprecated selectors and loop on structured errors.
2. Use filter-only `move_email` without `message_ids` and chase `allow_filter_scan=True` on large mailboxes.
3. Draft replies without `native_format` / Accessibility context and hit `REPLY_WINDOW_FOCUS_FAILED` or fall back to flat, logo-less drafts.
4. Use wrong parameter names (`body=` instead of `reply_body`) and break threading by falling back to standalone compose tools.

**Bottom line:** Fix the **training surface** (templates, manifest, README, docstrings, operator skill handoff). Runtime behavior is already correct for the deprecated paths; do not remove schema params until v4.

---

## What works well (keep as canonical)

| Asset | Role |
|-------|------|
| `docs/CLAUDE-conventions.md` | Strong policy: ID-first, `native_format`, deprecation contracts |
| `plugin/skills/email-drafting/SKILL.md` | Native reply, verification, standalone-draft guards (minor internal wording fix needed) |
| `plugin/skills/email-management/templates/search-patterns.md` | Best copy-paste reference: discovery → `message_id` → action |
| `plugin/skills/references/large-inbox-rules.md` | ID-first mutations, scan caps |
| `plugin/skills/email-archive-cleanup/SKILL.md` | Staged cleanup with explicit deprecation warnings |
| `plugin/skills/inbox-triage/SKILL.md` | Read-first triage; correct archive handoff |
| `tests/test_id_first_guidance.py` | Static guard on skills + conventions (incomplete coverage; see P4) |
| Runtime structured errors | `TARGET_SELECTOR_DEPRECATED`, `FILTER_SCAN_DISABLED`, `REPLY_WINDOW_FOCUS_FAILED` |

**Tool count unchanged (31).** No tools should be removed; several schema params are v3.x compatibility stubs until v4.

---

## Priority-ranked issues

Severity: **P0** = agents copy this and fail or mis-route often · **P1** = confusion under common workflows · **P2** = edge routing / missing handoff · **P3** = historical or low-traffic docs

---

### P0 — Copy-paste traps (fix first)

#### P0-1. `common-workflows.md` teaches broken action-tool calls

**File:** `plugin/skills/email-management/templates/common-workflows.md`

| Location (approx.) | Broken pattern | Runtime outcome | Why it matters |
|--------------------|----------------|-----------------|----------------|
| L41, L846, L873 | `move_email(to_mailbox=..., from_mailbox=...)` without `message_ids` or `allow_filter_scan` | `FILTER_SCAN_DISABLED` | Agents treat templates as executable recipes during inbox-zero / archive flows |
| L217–224 | `move_email(..., older_than_days=30, only_read=True)` without `allow_filter_scan` | `FILTER_SCAN_DISABLED` | Same; encourages slow-scan escape hatch on 24k mailboxes |
| L457–478 | `manage_trash(..., sender_exact=...)` | Invalid param / `TARGET_SELECTOR_DEPRECATED` class | `manage_trash` has no `sender_exact`; contradicts `large-inbox-rules.md` |
| L714–719 | `update_email_status(..., sender_domain=...)` | `TARGET_SELECTOR_DEPRECATED` | Same |
| L736–742 | `move_email(sender_exact=...)` | `TARGET_SELECTOR_DEPRECATED` | Same |
| L167, L797 | `save_email_attachment(message_id=...)` | Wrong param name | Correct param is `message_ids=[...]` |
| L591–596 | `reply_to_email(..., body=...)` | Wrong param / empty body | Correct param is `reply_body=`; agents may fall back to `compose_email` |

**Contrast:** Quick Reply / Deferred Response blocks (L305–360) correctly use `search_emails` → `message_id` → `reply_to_email`.

**Recommendation:**

1. Replace every filter-only `move_email` block with: bounded `search_emails` or `list_inbox_emails` → collect `message_ids` → `move_email(dry_run=True, message_ids=[...], ...)`.
2. Replace sender/subject action selectors with discovery on `search_emails(sender_exact=..., sender_domain=..., subject_keyword=...)` then `message_ids` mutations.
3. Fix `reply_body=` and `message_ids=` on attachment saves throughout the file.
4. Add a file header pointer: "For action tools, follow `search-patterns.md`; subject/sender filters are discovery-only."

---

#### P0-2. MCP manifest advertises working subject-keyword reply/forward

**File:** `apple-mail-mcpb/manifest.json` (and mirrored tool summaries in shipped artifacts)

**Problem:** `reply_to_email` and `forward_email` descriptions say "by message_id (preferred) **or subject keyword**" with subject-scan details. Runtime returns `TARGET_SELECTOR_DEPRECATED` when only `subject_keyword` is passed.

**Also affected:** `list_email_attachments`, `manage_drafts`, `move_email` / `update_email_status` / `manage_trash` (imply filter targeting still works on subject/sender), `export_emails` single-email scope.

**Why it matters:** Claude Desktop, Cowork, and MCP-only hosts surface manifest text as the tool contract. Agents never load skills.

**Recommendation:**

1. Change action-tool manifest lines to: "`message_id` / `message_ids` required; `subject_keyword` schema-compat only → `TARGET_SELECTOR_DEPRECATED`. Discover via `search_emails` first."
2. For `manage_drafts` send/open/delete: "`draft_id` required; `draft_subject` → `TARGET_SELECTOR_DEPRECATED`."
3. For filter scans: "`allow_filter_scan` + `older_than_days` only; subject/sender selectors deprecated."
4. Rebuild artifacts after manifest edit (`bash tools/dev-check.sh release`).

---

#### P0-3. Tool docstrings invite deprecated paths

**Files:** `plugin/apple_mail_mcp/tools/compose.py`, `manage.py`, `analytics.py`

**Problem:** `reply_to_email` / `forward_email` summaries and `subject_keyword` Arg lines read like live fallbacks. Validation string: `Error: 'subject_keyword' or 'message_id' is required` implies parity.

**Good pattern to copy:** `list_email_attachments` docstring explicitly marks `subject_keyword` as deprecated schema-only.

**Recommendation:**

1. First line of reply/forward docstrings: "`message_id` required. `subject_keyword` is schema-compatible only and returns `TARGET_SELECTOR_DEPRECATED`."
2. Change required-field error to: "`message_id` is required (discover via `search_emails` or `list_inbox_emails`)."
3. Mark `draft_subject` on `manage_drafts` send/open/delete the same way.
4. Align `move_email` / `update_email_status` / `manage_trash` docstrings: subject/sender kwargs deprecated regardless of `allow_filter_scan`.

---

#### P0-4. README contradicts shipped deprecation policy

**File:** `README.md`

| Lines (approx.) | Issue |
|-----------------|-------|
| L337 | `list_email_attachments` — "or subject keyword" on action path |
| L393–394 | "`subject_keyword` is fallback only" / "Cayman-approved-only degraded reply fallback" |

**Problem:** Implies subject-keyword reply still works with approval. Code hard-fails with `TARGET_SELECTOR_DEPRECATED`.

**Recommendation:**

1. Tool table: "`message_ids` required; `subject_keyword` deprecated on action tools."
2. Draft-safe section: replace fallback language with "run `search_emails` / `list_inbox_emails`, then `reply_to_email(message_id=...)`."
3. Mention `native_format=True` default and Accessibility requirement in reply row (already partially in L327 area; align full table).

---

### P1 — Agent workflow confusion (fix second)

#### P1-1. `email-drafting` internal contradiction on `subject_keyword`

**File:** `plugin/skills/email-drafting/SKILL.md`

- L45: correct — `subject_keyword` on `reply_to_email` → `TARGET_SELECTOR_DEPRECATED`.
- L56, L70–71: "Use `subject_keyword` only as a degraded path" reads like passing it to the action tool.

**Recommendation:** Replace "degraded path" with "discovery-only: pass `subject_keyword` to `search_emails` or `list_inbox_emails`, never to `reply_to_email` / `forward_email`."

---

#### P1-2. Native reply guidance missing outside `email-drafting`

**Files:** `apple-mail-operator/SKILL.md`, `inbox-triage/SKILL.md`, `email-management/SKILL.md`, `search-patterns.md`, `thread-management.md`, `inbox-zero-workflow.md`, `email-style-profile/SKILL.md`

**Problem:** These mention `reply_to_email` (especially already-replied safeguards) but not:

- Default `native_format=True` (rich quote + logo signature)
- macOS Accessibility permission for host process
- `REPLY_WINDOW_FOCUS_FAILED` (no draft saved)
- Recovery: visible Mail + retry, or `native_format=False` for headless/bulk/CI

**Why it matters:** Triage and operator skills explicitly route drafting away from their scope, yet embed pre-reply checks that invite calling `reply_to_email` without loading `email-drafting`.

**Recommendation:**

1. **`apple-mail-operator/SKILL.md`:** Add "Reply drafting handoff" subsection (5–8 lines) with native format + focus + link to `email-drafting`.
2. **`inbox-triage/SKILL.md`:** End daily loop with: "To draft a reply, load `email-drafting` (native path needs Mail focus)."
3. Optional one-liner cross-links in `search-patterns.md` and `thread-management.md` reply examples.

---

#### P1-3. `inbox-zero-workflow.md` quick-reference errors

**File:** `plugin/skills/email-management/examples/inbox-zero-workflow.md`

| Issue | Recommendation |
|-------|----------------|
| L277: "Create draft" → `manage_drafts(action="create")` | Change to `reply_to_email(message_id=...)` for thread replies |
| L76–77: `save_email_attachment` without `message_ids` | Add `message_ids=[...]` from prior search/list step |

---

#### P1-4. Competing triage workflows

| Doc | Risk |
|-----|------|
| `inbox-triage` (5–10 min, read-first) vs `email-management` daily triage (15–30 min, mutations) | "Check my email" may load wrong skill |
| `common-workflows.md` "Morning Inbox Check" uses keyword `search_emails` only | Misses `get_needs_response` + `exclude_replied=True` |
| `examples/email-triage.md` | Legacy keyword-sweep pattern |

**Recommendation:**

1. `email-management/SKILL.md`: strengthen routing table — "morning scan / check email" → `inbox-triage`, not this umbrella.
2. Update Morning Inbox Check in `common-workflows.md` to mirror `inbox-triage` loop or link to it explicitly.
3. Add banner atop `examples/email-triage.md`: "Superseded by `inbox-triage` skill for daily use."

---

#### P1-5. "Compose stack" wording

**File:** `plugin/skills/email-management/SKILL.md` (~L138)

**Problem:** "use email-drafting (compose stack)" may steer agents to `compose_email` instead of `reply_to_email`.

**Recommendation:** Replace with "use `email-drafting` → `reply_to_email(message_id=...)` for thread replies; `compose_email` only for new standalone mail."

---

### P2 — Lower traffic but worth addressing

#### P2-1. Skill routing overlap

- `email-management` vs `apple-mail-operator` for single-email lookup (both mention "find email").
- `email-style-profile` → must hand off to `email-drafting` before reply (stated but easy to skip).
- Structural duplication: identical "Already-replied safeguard" block in operator, triage, management, drafting.

**Recommendation:** Keep safeguard in `email-drafting` + `references/`; shorten copies elsewhere to "See email-drafting pre-draft verification."

---

#### P2-2. Historical `tasks/` docs

| File | Issue |
|------|-------|
| `tasks/scalability-24k-hardening-2026-05-22.md` | `move_email(sender=...)` examples |
| `tasks/id-first-refactor-spec.md` | Subject fallback as active reply path |
| `LIVE_FIELD_REPORT_2026-06-04.md` | `search_emails` to verify drafts |

**Recommendation:** Add one-line stale banners at top of pre-3.7 task docs pointing to `docs/CLAUDE-conventions.md` and this audit. Do not delete (history).

---

#### P2-3. `get_email_thread` subject fallback wording

**File:** `email-drafting/SKILL.md` L24

Allows "account + subject signature" when no id. Large-inbox warning exists (L59) but agents may skip search step.

**Recommendation:** Prefer "if no `message_id`, run bounded `search_emails` first; subject-only thread resolution is a last resort."

---

### P3 — Test and release hygiene

#### P3-1. Extend `test_id_first_guidance.py`

**Gap:** Guards `plugin/skills/**/*.md` and `docs/CLAUDE-conventions.md` only.

**Recommendation:** Add checks for:

- `README.md` — no "subject keyword fallback" on reply/forward without deprecation note
- `apple-mail-mcpb/manifest.json` — no "or subject keyword" on action tools without `TARGET_SELECTOR_DEPRECATED`
- `common-workflows.md` — no `move_email(` without `message_ids` or documented `allow_filter_scan`; no `sender_exact` on action tools

---

#### P3-2. Brand-voice manifest sweep (deferred)

Pre-existing em dashes in ~10 shipped descriptions (`tasks/todo.md`). Separate pass; rebuild artifacts after.

---

## What agents can still get wrong (symptom → cause → fix)

| Symptom | Likely cause | Agent fix | Doc fix (this audit) |
|---------|--------------|-----------|----------------------|
| `TARGET_SELECTOR_DEPRECATED` loop on reply | Passed `subject_keyword` to `reply_to_email` | Search first, pass `message_id` | P0-2, P0-3, P0-4, P1-1 |
| `FILTER_SCAN_DISABLED` then slow timeouts | Copied filter-only `move_email` from template | Collect ids, `move_email(message_ids=[...])` | P0-1 |
| Empty reply / threading break | Used `body=` or `compose_email` for reply | `reply_to_email(message_id=..., reply_body=...)` | P0-1, P1-3 |
| `REPLY_WINDOW_FOCUS_FAILED` | Native path without focus/Accessibility | Retry with Mail visible or `native_format=False` | P1-2 |
| Flat draft, no logo signature | Agent set `native_format=False` or used flatten path | Default native; flatten only for headless | P1-2 |
| Cannot find draft after reply | Searched with `search_emails` | `verify_draft` / `manage_drafts(action="list")` | Already documented; reinforce in operator |
| Duplicate / detached reply draft | `manage_drafts(create)` or `compose_email` for thread | `reply_to_email(message_id=...)` | P0-1, P1-3 |

**Old bugs (clipboard races, wrong-window paste, keyword bulk delete) should not recur** if agents obey structured errors. **New-era bugs** are mostly UX: flat drafts, focus failures, error loops from stale docs.

---

## Recommended implementation order

| Step | Work | Files | Verification |
|------|------|-------|--------------|
| 1 | Repair copy-paste templates | `common-workflows.md`, `inbox-zero-workflow.md` | Manual review; extend `test_id_first_guidance.py` |
| 2 | Honest deprecation in tool surface | `compose.py`, `manage.py`, `analytics.py` docstrings | `mypy`/existing tests |
| 3 | Manifest + README alignment | `apple-mail-mcpb/manifest.json`, `README.md` | `bash tools/dev-check.sh release` |
| 4 | Skill wording + handoffs | `email-drafting`, `apple-mail-operator`, `inbox-triage`, `email-management` | `plugin-dev:skill-reviewer` |
| 5 | Guidance test extension | `tests/test_id_first_guidance.py` | `pytest tests/test_id_first_guidance.py` |
| 6 | Stale banners on historical tasks | `tasks/scalability-*`, `id-first-refactor-spec`, field report | Optional |

**Do not** remove deprecated schema params in v3.x. **Do** make every layer tell the same story: discovery tools find ids; action tools consume ids; native reply is default; flatten path is explicit opt-in.

---

## Related documents

| Document | Relevance |
|----------|-----------|
| `tasks/id-first-search-retirement-recommendations-2026-06-29.md` | v4 policy target |
| `tasks/native-reply-handoff-2026-06-30.md` | Native reply ship status + live TO-TEST |
| `tasks/reply-draft-pr38-review-findings-2026-06-30.md` | PR #38 verification context |
| `docs/CLAUDE-conventions.md` | Canonical agent policy |
| `plugin/skills/email-management/templates/search-patterns.md` | Template gold standard |
| `CHANGELOG.md` 3.8.0 | Native reply release notes |

---

## Audit provenance

| Audit | Focus |
|-------|-------|
| Skills routing & template contradictions | Nine skills, references, templates |
| Deprecated-pattern scan | Skills, docs, README, manifest, tests |
| Tool docstrings vs policy | `compose.py`, manifest, conventions, skill frontmatter |

**Branch context:** v3.8.0 adds `native_format=True` default on `reply_to_email`; `main` at merge of PR #38 lacked this release. Uncommitted work on branch may include multiset attachment verification and `tools/expected_test_count.txt` gate (orthogonal to this audit).
