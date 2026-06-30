# Thread Management

Apple Mail does not expose true conversation threads to AppleScript. The MCP server reconstructs threads by stripping `Re:`, `Fwd:`, and locale-specific prefixes from subjects and grouping the result.

## Tool

`get_email_thread(message_id="...", account="...")` returns the reconstructed conversation around a known Mail message id. Use `subject_keyword` only as a fallback when no id is available.

## When To Use

- The user references a conversation that spans multiple replies.
- A single message lacks context and the prior exchange is needed to understand it.
- Before bulk-archiving a long-running discussion, to confirm the full set of related messages.

## Workflows

### Read a conversation in order

```text
results = search_emails(subject_keyword="Q2 planning", limit=5)
get_email_thread(message_id=results["emails"][0]["message_id"])
```

The result is already chronological. Read top to bottom for context.

### Archive a resolved thread

1. `search_emails(...)` to identify the target message id, then `get_email_thread(message_id="...")` to surface related messages.
2. Collect every `message_id` from the thread result (and any stragglers the user confirms).
3. `move_email(dry_run=True, message_ids=[...], to_mailbox="Archive/2026", max_moves=N)` — quote the count; then `move_email(dry_run=False, message_ids=[...], ...)` after confirmation. Do not pass `subject_keyword=` to `move_email` (returns `TARGET_SELECTOR_DEPRECATED`).

### Find the latest message in a long thread

The last entry returned by `get_email_thread()` is the most recent. Prefer replying with `reply_to_email(message_id=...)` when search or list tools already returned the Mail id; pass `message_id`; if no id is known, run search or list first. For bulk human review, use `mode="open"` so each saved draft stays visible in Mail. See **`email-drafting`** for compose tool selection.

## Cross-Account Threads

`get_email_thread()` honors the same account and mailbox scoping as `search_emails()`. For a thread that spans personal and work accounts, use explicit accounts and mailbox lists first. Whole-account thread scans are slower; use them only when single-account scope is known to be incomplete.

## Limitations

- Subject-prefix stripping is approximate; threads with subject edits ("Q2 planning → revised") will split.
- The MCP server cannot expose the underlying `Message-ID` or `In-Reply-To` headers, so deeply nested forwards may appear as separate threads.
- Use `include_content=True` sparingly on `get_email_thread` — full content for every message in a long thread is expensive.
