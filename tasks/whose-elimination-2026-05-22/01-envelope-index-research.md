# Envelope Index Research — Bypassing AppleScript for Read-Only Mail Queries

**Date:** 2026-05-22
**Scope:** Feasibility of reading Apple Mail's SQLite `Envelope Index` directly to replace `osascript whose`-based listing on a 24k-message Exchange inbox.

---

## TL;DR

- **Feasible and very high upside.** The local machine confirms `~/Library/Mail/V10/MailData/Envelope Index` is a vanilla SQLite3 file (WAL mode), 104 MB, indexing 44,812 messages across 180 mailboxes — including the user's Exchange (EWS) inbox. Indexes like `messages_mailbox_date_received_index` make "newest N in mailbox X" a millisecond operation versus AppleScript's multi-minute `whose` materialization.
- **Biggest risk: Apple's schema is undocumented and shifts between minor macOS releases.** Today's local DB reports `properties.version=4, minor_version=84003`. We must detect schema version at runtime and refuse to query on mismatch rather than crash.
- **Biggest win: list/search/count/overview tools collapse from O(mailbox size) AppleScript walks to single SQL queries.** All the slow tools in this repo (`list_inbox_emails`, `get_inbox_overview`, `search_emails`, `get_top_senders`, `get_statistics`, `get_mailbox_unread_counts`) become read-only SQL with bounded latency.
- **Schema version coverage:** Path is `V2`→`V3`→`V4`→`V5`→`V6`→`V7`→`V8`→`V9`→`V10` (one bump per couple of macOS majors; V10 spans macOS 13–26). Internal `properties.version=4` is current on Sequoia/Sonoma; older majors will need fallback to AppleScript.
- **FDA story:** `~/Library/Mail/` is gated by Full Disk Access *plus* per-app TCC. The Python process inherits FDA from whichever host launched it (Terminal, Claude Desktop, Claude Code). Each host needs its own grant; we cannot prompt for it programmatically. Detection = open the file read-only and catch `OperationalError: unable to open database file` / `EPERM`.

---

## Path + Schema

### Filesystem layout (confirmed locally)

```
~/Library/Mail/
  PersistenceInfo.plist
  V10/
    MailData/
      Envelope Index               (SQLite3, WAL)
      Envelope Index-wal
      Envelope Index-shm
      ExternalUpdates.storedata    (Core Data, ignore)
      FlagMailboxes.plist, RulesActiveState.plist, …
    <ACCOUNT-UUID>/
      INBOX.mbox/
        <MBOX-UUID>/
          Data/<n>/<m>/Messages/<id>.emlx              # full RFC822
          Data/<n>/<m>/Messages/<id>.partial.emlx      # body for attachment-bearing msgs
          Data/<n>/<m>/Attachments/<id>/<x>/<file>
        Info.plist
      Drafts.mbox/  Sent Messages.mbox/  Deleted Messages.mbox/  Junk.mbox/
```

Folder version per macOS major (composite from forensics references + Apple Community threads):

| macOS | Folder |
|------:|:------|
| 10.13 High Sierra | `V5` |
| 10.14 Mojave | `V6` |
| 10.15 Catalina | `V7` |
| 11 Big Sur | `V8` |
| 12 Monterey | `V9` |
| 13 Ventura → 26 Sequoia | `V10` |

The `Vn` bump rewrites caches; the *SQL schema* changes more often (tracked by `properties.version` / `minor_version`).

### Core tables (verified via `sqlite3 .schema` on V10)

The DB has ~50 tables. The ones we care about:

- **`messages`** — one row per message-in-mailbox. Critical columns:
  `ROWID, message_id, global_message_id, document_id, sender (→ addresses.ROWID via sender_addresses), subject (→ subjects.ROWID), summary (→ summaries.ROWID), date_sent (unix seconds), date_received (unix seconds), display_date, mailbox (→ mailboxes.ROWID), remote_mailbox, flags, read, flagged, deleted, size, conversation_id, date_last_viewed, list_id_hash, is_urgent, color, type, root_status`.
  Indexes that matter: `messages_mailbox_date_received_index`, `messages_mailbox_display_date_index`, `messages_conversation_id_mailbox_date_received_deleted_index`, `messages_sender_index`, `messages_subject_index`, `messages_deleted_date_received_index`.
- **`mailboxes`** — `ROWID, url, total_count, unread_count, deleted_count, unseen_count, change_identifier`. URL form e.g. `ews://<account-uuid>/Archive`.
- **`subjects`** — `ROWID, subject` (de-duped, RTRIM collation).
- **`summaries`** — `ROWID, summary` (snippet preview).
- **`addresses`** — `ROWID, address, comment` (NOCASE on address; `comment` holds the display name).
- **`recipients`** — `(message, address, type, position)` join; `type` = 0 To / 1 Cc / 2 Bcc (empirically).
- **`senders` + `sender_addresses`** — sender bucketing; for "From" use `addresses` joined via `sender_addresses.address`.
- **`attachments`** — `(message, attachment_id, name)`.
- **`conversations` + `conversation_id_message_id`** — thread grouping.
- **`labels`** — Gmail-style label join `(message_id, mailbox_id)`.
- **`message_global_data`** — modern Sequoia/Sonoma additions: `follow_up_*`, `model_category`, `model_subcategory`, `generated_summary`, `urgent`, `message_id_header` (the literal RFC `Message-ID:`).
- **`searchable_messages` / `searchable_attachments` / `searchable_rich_links`** — Spotlight integration bridge tables.
- **`properties`** — key/value, including `version`, `minor_version`, `UUID`, `last_write_framework_version`. **Use this for runtime schema detection.**

Caveats:
- Dates are **Unix epoch seconds** (not Core Data 2001-epoch), confirmed by sughodke gist and matching `date_received` ranges locally.
- No foreign key constraints declared on most relations even though they are semantic FKs — Word to the Wise call this out explicitly.
- `subject` and `summary` are *normalized* (single row per distinct string). Joins are mandatory for human-readable output.
- Message bodies are **not** in the DB — only `summary` snippet. Full body requires reading the `.emlx` from the per-account mbox directory (path derivable from `mailboxes.url` + `messages.ROWID`/`document_id`, but format-specific).

---

## Concurrency + Permissions

### Concurrency

`PRAGMA journal_mode` on the live DB returns `wal`. SQLite WAL **explicitly supports one writer + many readers concurrently**, and Mail.app is the lone writer. Opening read-only is safe provided we:

1. Use `sqlite3.connect("file:...?mode=ro", uri=True)` — *not* `immutable=1`. Immutable mode ignores the `-wal` file, which means we'd miss every message Mail received since the last checkpoint (Mail checkpoints lazily; on this box the WAL is 2.1 MB of unflushed pages).
2. Accept the `-shm` file as-is. Read-only connections can use an existing `-shm`; we must not create one (would require write perms anyway).
3. Use short transactions / `BEGIN DEFERRED` and re-open on `SQLITE_BUSY` (rare in WAL but possible during checkpoint).
4. Treat results as a snapshot — if Mail is mid-EWS sync, we'll see whatever has been committed to WAL at the moment our read transaction began. That is *exactly the consistency AppleScript provides today*; Mail's in-memory pending state is invisible either way.

No corruption reports found from third-party readers using `mode=ro`. Corruption stories in Apple Community threads are all about Mail itself or about people running `VACUUM` while Mail is live (don't do that — and we won't, we're read-only).

### Permissions / TCC

`~/Library/Mail/` is doubly protected: it sits behind both the Full Disk Access TCC service (`kTCCServiceSystemPolicyAllFiles`) **and** Mandatory Access Control sandboxing on `~/Mail`/`~/Library/Mail`. Granting "Files & Folders → Library" is not enough; FDA is required. The grant attaches to the binary that initiates the read, so:

- `apple-mail` CLI run from `Terminal.app` → Terminal needs FDA.
- Claude Desktop's `.mcpb` host launches Python → Claude Desktop needs FDA.
- Claude Code plugin's `start_mcp.sh` → the Claude Code app needs FDA.
- iTerm/Warp/VS Code integrated terminal each need their own grant.

Failure surface: `sqlite3.OperationalError: unable to open database file` (or `errno 1 Operation not permitted` from `open(2)`). There is **no Apple API to test FDA**; the canonical pattern is "try to read a known-protected file and trap the error" (Apple Developer Forums thread 114452).

---

## Open-source prior art

| Project | Lang | Last active | Schema versions seen | What it reads |
|---|---|---|---|---|
| [sughodke/Read Mail.app Database](https://gist.github.com/sughodke/1f198a2efe8dd7418fdaa57f003baea7) | Python (SQLAlchemy + pandas) | 2017 (V5 era) | V5 | messages × addresses × subjects × mailboxes; date histograms |
| [Word to the Wise — mailapp schema](https://labs.wordtothewise.com/mailapp/) | Docs (SchemaSpy) | 2017 | V2 + partial V5 | Reference documentation of tables/columns |
| [Sam Pullara — JavaRants/Medium analyzer](https://medium.com/spullara/build-your-own-mail-analyzer-for-mac-mail-app-747143e94ccc) | Python | 2018 | V5 | Top senders, domain analysis |
| [ydkhatri/mac_apt](https://github.com/ydkhatri/mac_apt) | Python (DFIR framework) | 2024+ active | V8/V9 (mounted-image use) | Forensic enumeration of headers + mailbox layout; reads from disk image, not live |
| [forge-work DFIR Assist — Mail Envelope Index](https://forge-work.com/dfir/knowledge/artifacts/macos-mail-envelope-index) | Docs/playbook | 2025 | V8/V9/V10 noted | Forensic methodology; explicit version-coverage table |
| [lgw4 / ttscoff vacuum gists](https://gist.github.com/lgw4/9679415) | shell | 2014–2019 | V2–V5 | `VACUUM` only (writes) — *not* what we want to copy |

**Nothing in this list reads V10 from Python on a live Sequoia install** — we'd be the first to ship that pattern as a maintained MCP tool. Closest reference is mac_apt, but it operates on offline images and assumes FDA-equivalent root access on a forensic mount.

---

## What stays on AppleScript

Direct DB reads **cannot** replace these because the source of truth lives in Mail's in-memory state, in plists, or in the EWS/IMAP server:

- **All mutations**: `compose_email`, `reply_to_email`, `forward_email`, `move_email`, `update_email_status`, `manage_drafts`, `manage_trash`, `save_email_attachment`, `create_mailbox`, `synchronize_account`, `create_rich_email_draft`. Writing directly to `Envelope Index` would desync Mail's caches and *will* trigger a forced re-index on next launch.
- **Drafts in progress.** Compose windows that haven't been autosaved are not in the DB.
- **Live sync state.** `synchronize_account` triggers Mail's EWS connector; observing whether sync finished requires Mail-side state.
- **Full message body / RFC822 source.** Not in the DB. `get_email_by_id`, `get_email_thread`, and `list_email_attachments` need either AppleScript (`source of message id …`) or direct `.emlx` reads (extra parsing complexity). Recommended: keep these on AppleScript for v1, migrate later.
- **`get_email_by_id` for very recent messages.** Race: if Mail received a message but hasn't committed the WAL frame yet, only AppleScript sees it. Mitigation: fall back to AppleScript on cache miss.

Tools that are **pure-SQL candidates** (the high-value win):

- `list_inbox_emails`, `get_inbox_overview`, `inbox_dashboard`
- `search_emails` (subject/sender/date filters; full-text body search stays on Spotlight/AppleScript)
- `get_top_senders`, `get_statistics`, `get_mailbox_unread_counts`, `get_needs_response`, `get_awaiting_reply`
- `list_mailboxes`, `list_account_addresses`, `list_accounts` (cheap, but free wins)
- `get_email_thread` *metadata* (conversation_id walk via `conversation_id_message_id`)

---

## Recommended fallback strategy

1. **Probe at startup.** `core.envelope_index.probe()` returns `(path, version, minor_version, framework_version, writable_test=False)` or a structured error code: `ENV_NOT_FOUND`, `ENV_FDA_DENIED`, `ENV_SCHEMA_UNKNOWN`, `ENV_BUSY`.
2. **Pin a supported schema window.** Hardcode an allowlist (`version=4, minor_version >= 84000`). On miss → log once, fall back to AppleScript for *that* tool call. Never crash, never silently return stale data.
3. **Wrap each direct-read tool in a guard:** `if envelope_index.available(): return query_sqlite(...) else: return query_applescript(...)`. Same return shape either way.
4. **Read-only URI everywhere:** `sqlite3.connect("file:" + urllib.parse.quote(path) + "?mode=ro", uri=True, timeout=2.0)` with `PRAGMA query_only=1` belt-and-braces.
5. **No caching of ROWIDs across calls.** Mail recycles them on rebuild. Use `message_id_header` (the RFC `Message-ID:`) or `document_id` as the stable external identifier.
6. **Detect FDA gracefully.** On `OperationalError: unable to open database file`, emit a structured tool error explaining "Grant Full Disk Access to <host app>" with a `defaults`/System Settings deep link. Do not retry in a hot loop.
7. **Stay behind a feature flag** (`APPLE_MAIL_USE_ENVELOPE_INDEX=1`) for the first release so we can ship the AppleScript path as the safe default and let opt-in users prove the SQL path on real workloads (24k Exchange inbox is the gold test case).
8. **Sanity-check on every read.** Compare `mailboxes.total_count` against `SELECT COUNT(*) FROM messages WHERE mailbox=?` on cold start; if they diverge by >5%, fall back to AppleScript and surface a soft warning — likely indicates a stale checkpoint or an in-progress reindex.

---

## References

- Word to the Wise Labs — Mail.app schema notes: https://labs.wordtothewise.com/mailapp/
- DFIR Assist — macOS Mail Envelope Index artifact: https://forge-work.com/dfir/knowledge/artifacts/macos-mail-envelope-index
- sughodke — Read Mail.app Database gist (Python): https://gist.github.com/sughodke/1f198a2efe8dd7418fdaa57f003baea7
- Sam Pullara — Build your own mail analyzer (Medium): https://medium.com/spullara/build-your-own-mail-analyzer-for-mac-mail-app-747143e94ccc
- SQLite WAL semantics: https://sqlite.org/wal.html
- SQLite URI / read-only / immutable: https://sqlite.org/uri.html
- Apple Developer Forums — testing for Full Disk Access: https://developer.apple.com/forums/thread/114452
- Apple Developer Forums — `~/Library/Mail` MAC protection beyond FDA: https://developer.apple.com/forums/thread/678819
- mac_apt forensic framework (Mail artifact handling): https://github.com/ydkhatri/mac_apt
- Forensics Wiki — macOS Mail artifact locations: https://forensics.wiki/mac_os_x_10.9_artifacts_location/
- Retrospect — macOS Sequoia/Tahoe FDA application data privacy: https://docs.retrospect.com/docs/macos-sequoia-application-data-privacy-full-disk-access

*Local verification on this machine (2026-05-22): macOS Sequoia, `~/Library/Mail/V10/MailData/Envelope Index` = 104 MB SQLite WAL, `properties.version=4`, `minor_version=84003`, framework `3864.600.51.1.1`, 44,812 messages across 180 mailboxes including EWS Exchange inbox. Schema dump captured for reference.*
