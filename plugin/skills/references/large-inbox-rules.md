# Large-inbox pre-flight (canonical)

Shared by `apple-mail-operator`, `inbox-triage`, `email-management`, and `email-archive-cleanup`. Edit here; the SKILL.md files include this by reference.

Apple Mail's AppleScript bridge slows non-linearly on large mailboxes (24k+ is common). Before running any discovery or bulk tool:

1. **Size the inbox once per session**: `get_inbox_overview(output_format="compact", include_mailboxes=false, include_recent=false)`. If it returns slowly or partially, treat the inbox as large and apply the rules below.
2. **Bound every scan.** Pass an explicit `recent_days` (start at 2, widen only on demand). Tools refuse unbounded scans by default — `recent_days=0` and `max_emails=0` return a structured `UNBOUNDED_SCAN_REQUIRED` error. If you really need every message, call `full_inbox_export` (slow; documented cost). Otherwise pass a bounded `recent_days` / `max_emails`.
3. **Discovery scans stay bounded; mutations stay ID-first.** `search_emails(sender=...)` and `list_email_attachments(subject_keyword=...)` can stall on 24k mail — pair sender filters with `subject_keyword=` or a tight `recent_days` ceiling. For `move_email`, `update_email_status`, and `manage_trash`, collect `message_ids` from a bounded `search_emails` / `list_inbox_emails` pass and pass `message_ids=[...]` (see rule 8).
4. **`get_awaiting_reply` is timeout-prone** (it cross-walks Sent mail). Start with `days_back=2, max_results=5`; if it stalls, skip it and check Sent directly with `search_emails(mailbox="Sent", recent_days=2, ...)`.
5. **Always drill by id, never re-search by subject.** Once `search_emails` / `list_inbox_emails` returns a `message_id`, use `get_email_by_id(message_id=...)` and `get_email_thread(message_id=...)`. Re-searching by subject re-pays the scan cost.
6. **Param names matter.** `list_inbox_emails` takes `max_emails` (not `limit`) and `read_status` ∈ `"all"`/`"unread"`/`"read"` — matches `search_emails`. Example: `list_inbox_emails(max_emails=25, read_status="unread", include_content=False)`. The legacy `include_read=False` / `unread_only=True` still work but emit a deprecation warning.
7. **`inbox_dashboard` is the rescue path.** When `get_inbox_overview` times out or returns partial JSON, fall back to `inbox_dashboard()` — it returns a structured snapshot (unread, recent, pinned, suggestions) in a single bounded call.
8. **Never filter-mutate without explicit opt-in.** Do not call `move_email`, `update_email_status`, or `manage_trash` with `subject_keyword=`, `sender=`, or `apply_to_all=True` unless `allow_filter_scan=True` **and** the user has approved a bulk campaign. Default path: bounded list/search → collect `message_id`s → `move_email(message_ids=[...])` (or the equivalent for status/trash). Filter scans re-pay mailbox walk cost and are timeout-prone on 24k+ inboxes.

## Structured error contract

| Code | When | Remediation |
|------|------|-------------|
| `UNBOUNDED_SCAN_REQUIRED` | Discovery tool called without `recent_days` / `max_emails` bounds | Pass bounded `recent_days` / `max_emails`, or call `full_inbox_export` if a full walk is genuinely required |
| `FILTER_SCAN_DISABLED` | Mutation tool called with subject/sender/`apply_to_all` filters but without `allow_filter_scan=True` | Follow `remediation.preferred`: `search_emails(...)` or `list_inbox_emails(...)` → collect `message_id`s → `tool_name(message_ids=[...])`. Use `remediation.escape_hatch` (`allow_filter_scan=True`) only for user-approved bulk campaigns |
