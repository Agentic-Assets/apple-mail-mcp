# Pre-draft verification (canonical)

Canonical source: `plugin/skills/references/pre-draft-verification.md`. After edits, run `python3 tools/validators/sync_skill_references.py` to refresh per-skill copies under `*/references/`.

Load **`email-drafting`** for compose tool selection, native reply defaults, and post-draft checks. This reference is the single source for **already-replied** and **pre-draft thread** rules.

## Already-replied safeguard

Before `reply_to_email`, verify the user has not already replied:

1. **Discovery:** `get_needs_response(check_already_replied=True, include_already_replied=False)` or `search_emails` / `list_inbox_emails` with `exclude_replied=True`.
2. **Thread check:** `get_email_thread(message_id=...)` then compare thread senders to `list_account_addresses(account=...)`. If any message was sent by the user, **abort** unless they explicitly asked to redraft.
3. **Override:** only when the user says "include already-replied" or "redraft"; set `include_already_replied=True` or `exclude_replied=False`.

## Reply workflow (ID-first)

```text
# 1. Discover (bounded)
results = search_emails(..., output_format="json")  # or list_inbox_emails
message_id = results["items"][0]["message_id"]      # list_inbox_emails uses ["emails"]

# 2. Verify thread (required)
get_email_thread(message_id=message_id, output_format="json")

# 3. Draft in-thread (default native_format=True; needs Mail focus + Accessibility)
reply_to_email(message_id=message_id, reply_body="...", mode="draft")
```

- Never pass `subject_keyword` to `reply_to_email` or `forward_email` (`TARGET_SELECTOR_DEPRECATED`).
- Never use `compose_email`, `create_rich_email_draft`, or `manage_drafts(action="create")` for thread replies.
- On `REPLY_WINDOW_FOCUS_FAILED`: no draft was saved. Retry with Mail visible and not being clicked. Do not switch to `native_format=False`; it is gated (`WINDOWLESS_FALLBACK_DISABLED`) and reserved for deliberate headless/CI via `allow_windowless_fallback=True`, which agents must never set. If focus still cannot be acquired, stop and report the blocker.

## JSON key reminder

| Discovery tool | Id list key |
|----------------|-------------|
| `search_emails(output_format="json")` | `results["items"]` |
| `list_inbox_emails(output_format="json")` | `results["emails"]` |
