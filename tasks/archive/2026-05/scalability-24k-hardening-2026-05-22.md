# Scalability Hardening — 24K-Mailbox Robustness Plan

> **Stale (pre-3.7):** Examples that pass `sender=` or `subject_keyword=` to action tools are obsolete; those selectors return `TARGET_SELECTOR_DEPRECATED`. See [`docs/CLAUDE-conventions.md`](../../docs/CLAUDE-conventions.md) and [`../active/agent-guidance-audit/agent-guidance-audit-2026-06-30.md`](../active/agent-guidance-audit/agent-guidance-audit-2026-06-30.md).

**Date:** 2026-05-22
**Branch:** `feat/apple-mail-plugin-robustness`
**Target version:** 3.1.9
**Bar:** Every tool must operate within a reasonable budget on a 24,000-email Apple Mail inbox. **No tool may trigger a full-inbox scan implicitly.** When the caller has an `email_id` / `message_id` / `thread_id`, follow-up actions must use the ID — never re-search by subject text.

---

## Why now

Field testing against a 24K-message TU account showed:

- `move_email` with `sender` only times out; only `subject_keyword` is reliable
- `get_awaiting_reply` times out
- `get_needs_response` surfaces noise without bounds discipline
- `inbox_dashboard` exists but is undocumented in skills
- `list_inbox_emails` was invoked with wrong param names (`limit` / `unread_only`); they appeared to be silently dropped

Cross-cut anti-pattern to eliminate: **tools that have a message ID re-searching the whole inbox by subject text.** That re-pays the scan cost every time and turns a constant-cost operation into a 24K-message scan.

---

## Findings synthesized from research agents

### Real code defects (highest leverage)

| # | File:Line | Severity | Issue |
|---|-----------|----------|-------|
| 1 | `compose.py:67-68` | **HIGH** | `_build_message_lookup`: `items 1 thru N of (every message of mailbox whose subject contains "X")` — Mail.app materializes the entire mailbox when `recent_days=0` and no date bound is in `whose_parts`. Falls through silently. |
| 2 | `compose.py:88-89` | **HIGH** | `_build_draft_lookup`: same anti-pattern over `draftsMailbox`. Drafts mailbox is usually small but the pattern is wrong. |
| 3 | `analytics.py:362` (`get_statistics`) | **MEDIUM** | `days_back=0` (all-time) is documented as supported but lacks the `allow_full_scan=True` gate that `list_inbox_emails` / `search_emails` enforce. |
| 4 | `smart_inbox.py:708` (`get_top_senders`) | **MEDIUM** | Same `days_back=0` gap. |
| 5 | `smart_inbox.py:332` (`get_awaiting_reply`) | **MEDIUM** | Default `days_back=7` is fine, but no error envelope tells the agent to drop `days_back` lower when a timeout occurs. (Code already returns a friendly message; skills don't teach it.) |
| 6 | `analytics.py:1227` (`inbox_dashboard`) | **LOW** | Tight enough on defaults, but no `allow_full_scan` gate when caller passes large `max_per_account`. |
| 7 | `inbox.py:331` (`list_inbox_emails`) | **DEFENSIVE** | Accepts only the canonical param names; agents frequently misremember `limit` / `unread_only`. Add aliases + structured warning so the call still works and the agent learns the right names. |

### Skill gaps (also high impact — agents implement workflows, not just call tools)

- No skill teaches a "large-inbox pre-flight" mental model
- `inbox_dashboard` is essentially undocumented as the rescue path when overview-style tools time out
- `get_awaiting_reply` timeout fallback (lower `days_back`) is not taught
- `move_email(sender=…)` co-filter requirement (must pair with `subject_keyword` or tight `recent_days`) is not taught
- `list_inbox_emails(include_read=False, …)` is never demonstrated, so agents default to inventing `unread_only`
- ID-discipline ("never re-search by subject when you have an ID") is in some skills but not enforced as a top-level rule

### Manifest

- All five version files at 3.1.8, 27 tools, MCPB tools[] in sync — release-ready but we're bumping to **3.1.9** for the hardening.
- One nit on `apple-mail-mcpb/manifest.json` `export_emails` wording (`entire mailbox capped at max_emails`) — clean up to "up to max_emails messages from a mailbox (default 1000)".

### What's NOT broken (verified, do not change)

- `_search_mail_records` already bounds sender scans via `limit+offset` slice — `move_email(sender=…)` is not unbounded in code; the timeout the user observed is the 120s `run_applescript` default coupled with Mail.app's true `whose sender` traversal cost on 24K. Mitigation is to require a co-filter (skill rule) and to expose adaptive timeouts (already supported via `timeout` arg; surface it in skills).
- Array-bounds claims in `search.py` / `inbox.py` / `analytics.py` from prior audits are false positives — guards exist.
- AppleScript repeat loops in `analytics.py` / `manage.py` / `smart_inbox.py` / `search.py` use pre-sliced message lists, not raw `messages of inbox`. Confirmed safe.

---

## Phases

### Phase 0 — Plan (this document)

Write this file and commit. No code changes yet.

| Step | Tool / Agent |
|------|--------------|
| Draft plan | (lead — synthesizing research outputs) |

### Phase 1 — Eliminate full-inbox traps in `compose.py`

Fix the two HIGH issues by requiring a bounded `whose` clause whenever a message lookup falls back from `message_id` to `subject_keyword`. ID path stays first-class; subject path is the bounded fallback.

**Concrete edits:**

1. `compose.py:_build_message_lookup` — make the `whose` clause **always** include a date bound. If `recent_days <= 0`, raise an error to the caller telling them to either supply `message_id` or set `recent_days > 0` (or set `allow_full_scan=True`).
2. Same change in `_build_draft_lookup` (or accept a `recent_days` arg and gate identically).
3. Verify callers in `reply_to_email`, `forward_email`, `manage_drafts` pass through a `recent_days` arg or default to `2.0` (matching `search_emails`).
4. Add `allow_full_scan: bool = False` knob on `reply_to_email`, `forward_email`, `manage_drafts` so users can opt out when needed.

**Tests:**
- Assert the generated AppleScript snippet always contains `date received >=` when `message_id` is not provided.
- Assert error returned when `recent_days=0` without `allow_full_scan=True`.

| Step | Agent / Skill |
|------|---------------|
| Edits | lead (direct Edit) — file is large, surgical changes |
| Tests | `generalPurpose` subagent — author pytest cases under `tests/test_mail_compose_tools.py` |
| Review | `plugin-dev:plugin-validator` after edits |

### Phase 2 — Add `allow_full_scan` gate to `get_statistics` & `get_top_senders`

Mirror the `list_inbox_emails` / `search_emails` convention.

**Concrete edits:**

1. `analytics.py:get_statistics` — when `days_back <= 0`, require `allow_full_scan=True`. Add the parameter to the signature, threading through.
2. `smart_inbox.py:get_top_senders` — same.
3. Update docstrings + MCPB manifest descriptions to mention the gate.

**Tests:**
- One pytest per tool asserting the error message when `days_back=0` without the gate, and success when gated.

| Step | Agent / Skill |
|------|---------------|
| Edits | lead (direct Edit) |
| Tests | `generalPurpose` subagent |
| Review | `plugin-dev:plugin-validator` |

### Phase 3 — `list_inbox_emails` alias + warning for legacy param names

Make the function tolerant of `limit` / `unread_only` so agents that misremember don't fail silently; emit a `warnings[]` entry in JSON / a `WARNING:` prefix in text mode so the agent learns the canonical names.

**Concrete edits:**

1. `inbox.py:list_inbox_emails` — accept `limit: Optional[int] = None`, `unread_only: Optional[bool] = None`. If provided, map to `max_emails` / `include_read=not unread_only` AND record a warning.
2. Plumb the warning into JSON `output_format="json"` (add to top-level) and text mode (prefix line `WARNING: 'limit' is deprecated — use 'max_emails'`).

**Tests:**
- Assert call with `limit=5` returns 5 emails and emits the warning.
- Assert call with `unread_only=True` is equivalent to `include_read=False`.

| Step | Agent / Skill |
|------|---------------|
| Edits | lead |
| Tests | `generalPurpose` subagent |

### Phase 4 — Manifest wording polish + version bump to 3.1.9

1. `apple-mail-mcpb/manifest.json` — reword `export_emails` description.
2. Bump version to `3.1.9` in all 5 manifest files:
   - `pyproject.toml`
   - `plugin/.claude-plugin/plugin.json`
   - `.claude-plugin/marketplace.json` `plugins[0].version`
   - `server.json` (top-level + `packages[0].version`)
   - `apple-mail-mcpb/manifest.json`
3. Update `apple-mail-mcpb/manifest.json` `tools[]` descriptions for `get_statistics`, `get_top_senders`, `reply_to_email`, `forward_email`, `manage_drafts`, `list_inbox_emails` to reflect new behaviors.
4. Rebuild `apple-mail-plugin.zip` and `apple-mail-mcp-v3.1.9.mcpb`.

| Step | Agent / Skill |
|------|---------------|
| Edits + rebuild | lead |
| Review | `plugin-dev:plugin-validator` (post) |

### Phase 5 — Skill upgrades

Add the shared **"Large-inbox pre-flight"** block to four skills, promote `inbox_dashboard` to a documented rescue path, and tighten examples.

**Concrete edits:**

1. Add pre-flight block (per skill-reviewer's suggested copy) to:
   - `plugin/skills/inbox-triage/SKILL.md`
   - `plugin/skills/email-management/SKILL.md`
   - `plugin/skills/email-archive-cleanup/SKILL.md`
   - `plugin/skills/apple-mail-operator/SKILL.md`
2. Rewrite `inbox_dashboard` positioning in `apple-mail-operator/SKILL.md`; cross-link from `inbox-triage` and `email-management`.
3. In `email-archive-cleanup/SKILL.md` and `email-management/SKILL.md`, mark `move_email(sender=…)` calls as requiring `subject_keyword=` co-filter OR `recent_days` ceiling OR `message_ids=[…]` path.
4. Add ID-discipline callout: "Once you have a `message_id` from search/list, use `get_email_by_id` / `move_email(message_ids=[…])` / `reply_to_email(message_id=…)`. Never re-search by subject."
5. Show `list_inbox_emails(max_emails=25, include_read=False)` in at least one example per affected skill.
6. Minor edits in `email-drafting`, `email-attachments`, `mail-rules-advisor`, `mailbox-taxonomy`, `email-style-profile` per skill-reviewer findings.

| Step | Agent / Skill |
|------|---------------|
| Edits | `generalPurpose` subagent (in parallel across skills) |
| Review | `plugin-dev:skill-reviewer` after edits |

### Phase 6 — Verification gates (all must pass)

Run sequentially after Phases 1–5 complete:

1. `bash tools/validate_manifests.sh` → exit 0, version=3.1.9, tools=27
2. `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh` → exit 0
3. `.venv/bin/pytest tests/ -q` → at least 276 passing + any new tests pass
4. `python3 tools/check_wrapper_surface.py` → exit 0
5. `claude plugin validate ./plugin` → ✔
6. `claude plugin validate .` → ✔
7. `plugin-dev:plugin-validator` agent — final pass
8. `plugin-dev:skill-reviewer` agent — final pass
9. (Best-effort) `.venv/bin/apple-mail quick-check --json` on 24K TU account — confirm metadata ops still ≤ thresholds

| Step | Agent / Skill |
|------|---------------|
| Run gates | lead (Bash) |
| Final agent pass | `plugin-dev:plugin-validator` + `plugin-dev:skill-reviewer` in parallel |
| Live | `shell` subagent if needed |

### Phase 7 — Commit & push

One commit with all robustness changes; push to existing branch.

| Step | Agent / Skill |
|------|---------------|
| Commit + push | lead |

---

## Out-of-scope (defer to backlog)

These were identified by research agents but are bigger-than-one-PR and not blocking the 24K bar:

- **Session-level caches** (mailbox-handle cache, sent-mailbox replied-ids cache, message-ID result cache) — would unlock another ~3x speedup on bulk operations but require careful invalidation. Park in `tasks/reference/robustness-backlog-2026-05-22.md` as **v3.2.0 candidate**.
- **Per-account adaptive timeouts** in `core.run_applescript` (e.g. learn slow accounts, raise default for Exchange). Park as v3.2.0.
- **New tools** the user proposed (`get_email_by_sender_summary`, `mark_thread_done`) — sensible but additions not robustness. Park as v3.2.0 backlog.
- **`save_email_attachment` ID-resolve redundancy** (manage.py:390-406) — LOW severity, functional. Park.

---

## Acceptance criteria

- All Phase 6 gates green
- No `@mcp.tool` introduces an unbounded `whose` clause on inbox-wide collections without an explicit `allow_full_scan=True` gate
- All four target skills carry the pre-flight block verbatim
- `inbox_dashboard` is documented as the rescue path in at least one skill
- `list_inbox_emails(limit=5)` works and surfaces a warning
- Manifests at 3.1.9 across 5 files; zip + mcpb rebuilt
- Branch pushed; ready for PR
