# Large-inbox pre-flight (canonical)

Shared by `apple-mail-operator`, `inbox-triage`, `email-management`, and `email-archive-cleanup`. Edit here; the SKILL.md files include this by reference.

Apple Mail's AppleScript bridge slows non-linearly on large mailboxes (24k+ is common). Before running any discovery or bulk tool:

1. **Size the inbox once per session**: `get_inbox_overview(output_format="compact", include_mailboxes=false, include_recent=false)`. If it returns slowly or partially, treat the inbox as large and apply the rules below.
2. **Bound every scan.** Pass an explicit `recent_days` (start at 2, widen only on demand). Tools refuse unbounded scans by default — `recent_days=0` and `max_emails=0` return a structured `UNBOUNDED_SCAN_REQUIRED` error. If you really need every message, call `full_inbox_export` (slow; documented cost). Otherwise pass a bounded `recent_days` / `max_emails`.
3. **Co-filter sender-based actions.** `move_email(sender=...)`, `search_emails(sender=...)`, and `list_email_attachments(subject_keyword=...)` can stall on 24k mail. Pair sender filters with `subject_keyword=` or a tight `recent_days` ceiling, or collect `message_ids` from a bounded search and pass `message_ids=[...]` instead.
4. **`get_awaiting_reply` is timeout-prone** (it cross-walks Sent mail). Start with `days_back=2, max_results=5`; if it stalls, skip it and check Sent directly with `search_emails(mailbox="Sent", recent_days=2, ...)`.
5. **Always drill by id, never re-search by subject.** Once `search_emails` / `list_inbox_emails` returns a `message_id`, use `get_email_by_id(message_id=...)` and `get_email_thread(message_id=...)`. Re-searching by subject re-pays the scan cost.
6. **Param names matter.** `list_inbox_emails` takes `max_emails` (not `limit`) and `read_status` ∈ `"all"`/`"unread"`/`"read"` — matches `search_emails`. Example: `list_inbox_emails(max_emails=25, read_status="unread", include_content=False)`. The legacy `include_read=False` / `unread_only=True` still work but emit a deprecation warning.
7. **`inbox_dashboard` is the rescue path.** When `get_inbox_overview` times out or returns partial JSON, fall back to `inbox_dashboard()` — it returns a structured snapshot (unread, recent, pinned, suggestions) in a single bounded call.

## Structured error contract

If a tool returns `code: UNBOUNDED_SCAN_REQUIRED`, follow the `remediation.fallback_tool` field. The typical remediation is either (a) pass a bounded `recent_days` / `max_emails`, or (b) call `full_inbox_export` if a full walk is genuinely required.
