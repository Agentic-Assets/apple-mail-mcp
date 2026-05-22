---
name: inbox-triage
description: This skill should be used when the user asks to "check my email", "what came in today", "what needs my attention", "morning email scan", "triage my inbox", "anything urgent in my mail", or wants a fast 5–10 minute read-only pass over Apple Mail without full inbox-zero cleanup. Uses get_inbox_overview, get_needs_response, get_awaiting_reply, list_inbox_emails, and get_email_by_id. Do NOT use for deep folder reorganization (mailbox-taxonomy), bulk archive or delete campaigns (email-archive-cleanup), proposing Mail filter text (mail-rules-advisor), MCP setup (apple-mail-operator), or composing replies — use email-drafting after this scan.
---

# Inbox Triage

Fast, read-first email check for Apple Mail — what arrived, what needs a reply, what you're still waiting on. Target **5–10 minutes**, not inbox zero.

## Large-inbox pre-flight (required when inbox > ~5,000 messages)

Apple Mail's AppleScript bridge slows non-linearly on large mailboxes (24k+ is common). Before running any discovery or bulk tool:

1. **Size the inbox once per session**: `get_inbox_overview(output_format="compact", include_mailboxes=false, include_recent=false)`. If it returns slowly or partially, treat the inbox as large and apply the rules below.
2. **Bound every scan.** Pass an explicit `recent_days` (start at 2, widen only on demand). Never call `recent_days=0` / `allow_full_scan=True` without user confirmation.
3. **Co-filter sender-based actions.** `move_email(sender=...)`, `search_emails(sender=...)`, and `list_email_attachments(subject_keyword=...)` can stall on 24k mail. Pair sender filters with `subject_keyword=` or a tight `recent_days` ceiling, or collect `message_ids` from a bounded search and pass `message_ids=[...]` instead.
4. **`get_awaiting_reply` is timeout-prone** (it cross-walks Sent mail). Start with `days_back=2, max_results=5`; if it stalls, skip it and check Sent directly with `search_emails(mailbox="Sent", recent_days=2, ...)`.
5. **Always drill by id, never re-search by subject.** Once `search_emails` / `list_inbox_emails` returns a `message_id`, use `get_email_by_id(message_id=...)` and `get_email_thread(message_id=...)`. Re-searching by subject re-pays the scan cost.
6. **Param names matter.** `list_inbox_emails` takes `max_emails` (not `limit`) and `include_read` (not `unread_only`). Example: `list_inbox_emails(max_emails=25, include_read=False, include_content=False)`.
7. **`inbox_dashboard` is the rescue path.** When `get_inbox_overview` times out or returns partial JSON, fall back to `inbox_dashboard()` — it returns a structured snapshot (unread, recent, pinned, suggestions) in a single bounded call.

## Already-replied safeguard (default)

Before creating a draft reply, the agent **must** verify the user hasn't already replied to the email. The discovery tools enforce this by default:

- `get_needs_response()` filters out already-replied emails (Message-ID matched against the Sent mailbox). Pass `include_already_replied=True` only if the user explicitly asks to see them.
- `list_inbox_emails()` and `search_emails()` accept `exclude_replied=True` and `flag_replied=True` — when sourcing candidates for drafting, set `exclude_replied=True`.
- As a final check before calling `reply_to_email`, fetch the thread with `get_email_thread()` and confirm no message in the thread was sent by the user (one of `list_account_addresses` outputs).

Override: if the user explicitly says "include already-replied" or "I want to redraft", set `include_already_replied=True` (on `get_needs_response`) or `exclude_replied=False` (elsewhere).

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

Set **`DEFAULT_MAIL_ACCOUNT`** to the user's primary Mail account name (e.g. `Work`, `cayman@agenticassets.ai`). Without it, tools may fan out across every account and run slowly.

For agent testing, run the MCP server with **`--draft-safe`** so send tools stay blocked.

## Daily loop (5–10 min)

Run on the **configured default account** unless the user names another.

### 1. Snapshot (30–60s)

```
get_inbox_overview(
  output_format="compact",
  include_mailboxes=false,
  include_recent=true,
  include_suggestions=false
)
```

Note unread totals and recent subjects. Do not open every message yet.

### 2. Needs your reply (1–3 min)

```
get_needs_response(days_back=2, max_results=10)
```

Default `include_already_replied=False` filters out emails the user has already replied to (Message-ID matched against Sent). Only pass `include_already_replied=True` if the user explicitly wants the full set (e.g. "show everything, even the ones I answered").

Subject-only detection is the default (fast). Use `scan_body=True` only when the user asks to hunt question marks in bodies.

Present as a short prioritized list: subject, sender, age, priority hint.

### 3. Waiting on others (optional, ~1 min)

```
get_awaiting_reply(days_back=7, max_results=5)
```

Use when the user cares about follow-ups they already sent.

### 4. Scan recent inbox (1–2 min)

```
list_inbox_emails(max_emails=25, include_content=false, output_format="json", exclude_replied=True, flag_replied=True)
```

When the scan is feeding a drafting candidate list, keep `exclude_replied=True` so the agent does not propose replies to messages already answered. `flag_replied=True` prefixes any retained items with `[REPLIED]` in text output and adds `already_replied` to JSON.

Skim subjects. Flag obvious P0 keywords (urgent, deadline, outage) with `search_emails(..., exclude_replied=True)` only if overview/needs-response missed them.

### 5. Drill-down by exact id (when needed)

After search or list returns a `message_id`, fetch the full message without re-searching:

```
get_email_by_id(message_id="12345", include_content=true, output_format="json")
```

Repo CLI equivalent: `apple-mail show --id 12345 --json`.

## Output format for the user

Summarize in plain language:

1. **Needs reply** — count + top 3 subjects
2. **Waiting on others** — count + top 2 if any
3. **Notable recent** — anything flagged/urgent from overview
4. **Suggested next action** — read one message, reply, defer, or schedule full cleanup

Do not bulk-move or trash during triage unless the user explicitly asks.

## Performance rules

- Keep `days_back` small (2 for needs-response, 7 for awaiting-reply).
- Avoid `get_statistics(account_overview)` in the daily loop — use weekly in `email-management`.
- Avoid `all_accounts=True` unless the user has no default account and wants every account.
- Prefer `list_mailboxes(include_counts=false)` when listing folders.

## Related skills

- **`email-management`** — sustained inbox-zero programs and cross-cutting habits  
- **`mailbox-taxonomy`** / **`email-archive-cleanup`** / **`mail-rules-advisor`** — structure, execution, automation proposals  
- **`email-drafting`** + **`email-style-profile`** — replies and voice alignment after triage identifies a message  
- **`email-attachments`** — when the next action is extracting files, not reading queues  
- **`apple-mail-operator`** — onboarding, account/mailbox introspection, troubleshooting
