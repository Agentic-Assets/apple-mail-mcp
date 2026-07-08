# Research paper mail → project tracker (canonical)

When inbound mail clearly belongs to an **active academic paper or R&R**, link operational follow-up to the operator's research issue tracker (e.g. Linear **Research** team: one project per paper).

After edits, run `python3 tools/validators/sync_skill_references.py` to refresh per-skill copies.

## When to trigger

After reading a human co-author or editor message, check for:

- Revise-and-resubmit (R&R) tasking or reviewer workstreams
- Split author assignments (e.g. "your section", attached PDF brief per author)
- Journal / publisher contract or royalty onboarding tied to a known paper
- Empirical specs or deadlines for a named working paper

Do **not** create tracker noise for generic association newsletters or unrelated seminar invites.

## Workflow

1. **Read the mail** by `message_id`; save attachments if the message assigns concrete work (`save_email_attachment` with `message_ids=[...]`).
2. **Find the paper project** in the Research team (project name usually matches the paper title or acronym).
3. **Create or update one issue** per actionable workstream (not per email):
   - Title: paper acronym + deliverable (e.g. `R&R: draft transaction costs spec`)
   - Description: deliverable, deadline, source `message_id`, link to saved brief
   - Assign to the operator; attach PDF via issue attachment upload or linked drive URL
4. **Email vs tracker:**
   - Acknowledgment / timeline → `reply_to_email` draft
   - Spec writing, empirical work, form completion → tracker issue (+ portal action if DocuSign)

## Attachment handling

```
list_email_attachments(message_ids=[...])
save_email_attachment(message_ids=[...], attachment_name="...", save_path="$HOME/...")
```

Save paths must be under the operator home directory. Prefer uploading briefs to the research issue; use cloud storage links only when the tracker cannot accept files.

## Coexistence with inbox triage

- Triage skills stay mail-first; tracker updates are **follow-up**, not a substitute for thread-check and draft rules.
- Log significant mail actions in the operator's local action log when configured; the research tracker is the durable queue for paper work.
