# Recent-first, small-batch triage (canonical)

Canonical source: `plugin/skills/references/recent-first-triage.md`. After edits, run `python3 tools/validators/sync_skill_references.py` to refresh per-skill copies under `*/references/`.

When the user asks to **check mail**, **triage**, or **draft replies**, work the **newest received messages first** in **small batches**. Do not bury fresh inbox items under wide historical scans.

## Order of operations

1. Start from the **most recently received** messages in the target account or folder.
2. Process **one small batch at a time** (default **3 to 5** messages), newest to oldest within that batch.
3. Finish read, thread-check, draft-or-no-action, and verification for that batch **before** pulling the next batch.
4. Only move to older mail after the current recent window is cleared or the user explicitly asks to go deeper.

## Discovery limits (default triage pass)

| Tool | Default | Avoid on first pass |
|------|---------|---------------------|
| `list_inbox_emails` | `max_emails=5` (raise to 8 only if needed) | `max_emails=25` or larger |
| `search_emails` | `limit=5`, `recent_days=2` to `7` | `limit=30+`, `date_from` weeks/months ago, domain-wide sweeps |
| `get_needs_response` | `days_back=3`, `max_results=5` first | `days_back=30` as the opening move |
| `get_email_thread` | anchor on current `message_id`; small `recent_days` | 30 to 60 day subject-only scans to "find work" |

## Per-message loop (within each batch)

For each message, in **recency order**:

1. Read by exact id: `get_email_by_id(message_id=...)` or from the bounded list row.
2. Thread-check across Inbox, Sent, and Drafts: `get_email_thread(message_id=...)`.
3. If the user already replied after the latest inbound, mark **no-action** and continue.
4. If a reply is needed, draft **that one thread** with `reply_to_email(message_id=..., mode="draft")`, verify, then move to the next message.
5. Pull the next batch of 3 to 5 older messages only after the current batch is complete.

## Anti-patterns

- Do **not** open with `date_from` weeks or months ago and draft replies to stale threads while newer inbox mail is unreviewed.
- Do **not** batch-draft many old threads in one pass because they appear in a broad `sender_domain` or `utulsa.edu` search.
- Do **not** treat **unread** alone as "needs reply" when the message is weeks old and newer human mail is still in queue.
- Do **not** fan out `get_needs_response(days_back=30)` plus `list_inbox_emails(max_emails=25)` plus wide `search_emails` in parallel on the first pass.

## Handoffs

- **Read-only scan:** `inbox-triage` (this reference + `large-inbox-rules.md`).
- **Reply after one message is chosen:** `email-drafting` + `pre-draft-verification.md`.
- **Navigation / tool choice:** `apple-mail-operator`.
