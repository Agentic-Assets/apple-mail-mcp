# Bulk Cleanup Operations

Bulk operations remove or relocate many messages at once. Apple Mail offers no undo for permanent deletes, so this reference exists to keep cleanup safe and reversible.

## ID-first rule (v3.7.0)

Mutations default to **`message_ids=[...]`** from a prior bounded `search_emails` or `list_inbox_emails` call. Filter-based `move_email` / `update_email_status` / `manage_trash` without ids returns **`TARGET_SELECTOR_DEPRECATED`** for subject or sender target selectors; date-only or explicit bulk scans still require **`allow_filter_scan=True`**.

See **`email-archive-cleanup`** for the canonical campaign shape.

## Safety Defaults

The MCP server enforces conservative defaults to prevent runaway destructive operations:

| Tool | Default cap | Override parameter |
|------|-------------|--------------------|
| `manage_trash` (move_to_trash, delete_permanent) | 5 messages | `max_deletes=N` |
| `manage_trash` (empty_trash) | hard confirmation required | `confirm_empty=True` |
| `update_email_status` | 10 messages | `max_updates=N` |
| `move_email` | 50 messages | `max_moves=N` |

Raise these caps only after a confirming search shows the user exactly which messages will be affected.

## Safe Cleanup Sequence

1. **Identify candidates** with `search_emails()` or `list_inbox_emails()`, narrowed by sender, date range, mailbox, or read status.
2. **Collect `message_id`s** from the preview (first ten subjects for human confirmation).
3. **Dry-run by id** — `move_email(message_ids=[...], to_mailbox="...", dry_run=True)` or `manage_trash(message_ids=[...], dry_run=True)`.
4. **Move to Trash first** with `manage_trash(action="move_to_trash", message_ids=[...], dry_run=False)`. Reversible inside Apple Mail.
5. **Verify** by listing Trash or re-searching the source window.
6. **Permanent delete only when certain** with `manage_trash(action="delete_permanent", message_ids=[...])`.
7. **Empty Trash is the nuclear option.** Run `manage_trash(action="empty_trash", confirm_empty=True)` only after explicit user confirmation.

## Pre-Cleanup Backup

Before deleting a large mailbox, export it: `export_emails(scope="entire_mailbox", mailbox="Archive/2023", format="html")`. The user gets a local copy in case a permanent delete removes something important.

## Common Cleanup Patterns

### Purge old read newsletters

```text
search_emails(sender_exact="newsletter@example.com", read_status="read", recent_days=30)
# collect message_ids from results
manage_trash(action="move_to_trash", message_ids=[...], dry_run=True)
manage_trash(action="move_to_trash", message_ids=[...], dry_run=False)
```

### Archive everything older than 90 days

```text
search_emails(date_from="2025-01-01", date_to="2025-02-20", read_status="read")
move_email(message_ids=[...], to_mailbox="Archive/2025", dry_run=True)
move_email(message_ids=[...], to_mailbox="Archive/2025")
```

### Empty a defunct project folder

1. `export_emails(scope="entire_mailbox", mailbox="Projects/OldProject")` for the audit trail.
2. `list_inbox_emails` or `search_emails` in that mailbox → collect ids.
3. `manage_trash(action="move_to_trash", message_ids=[...])` in batches of ≤50.
4. Verify, then `manage_trash(action="empty_trash", confirm_empty=True)` if appropriate.

## Confirmation Script

Before any bulk destructive action, restate to the user:

- The exact tool call about to run (with `message_ids` count).
- The expected affected count from the preview search.
- Whether the action is reversible (move_to_trash) or permanent (delete_permanent, empty_trash).

If any of those three are unclear, stop and ask.
