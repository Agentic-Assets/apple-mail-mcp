# Pre-draft verification (canonical)

Canonical source: `plugin/skills/references/pre-draft-verification.md`. After edits, run `python3 tools/validators/sync_skill_references.py` to refresh per-skill copies under `*/references/`.

Load **`email-drafting`** for compose tool selection, native reply defaults, and post-draft checks. This reference is the single source for **already-replied** and **pre-draft thread** rules. For **processing order** (newest first, small batches), see [`recent-first-triage.md`](recent-first-triage.md).

## Already-replied safeguard

Discovery rows self-report reply/draft state: `was_replied_to` (bool, always present) and `has_draft` (true / false / null) sit on every row from `list_inbox_emails`, `search_emails`, `get_email_by_id`, `get_email_by_ids`, `get_email_thread`, `get_needs_response`, `inbox_dashboard`, and `get_inbox_overview` recent rows, alongside a top-level `draft_scan` status object. Annotation is automatic; no kwarg is required to turn it on.

1. **Discovery is safe by default:** `get_needs_response(...)` already excludes rows where `was_replied_to=true` or `has_draft=true`, and reports `skipped_replied_count` / `skipped_drafted_count` so the exclusion stays visible. `list_inbox_emails` / `search_emails` keep `exclude_replied` (now backed by the native flag, no Sent scan needed) plus the new `exclude_drafted=False`.
2. **Has-draft check (primary):** never draft a reply when the candidate row shows `has_draft=true`; a matching draft already exists. `has_draft=null` means the draft scan was skipped or errored (check `draft_scan.status`), not that no draft exists, so fall back to a manual check: `manage_drafts(action="find", ...)` or the "Missed-replies queue" workflow in `email-drafting`.
3. **Replied check:** `was_replied_to=true` is Mail's native reply flag; when it is true and bullet 2 shows no matching draft, do not draft: abort and report which reply already covers it. **Thread check (manual fallback, only when row fields are absent):** `get_email_thread(account=..., message_id=...)` then search Sent for operator addresses. In that manual fallback, **abort only when the operator sent a message after the latest inbound** (this timing caveat governs the manual Sent-scan fallback, not Mail's native `was_replied_to` flag); an older operator reply before the newest inbound does not count as already handled.
4. **Co-founder / teammate coverage (Agentic Assets commercial mail):** A co-founder or teammate reply to an external partner does **not** close the thread for the operator on Tier-1 partner, client, or investor relationships. Still draft a short courtesy acknowledgment unless the operator already replied in Sent after the inbound.
5. **Courtesy default:** When uncertain on warm human mail, default to a 1–3 sentence courtesy draft. Skipping requires a specific reason, evidenced primarily by the new fields: operator already replied (`was_replied_to=true` / thread check), a verified good draft already exists (`has_draft=true` / `verify_draft`), clear automated noise, or cold PR.
6. **Override:** `get_needs_response` is the only tool that excludes replied/drafted rows by default; override it with `include_already_replied=True` / `include_drafted=True` only when the user says "include already-replied", "include drafted", or "redraft". `list_inbox_emails` / `search_emails` return every row by default (no exclusion), so check each row's `was_replied_to` / `has_draft` before drafting; use `exclude_replied=True` / `exclude_drafted=True` as opt-in narrowing when you want the tool to filter for you instead.

**`include_draft_state`:** all 8 annotated tools accept `include_draft_state: bool = True`. Default `True` runs the bounded Drafts snapshot that produces `has_draft`; pass `False` only for the bare-fastest listing on a huge account, in which case `has_draft` comes back `null` and nothing is excluded for draft state, so fall back to a manual draft check (bullet 2).

## Reply workflow (ID-first)

```text
# 1. Discover (bounded): was_replied_to/has_draft on each row are the primary replied-state check
results = search_emails(..., output_format="json")  # or list_inbox_emails
message_id = results["items"][0]["message_id"]      # list_inbox_emails uses ["emails"]

# 2. Thread check (recommended context; run when row fields are absent, has_draft=null, or extra certainty is needed)
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

Every row in both also carries `was_replied_to` and `has_draft`; the response carries a top-level `draft_scan`. Text (non-JSON) output shows the same signal inline via `[REPLIED]` / `[HAS DRAFT]` markers; JSON remains the recommended format for programmatic checks.
