# Common Email Workflows - Quick Reference

This document provides ready-to-use workflow templates for common email management tasks. Copy and adapt these patterns to your specific needs.

## Quick Triage Workflows

### Morning Inbox Check (10 minutes)

```
# 1. Get overview
get_inbox_overview()

# 2. Check urgent items
search_emails(subject_keyword="urgent", read_status="unread")
search_emails(subject_keyword="ASAP", read_status="unread")

# 3. Check VIP senders
search_emails(sender_exact="boss@company.com", read_status="unread")
search_emails(sender_exact="key-client@example.com", read_status="unread")

# 4. Flag action items
update_email_status(action="flag", message_ids=[...], max_updates=5)

# 5. Quick cleanup
manage_trash(action="move_to_trash", message_ids=[...], mailbox="INBOX", max_deletes=10)
```

### End of Day Cleanup (5 minutes)

```
# 1. Check unread count
get_mailbox_unread_counts(summary_only=True)

# 2. Quick scan recent
list_inbox_emails(max_emails=20, include_content=False)

# 3. Mark read non-essential
update_email_status(action="mark_read", message_ids=[...], mailbox="INBOX", max_updates=10)

# 4. Archive processed emails
move_email(to_mailbox="Archive", from_mailbox="INBOX", max_moves=20)

# 5. Review flagged items for tomorrow
search_emails(mailboxes=["INBOX", "Archive"], read_status="all")  # Check flags
```

## Search & Find Workflows

### Find Specific Email Thread

```
# Option 1: Search by subject
search_emails(
    account="Work",
    subject_keyword="Project Alpha",
    include_content=True,
    max_results=5,
    max_content_length=300
)

# Option 2: Get full thread
get_email_thread(
    account="Work",
    message_id="<message_id-from-search>",
    mailboxes=["INBOX", "Sent", "Archive"],
    max_messages=20,
    output_format="json",
    include_preview=False
)

# Option 3: Advanced search
search_emails(
    account="Work",
    mailboxes=["INBOX", "Sent", "Archive"],
    subject_keyword="Project Alpha",
    sender_exact="client@example.com",
    include_content=True,
    max_results=10
)
```

### Find Emails from Specific Person

```
# All emails from sender
search_emails(
    account="Work",
    sender_exact="colleague@company.com",
    mailboxes=["INBOX", "Sent", "Archive"],
    recent_days=30,
    max_results=50
)

# Unread emails from sender
search_emails(
    account="Work",
    sender_exact="colleague@company.com",
    read_status="unread",
    mailbox="INBOX"
)

# Emails with attachments from sender
search_emails(
    account="Work",
    sender_exact="colleague@company.com",
    has_attachments=True,
    mailboxes=["INBOX", "Archive"],
    recent_days=30,
    max_results=20
)
```

### Find Emails by Date Range

```
# Emails from last month
search_emails(
    account="Work",
    date_from="2025-01-01",
    date_to="2025-01-31",
    mailboxes=["INBOX", "Sent", "Archive"],
    max_results=100
)

# Recent emails with keyword
search_emails(
    account="Work",
    subject_keyword="invoice",
    date_from="2025-01-15",
    mailboxes=["INBOX", "Archive"],
    max_results=20
)
```

### Find Emails with Attachments

```
# All emails with attachments
search_emails(
    account="Work",
    has_attachments=True,
    mailbox="INBOX",
    max_results=50
)

# Specific sender with attachments
attachment_matches = search_emails(
    account="Work",
    sender_exact="supplier@example.com",
    has_attachments=True,
    mailboxes=["INBOX", "Archive"],
    recent_days=30,
    max_results=20,
    output_format="json",
)

# Review matches, then list attachments by message id
message_ids = [item["message_id"] for item in attachment_matches["items"]]
list_email_attachments(
    account="Work",
    message_ids=message_ids[:5],
)

# Save specific attachment
save_email_attachment(
    account="Work",
    message_id=message_ids[0],
    attachment_name="invoice.pdf",
    save_path="~/Desktop/invoice.pdf"
)
```

## Organization Workflows

### Daily Filing Routine

```
# 1. File project emails
project_matches = search_emails(
    account="Work",
    subject_keyword="Project Alpha",
    mailbox="INBOX",
    read_status="all",
    max_results=10,
    output_format="json",
)

project_ids = [item["message_id"] for item in project_matches["items"]]
move_email(
    account="Work",
    message_ids=project_ids,
    to_mailbox="Projects/Alpha",
    from_mailbox="INBOX",
    max_moves=len(project_ids),
)

# 2. File client emails
client_matches = search_emails(
    account="Work",
    sender_exact="client@example.com",
    mailbox="INBOX",
    recent_days=30,
    max_results=10,
    output_format="json",
)

client_ids = [item["message_id"] for item in client_matches["items"]]
move_email(
    account="Work",
    message_ids=client_ids,
    to_mailbox="Clients/ClientName",
    from_mailbox="INBOX",
    max_moves=len(client_ids),
)

# 3. Preview older read leftovers before archiving
move_email(
    account="Work",
    to_mailbox="Archive",
    from_mailbox="INBOX",
    older_than_days=30,
    only_read=True,
    max_moves=20,
    dry_run=True
)
```

### Bulk Folder Organization

```
# 1. Review current structure
list_mailboxes(account="Work", include_counts=True)

# 2. Identify emails to organize
get_statistics(
    account="Work",
    scope="account_overview",
    days_back=30
)

# 3. Batch move by reviewed ids
# Example: Move all emails from a client
client_matches = search_emails(
    account="Work",
    sender_exact="bigclient@example.com",
    mailboxes=["INBOX", "Archive"],
    recent_days=30,
    max_results=50,
    output_format="json",
)

# Then move them (repeat with batches if >10)
client_ids = [item["message_id"] for item in client_matches["items"]]
move_email(
    account="Work",
    message_ids=client_ids[:10],
    to_mailbox="Clients/BigClient",
    from_mailbox="INBOX",
    max_moves=min(len(client_ids), 10),
)
```

### Archive Old Emails

```
# 1. Find old read emails
search_emails(
    account="Work",
    date_from="2020-01-01",
    date_to="2024-12-31",
    read_status="read",
    mailbox="INBOX",
    max_results=50
)

# 2. Review what you found
# (Check if any need to be kept in current folders)

# 3. Export if important, using a reviewed message id
export_emails(
    account="Work",
    scope="single_email",
    message_id="<reviewed-message-id>",
    mailbox="INBOX",
    save_directory="~/Documents/Email-Archives",
    format="txt"
)

# 4. Move reviewed ids to archive
move_email(
    account="Work",
    message_ids=["<reviewed-message-id-1>", "<reviewed-message-id-2>"],
    to_mailbox="Archive/2024",
    from_mailbox="INBOX",
    max_moves=2,
)
```

## Response Workflows

### Quick Reply

```
# 1. Find the email and keep its message_id
search_emails(
    account="Work",
    subject_keyword="Quick Question",
    include_content=True,
    max_results=1,
    max_content_length=300
)

# 2. Verify the thread has not already been answered by the user
get_email_thread(
    account="Work",
    message_id="<message_id from search>"
)

# 3. Reply immediately by exact id so Mail includes the original thread
reply_to_email(
    account="Work",
    message_id="<message_id from search>",
    reply_body="Yes, that works for me. Thanks!",
    reply_to_all=False
)

# 4. Archive the thread
move_email(
    account="Work",
    message_ids=["<message_id from search>"],
    to_mailbox="Archive",
    from_mailbox="INBOX",
    max_moves=1
)
```

### Deferred Response (Draft)

```
# 1. Review email content and keep its message_id
search_emails(
    account="Work",
    subject_keyword="Complex Request",
    include_content=True,
    max_results=1,
    max_content_length=500
)

# 2. Verify the thread has not already been answered by the user
get_email_thread(
    account="Work",
    message_id="<message_id from search>"
)

# 3. Create a reply draft (native quote + signature; pass native_format=False for a plain-text headless draft) for later
reply_to_email(
    account="Work",
    message_id="<message_id from search>",
    mode="draft",
    reply_body="Thank you for your email. I'm reviewing your request and will provide a detailed response by [date].\n\n[Draft notes: Need to check with team, review budget, etc.]"
)

# 4. Flag original email
update_email_status(
    account="Work",
    action="flag",
    message_ids=["<message_id from search>"],
    mailbox="INBOX",
    max_updates=1
)
```

### Reply to All in Thread

```
# 1. View full thread context
get_email_thread(
    account="Work",
    message_id="<message_id from search/list>",
    mailbox="INBOX",
    max_messages=20
)

# 2. Reply to all by exact id so Mail includes the original thread
reply_to_email(
    account="Work",
    message_id="<message_id from search/list>",
    reply_body="Based on the discussion, I agree with the proposal. Let's move forward.",
    reply_to_all=True
)
```

### Forward with Context

```
# 1. Find the email
matches = search_emails(
    account="Work",
    subject_keyword="Customer Issue",
    include_content=True,
    max_results=1,
    max_content_length=500,
    output_format="json",
)
message_id = matches["items"][0]["message_id"]

# 2. Forward to colleague
forward_email(
    account="Work",
    message_id=message_id,
    to="colleague@company.com",
    message="Hi [Name],\n\nCan you please help with this customer issue? It seems related to your area.\n\nThanks!",
    mailbox="INBOX"
)

# 3. Update status and move
update_email_status(
    account="Work",
    action="mark_read",
    message_ids=[message_id],
    mailbox="INBOX",
    max_updates=1,
)

move_email(
    account="Work",
    message_ids=[message_id],
    to_mailbox="Waiting For",
    from_mailbox="INBOX",
    max_moves=1,
)
```

## Cleanup Workflows

### Delete Spam and Newsletters

```
# 1. Identify unwanted senders
get_statistics(
    account="Personal",
    scope="account_overview",
    days_back=30
)
# Look for frequent senders you don't read

# 2. Search for their emails
search_emails(
    account="Personal",
    sender_exact="newsletter@unwanted.com",
    mailbox="INBOX",
    recent_days=30,
    max_results=50
)

# 3. Bulk delete (move to trash first - reversible)
manage_trash(
    account="Personal",
    action="move_to_trash",
    sender_exact="newsletter@unwanted.com",
    mailbox="INBOX",
    max_deletes=20
)

# 4. Verify trash
search_emails(
    account="Personal",
    sender_exact="newsletter@unwanted.com",
    mailbox="Trash",
    recent_days=30
)

# 5. Permanently delete if confirmed (optional)
manage_trash(
    account="Personal",
    action="delete_permanent",
    sender_exact="newsletter@unwanted.com",
    max_deletes=20
)
```

### Clean Up Old Emails

```
# 1. Find emails older than 90 days
search_emails(
    account="Work",
    date_from="2020-01-01",
    date_to="2024-10-01",
    read_status="read",
    mailbox="INBOX",
    max_results=50
)

# 2. Export important ones first (if needed)
export_emails(
    account="Work",
    scope="entire_mailbox",
    mailbox="INBOX",
    save_directory="~/Documents/Email-Backup",
    format="txt"
)

# 3. Move reviewed ids to archive or delete
move_email(
    account="Work",
    message_ids=["<reviewed-message-id-1>", "<reviewed-message-id-2>"],
    to_mailbox="Archive/2024",
    from_mailbox="INBOX",
    max_moves=2,
)
```

### Empty Trash

```
# 1. Review what's in trash first
search_emails(
    account="Work",
    mailbox="Trash",
    recent_days=30,
    max_results=20
)

# 2. Export if anything important
export_emails(
    account="Work",
    scope="entire_mailbox",
    mailbox="Trash",
    save_directory="~/Desktop/Trash-Backup"
)

# 3. Empty trash (CAREFUL - irreversible)
manage_trash(
    account="Work",
    action="empty_trash",
    confirm_empty=True
)
```

## Draft Management Workflows

### Weekly Draft Review

```
# 1. List all drafts and keep the returned Id for each target draft
manage_drafts(
    account="Work",
    action="list"
)

# 2. Send completed drafts only when the server is intentionally not --draft-safe
#    and the user explicitly confirmed. Default plugin installs block sending.
manage_drafts(
    account="Work",
    action="send",
    draft_id="12345"
)

# 3. Delete outdated drafts
manage_drafts(
    account="Work",
    action="delete",
    draft_id="12346"
)

# 4. Edit others (do in Mail app)
```

### Create Draft for Complex Email

```
# 1. Find the latest message and keep its message_id
search_emails(
    account="Work",
    subject_keyword="Complex Topic",
    mailboxes=["INBOX", "Sent"],
    limit=5
)

# 2. Review the thread by message_id
get_email_thread(
    account="Work",
    message_id="<message_id-from-search>",
    mailboxes=["INBOX", "Sent"],
    output_format="json",
    include_preview=False
)

# 3. Create a reply draft with original thread context
reply_to_email(
    account="Work",
    message_id="<message_id-from-search>",
    cc="team@company.com",
    body="[Draft - Need to expand]\n\n1. Summary of situation\n2. Analysis\n3. Recommendation\n\n[Notes to self: Check data, consult with team]",
    mode="draft"
)

# 4. Schedule time to complete
# (Set calendar reminder to finish draft)
```

## Analysis Workflows

### Weekly Email Analytics

```
# 1. Get account overview
get_statistics(
    account="Work",
    scope="account_overview",
    days_back=7
)

# 2. Analyze top senders
# (Use sender names from overview)
get_statistics(
    account="Work",
    scope="sender_stats",
    sender_exact="frequent-sender@example.com",
    days_back=30
)

# 3. Check mailbox distribution
list_mailboxes(
    account="Work",
    include_counts=True
)

# 4. Review unread counts
get_mailbox_unread_counts(summary_only=True)

# 5. Identify actions:
# - Unsubscribe from high-volume, low-value senders
# - Create folders for frequent senders
# - Archive/delete old emails in cluttered folders
```

### Sender Analysis and Action

```
# 1. Get sender statistics
get_statistics(
    account="Work",
    scope="sender_stats",
    sender_exact="automated-reports@company.com",
    days_back=90
)

# 2. If too many emails, decide action:
#    Option A: Create filter (in Mail app)
#    Option B: Move to dedicated folder
#    Option C: Unsubscribe

# 3. Organize existing emails
report_matches = search_emails(
    account="Work",
    sender_exact="automated-reports@company.com",
    mailboxes=["INBOX", "Archive"],
    recent_days=30,
    max_results=50,
    output_format="json",
)

report_ids = [item["message_id"] for item in report_matches["items"]]
move_email(
    account="Work",
    message_ids=report_ids[:20],
    to_mailbox="Automated Reports",
    from_mailbox="INBOX",
    max_moves=min(len(report_ids), 20),
)
```

## Batch Operation Workflows

### Flag Multiple Emails for Review

```
# 1. Search for pattern
review_matches = search_emails(
    account="Work",
    subject_keyword="Q4 Review",
    mailboxes=["INBOX", "Archive"],
    recent_days=30,
    max_results=20,
    output_format="json",
)

# 2. Batch flag
review_ids = [item["message_id"] for item in review_matches["items"]]
update_email_status(
    account="Work",
    action="flag",
    message_ids=review_ids[:10],
    mailbox="INBOX",
    max_updates=min(len(review_ids), 10),
)
```

### Mark Multiple Emails as Read

```
# 1. Identify emails to mark read
search_emails(
    account="Work",
    sender_domain="notifications.example.com",
    read_status="unread",
    mailbox="INBOX",
    max_results=20
)

# 2. Batch mark as read
update_email_status(
    account="Work",
    action="mark_read",
    sender_domain="notifications.example.com",
    mailbox="INBOX",
    max_updates=20
)
```

### Bulk Move by Sender

```
# 1. Find all emails from sender
search_emails(
    account="Work",
    sender_exact="project-team@company.com",
    mailbox="INBOX",
    recent_days=30,
    max_results=50
)

# 2. Move in batches (max_moves=10 is safe)
move_email(
    account="Work",
    sender_exact="project-team@company.com",
    to_mailbox="Projects/Team Project",
    from_mailbox="INBOX",
    max_moves=10,
    dry_run=True
)

# 3. Repeat if more than 10 emails
# (Run the move_email command again)
```

## Backup and Export Workflows

### Export Important Mailbox

```
# 1. Check mailbox contents
search_emails(
    account="Work",
    mailbox="Important Project",
    recent_days=30,
    max_results=20
)

# 2. Export entire mailbox
export_emails(
    account="Work",
    scope="entire_mailbox",
    mailbox="Important Project",
    save_directory="~/Documents/Email-Backups/Important-Project",
    format="txt"
)

# 3. Verify export
# (Check ~/Documents/Email-Backups/Important-Project directory)
```

### Export Single Important Email

```
# 1. Find the email
contract_matches = search_emails(
    account="Work",
    subject_keyword="Contract Agreement",
    include_content=True,
    max_results=1,
    max_content_length=0,  # Full content
    output_format="json",
)
message_id = contract_matches["items"][0]["message_id"]

# 2. Export with attachments
list_email_attachments(
    account="Work",
    message_ids=[message_id],
)

save_email_attachment(
    account="Work",
    message_id=message_id,
    attachment_name="contract.pdf",
    save_path="~/Documents/Contracts/contract.pdf"
)

# 3. Export email text
export_emails(
    account="Work",
    scope="single_email",
    message_id=message_id,
    save_directory="~/Documents/Contracts",
    format="html"
)
```

## Weekly Maintenance Workflow

```
# Monday Morning (30 min)

# 1. Review weekend emails
get_inbox_overview()

# 2. Triage urgent items
search_emails(subject_keyword="urgent", read_status="unread")
search_emails(subject_keyword="action required", read_status="unread")

# 3. Process inbox to zero
# (Use inbox zero workflow)

# 4. Review weekly tasks
search_emails(mailboxes=["INBOX", "Archive"], read_status="all")  # Check flags

# 5. Set up for the week
manage_drafts(action="list")
```

```
# Friday Afternoon (30 min)

# 1. Complete pending replies
manage_drafts(action="list")
# Send or delete drafts by exact draft_id from the list output

# 2. Clean up flagged items
search_emails(mailboxes=["INBOX", "Archive"], read_status="all")  # Review flags
update_email_status(action="unflag", ...)  # Clear completed

# 3. Archive week's emails
move_email(to_mailbox="Archive", from_mailbox="INBOX", max_moves=50)

# 4. Review statistics
get_statistics(scope="account_overview", days_back=7)

# 5. Plan for next week
# Note patterns, senders to filter, folders to create
```

## Tips for Using These Workflows

1. **Copy and adapt**: These are templates - adjust parameters for your needs
2. **Chain commands**: Run multiple commands in sequence for complex workflows
3. **Use max limits**: Always respect max_moves, max_deletes safety limits
4. **Review before deleting**: Always search first, then delete
5. **Export before cleanup**: Backup important emails before bulk operations
6. **Start small**: Test with small max values (5-10) before increasing

## Quick Reference: Most Common Commands

```
# Daily essentials
get_inbox_overview()
list_inbox_emails(max_emails=20)
search_emails(subject_keyword="...", mailboxes=["INBOX", "Archive"])
get_email_thread(message_id="<message_id from search>")
reply_to_email(message_id="<message_id from search>", reply_body="...")
move_email(to_mailbox="Archive", from_mailbox="INBOX", max_moves=10)

# Weekly maintenance
list_mailboxes(include_counts=True)
manage_drafts(action="list")
get_statistics(scope="account_overview", days_back=7)
update_email_status(action="flag" or "mark_read", ...)

# Cleanup operations
manage_trash(action="move_to_trash", ...)
export_emails(scope="entire_mailbox", mailbox="...", ...)
```

---

**Remember**: These workflows are starting points. Adapt them to your specific email patterns and work style.
