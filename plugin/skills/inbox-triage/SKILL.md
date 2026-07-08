---
name: inbox-triage
description: This skill should be used when the user asks to "check my email", "what came in today", "what needs my attention", "morning email scan", "triage my inbox", "draft responses to recent email", "anything urgent in my mail", or wants a fast read-only pass over Apple Mail without full inbox-zero cleanup. Uses get_inbox_overview, get_needs_response, get_awaiting_reply, list_inbox_emails, and get_email_by_id. Work newest mail first in batches of 3 to 5 (see recent-first-triage.md). Do NOT use for deep folder reorganization (mailbox-taxonomy), bulk archive or delete campaigns (email-archive-cleanup), proposing Mail filter text (mail-rules-advisor), MCP setup (apple-mail-operator), or composing replies; use email-drafting after this scan.
---

# Inbox Triage

Fast, read-first email check for Apple Mail: what arrived, what needs a reply, what you're still waiting on. Target **5–10 minutes**, not inbox zero.

## Recent-first, small-batch (required)

See [`recent-first-triage.md`](references/recent-first-triage.md). **Newest received mail first.** Process **3 to 5 messages per batch**, one thread at a time, before widening the window or pulling older mail. Do not open with wide `date_from` sweeps or `list_inbox_emails(max_emails=25)` while fresher inbox items are still unreviewed.

## Large-inbox pre-flight (required when inbox > ~5,000 messages)

See [`large-inbox-rules.md`](references/large-inbox-rules.md) for the canonical pre-flight checklist. On large **Exchange** profiles also read [`exchange-account-patterns.md`](references/exchange-account-patterns.md) (unreliable subject search, weak `get_needs_response`, incomplete threads). Triage is read-only, so the bounded defaults below are usually enough; do not reach for `full_inbox_export` inside the daily loop.

## Before drafting

This skill is read-first. When the user wants a reply, hand off to **`email-drafting`** after you have a `message_id`. See [`pre-draft-verification.md`](references/pre-draft-verification.md).

## When To Use

| User says | Use this skill |
|-----------|----------------|
| "Check my email" / "what came in today" | Yes |
| "What needs my attention" / "anything urgent" | Yes |
| "Morning email scan" / "quick triage" | Yes |
| "Clean up my inbox" / "inbox zero" / multi-week program | No → `email-management` |
| "Folder strategy / noisy senders / rule ideas" | No → `mailbox-taxonomy` / `mail-rules-advisor` |
| "Archive or bulk move/delete" | No → `email-archive-cleanup` |
| "Mail MCP errors / which tool do I use?" | No → `apple-mail-operator` |
| "Write / reply to this email" | No → `email-drafting` |

## Setup (once)

Set **`DEFAULT_MAIL_ACCOUNT`** to the user's primary Mail account name (e.g. `Work`, `primary@example.com`). Without it, tools may fan out across every account and run slowly.

For agent testing, run the MCP server with **`--draft-safe`** so send tools stay blocked.

## Daily loop (5–10 min)

Run on the **configured default account** unless the user names another. Work **newest to oldest** in batches of **3 to 5** messages.

### 1. Snapshot (30–60s)

```
get_inbox_overview(
  output_format="compact",
  include_mailboxes=false,
  include_recent=true,
  include_suggestions=false
)
```

Note unread totals and the **newest** subjects. Do not open every message yet.

### 2. Newest inbox slice (first batch)

Prefer ids from JSON search:

```
search_emails(limit=5, recent_days=3, output_format="json", sort="date_desc")
```

Use `list_inbox_emails(max_emails=5, include_content=false, output_format="json")` only for a quick subject skim. If rows lack `message_id`, re-resolve each human item with `search_emails(sender=..., limit=10, output_format="json")` before thread-check or archive.

Process this batch (read → thread-check → draft or no-action) before pulling more. Raise to `max_emails=8` / `limit=8` only if the first batch is clear and the user wants to continue.

### 3. Needs your reply (only after step 2, or when user asks)

```
get_needs_response(days_back=3, max_results=5, check_already_replied=True, include_already_replied=False, output_format="json")
```

Widen to `days_back=7` or `max_results=10` only if step 2 found nothing human-actionable. Do **not** start with `days_back=30`.

### 4. Waiting on others (optional, ~1 min)

```
get_awaiting_reply(days_back=7, max_results=5)
```

Use when the user cares about follow-ups they already sent.

### 5. Drill-down by exact id (when needed)

After search or list returns a `message_id`, fetch the full message without re-searching:

```
get_email_by_id(message_id="12345", include_content=true, output_format="json")
```

In JSON rows, `message_id` is the numeric Apple Mail id for follow-up tool calls (`get_email_by_id`, `get_email_thread`, `reply_to_email`, `move_email`). `internet_message_id` is the RFC Message-ID header used for replied-header correlation only; do not pass it to tools that expect a numeric Mail id.

Repo CLI equivalent: `apple-mail show --id 12345 --json`.

**To draft:** load **`email-drafting`** (not this skill). Draft **one thread at a time**: pass the `message_id` from triage into `reply_to_email(message_id=..., reply_body=..., mode="draft")`; default `native_format=True` needs Mail focus + Accessibility (see `email-drafting` for `REPLY_WINDOW_FOCUS_FAILED` recovery). Finish verify for that draft before starting the next message in the batch.

### 6. Next batch (only after current batch is done)

Pull the next 3 to 5 older messages with `search_emails(limit=5, offset=<previous+5>, recent_days=7, output_format="json")`. Re-pull `offset=0` after an archive wave because offsets shift.

### 7. Research paper mail (after human read)

When mail assigns R&R workstreams, co-author specs, or paper PDF briefs, see [`research-project-tracking.md`](references/research-project-tracking.md): create/update the Research-team project issue and attach saved briefs. Email acknowledgment and tracker work are separate steps.

## Output format for the user

Summarize in plain language:

1. **Needs reply**: count + top 3 subjects
2. **Waiting on others**: count + top 2 if any
3. **Notable recent**: anything flagged/urgent from overview
4. **Suggested next action**: read one message, reply, defer, or schedule full cleanup

Do not bulk-move or trash during triage unless the user explicitly asks.

## Archive handoff (when the user asks mid-triage)

Triage stays read-first, but a quick "archive these" request is common. Do not call `move_email(subject_keyword=...)` or `move_email(sender=...)` from triage; those return `TARGET_SELECTOR_DEPRECATED`.

1. Collect numeric `message_id`s from the triage pass you already ran (`list_inbox_emails`, `get_needs_response`, or `search_emails` JSON).
2. Confirm the subject/sender list with the user.
3. `move_email(dry_run=True, message_ids=[...], to_mailbox="Archive", max_moves=25)`; quote the count.
4. `move_email(dry_run=False, message_ids=[...], ...)` after confirmation.

For larger cleanups, hand off to **`email-archive-cleanup`**.

## Performance rules

- **Recent-first:** newest mail first; batches of 3 to 5; see [`recent-first-triage.md`](references/recent-first-triage.md).
- Keep `days_back` small (`3` for needs-response on first pass, `7` for awaiting-reply).
- Avoid `get_statistics(account_overview)` in the daily loop; use weekly in `email-management`.
- Avoid `all_accounts=True` unless the user has no default account and wants every account.
- To scope a scan across a few folders, use `search_emails(mailboxes=["INBOX", "Sent", ...], limit=5)` instead of whole-profile fan-out. This avoids the slow path on large Exchange/Gmail accounts.
- Prefer `list_mailboxes(include_counts=false)` when listing folders.

## Related skills

- **`email-management`**: sustained inbox-zero programs and cross-cutting habits  
- **`mailbox-taxonomy`** / **`email-archive-cleanup`** / **`mail-rules-advisor`**: structure, execution, automation proposals  
- **`email-drafting`** + **`email-style-profile`**: replies and voice alignment after triage identifies a message  
- **`email-attachments`**: when the next action is extracting files, not reading queues  
- **`apple-mail-operator`**: onboarding, account/mailbox introspection, troubleshooting
