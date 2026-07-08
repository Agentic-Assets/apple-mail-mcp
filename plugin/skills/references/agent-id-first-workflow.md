# Agent quick reference: Apple Mail MCP

One-page contract for coding agents. Full policy: `docs/CLAUDE-conventions.md`. Copy-paste patterns: `email-management/templates/search-patterns.md`.

## Default mutation path

1. **Discover (newest first, small batch):** `list_inbox_emails(max_emails=5)` or `search_emails(limit=5, recent_days=2..7)`; see `plugin/skills/references/recent-first-triage.md`.
2. **Collect ids:** `message_id` / `message_ids` from JSON (`search_emails` → `"items"`; `list_inbox_emails` → `"emails"`).
3. **Preview:** `dry_run=True` on moves, trash, status updates when supported.
4. **Act:** pass exact ids only; never `subject_keyword`, `sender`, or `draft_subject` on action tools.

## Tool routing

| Intent | Skill / tool |
|--------|----------------|
| MCP setup, find one email | `apple-mail-operator` |
| Daily 5–10 min scan | `inbox-triage` → `get_needs_response` |
| Reply / forward / draft | `email-drafting` → `reply_to_email(message_id=..., reply_body=...)` |
| Bulk archive / trash | `email-archive-cleanup` after id collection |
| Attachments | `list_email_attachments(message_ids=...)` → `save_email_attachment` |

## Hard-fail selectors (v3.x)

Passing these to **action** tools returns `TARGET_SELECTOR_DEPRECATED` (even with `allow_filter_scan=True`):

- Deprecated `subject_keyword` / `sender` on `move_email`, `update_email_status`, `manage_trash`, `reply_to_email`, `forward_email`, attachments, `export_emails(single_email)`; returns `TARGET_SELECTOR_DEPRECATED`
- Deprecated `draft_subject` on `manage_drafts(send/open/delete)`; returns `TARGET_SELECTOR_DEPRECATED`

`allow_filter_scan=True` is only for **human-approved date/bulk** paths (`older_than_days`, `apply_to_all`), not subject/sender.

## Native reply defaults

`reply_to_email` defaults to `native_format=True` (rich quote + logo signature). Requires Mail window focus and Accessibility permission. On `REPLY_WINDOW_FOCUS_FAILED`, no draft was saved. Retry with Mail visible and not being clicked. Do not switch to `native_format=False`; it is gated (`WINDOWLESS_FALLBACK_DISABLED`) and reserved for deliberate headless/CI via `allow_windowless_fallback=True`, which agents must never set. The windowless path is not a normal fallback.
