# Pre-draft verification (canonical)

Canonical source: `plugin/skills/references/pre-draft-verification.md`. After edits, run `python3 tools/validators/sync_skill_references.py` to refresh per-skill copies under `*/references/`.

Load **`email-drafting`** for compose tool selection, native reply defaults, and post-draft checks. This reference is the single source for **already-replied** and **pre-draft thread** rules. For **processing order** (newest first, small batches), see [`recent-first-triage.md`](recent-first-triage.md).

## Already-replied safeguard

Before `reply_to_email`, verify the user has not already replied:

1. **Discovery:** `get_needs_response(check_already_replied=True, include_already_replied=False)` or `search_emails` / `list_inbox_emails` with `exclude_replied=True`.
2. **Thread check:** `get_email_thread(account=..., message_id=...)` then search Sent for operator addresses. **Abort only when the operator sent a message after the latest inbound.** An older operator reply before the newest inbound does not count as already handled.
3. **Co-founder / teammate coverage (Agentic Assets commercial mail):** A co-founder or teammate reply to an external partner does **not** close the thread for the operator on Tier-1 partner, client, or investor relationships. Still draft a short courtesy acknowledgment unless the operator already replied in Sent after the inbound.
4. **Courtesy default:** When uncertain on warm human mail, default to a 1–3 sentence courtesy draft. Skipping requires a specific reason (operator replied after inbound, verified good draft exists, clear automated noise, cold PR).
5. **Override:** only when the user says "include already-replied" or "redraft"; set `include_already_replied=True` or `exclude_replied=False`.

## Reply workflow (ID-first)

```text
# 1. Discover (bounded)
results = search_emails(..., output_format="json")  # or list_inbox_emails
message_id = results["items"][0]["message_id"]      # list_inbox_emails uses ["emails"]

# 2. Verify thread (required)
get_email_thread(account=..., message_id=message_id, output_format="json")

# 3. Draft in-thread (default native_format=True; needs Mail focus + Accessibility)
reply_to_email(message_id=message_id, reply_body="...", mode="draft")
```

- Never pass `subject_keyword` to `reply_to_email` or `forward_email` (`TARGET_SELECTOR_DEPRECATED`).
- Never use `compose_email`, `create_rich_email_draft`, or `manage_drafts(action="create")` for thread replies.
- On `REPLY_WINDOW_FOCUS_FAILED`: log `draft_deferred` in action_log with intended one-sentence body summary, continue other courtesy drafts in the batch, and report the blocker. Retry with Mail visible when practical. Do not switch to `native_format=false`; it is gated (`WINDOWLESS_FALLBACK_DISABLED`) and reserved for deliberate headless/CI via `allow_windowless_fallback=True`, which agents must never set.

## JSON key reminder

| Discovery tool | Id list key |
|----------------|-------------|
| `search_emails(output_format="json")` | `results["items"]` |
| `list_inbox_emails(output_format="json")` | `results["emails"]` |
