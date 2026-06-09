# Audit: `allow_full_scan` and scan escape hatches

**Date:** 2026-06-09  
**Repo version audited:** `3.7.1` (`pyproject.toml`)  
**Trigger:** Cursor agent called `search_emails` with `allow_full_scan=true` alongside `date_from=2026-03-01`, `recent_days=0`, `mailbox=All`, `all_accounts=true`, `sender=Emily.Irvine@taylorandfrancis.com`, `limit=50`.  
**Scope:** Read-only audit. No code changes.

---

## Executive summary

1. **`allow_full_scan` is not in current source (v3.2.0+).** It was retired in favor of structured `UNBOUNDED_SCAN_REQUIRED` errors and a separate `full_inbox_export` tool. CI enforces this (`tests/test_no_unbounded_whose.py`).

2. **Your Cursor session is almost certainly running a stale tool schema (plugin v3.1.7).** Cached MCP descriptors under `.cursor/projects/.../mcps/plugin-apple-mail-apple-mail/tools/` still advertise `allow_full_scan` on three tools. The Cursor plugin cache at `~/.cursor/plugins/cache/apple-mail-mcp/apple-mail/7907cd8.../` is also **v3.1.7** and still contains `allow_full_scan` in Python source and skills.

3. **The agent’s parameter combo was contradictory but explainable.** Stale docs say `recent_days=0` requires `allow_full_scan=True`. The agent also passed `date_from`, which (even in old code) **bypasses** the `allow_full_scan` gate. So `allow_full_scan=true` was unnecessary noise, likely copied from outdated tool descriptions.

4. **Other opt-in scan gates still exist** and are intentional: `allow_filter_scan`, `allow_body_scan`, `apply_to_all`, and the dedicated `full_inbox_export` tool. These are separate from the retired `allow_full_scan` knob.

---

## What happened on your specific call

### Parameters observed

| Parameter | Value |
|-----------|-------|
| `sender` | `Emily.Irvine@taylorandfrancis.com` |
| `date_from` | `2026-03-01` |
| `recent_days` | `0` |
| `allow_full_scan` | `true` |
| `mailbox` | `All` |
| `all_accounts` | `true` |
| `limit` | `50` |
| `include_content` | `false` |
| `output_format` | `json` |

### Why `allow_full_scan=true` appeared

The Cursor MCP tool descriptor for `search_emails` (stale) says:

- `recent_days=0` requires `allow_full_scan=True`
- Docstring lists `allow_full_scan` as a formal parameter (default `false`)

An agent seeing `recent_days=0` in its plan will often set `allow_full_scan=true` even when it also sets `date_from`, because the schema text does not clearly say “when `date_from` is set, ignore `recent_days` and do not use `allow_full_scan`.”

### What the gate actually does (old vs new)

**Old behavior (v3.1.7, still in Cursor cache)** — `search_emails` in  
`~/.cursor/plugins/cache/.../apple_mail_mcp/tools/search.py`:

```python
if date_from is None and effective_recent_days <= 0 and not allow_full_scan:
    # error: full_scan_requires_opt_in
```

So with **`date_from` set**, `allow_full_scan` is **not consulted** for the unbounded-window check. Setting it to `true` had no effect on whether the call was allowed.

**Current behavior (v3.7.1)** — `plugin/apple_mail_mcp/tools/search.py`:

```python
if date_from is None and effective_recent_days <= 0:
    # UNBOUNDED_SCAN_REQUIRED → remediation.fallback_tool = full_inbox_export
```

Again, **`date_from` present → gate does not fire**. There is no `allow_full_scan` parameter at all.

### Was this a “full scan” anyway?

**No unbounded full-mailbox walk**, even with stale runtime, because:

- Explicit `date_from` bounds the AppleScript date filter.
- `limit=50` caps returned results.
- With `recent_days=0`, scan slice size uses `scan_cap = limit + offset + 1` (roughly **51 messages per mailbox slice** in current code), not the entire mailbox.

**Still expensive** (but bounded):

- `mailbox="All"` → up to **10 mailboxes per account** (`SCAN_BOUNDS["MAX_MAILBOXES_PER_SEARCH_ALL"]`).
- `all_accounts=true` → parallel fan-out across every Mail account.
- Sender-only filter over `mailbox=All` + multi-account is a known slow pattern (documented in tool docstrings).

So the call was **wide and slow-prone**, not an unbounded “walk everything” scan. The scary part is the agent **believed** it needed a full-scan opt-in because of stale schema text.

---

## `allow_full_scan` in current source (v3.7.1)

### Present in production Python? **No**

| Location | `allow_full_scan` |
|----------|-------------------|
| `plugin/apple_mail_mcp/tools/*.py` | **None** |
| `plugin/skills/**` | **None** |
| `apple-mail-mcpb/manifest.json` | **None** |
| `README.md` (current) | **None** (points to `UNBOUNDED_SCAN_REQUIRED` / `full_inbox_export`) |
| `docs/CLAUDE-conventions.md` | Documented as **retired v3.2.0** |

Enforcement:

- `tests/test_no_unbounded_whose.py::test_no_allow_full_scan_in_tools`
- `tests/test_no_unbounded_whose.py::test_tool_signatures_have_no_allow_full_scan_param`
- `tests/test_scalability_24k.py` — passing `allow_full_scan=True` to tools must **fail** (TypeError / not in signature)

### Historical tools that **used to** expose `allow_full_scan` (pre-v3.2.0)

Planning map in `tasks/whose-elimination-2026-05-22/05-codebase-whose-map.md` (historical; behavior since changed):

| Tool | Module | Old gate |
|------|--------|----------|
| `search_emails` | `search.py` | `recent_days <= 0` and no `date_from` |
| `list_inbox_emails` | `inbox.py` | `max_emails <= 0` |
| `get_email_thread` | `search.py` | `recent_days <= 0` |
| `get_statistics` | `analytics.py` | `days_back <= 0` |
| `get_top_senders` | `smart_inbox.py` | `days_back <= 0` |
| `reply_to_email` | `compose.py` | `recent_days <= 0` (subject lookup) |
| `forward_email` | `compose.py` | `recent_days <= 0` (subject lookup) |

**Replacement pattern (current):** refuse with `code: UNBOUNDED_SCAN_REQUIRED` and `remediation.fallback_tool: "full_inbox_export"` (or pass bounded `recent_days` / `date_from` / `message_ids`).

---

## Stale `allow_full_scan` still visible to Cursor agents

### Cursor MCP tool descriptors (this machine)

Path pattern:  
`~/.cursor/projects/<workspace>/mcps/plugin-apple-mail-apple-mail/tools/*.json`

Tools that **still list `allow_full_scan` in the JSON schema** (stale):

| Tool | Stale schema param | Notes |
|------|-------------------|-------|
| `search_emails` | `allow_full_scan` (boolean, default false) | Description still says `recent_days=0` requires it |
| `list_inbox_emails` | `allow_full_scan` | Description still says `max_emails=0` requires it |
| `get_email_thread` | `allow_full_scan` | Description still says `recent_days=0` requires it |

Other tools in the same folder (**27 tools total**) do **not** expose `allow_full_scan` in JSON, including `get_statistics` and `get_top_senders` (their stale descriptions mention `days_back=0 = all time` without the boolean).

**Missing vs current repo:** Cursor cache has **27** tools; current repo ships **28** (`full_inbox_export` is not in the stale Cursor tool set).

### Cursor plugin runtime cache

| Path | Version | `allow_full_scan` in code/skills |
|------|---------|----------------------------------|
| `~/.cursor/plugins/cache/apple-mail-mcp/apple-mail/7907cd8a9514ef337e05a25620584b35cf0a5b03/` | **3.1.7** | **Yes** — `search.py`, `inbox.py`, skills, `tools/CLAUDE.md` |

Same stale `allow_full_scan` MCP JSON descriptors were found under other Cursor project folders on this machine (personal site, CRE-EQUIRE, etc.).

### Fresh installs (for comparison)

| Surface | Version | `allow_full_scan` |
|---------|---------|-------------------|
| Claude Code cache | 3.7.1 | No |
| Codex cache | 3.7.1 | No |
| Repo checkout | 3.7.1 | No |

**Root cause for your Cursor agent:** Cursor was **not** refreshed in the prior session (only Codex + Claude Code were). The agent reads tool schemas from stale v3.1.7 material.

---

## All scan-related gates in current source (v3.7.1)

These are the **live** knobs that still affect how much Mail is scanned.

### A. Retired: `allow_full_scan`

- **Status:** removed; must not be reintroduced (lint + tests).

### B. Structured refusal: `UNBOUNDED_SCAN_REQUIRED`

Tools that **reject** “no date window” / “zero cap” inputs:

| Tool | Refusal condition | Preferred fix |
|------|-------------------|---------------|
| `search_emails` | `date_from is None` and `recent_days <= 0` | `recent_days=7` or `date_from='YYYY-MM-DD'` |
| `list_inbox_emails` | `max_emails <= 0` | `max_emails=50` (or similar) |
| `get_email_thread` | `recent_days <= 0` | `recent_days=7` or use `message_id` |
| `get_statistics` | `days_back <= 0` | `days_back=7` or `30` |
| `get_top_senders` | `days_back <= 0` | `days_back=7` or `30` |
| `reply_to_email` | `recent_days <= 0` on subject lookup | `message_id` or `recent_days=2` |
| `forward_email` | same | same |
| `move_email` | filter path: `recent_days <= 0` and no `older_than_days` | `message_ids` or bounded window |
| `update_email_status` | filter path: same pattern | `message_ids` or bounded window |
| `manage_trash` | filter path: same pattern | `message_ids` or bounded window |

Fallback named in errors: **`full_inbox_export`** (audited slow path).

### C. Opt-in slow scans: `allow_filter_scan` (mutations only)

| Tool | Default | When required |
|------|---------|---------------|
| `move_email` | `False` | Any filter-based move (`sender`, `subject_keyword`, etc.) without `message_ids` |
| `update_email_status` | `False` | Filter-based bulk status changes |
| `manage_trash` | `False` | Filter-based trash/delete (not `message_ids`) |

Returns `FILTER_SCAN_DISABLED` when filters used without opt-in.

**Note:** Cursor stale MCP JSON for these tools does **not** include `allow_filter_scan` (that gate was added in later releases). Agents on stale Cursor may not see the gate at all.

### D. Opt-in body scans: `allow_body_scan`

| Tool | Default | When required |
|------|---------|---------------|
| `search_emails` | `False` | Any non-empty `body_text` |

Returns `BODY_SCAN_DISABLED` without opt-in.

### E. Bulk mutation flag: `apply_to_all`

| Tool | Risk |
|------|------|
| `update_email_status` | Mass update with no filters; still requires `allow_filter_scan=True` |
| `manage_trash` | Mass trash; still requires `allow_filter_scan=True` |

### F. Dedicated full-mailbox tool: `full_inbox_export`

| Tool | Purpose | Caps |
|------|---------|------|
| `full_inbox_export` | Walk entire mailbox metadata | Default `max_emails=10_000`, batched; documented 2–5 min on large inboxes |

This is the **only** tool designed for whole-mailbox walks. No boolean opt-in on other tools unlocks the same behavior anymore.

### G. Implicit wideners (no boolean, but costly)

| Parameter / pattern | Tools | Effect |
|---------------------|-------|--------|
| `mailbox="All"` | `search_emails`, others | Multi-folder fan-out; capped at 10 folders for `All` searches |
| `all_accounts=true` | `search_emails`, `list_inbox_emails`, etc. | Parallel per-account work |
| Large `max_emails` / `limit` | several | Larger bounded slice |
| `export_emails(entire_mailbox)` | `export_emails` | Body reads; default cap 100, warns above 500 |
| `inbox_dashboard` high `max_total` | `inbox_dashboard` | More recent emails fetched |

---

## Full tool inventory (28 tools) — scan sensitivity

| Tool | `allow_full_scan` (current) | Other scan gates | Full-mailbox risk |
|------|----------------------------|------------------|-------------------|
| `list_accounts` | No | — | None |
| `list_account_addresses` | No | — | None |
| `list_mailboxes` | No | — | Low |
| `get_mailbox_unread_counts` | No | — | Low (count APIs) |
| `get_inbox_overview` | No | bounded scripts | Low |
| `list_inbox_emails` | **No** | `max_emails<=0` → error | Low if `max_emails` bounded |
| `search_emails` | **No** | `UNBOUNDED` without date; `allow_body_scan` | Medium–high with `All` + `all_accounts` |
| `get_email_by_id` | No | ID lookup | None |
| `get_email_thread` | **No** | `recent_days<=0` → error | Medium |
| `get_awaiting_reply` | No | bounded caps | Low |
| `get_needs_response` | No | bounded caps | Low |
| `get_top_senders` | **No** | `days_back<=0` → error | Medium (bounded slice) |
| `get_statistics` | **No** | `days_back<=0` → error | Medium (bounded slice) |
| `list_email_attachments` | No | `max_results`; prefer `message_ids` | Low–medium |
| `export_emails` | No | `max_emails` cap | Medium (body reads) |
| `full_inbox_export` | No | **intentional** full walk | **High** (by design) |
| `inbox_dashboard` | No | `max_total`, `max_per_account` | Low–medium |
| `compose_email` | No | — | None |
| `create_rich_email_draft` | No | — | None |
| `reply_to_email` | **No** | `recent_days<=0` on subject path | Low with `message_id` |
| `forward_email` | **No** | same | Low with `message_id` |
| `manage_drafts` | No | bounded Drafts window | Low |
| `move_email` | **No** | `allow_filter_scan`; `UNBOUNDED` on filters | Medium with filters |
| `update_email_status` | **No** | `allow_filter_scan`; `apply_to_all` | Medium–high |
| `manage_trash` | **No** | `allow_filter_scan`; `apply_to_all` | Medium–high |
| `save_email_attachment` | No | ID / bounded lookup | Low |
| `create_mailbox` | No | — | None |
| `synchronize_account` | No | requires `confirm_sync` | N/A (sync, not scan) |

---

## Stale documentation still mentioning `allow_full_scan`

| Location | Status |
|----------|--------|
| `plugin/**` (current) | Clean |
| `tasks/whose-elimination-2026-05-22/*` | Historical planning (explicitly marked historical) |
| `tasks/scalability-24k-hardening-2026-05-22.md` | Historical planning |
| Cursor cache v3.1.7 skills + `tools/CLAUDE.md` | **Stale — still teaches `allow_full_scan`** |
| Cursor MCP JSON descriptors | **Stale — still exposes parameter** |

---

## Answers to your specific questions

### “Why is `allow_full_scan` even there?”

**In current repo code: it isn’t.** It survives only in:

1. Cursor’s stale v3.1.7 plugin/MCP cache on this machine  
2. Historical `tasks/` planning documents  
3. Test comments documenting the retirement  

### “Why did the agent set `allow_full_scan=true` when it already had `date_from`?”

Because the **stale tool description** ties `recent_days=0` to `allow_full_scan=True` without a prominent exception for explicit `date_from`. The agent set `recent_days=0` (correct when using a fixed start date) and mirrored the outdated schema guidance. In both old and new server logic, **`date_from` already satisfies the bounded-window requirement**; `allow_full_scan` was redundant.

### “It should not be allowed to do a full scan with this tool or any of the tools.”

**Current design intent matches that goal**, with one explicit exception:

- Routine tools refuse unbounded parameters and point to `full_inbox_export`.
- **Gap:** Cursor is still advertising a removed parameter, which trains agents to think full-scan opt-in exists on `search_emails` / `list_inbox_emails` / `get_email_thread`.
- **Separate gap:** `full_inbox_export` still exists for rare audited exports (by design). Mutation tools still have `allow_filter_scan` for approved bulk campaigns.

---

## Recommended follow-ups (not implemented in this audit)

1. **Refresh Cursor** Apple Mail plugin/MCP to v3.7.1 (same procedure as Codex/Claude in `README.md` “Refresh another Mac”).
2. **Verify** Cursor project `mcps/plugin-apple-mail-apple-mail/tools/*.json` no longer list `allow_full_scan` after refresh.
3. **Optional hardening** (future code change): reject unknown kwargs like `allow_full_scan` with a structured “parameter retired” error so stale clients fail loudly instead of silently ignoring.
4. **Optional policy:** decide whether `full_inbox_export` should require a human confirmation flag (it currently does not use a boolean opt-in; cost is documented only).

---

## References

- Retirement convention: `docs/CLAUDE-conventions.md` (Forbidden patterns table, `allow_full_scan` row)
- CI guard: `tests/test_no_unbounded_whose.py`
- Stale Cursor source: `~/.cursor/plugins/cache/apple-mail-mcp/apple-mail/7907cd8a9514ef337e05a25620584b35cf0a5b03/apple_mail_mcp/tools/search.py` (lines ~727, ~801)
- Current `search_emails` gate: `plugin/apple_mail_mcp/tools/search.py` (lines ~1135–1154, ~1182–1190)
