# Large-inbox pre-flight (canonical)

Canonical source: `plugin/skills/references/large-inbox-rules.md`. After edits, run `python3 tools/validators/sync_skill_references.py` to refresh per-skill copies under `*/references/`.

Apple Mail's AppleScript bridge slows non-linearly on large mailboxes (24k+ is common). Before running any discovery or bulk tool:

1. **Size the inbox once per session**: `get_inbox_overview(output_format="compact", include_mailboxes=false, include_recent=false)`. If it returns slowly or partially, treat the inbox as large and apply the rules below.
2. **Bound every scan.** Pass an explicit `recent_days` (start at 2, widen only on demand). Tools refuse unbounded scans by default — `recent_days=0` and `max_emails=0` return a structured `UNBOUNDED_SCAN_REQUIRED` error. If you really need every message, call `full_inbox_export` (slow; documented cost). Otherwise pass a bounded `recent_days` / `max_emails`.
3. **Discovery scans stay bounded; mutations stay ID-first.** Prefer `search_emails(sender_exact=...)` or `search_emails(sender_domain=...)` when possible; fuzzy `sender=...` is discovery-only and should stay bounded with a tight `recent_days` ceiling. For attachments, search with `has_attachments=True` first, then call `list_email_attachments(message_ids=[...])`. For `move_email`, `update_email_status`, and `manage_trash`, collect `message_ids` from a bounded `search_emails` / `list_inbox_emails` pass and pass `message_ids=[...]` (see rule 8).
4. **`get_awaiting_reply` is timeout-prone** (it cross-walks Sent mail). Start with `days_back=2, max_results=5`; if it stalls, skip it and check Sent directly with `search_emails(mailbox="Sent", recent_days=2, ...)`.
5. **Always drill by id, never re-search by subject.** Once `search_emails` / `list_inbox_emails` returns a `message_id`, use `get_email_by_id(message_id=...)` and `get_email_thread(message_id=...)`. Re-searching by subject re-pays the scan cost.
6. **Param names matter.** `list_inbox_emails` takes `max_emails` (not `limit`) and `read_status` ∈ `"all"`/`"unread"`/`"read"` — matches `search_emails`. Example: `list_inbox_emails(max_emails=25, read_status="unread", include_content=False)`. The legacy `include_read=False` / `unread_only=True` still work but emit a deprecation warning.
7. **`inbox_dashboard` is the rescue path.** When `get_inbox_overview` times out or returns partial JSON, fall back to `inbox_dashboard()` — it returns a structured snapshot (unread, recent, pinned, suggestions) in a single bounded call.
8. **Never target actions by subject or sender.** Each of `move_email`, `update_email_status`, and `manage_trash` returns `TARGET_SELECTOR_DEPRECATED` for `subject_keyword=`, `subject_keywords=`, or `sender=` target selectors. Default path: bounded list/search -> collect `message_id`s -> `move_email(message_ids=[...])` (or the equivalent for status/trash). Date-only or explicit bulk paths still require `allow_filter_scan=True` plus user approval; those scans re-pay mailbox walk cost and are timeout-prone on 24k+ inboxes.

## Structured error contract

| Code | When | Remediation |
|------|------|-------------|
| `UNBOUNDED_SCAN_REQUIRED` | Discovery tool called without `recent_days` / `max_emails` bounds | Pass bounded `recent_days` / `max_emails`, or call `full_inbox_export` if a full walk is genuinely required |
| `FILTER_SCAN_DISABLED` | Mutation tool called with a date/bulk filter path but without `allow_filter_scan=True` | Follow `remediation.preferred`: `search_emails(...)` or `list_inbox_emails(...)` -> collect `message_id`s -> `tool_name(message_ids=[...])`. Use `remediation.escape_hatch` (`allow_filter_scan=True`) only for user-approved bulk campaigns |
| `TARGET_SELECTOR_DEPRECATED` | Action tool called with subject, sender, or draft-subject target selectors instead of exact ids | Run bounded discovery first, review candidates, then retry with `message_ids=[...]`, `message_id=...`, or `draft_id=...` |
