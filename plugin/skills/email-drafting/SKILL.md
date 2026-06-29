---
name: email-drafting
description: 'Use when drafting, replying, forwarding, or verifying Apple Mail drafts. Covers compose_email, reply_to_email, forward_email, create_rich_email_draft, manage_drafts, and verify_draft with draft-safe defaults, exact message ids for replies, standalone-draft guardrails, signatures, and post-draft verification. Do NOT use for inbox triage, Mail MCP setup, folder taxonomy, Mail rules, staged bulk moves, or attachment extraction.'
---

# Email Drafting

Compose-first workflows against Apple Mail. Default plugin installs run **`--draft-safe`**: compose tools default to quiet `mode="draft"` (no leftover compose windows), and `mode="send"` returns a structured error until the server is reconfigured. When send is allowed, still confirm intent with the user before calling `mode="send"` or `manage_drafts(action="send")`.

## Already-replied safeguard (default)

Before creating a draft reply, the agent **must** verify the user hasn't already replied to the email. The discovery tools enforce this by default:

- `get_needs_response()` filters out already-replied emails (Message-ID matched against the Sent mailbox). Pass `include_already_replied=True` only if the user explicitly asks to see them.
- `list_inbox_emails()` and `search_emails()` accept `exclude_replied=True` and `flag_replied=True` — when sourcing candidates for drafting, set `exclude_replied=True`.
- As a final check before calling `reply_to_email`, fetch the thread with `get_email_thread()` and confirm no message in the thread was sent by the user (one of `list_account_addresses` outputs).

Override: if the user explicitly says "include already-replied" or "I want to redraft", set `include_already_replied=True` (on `get_needs_response`) or `exclude_replied=False` (elsewhere).

## Pre-draft verification (required before replying)

Run this **before** any `reply_to_email` call:

1. Fetch the conversation: `get_email_thread(message_id=...)` (or the equivalent account + subject signature when no id is available).
2. Cross-check senders in the thread against `list_account_addresses(account=...)`. If any message in the thread was sent by one of the user's own addresses, **abort the draft** and report which message already replied (date, subject snippet) — unless the user explicitly said "redraft" or "include already-replied".
3. Only after the thread shows no user-sent message do you proceed to `reply_to_email(message_id=...)`.

Never use standalone draft creators (`compose_email`, `create_rich_email_draft`, or `manage_drafts(action="create")`) to answer an existing message. They create standalone new messages, so the original chain is not included. If a standalone draft has a `Re:` / `Fwd:` subject or quoted-thread body, the tool returns an error unless `standalone_confirmed=True`; use that override only for a genuinely new message whose subject happens to look threaded.

## When To Use This Skill

| Request signal | Use this skill |
|----------------|----------------|
| "Reply / forward / write / draft" | Yes |
| "Make a nicer HTML newsletter-style draft" | Yes |
| Manage open drafts (`list/send/delete`) with guardrails | Yes |
| Bulk archive or reorganize folders | No → `email-archive-cleanup` |
| Decide folder strategy | No → `mailbox-taxonomy` |

## Preconditions

1. Know the **`account`** (defaults follow `DEFAULT_MAIL_ACCOUNT`) and signature intent. Compose/reply/forward default to **`include_signature=True`**, which applies **`DEFAULT_MAIL_SIGNATURE`** when that env var is set; when unset, the tool does not force a named signature and Mail may still apply the account's normal default signature. Pass `include_signature=False` to suppress plugin-applied signatures, or `signature_name` to override the default for one call. For replies, disabling signatures cannot skip `reply_body` insertion.
2. Confirm the `mcp__apple-mail__*` tools are actually registered before any drafting call. If they are absent, fix MCP registration or use the documented MCP-only absolute-path fallback; do not draft with generic AppleScript, Mail UI scripting, shell `osascript`, or a standalone compose fallback.
3. For replies/forwards, use the Mail **`message_id`** returned by `search_emails`, `list_inbox_emails`, `get_email_by_id`, or thread tools whenever available. Do not switch to `subject_keyword` just because the subject is visible; subject lookup is only for cases where no message id is available.
4. Reply drafting requires `reply_to_email(message_id=...)`. Use `subject_keyword` or any standalone/degraded fallback for a reply only if Cayman explicitly approves that degraded path for the specific message.
5. Load **`USER_EMAIL_PREFERENCES`** plus any capture from **`email-style-profile`** before writing content.

## Pre-call Checklist (every mutate call)

Restate these in chat **before** invoking `compose_email`, `reply_to_email`, `forward_email`, `create_rich_email_draft`, or `manage_drafts(action="send")`:

1. **Recipients** — explicit `to`, `cc`, `bcc`. Confirm spelling and that no replies stay private.
2. **Subject line** — exact text. For replies/forwards, confirm the inherited subject if Mail will prepend `Re:` / `Fwd:`.
3. **Mode** — `draft` (quiet save, default) vs `open` (saved + window stays open for review) vs `send` (blocked under `--draft-safe`). `mode="send"` requires explicit user confirmation and a non-draft-safe configuration.
4. **Signature intent** — `include_signature=True` applies `DEFAULT_MAIL_SIGNATURE` if set; when unset, Mail may still apply the account's normal default signature. Pass `signature_name` to override, `include_signature=False` to suppress plugin-applied signatures. For replies, disabling signatures cannot skip body insertion above the quoted original. `create_rich_email_draft` does not accept signature params — switch to a plain compose tool when a named signature is required.
5. **Source message id** (replies/forwards only) — pass the `message_id` returned by search/list. Fall back to `subject_keyword` only when no id is available.
6. **Standalone-confirmed override** — `compose_email`, `create_rich_email_draft`, and `manage_drafts(action="create")` refuse `Re:`/`Fwd:` subjects or bodies containing quoted-thread markers and return a structured error. If the user genuinely wants a fresh standalone message that happens to look threaded (e.g. a new "Re: weekly review" note unrelated to any prior thread), pass `standalone_confirmed=True` (CLI: `--standalone-confirmed`); never use this override to substitute for `reply_to_email` / `forward_email`.

**Large-inbox caveat:** the pre-draft `get_email_thread` verification can stall on long threads in a 24k mailbox. Prefer fetching by `message_id` (`get_email_thread(message_id=...)` or `get_email_by_id(message_id=...)`) rather than re-resolving the thread via account + subject signature.

`mode="open"` saves first then leaves the compose window open, so closing it should not trigger Mail's Save/Don't Save prompt.

## Tool Selection Pattern

| Situation | Tool | Notes |
|-----------|------|-------|
| New outbound mail | `compose_email` | Standalone only; default `mode="draft"`; use `mode="open"` only for explicit saved-open review; `mode="send"` blocked under `--draft-safe` |
| Structured reply context | `reply_to_email` | Default quiet draft (`send=False` / `mode="draft"`); pass `message_id=...` from search/list; `subject_keyword` is fallback only; verification requires `reply_body` above the quoted original |
| Share thread outward | `forward_email` | Default `mode="draft"`; pass `message_id=...` from search/list; `subject_keyword` is fallback only |
| Marketing / HTML layout | `create_rich_email_draft` | Standalone only; produces multipart `.eml`, saves to Drafts by default; use `review_in_mail=True` for saved-open review; no Mail signature params — use plain compose tools when a named signature is required |
| Low-level draft listing / CRUD | `manage_drafts` | Standalone `action="create"` only; respect cap defaults; never batch-delete without confirming folder scope. `action="list"` returns each draft's Id, To, and a body snippet (triage without re-fetching), reads **newest drafts first**, accepts `limit=...`, and accepts `subject_contains="..."` (case-insensitive "find the draft I just made"). `action="find"` locates reply drafts by bounded In-Reply-To / References header scan. For `send`, `open`, or `delete`, prefer exact `draft_id` from the list output over `draft_subject` |
| Exact draft readiness check | `verify_draft` | Read-only JSON snapshot for one Drafts id: recipients, body sentinel, attachments, signature state, quoted original, and thread headers |
| Remove orphaned blank drafts | `manage_drafts(action="cleanup_empty")` | Deletes drafts with blank subject AND empty body; `dry_run=True` by default (preview first), capped by `max_deletes` (default 20). Confirm the preview count with the user before `dry_run=False` |

## Safety And Compliance

| Risk | Mitigation |
|------|-------------|
| Accidental dispatch | Maintain `--draft-safe`; disallow `mode="send"` silently |
| Over-broad lookups | Prefer `message_id` from search/list; when id is unknown, narrow `recent_days` and anchor `subject_keyword` |
| Sensitive content | Warn before quoting full threads into new messages |
| Signature alignment | Prefer matching recent Sent-tone via `email-style-profile` routines |

### Draft-Safe And Read-Only Modes Reminder

- **`--draft-safe`** (default plugin install): `compose_email`, `reply_to_email`, and `forward_email` stay on quiet `mode="draft"` unless the user explicitly requests `mode="open"`; `mode="send"` and `manage_drafts(action="send")` return structured errors — treat send requests as drafting tasks until configuration changes.
- **`--read-only`**: Unregisters `compose_email`, `reply_to_email`, and `forward_email`; also enables draft-safe send blocking for `manage_drafts(action="send")`. `create_rich_email_draft` and `manage_drafts` remain for draft workflows where permitted.

## Rich Draft Guidance

Choose `create_rich_email_draft` when plain-text AppleScript insertion would show escaped HTML artifacts. With a nonblank subject and default `open_in_mail=True`, it writes the `.eml`, opens Mail only long enough to save the draft, then closes the fresh compose window. Blank subject → `.eml` only (Mail not opened). Use `open_in_mail=False` when the caller only needs the `.eml` artifact, and use `review_in_mail=True` only when the user explicitly wants Mail left open after the draft has been saved.

## Post-Draft Verification

Summarize artifacts for the operator:

1. Mailbox + identifiers (`message_id` if surfaced).
2. Draft location and whether Mail was left open for explicit review.
3. For replies, whether `reply_body` was verified above the quoted original.
4. Next actions (edit, attachments, approvals).

To verify a freshly-created draft, do **not** use `search_emails` — it runs a
date-filtered scan that is slow on large accounts and silently drops brand-new
drafts (an unsent `outgoing message` has a null received date). Instead use the
exact Drafts verification: `verify_draft(draft_id="...", expected_body_contains="...")`
or bounded Drafts lookup: `manage_drafts(action="list", subject_contains="...")`
(newest-first) or `get_email_by_id(message_id=..., mailbox="Drafts")`. Use
the returned exact `draft_id` for `manage_drafts(action="open"|"delete"|"send")`. Confirm
`to`/`cc` are the intended recipients and the body is present. For replies,
`reply_body` must appear above the quoted original; mere presence below the
quote is not enough.

**Reply threading note:** `reply_to_email` uses Mail's native `reply` command,
constructs and assigns the new plain-text `reply_body` above the quoted-original
block, then saves. Verification checks the exact Drafts artifact id first when
Mail exposes one, falls back to bounded newest-Drafts only when needed, and fails
with a structured artifact id if the body is missing or appears after the quote.
For machine-readable reply draft metadata, call `reply_to_email(..., output_format="json")`
to get `draft_id`, `verified_draft_id`, verification status, mode, and `sent`.
`body_html`
on `reply_to_email` is accepted for compatibility but ignored; use
`create_rich_email_draft` / `compose_email` only for rich HTML on a genuinely
standalone message.

## Related Skills

- **`email-style-profile`** — learn voice from Sent mail samples.
- **`email-attachments`** — after drafting, optionally attach binaries with validated filesystem paths (`compose_*` attachments parameters).
- **`apple-mail-operator`** — if tools error on account scope or timeouts, fix infra before rewriting prose.
