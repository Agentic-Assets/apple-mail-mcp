---
name: apple-mail-operator
description: This skill should be used when the user asks "how does this Mail MCP work", "which tool should I use", "why is Mail slow", "set up Mail for the assistant", "list my accounts/mailboxes", "find an email quickly", or needs safe read/search navigation in Apple Mail with the MCP. Uses list_accounts, list_mailboxes, get_inbox_overview, list_inbox_emails, search_emails, get_email_by_id, and get_email_thread. Do NOT use for sustained inbox-zero programs (see email-management), a 5–10 minute triage ritual (see inbox-triage), or drafting mail (see email-drafting).
---

# Apple Mail Operator

Operational guide for using the Apple Mail MCP safely and quickly. Focus on bootstrap, selecting the correct tool per intent, avoiding slow cross-account scans, and understanding draft-safe versus send-capable setups.

## Large-inbox pre-flight (required when inbox > ~5,000 messages)

See [`large-inbox-rules.md`](../references/large-inbox-rules.md) for the canonical pre-flight checklist.

### When to reach for `full_inbox_export`

`full_inbox_export` is the only tool that walks the entire inbox. Use it for the rare full-inbox case — annual cleanup, full audit, compliance archive — and warn the user it is slow (minutes on a 24k inbox). For routine discovery, always pass a bounded `recent_days` / `max_emails` instead.

## Already-replied safeguard (default)

Before creating a draft reply, the agent **must** verify the user hasn't already replied to the email. The discovery tools enforce this by default:

- `get_needs_response()` filters out already-replied emails (Message-ID matched against the Sent mailbox). Pass `include_already_replied=True` only if the user explicitly asks to see them.
- `list_inbox_emails()` and `search_emails()` accept `exclude_replied=True` and `flag_replied=True` — when sourcing candidates for drafting, set `exclude_replied=True`.
- As a final check before calling `reply_to_email`, fetch the thread with `get_email_thread()` and confirm no message in the thread was sent by the user (one of `list_account_addresses` outputs).

Override: if the user explicitly says "include already-replied" or "I want to redraft", set `include_already_replied=True` (on `get_needs_response`) or `exclude_replied=False` (elsewhere).

See **[[email-drafting]]** for the required pre-draft verification step.

## When To Use This Skill

| Request signal | Use this skill |
|----------------|----------------|
| "How do I configure this?", "What's DEFAULT_MAIL_ACCOUNT?" | Yes |
| "List accounts / aliases / folders" | Yes |
| "How do I read or find a thread without moving mail?" | Yes |
| Performance or timeout troubleshooting | Yes |
| "Clean my inbox forever" / Inbox Zero program | No → `email-management` |
| "Quick scan what needs reply today" | No → `inbox-triage` |
| Draft or send a message | No → `email-drafting` |

## Bootstrap Checklist

1. Confirm Mail.app is running and macOS Automation + Mail Data Access are granted for the host app (terminal or IDE running the MCP).
2. Prefer setting **`DEFAULT_MAIL_ACCOUNT`** so tools default to one account instead of fanning out across every mailbox.
3. Default plugin installs run **`--draft-safe`**: drafts and open-for-review workflows work; **`mode="send"`** paths error until the server is reconfigured intentionally.
4. Set optional **`USER_EMAIL_PREFERENCES`** for stable tone and workflow hints; those preferences surface on preference-aware tool docstrings plus the **`email-style-profile`** skill.

### Missing MCP Tools Red Line

If `mcp__apple-mail__*` tools are absent from the client tool list, stop and fix MCP registration first. Do not create reply drafts through generic AppleScript, Mail UI scripting, shell `osascript`, or a standalone compose fallback. The only acceptable degraded path is the documented MCP-only absolute-path fallback that launches `plugin/start_mcp.sh --draft-safe`; after adding it, restart the MCP client and confirm the Apple Mail tools are registered before drafting.

## Decision Tree — Read And Navigate

| Goal | Primary tool chain |
|------|-------------------|
| See configured accounts | `list_accounts()` |
| See outbound identities | `list_account_addresses(account="...")` |
| Snapshot unread + recent hints | `get_inbox_overview()` — start compact `output_format`, avoid heavy dashboards during debugging |
| Page recent inbox bodies | `list_inbox_emails(max_emails=..., include_content=false|true)` |
| Locate a needle | Narrow `search_emails(...)` (`recent_days=2` unless user insists on widening) → `get_email_by_id(message_id=...)` |
| Conversation context | `get_email_thread(...)` instead of chained subject guesses |
| Mailbox map | `list_mailboxes(include_counts=true)` |
| Idle mail fetch | `synchronize_account(account="...", confirm_sync=True)` only after the user accepts that Mail may download a large backlog |

## Performance Rules

- Run **narrow** queries first (`recent_days` small, explicit `account=`, `include_content=false`, tight `limit`).
- Reserve `all_accounts=True` / cross-account scans for explicit user requests — large Exchange profiles may time out; partial JSON with `errors` is expected behavior.
- Prefer `search_emails(mailboxes=["INBOX", "Sent", ...])` to scan a few named folders over whole-profile fan-out on large Exchange/Gmail accounts. It returns a structured per-folder error for any missing or slow mailbox instead of failing the call.
- After `list_inbox_emails` or `search_emails` returns `message_id`, always drill with `get_email_by_id` rather than fuzzy re-search. `get_email_by_id` is also where per-message recipients (`to`/`cc`/`bcc`) and thread headers (`in_reply_to`/`references`) now come from — bulk `search_emails` no longer returns them.
- **Never verify drafts with `search_emails`.** Drafts are unsent `outgoing message` objects with a null received date, so the date-filtered search is both slow and silently drops them. For troubleshooting, use `verify_draft(draft_id=...)` or `verify_drafts(draft_ids=[...])` for exact readiness checks, `manage_drafts(action="list", subject_contains="...")` for bounded newest-first discovery, `manage_drafts(action="find", in_reply_to="...")` for bounded header lookup, or `get_email_by_id(message_id=..., mailbox="Drafts")` for exact raw message fetches. Route draft lifecycle actions through `email-drafting`. `reply_to_email` verifies its own saved artifact before success: exact Drafts id first, bounded newest-Drafts fallback, and `reply_body` above the quoted original.

## Operator Safety Patterns

| Need | Guidance |
|------|----------|
| No accidental sends | Keep `--draft-safe`; require explicit user confirmation before any send attempt |
| Quiet bulk drafts | Default `mode="draft"` on compose tools; do not leave unsaved compose windows |
| Review each draft in Mail | Use `mode="open"` (saves first, then leaves window open); for rich `.eml`, `review_in_mail=True` |
| Reply to a known message | Use `reply_to_email(message_id=...)`. `compose_email`, `create_rich_email_draft`, and `manage_drafts(action="create")` are standalone-only and **error out** on `Re:`/`Fwd:` subjects or quoted-thread bodies unless you explicitly pass `standalone_confirmed=True` (use that override only for a genuinely new message that happens to start with `Re:`) |
| Read-only auditing | Mention `--read-only` server flag — removes send-facing compose registrations |
| Destructive moves/deletes | Defer to `email-archive-cleanup` or `email-management`; never bury trash/delete actions inside troubleshooting |

### When to reach for `inbox_dashboard`

`inbox_dashboard()` returns a structured snapshot (unread, recent, pinned, suggestions) in a single call. Prefer it over chained `get_inbox_overview` + `list_inbox_emails` + `get_needs_response` calls when:

- `get_inbox_overview` is timing out or returning partial JSON with `errors` on a large inbox.
- The user asks for a visual or one-glance summary ("show me what my inbox looks like", "give me the dashboard").
- You need consolidated unread + recent + suggested-action data without paying three separate AppleScript round-trips.

It is heavier than a compact `get_inbox_overview` on small inboxes, so keep `get_inbox_overview(output_format="compact", ...)` as the daily-loop default. Escalate to `inbox_dashboard()` as the rescue path.

## Additional Resources

- Root README → Configuration section for **`DEFAULT_MAIL_ACCOUNT`**, **`USER_EMAIL_PREFERENCES`**, **`--draft-safe`**, **`--read-only`**.
- Sibling **`inbox-triage`** for scripted daily queues.
- Sibling **`email-management`** when the objective is habitual processing and bulk transformation, not tooling orientation.
