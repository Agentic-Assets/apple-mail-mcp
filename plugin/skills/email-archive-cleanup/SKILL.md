---
name: email-archive-cleanup
description: This skill should be used when the user asks to "archive old mail safely", "move everything from X sender", "clear newsletters", "bulk mark read after export", "preview what would move with dry_run=True", or run staged cleanup campaigns. Uses search_emails, move_email(dry_run=True) preview → execute, update_email_status, export_emails, manage_trash (with max_deletes / max_moves caps), synchronize_account, and get_statistics when measuring progress. Do NOT use for taxonomy design sessions without execution (mailbox-taxonomy), proposing Mail filter rules copy (mail-rules-advisor only), drafting mail (email-drafting), or the 5-minute triage skim (inbox-triage).
---

# Archive And Cleanup Campaigns

High-leverage transformations with **explicit human checkpoints**. Optimize for reversible moves (`Archive`, `Trash` soft-delete) unless the operator understands permanent deletes.

## Large-inbox pre-flight (required when inbox > ~5,000 messages)

See [`large-inbox-rules.md`](references/large-inbox-rules.md) for the canonical pre-flight checklist.

### When to reach for `full_inbox_export`

`full_inbox_export` is the only tool that walks the entire inbox. Use it as the evidence step for annual cleanups, full audits, or pre-migration snapshots; pair it with bounded `export_emails` scopes for the on-disk artifact before any irreversible `manage_trash(action="delete_permanent")`. It is slow (minutes on a 24k inbox); for staged campaigns, keep using bounded `search_emails` + `message_ids=[...]`, `export_emails(scope="filtered", ...)`, or `export_emails(scope="correspondent", email_address=..., include_sent=True, ...)` flows instead.

## ID-first flow (mandatory for bulk moves, status, and trash)

`move_email`, `update_email_status`, and `manage_trash` **require `message_ids` by default**. Passing `subject_keyword=` or `sender=` to action tools returns `code: TARGET_SELECTOR_DEPRECATED`; collect `message_ids` first. Date-only or explicit bulk paths still require `allow_filter_scan=True`.

On a 24k inbox, filter-based mutations re-pay the scan cost for every batch and often time out. Always:

1. **List or search (bounded)**: `search_emails(sender_exact="...", subject_keyword="...", recent_days=30, limit=50)`, `search_emails(sender_domain="...", recent_days=30, limit=50)`, or `list_inbox_emails(...)`; inspect sample subjects.
2. **Collect `message_id`s**: extract ids from the JSON/text result.
3. **Simulate**: `move_email(dry_run=True, message_ids=[ids], to_mailbox="...", max_moves=50)` (or `manage_trash(dry_run=True, message_ids=[ids], ...)`).
4. **Execute**: `move_email(dry_run=False, message_ids=[ids], ...)` after the operator confirms counts.

Repeat in batches until stop conditions. Re-run `search_emails(offset=0)` after each archive wave because offsets shift on active inboxes (`exchange-account-patterns.md`).

### `allow_filter_scan=True` (rare escape hatch only)

Use only when the user has explicitly approved a bulk campaign and ID collection is impractical (e.g. migrating an entire sender across years). The tool prefixes responses with a slow-scan warning. Do not use `sender=` or `subject_keyword=` on action tools. Use `search_emails(...)` to collect ids, then act by `message_ids`.

## Standard Campaign Shape

### 1. Frame The Objective And Scope

- Confirm target **account**, mailbox, sender/topic/date window.
- Decide whether backlog is exploratory (`recent_days` wide) or targeted with discovery filters (`sender_exact=`, `sender_domain=`, `subject_keyword=`).

### 2. Establish Evidence

Sequence:

1. `search_emails(..., limit≤50)` preview; escalate only after inspecting sample subjects.
2. Optional analytics: `get_statistics(scope="sender_stats", sender="...")` or `scope="mailbox_breakdown"` for volume proof.
3. When deletion risk looms: `export_emails(...)` snapshots before **`manage_trash`**.

Always quote expected totals after dry runs.

### 3. Simulate Mutations (`dry_run=True`, `message_ids` required)

Mandatory first pass after collecting ids from step 2:

```
ids = [e["message_id"] for e in preview["items"]]  # search_emails JSON uses "items"; list_inbox_emails uses "emails"
move_email(dry_run=True, message_ids=ids, to_mailbox="Archive", max_moves=50)
```

Trash paths: **`manage_trash(action="move_to_trash", dry_run=True, message_ids=ids, ...)`** before committing. Status paths: **`update_email_status(dry_run=True, message_ids=ids, action="mark_read", ...)`**.

Quote expected totals from the dry-run response. Discuss raising `max_moves` / `max_deletes`; defaults protect against catastrophe. If you hit `TARGET_SELECTOR_DEPRECATED`, you used a search selector on an action tool. Go back to search/list and collect ids.

### 4. Execute In Batches (`message_ids` only)

| Action | Typical tool |
|--------|---------------|
| File / archive batch | `move_email(dry_run=False, message_ids=ids, to_mailbox="...", max_moves=50)` |
| Mark processed | `update_email_status(action="mark_read"\|"unflag", message_ids=ids, max_updates≤50 after confirmation)` |
| Remove noise | `manage_trash(action="move_to_trash", message_ids=ids, max_deletes≤50)`; escalate `delete_permanent` only post-export + verbal/written affirmation |
| Hydrate caches | `synchronize_account(account="...", confirm_sync=True)` only after explicit user approval; it can fetch large remote backlogs |

Slice `ids` into batches of ≤50. Re-run narrower `search_emails` between batches until stop conditions.

### 5. Regression Check

Finish with **`get_statistics(scope="account_overview")`** or **`get_inbox_overview()`** verifying queues match operator expectations.

## Safety Reference

| Hazard | Mitigation |
|--------|-------------|
| Over-broad keywords | Narrow by `mailbox=`, combine sender + unread flags |
| Mailbox typos | `list_mailboxes` validation before destructive move |
| Accidental unread wipe | Separate pass for unread-only subsets |
| Cross-account fallout | Iterate per account instead of omnibus `all_accounts=True` mutations |

Permanent deletes invoke irreversible **`manage_trash(action="delete_permanent")`**; escalate only with evidence export + checklist.

## Sibling Routing

| If the user pivots... | Skill |
|------------------------|-------|
| Needs smarter folder ontology | **`mailbox-taxonomy`** |
| Wants sieve text for automation | **`mail-rules-advisor`** |
| Suddenly needs reply drafts | **`email-drafting`** |
