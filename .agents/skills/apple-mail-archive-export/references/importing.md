# Importing the archive into a mail client

Read this when the user wants the archive *usable in a mail app* rather than just
preserved. The export produces three consumable shapes; which one to hand over
depends on the client, and getting this wrong wastes the user's time on a failed
import rather than losing data.

## Contents

1. Which output to give which client
2. Thunderbird
3. Apple Mail
4. MailMate, mutt, Evolution
5. Outlook
6. Flag headers: what each client actually reads
7. Import problems and what they mean

---

## 1. Which output to give which client

| Client | Give it | Notes |
|---|---|---|
| Thunderbird | `mbox/*.mbox` | Needs the ImportExportTools NG add-on; no built-in mbox import |
| Apple Mail | `mbox/*.mbox` | Native: File > Import Mailboxes > Files in mbox format |
| MailMate | `mbox/*.mbox` or `eml/` | Reads both |
| mutt / Evolution | `mbox/*.mbox` | Native mbox |
| Outlook (Mac/Win) | neither directly | No mbox support at all; see section 5 |
| Anything, worst case | `eml/` | One file per message opens in nearly every client and in Quick Look |

Keep the `.eml` tree as the archive of record regardless. mbox has to mutate body
bytes to escape lines beginning `From `, so it is a convenience copy, not the
canonical one.

**Anything much over 4 GiB is a size problem before it is a format problem.**
Large single mbox files are widely reported to fail on import, 4 GiB being the
threshold usually named, and the failure arrives hours in, leaving a partial mailbox
that looks finished. Nobody here has measured where the cliff actually is, so treat
4 GiB as the conventional point to split at and not a proven limit. Run
`split_mbox.py --out "$ARCHIVE/10-export"` first and import from `mbox-by-year/`,
which holds `Inbox-2024.mbox` style per-year files whose concatenation is proved
byte-identical to the original. One client folder appears per year, so a 2024 folder
and a 2025 folder rather than one unmanageable Inbox.

## 2. Thunderbird

Thunderbird stores mail as mbox internally but ships **no import UI for it**,
which surprises people who assume the format match is enough. Two routes:

**ImportExportTools NG (recommended)**

1. Tools > Add-ons and Themes, search "ImportExportTools NG", install, restart.
2. Right-click a local folder in the folder pane, e.g. Local Folders.
3. ImportExportTools NG > Import mbox file > Import directly one or more mbox
   files, then select the `.mbox` files.

One Thunderbird folder appears per file. Import folders individually rather than
all at once on a large archive so a single failure does not force a restart of
the whole job.

**Drop-in (no add-on)**

Quit Thunderbird, copy the `.mbox` files into the profile's `Mail/Local Folders/`
directory with the `.mbox` extension removed, and restart. Thunderbird builds its
own `.msf` index on first open. This is faster for many folders but silently
ignores files it cannot parse, so verify the message counts afterward.

## 3. Apple Mail

File > Import Mailboxes > Files in mbox format, then point at the `mbox`
directory. Apple Mail expects each mailbox as a *directory* named `Foo.mbox`
containing an `mbox` file; it is usually tolerant of flat `Foo.mbox` files, but if
the import comes up empty, restructure:

```bash
cd "$ARCHIVE/10-export/mbox"
for f in *.mbox; do
  d="${f%.mbox}.mbox.d"; mkdir -p "$d"; cp "$f" "$d/mbox"
done
```

Import into a **local** "On My Mac" mailbox, never into the account being
archived. Importing into a live account uploads everything to that server.

## 4. MailMate, mutt, Evolution

MailMate reads both `.mbox` and a directory of `.eml` and has the best search of
the three. mutt takes an mbox path directly (`mutt -f Inbox.mbox`). Evolution
imports mbox natively via File > Import.

## 5. Outlook

Outlook supports neither mbox nor bulk `.eml` import. Two workable paths:

- Import the mbox into Thunderbird or Apple Mail first, then re-export to PST
  (Thunderbird plus ImportExportTools NG can write EML per message, and
  commercial converters handle mbox to PST).
- Add the archive as a *local* IMAP store and let Outlook subscribe. Heavier
  setup, but no third-party converter touches the mail.

State plainly that Outlook is the awkward target here; do not imply a clean path
that does not exist.

## 6. Flag headers: what each client actually reads

There is no universal convention, so `make_mbox.py` writes both families. They do
not conflict, and each client ignores the one it does not know.

| Header | Read by | Encoding |
|---|---|---|
| `Status: RO` | mutt, Evolution, MailMate | `R` read, `O` old |
| `X-Status: AFT` | mutt, Evolution | `A` answered, `F` flagged, `T` draft |
| `X-Keywords: $Forwarded` | several | keyword list |
| `X-Mozilla-Status: 0001` | **Thunderbird** | hex bits: `0x1` read, `0x2` replied, `0x4` flagged, `0x1000` forwarded |
| `X-Mozilla-Status2: 00000000` | Thunderbird | high 16 bits, unused here |

Thunderbird ignores `Status:` entirely, which is why an mbox that carries only the
mutt convention imports with everything marked unread.

**Expect the unread count to disagree with what Mail.app showed.** Mail's sidebar
badge can be a stale server-side counter: on one account it read 6 unread while
per-message state said 19, and Mail's own on-disk `flags` bit agreed with 19 on
51 of 51 messages. The archive carries per-message truth, so the importing client
is right and the old badge was wrong. Say so before the user reports it as a bug.

## 7. Import problems and what they mean

| Symptom | Cause |
|---|---|
| Import completes, zero messages | Client wanted a `Foo.mbox/mbox` directory (section 3), or refused a file whose first line is not `From ` |
| Message count is higher than expected | Body lines beginning `From ` were not escaped, so the client split one message into several. The export uses mboxrd; a *different* tool in the chain is the culprit |
| Truncated or garbled headers | Something injected a header into the middle of a folded one. `check_mbox.py` detects this; see `pitfalls.md` |
| Everything shows as unread | Only `Status:` was written and the target is Thunderbird (section 6) |
| Attachments appear as 0 KB | The export dropped detached payloads. Re-run `verify_export.py`; this is the failure this skill exists to prevent |
| Import is extremely slow | Normal for multi-GB folders. Import per folder rather than all at once |
| Import stalls, dies, or ends short on one huge folder | The file is past the few-GiB size where large-mbox imports are reported to fail. Split it with `split_mbox.py` and import `mbox-by-year/` one year at a time |
