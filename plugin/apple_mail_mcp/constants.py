"""Shared constants for Apple Mail MCP tools."""

# Newsletter detection patterns (sender-based)
NEWSLETTER_PLATFORM_PATTERNS = [
    "substack.com",
    "beehiiv.com",
    "mailchimp",
    "sendgrid",
    "convertkit",
    "buttondown",
    "ghost.io",
    "revue.co",
    "mailgun",
]

NEWSLETTER_KEYWORD_PATTERNS = [
    "newsletter",
    "digest",
    "weekly",
    "daily",
    "bulletin",
    "briefing",
    "news@",
    "updates@",
]

# Folders to skip during broad searches.
# Includes localized variants so non-English Mail.app accounts (Exchange,
# Outlook, Gmail in non-English locales) skip system folders correctly.
SKIP_FOLDERS = [
    # English / IMAP standards
    "Trash",
    "Junk",
    "Junk Email",
    "Deleted Items",
    "Sent",
    "Sent Items",
    "Sent Messages",
    "Drafts",
    "Spam",
    "Deleted Messages",
    # French (Exchange/Outlook + Gmail FR)
    "Corbeille",
    "Courrier indésirable",
    "Indésirables",
    "Éléments supprimés",
    "Éléments envoyés",
    "Messages envoyés",
    "Brouillons",
    "Boîte d'envoi",
    # German
    "Papierkorb",
    "Gesendet",
    "Entwürfe",
    "Werbung",
    # Spanish
    "Papelera",
    "Enviados",
    "Borradores",
    "Correo no deseado",
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
#
# Hard ceilings (2026-07, AGENTIC-988 bounded-export hardening): on a
# ~9,700-message Exchange inbox, cold-cache property reads on a 250-message
# scan slice (search) or 500-message bind (inbox read-status filter) blew
# past wrapper timeouts. `SEARCH_HARD_CEILING` / `INBOX_HARD_CEILING` clamp
# every scan path to 50 messages read per call regardless of how the
# scaled caps below compute.
# ---------------------------------------------------------------------------
SCAN_BOUNDS = {
    # Compose subject/draft fallbacks (drafts mailboxes are usually small).
    "DRAFT_LOOKUP": 75,
    "MESSAGE_LOOKUP": 75,
    # Trash listing and analytics slices.
    "TRASH_SCAN": 100,
    # smart_inbox per-mailbox ceilings.
    "INBOX_SHORT": 25,
    "INBOX_LONG": 75,
    # list_inbox_emails unread/read filter: scans up to max(max_emails*10, floor).
    "INBOX_DEFAULT_CAP": 100,
    "INBOX_MAX_CAP": 50,
    # Hard ceiling applied after INBOX_MAX_CAP in _build_inbox_collection_block.
    "INBOX_HARD_CEILING": 50,
    # search_emails window scaling via bounded_scan.compute_scan_upper_bound().
    "SEARCH_BASE_CAP": 40,
    "SEARCH_WINDOW_CAP": 50,
    "SEARCH_DAYS_SCALE": 3,  # added per recent_days day (was 25 inline)
    "BODY_SEARCH_AUTO_CAP": 25,  # body_text without explicit date_from
    # Hard ceiling applied after scan_cap in _build_search_script.
    "SEARCH_HARD_CEILING": 50,
    # Multi-mailbox search fan-out limits.
    "MAX_MAILBOXES_PER_SEARCH": 20,
    "MAX_MAILBOXES_PER_SEARCH_ALL": 10,
}
