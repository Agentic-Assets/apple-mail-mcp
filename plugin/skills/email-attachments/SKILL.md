---
name: email-attachments
description: This skill should be used when the user asks to "list attachments on messages about X", "save this PDF from email", "which invoices have ZIP files", or needs disk-safe attachment extraction. Uses bounded search_emails (has_attachments filters) to collect message_ids first, then list_email_attachments, save_email_attachment, get_email_by_id for confirmation, and optionally export_emails for bundles. Subject lookup is a degraded discovery path only when exact ids are unavailable. Do NOT use when the real goal is writing responses (email-drafting), diagnosing slow accounts (apple-mail-operator), bulk deleting mail (email-archive-cleanup), or designing folder hierarchies (mailbox-taxonomy).
---

# Email Attachments

Attachment-focused traversal with deliberate **filesystem hygiene**. Never save into sensitive system paths — the MCP blocks known dangerous destinations; still confirm user intention.

## When To Use This Skill

| Signal | Skill |
|--------|-------|
| "Save attachment ..." | Here |
| "What files shipped with invoice thread?" | Here |
| "Reply summarizing attachments" | Start here for inventory → **`email-drafting`** |

## Operational Flow

### 1. Narrow The Message Universe

Prefer known `message_id` from upstream search/list.

Otherwise:

```
search_emails(subject_keyword="...", has_attachments=true, recent_days=7, limit=20)
```

Use the subject search above only as a degraded discovery path after confirming no exact id is available. Review the results and collect `message_id` before listing or saving attachments.

For genuine full-inbox attachment audits (rare), escalate to `full_inbox_export` instead of unbounded `recent_days`.
Widen timeframe only after checking performance.

### 2. Inspect Attachments Cheaply

Prefer ids from step 1:

```
list_email_attachments(message_ids=[12345, 12346], max_results=10)
```

If ids are unknown, run bounded discovery first, then call by reviewed ids:

```
list_email_attachments(message_ids=[12345], max_results=10)
```

See [`large-inbox-rules.md`](../references/large-inbox-rules.md) for the canonical pre-flight.

`list_email_attachments` and `save_email_attachment` require exact `message_ids`; use bounded `search_emails(..., has_attachments=True)` first when ids are unknown.

If duplicates exist, escalate with `search_emails` + **`get_email_by_id`** targeting specific numeric ids prior to save.

### 3. Persist With Validation

```
save_email_attachment(message_ids=["12345"], attachment_name="Quarterly.pdf",
                      save_path="/Users/<user>/Documents/Finance/Quarterly.pdf",
                      message_ids=["12345"])
```

Rules:

- Path must reside under **`$HOME`** per server validation.
- When multiple attachments match partial names, disambiguate with additional filters or sequential saves per `message_ids`.

### 4. Integrity Pass

Echo saved path, approximate size expectation, optionally open file externally (outside MCP).

When batch exports help (entire mailbox evidence trail), optionally layer **`export_emails`** afterward.

### 5. Aftercare

Recommend virus scanning posture for unsolicited archives; never auto-enable macros/ZIPs.

## Pitfalls Table

| Issue | Guidance |
|-------|----------|
| Ambiguous filenames | Prefer exact match substrings surfaced by `list_email_attachments` |
| Password-protected zips | Note inability to introspect payload |
| Extremely large corp attachments | Mention Mail may choke — consider chunked manual download |

## Related Skills

- **`email-drafting`** — cite attachment paths when emailing summaries.
- **`apple-mail-operator`** — if attachment listing times out due to account scope mishaps.
