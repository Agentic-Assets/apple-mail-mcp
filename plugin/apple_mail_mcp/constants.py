"""Shared constants for Apple Mail MCP tools."""

# Newsletter detection patterns (sender-based)
NEWSLETTER_PLATFORM_PATTERNS = [
    "substack.com", "beehiiv.com", "mailchimp", "sendgrid",
    "convertkit", "buttondown", "ghost.io", "revue.co", "mailgun",
]

NEWSLETTER_KEYWORD_PATTERNS = [
    "newsletter", "digest", "weekly", "daily",
    "bulletin", "briefing", "news@", "updates@",
]

# Folders to skip during broad searches.
# Includes localized variants so non-English Mail.app accounts (Exchange,
# Outlook, Gmail in non-English locales) skip system folders correctly.
SKIP_FOLDERS = [
    # English / IMAP standards
    "Trash", "Junk", "Junk Email", "Deleted Items",
    "Sent", "Sent Items", "Sent Messages", "Drafts",
    "Spam", "Deleted Messages",
    # French (Exchange/Outlook + Gmail FR)
    "Corbeille", "Courrier indésirable", "Indésirables",
    "Éléments supprimés", "Éléments envoyés", "Messages envoyés",
    "Brouillons", "Boîte d'envoi",
    # German
    "Papierkorb", "Gesendet", "Entwürfe", "Werbung",
    # Spanish
    "Papelera", "Enviados", "Borradores", "Correo no deseado",
]

# Thread subject prefixes to strip when matching threads
THREAD_PREFIXES = ["Re:", "Fwd:", "FW:", "RE:", "Fw:"]

# Human-friendly time range mappings (name -> days)
TIME_RANGES = {
    "today": 1,
    "yesterday": 2,
    "week": 7,
    "month": 30,
    "all": 0,
}


# ---------------------------------------------------------------------------
# Scan bounds (Phase A of the whose-elimination refactor)
#
# Centralized bounded-slice caps that will replace per-tool magic numbers in
# wave-2 migrations. Kept here so a single edit retunes every tool.
# ---------------------------------------------------------------------------
SCAN_BOUNDS = {
    "DRAFT_LOOKUP": 100,        # was DRAFT_LIST_CAP in compose.py
    "MESSAGE_LOOKUP": 100,      # was MESSAGE_LOOKUP_CAP in compose.py
    "TRASH_SCAN": 200,          # was SCAN_CAP in manage.py
    "INBOX_SHORT": 30,          # smart_inbox short windows
    "INBOX_LONG": 100,          # smart_inbox long windows
    "INBOX_DEFAULT_CAP": 200,   # list_inbox_emails baseline
    "INBOX_MAX_CAP": 1000,      # list_inbox_emails upper bound
    "SEARCH_BASE_CAP": 200,
    "SEARCH_WINDOW_CAP": 500,
    "MAX_MAILBOXES_PER_SEARCH": 50,  # search_emails mailbox="All" cap
}
