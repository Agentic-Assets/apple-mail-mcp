---
name: apple-mail-archive-export
description: >-
  Bulk-export or recover an entire Apple Mail account from its local on-disk
  store to verified .eml, .mbox, and a searchable SQLite index, reassembling
  detached attachments - without launching Mail.app, AppleScript, or the Apple
  Mail MCP. Use this whenever someone wants to archive, back up, export, bulk
  download, migrate, or rescue a mailbox, especially when they are leaving a job
  or school and losing access to a work or university email account, or when the
  server is already unreachable but the mail still appears in Mail.app. Also use
  it for questions about .emlx or .partial.emlx files, ~/Library/Mail internals,
  extracting attachments in bulk, converting Apple Mail to mbox or Thunderbird,
  or making an old mailbox searchable offline. Reach for this even when the
  request sounds small ("can I get my old emails out of Mail?", "export my
  inbox", "save all my attachments") - the naive approaches silently lose about
  half the attachments on a typical Exchange account, so the careful path matters
  even at small scale.
---

# Apple Mail archive export

Recover a complete, verified, searchable local archive of an Apple Mail account
by reading its `.emlx` store directly off disk.

This works when the mail server is dead, the credentials are expired, or the
account was never fully synced, because everything needed is already on the Mac.
It never launches Mail.app and never touches the live store.

## Why this exists instead of the obvious approaches

The obvious paths silently lose data, and each failure looks like success:

- **Apple's own File > Export Mailbox** omits attachments when an account is
  offline, and mishandles `From ` escaping.
- **The Apple Mail MCP's `full_inbox_export` is disabled in its own source**
  (`UNBOUNDED_EXPORT_DISABLED`) because full-mailbox walks spike Mail.app CPU;
  callers are capped at 50 messages per call. A 39,000-message mailbox would be
  ~786 serialized AppleScript round trips through a live Mail instance.
- **The popular `.emlx` converters** contain no handling for `.partial.emlx`, so
  they drop attachments on every message whose attachments Mail detached -
  typically **about half** of an Exchange account.

Direct filesystem extraction did a 39,269-message / 15 GB account in **188
seconds** with byte-level proof. Details and citations: `references/pitfalls.md`.

One honest exception, worth knowing so you can offer it: on a **small, still-live**
account, reading each message through Mail's AppleScript `source` property is
lossless *and* can fetch parts the local cache never stored. Some detached
messages have no payload on disk at all - 2,316 of 25,412 in a reference store -
and no filesystem method can recover those. It is the wrong default because it
needs a live server, does not scale, and makes Mail rewrite its own cache while
serving reads, but it is the right repair for the specific messages
`export_emlx.py` reports as `missing_parts`.

## The one idea that matters most

**`.partial.emlx` does not mean "not downloaded."** It means Mail moved the
attachment part *bodies* into a sibling `Attachments/` directory and left the
full MIME skeleton behind with `X-Apple-Content-Length` stubs. The bodies are
local. A correct export splices them back in.

Get this wrong and you produce a large, plausible archive missing half its
attachments. This is the single most common way these exports fail.

## Workflow

Run these in order. Steps 1-3 are mandatory; step 4 onward is shaping the output.

### 1. Discover which account you actually want

```bash
SKILL=<this skill dir>
python3 "$SKILL/scripts/discover_accounts.py"
```

Account directories are named by opaque UUID, so this correlates
`Accounts4.sqlite`, each mailbox's `Info.plist`, and real message headers to tell
you which UUID is which address, how many messages it holds, and how
attachment-detached it is.

If reads fail with `EPERM`, the terminal lacks **Full Disk Access** - grant it in
System Settings > Privacy & Security > Full Disk Access. The script says so
explicitly rather than leaving you to guess.

Confirm the UUID with the user before continuing. Exporting the wrong account
wastes hours and, on a shared machine, exposes mail that was not yours to read.

### 2. Snapshot before anything else

```bash
ARCHIVE=~/Documents/<Name>-Mail-Archive
bash "$SKILL/scripts/snapshot_account.sh" <AccountUUID> "$ARCHIVE"
```

This is ~1 minute for 15 GB on an internal SSD, and it converts every later
mistake from "lost mail" into "delete the copy and redo." It uses `ditto`
deliberately - macOS `rsync` is openrsync, whose `-a` silently drops every
extended attribute and exits 0. Then it SHA-256s both trees and refuses to
declare success unless they match.

Quit Mail.app first (Cmd-Q, never force-quit). `.emlx` files are written whole so
they are not at risk, but Mail continuously rewrites `Info.plist` and the
Envelope Index.

**Before writing tens of GB, check the destination is not cloud-synced** unless
that is the goal. iCloud Desktop/Documents sync, Dropbox, and Google Drive
folders will all start uploading, and Drive enforces the account's quota.

If this is the only copy of the mail, say so plainly and check whether a backup
exists (`tmutil destinationinfo`). Two copies on one SSD share a failure domain;
that is worth stating rather than implying the mail is now safe.

### 3. Export, then prove it

```bash
python3 "$SKILL/scripts/export_emlx.py" \
  --snapshot "$ARCHIVE/01-raw-snapshot/<AccountUUID>" \
  --out "$ARCHIVE/10-export"

python3 "$SKILL/scripts/verify_export.py" \
  --snapshot "$ARCHIVE/01-raw-snapshot/<AccountUUID>" \
  --out "$ARCHIVE/10-export" --sample 2500
```

`export_emlx.py` writes one `.eml` per message with attachments re-embedded, the
original folder tree preserved, chronologically sortable filenames, plus
`index.sqlite` (FTS5), `messages.jsonl`, and `export-report.json`.

**`verify_export.py` is not optional, and running it is the main discipline this
skill is teaching.** It decodes attachments back out of the exported `.eml` files
and byte-compares them against the originals on disk. The reason: a full export
once reported `39,269 written, 0 parse errors` while silently emptying every
attached email in the corpus, because Python's `set_payload()` on a
`message/rfc822` part accepts a string and produces an empty part without
complaining. Counts and exit codes cannot see that class of bug; only a byte
comparison can. It exits non-zero on any mismatch, so you can gate on it.

Sanity-check the report against the source before believing it:

| Report field | Should equal |
|---|---|
| `total` / `written` | the `.emlx` count from step 1 |
| `reassembled` | the `.partial.emlx` count from step 1 |
| `parse_errors`, `unreadable`, `reassembly_failed` | 0 |
| `invariants_ok` | `true` (the script asserts its own sums and exits non-zero if they do not close) |
| `missing_parts` | small, and every entry explainable |
| `unidentified_files` (verify) | 0. Anything else is a message the verifier declined to examine |

If `written` is far below `total`, or `reassembled` is 0 on an account with many
partials, stop and investigate rather than shipping. Pairing `reassembled`
against the independently counted partial total is the single most useful check,
because it fails loudly in exactly the case where detached attachments were
skipped instead of spliced.

Several report fields have alarming names and benign meanings - `xacl_mismatch`
in the hundreds is normal, and the export legitimately finds more messages than
Mail displays. Read `references/report-fields.md` before reporting a number as a
problem.

**Sampling is stratified, so do not read `--sample 2500` as "2,500 random
messages."** The verifier forces in every message with an unusual shape (a nested
email, an `.emlxpart` sidecar, a re-zipped bundle, a rare payload extension, an
unusual part count, an oversize body) and fills the rest of the budget uniformly.
This matters because a uniform draw is close to useless for the bugs that actually
occur: on the 39,269-message reference account a uniform 2,500-message sample
contained none of the 26 messages carrying a nested email, while the stratified one
found a message whose entire body was missing. Use `--all` when the account is
small enough to afford it, and quote the coverage percentage when it is not.

### 4. Add browsable and searchable surfaces

```bash
python3 "$SKILL/scripts/make_mbox.py"  --out "$ARCHIVE/10-export"       # optional
python3 "$SKILL/scripts/check_mbox.py" --out "$ARCHIVE/10-export"       # if you made mbox
python3 "$SKILL/scripts/search.py" --archive "$ARCHIVE/10-export" --stats
```

`make_mbox.py` produces one mboxrd file per folder for importing into
Thunderbird, Apple Mail, or MailMate. It roughly doubles disk usage, so offer it
rather than assuming - the `.eml` tree plus the index already covers search and
long-term durability. Keep `.eml` as the archive of record, since mbox must
mutate body bytes to escape lines beginning `From `.

**If you generate mbox, run `check_mbox.py`.** It reverses the conversion -
strips the envelope line and injected flag headers, un-escapes `>From `, and
demands the original `.eml` back byte for byte. mbox is the one output that has
to mutate bytes, and header damage there is invisible to every count-based check:
an earlier version of `make_mbox.py` corrupted the folded `Received:` header on
85% of an Exchange mailbox while separator counts, attachment byte-comparison and
Message-ID multisets all passed. A round trip can only pass if every byte was
preserved or reversibly transformed, which is why it is worth its own gate.

For getting the result into a specific client - Thunderbird needs an add-on,
Apple Mail wants a particular directory shape, Outlook takes neither - see
`references/importing.md`. It also covers which flag headers each client reads,
and why the imported unread count will disagree with Mail's old sidebar badge.

`search.py` gives full-text search over decoded subject, body, sender,
recipients, and attachment names. Decoding is the point: bodies are base64 or
quoted-printable on disk, so `ripgrep` over raw files under-recalls badly.

### 5. Make the archive self-sufficient, then write its README

Copy the scripts the user will actually need later next to the archive, so
searching it does not depend on this skill still being installed:

```bash
mkdir -p "$ARCHIVE/20-tools"
cp "$SKILL/scripts/"{search.py,verify_export.py,check_mbox.py,export_emlx.py} \
   "$ARCHIVE/20-tools/"
```

They are standard-library Python 3, so a bare `python3 20-tools/search.py --stats`
works years from now with nothing to install.

**Check where the archive is actually sitting before declaring done.** A scratch
or temp directory is wiped on reboot, and `~/Desktop` and `~/Documents` may be
iCloud-synced. Move it somewhere durable and suffix the directory `.noindex` so
Spotlight does not index a second full copy of the mailbox.

Then write the README. Future-you will not remember any of this. Record where the
copies are, the verified counts, the date coverage, what is *not* in the archive,
and the search commands. State limitations plainly:

- **The cache may not go back as far as the user's tenure.** Coverage starts
  wherever Mail last rebuilt its store. Check the earliest `Date:` header and
  report it - if mail predates that, it exists only on the server, which makes
  "ask IT for a full mailbox export before the account is purged" urgent and
  time-sensitive. This is the highest-value thing to surface early, because it is
  the only part that is still recoverable and it expires.
- **Signatures no longer verify.** Mail stores messages LF-normalized rather than
  in CRLF wire format, so DKIM/S-MIME cannot be re-validated. That loss happened
  when Mail wrote the files, not during export - say so rather than implying the
  export is byte-faithful to the original transmission.
- **The archive is a slight superset of what Mail shows.** Files with no database
  row (messages Mail deleted but never unlinked) are included, because the
  exporter walks the filesystem.

## Never do these to a dead account

`Mailbox > Rebuild` in Mail is the most dangerous menu item available: its model
is "discard local, re-fetch from server," which with no server is a one-way
delete. It is also the folk remedy for "Mail is acting weird," and a broken
account is acting weird, so warn explicitly.

Do not delete the account from Mail Settings or Internet Accounts - uncheck
**"Enable this account"** instead, which is reversible. Avoid `Erase Deleted
Items`. Never call the MCP's `synchronize_account` on a decommissioned account.

## Bundled scripts

| Script | Purpose |
|---|---|
| `scripts/discover_accounts.py` | Inventory accounts: UUID -> address, counts, size, partial ratio |
| `scripts/snapshot_account.sh` | `ditto` snapshot + SHA-256 proof both trees match |
| `scripts/export_emlx.py` | `.emlx` -> `.eml` with attachment reassembly, FTS5 index, JSONL, report |
| `scripts/verify_export.py` | Byte-compare exported attachments against originals, covering both the decoded `Attachments/` and encoded `.emlxpart` sources; stratified sampling; exits non-zero on mismatch |
| `scripts/make_mbox.py` | One mboxrd file per folder, flags written for both mutt and Thunderbird |
| `scripts/check_mbox.py` | Byte-exact round trip of every mbox message back to its `.eml`; exits non-zero on mismatch |
| `scripts/search.py` | Full-text + filtered search CLI over the archive |

All are standard-library Python 3 or bash. No dependencies to install.

## Reference files

- **`references/emlx-format.md`** - the on-disk format: byte layout, plist keys,
  the hash-bucket rule, attachment reassembly mechanics, the `flags` bitfield,
  and how to read the Envelope Index safely. Read this when writing custom
  parsing or locating one message by id.
- **`references/pitfalls.md`** - every trap hit during real recoveries, ordered by
  damage. Read this before deviating from the workflow, and when something looks
  wrong. It is also the place to look when tempted to swap `ditto` for `rsync`,
  trust the Envelope Index for enumeration, inject a header between existing
  headers, or skip verification.
- **`references/report-fields.md`** - what every field in `export-report.json`,
  `verify_export.py` and `check_mbox.py` means, which invariants to assert, and
  which alarming-looking numbers are benign. Read this before telling a user a
  number looks wrong; several of them routinely cause false alarms.
- **`references/importing.md`** - getting the archive into Thunderbird, Apple
  Mail, MailMate or Outlook, which flag headers each client reads, and how to
  read a failed import. Read this whenever the goal is a working mailbox rather
  than a preserved one.
- **`references/future-work.md`** - known gaps and how to close them. Read this
  before extending the skill, when a request lands just outside what the scripts
  do (attachment-only extraction, exporting every account at once, marking
  messages Mail no longer lists), or when something here feels clumsy and you are
  wondering whether that is deliberate. It also records what is genuinely
  impossible, so you do not spend effort on it.

## Match the output to what they actually asked for

The full workflow produces three things: a durable archive of record, importable
mbox, and fast search. They have very different costs, and people asking for one
often get quoted the price of all three. Ask which problem is being solved before
committing to 56 GB.

| What they actually want | What that needs | Rough cost on a 39k-message, 15 GB account |
|---|---|---|
| "I'm losing access, save everything" | the whole workflow | ~56 GB, ~5 min |
| "I need to find things in my old mail" | snapshot + `index.sqlite` only | ~15 GB, and the index is **128 MB** |
| "Get my mail into Thunderbird" | snapshot + `.eml` + `mbox` | ~40 GB |
| "Just get me the attachments" | snapshot + `.eml`, then extract | ~20 GB |

**Search is the cheapest of these by a wide margin, and the export is oversized
for it alone.** On the reference account `index.sqlite` is 128 MB out of a 56 GB
archive, about 0.2%, and it is the entire reason queries return in ~50 ms. If
someone still has server access and just wants better search than Mail's own, an
index-only run is the proportionate answer, not a full archive.

Two caveats before offering that. The index stores paths to the exported `.eml`
files, so an index-only build points back into the live Mail store: `--open` and
full-message reads then depend on Mail not moving or purging anything, and results
drift as Mail rewrites files underneath. And the durability argument does not apply,
because you would have fast search resting on the same cache they are trying to get
off. So index-only is right for "my mail is fine, Mail's search isn't" and wrong for
"I'm about to lose this mailbox."

Worth knowing when someone asks whether they needed any of this: Mail's own search
does work, and works well - body indexing was complete on 90,415 of 90,418 messages
in a reference store. What it cannot do is be scripted (Spotlight returns **zero**
results inside `~/Library/Mail`, which macOS excludes), find the messages whose
Envelope Index rows Mail has already deleted (533 of 39,269 on the reference
account), or survive Mail purging a dead account's cache. Say that plainly rather
than implying their mail was unsearchable before.

## Scaling notes

For a mailbox under a few thousand messages the whole workflow is under a minute
and there is no reason to cut corners. For very large accounts (100k+), export
per top-level mailbox with `--only` to keep runs restartable, and expect the
attachment re-encoding to dominate wall time rather than message parsing.

The archive footprint is roughly: raw snapshot (1x) + `.eml` export (~1.2x) +
optional mbox (~1.2x). Confirm free space before starting, and mention the total
so the user is not surprised.
