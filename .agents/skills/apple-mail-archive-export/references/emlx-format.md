# Apple Mail on-disk format (V10 / macOS 26)

Read this when you need to parse `.emlx` yourself, locate one specific message by
id, or understand why `export_emlx.py` does something non-obvious. Everything
here was verified against a real 91,398-message store across seven accounts
(Exchange, Gmail/IMAP, iCloud, On My Mac) rather than taken from documentation.

## Contents

1. Store layout
2. `.emlx` byte layout
3. The plist trailer
4. Hash buckets
5. `.partial.emlx` and attachment reassembly
6. MIME part numbering
7. The `flags` bitfield
8. The Envelope Index database

---

## 1. Store layout

```
~/Library/Mail/PersistenceInfo.plist   -> LastUsedVersionDirectoryName = "V10"
~/Library/Mail/V10/
  MailData/                            Envelope Index (+ -wal, -shm), signatures
  <AccountUUID>/
    <Name>.mbox/
      Info.plist
      <StoreUUID>/Data/...             this mailbox's OWN messages (optional)
      <Child>.mbox/                    child mailbox, SIBLING of the StoreUUID dir
```

Three things routinely trip people up:

**The UUID directory inside a `.mbox` is not per-mailbox.** Every data-bearing
mailbox in a given store uses the *same* UUID string, which equals
`PersistenceInfo.plist -> VersionDirectoryIdentifiers.V10`. It is a per-store
identifier. Do not parse it or hardcode it; glob for `*/Data`.

**Children nest inside the parent.** `Conferences.mbox/ARES.mbox/2025.mbox/`.
A node may be data-bearing, child-bearing, both, or neither - in the reference
store 156 of 217 `.mbox` dirs had no data dir at all.

**Stop descending at the first child `.mbox` when collecting a node's
messages.** A naive `rglob("**/Messages/*.emlx")` from a parent attributes every
descendant's mail to the parent. This is the single most common structural bug.

`Info.plist` is present in every mailbox but only `MailboxID` and `MailboxName`
are universal. `ExchangeFolderId` / `ExchangeSyncState` appear on EWS accounts
only; `UIDVALIDITY` / `UIDNEXT` on IMAP only. That difference is how
`discover_accounts.py` tells account types apart. `ExchangeSyncState` is a
multi-KB gzipped base64 blob - skip it when dumping.

## 2. `.emlx` byte layout

Three sections, no magic number:

```
[0 .. 10]        length header, 11 bytes in V10
[11 .. 11+N]     N bytes of RFC822
[11+N .. EOF]    Apple plist trailer
```

The length header is a **fixed-width field, not a bare number** - decimal digits
left-aligned in a 10-char field, right-padded with `0x20`, then `0x0A`:

```
b'1421      \n'   # 4 digits + 6 spaces + \n
b'910010787 \n'   # 9 digits + 1 space  + \n
```

Mail reserves the width so it can rewrite the count in place. `int(buf[:nl])`
works because Python strips whitespace, but any fixed-slice or strict-digit
parser breaks. Older V2-era stores are unpadded, so parse to the first `\n`
rather than assuming 11 bytes.

`N` counts **only** the RFC822 bytes: not the header line, not the trailer.

```python
nl   = buf.index(b'\n')
N    = int(buf[:nl])
rfc  = buf[nl+1 : nl+1+N]
plst = buf[nl+1+N : ]
```

### The payload is LF-normalized, and this is permanent

**Zero CRLF pairs across every account sampled.** Mail rewrites the wire format
to bare LF on storage. Consequences:

- The stored bytes are **not** RFC 5322 wire format. A byte-exact reconstruction
  of what the server originally sent is impossible from the `.emlx` alone.
- The header/body separator is `\n\n`, not `\r\n\r\n`.
- **DKIM, S/MIME, and PGP signatures cannot be re-verified after export.** This
  loss happened when Mail wrote the file, not during conversion. Say so plainly
  rather than implying the export is byte-faithful to the original transmission.
- Decide once whether your `.eml` output stays LF (faithful to disk) or converts
  to CRLF (RFC-compliant). Do not mix. `export_emlx.py` keeps LF.

Integrity across the reference store was perfect: 0 zero-length files, 0 where
`11+N > filesize`, 0 unparseable headers, 0 where the declared count disagreed
with the trailer offset. Still validate that the trailer starts with `<?xml` -
it is the cheap corruption detector.

One file was 910 MB (a draft with a large attachment). **Stream; never
unconditionally `read()`.** `export_emlx.py` streams anything over 300 MB.

## 3. The plist trailer

The key list in most published documentation is **obsolete**. V10 actually
contains:

| Key | Type | Coverage | Meaning |
|---|---|---|---|
| `flags` | int (64-bit) | 100% | bitfield, section 7 |
| `date-received` | int | 100% | **Unix epoch seconds**, not CoreData 2001 |
| `conversation-id` | int | ~99.5% | Mail-local thread key |
| `date-last-viewed` | int | ~99.5% | Unix epoch; 0 = never opened |
| `remote-id` | str | ~98% | EWS ItemId base64, or IMAP UID |
| `color` | str | varies | 6-hex RGB, `000000` in ~99.99% of cases. **Not** the flag color |
| `gmail-label-ids` | list | Gmail only | label ids |

There is **no** `subject`, `date-sent`, `sender`, `to`, `uid`, `message-id`,
`type`, `original-mailbox`, or `is-partial-message` key. Subject, sender and date
come from the RFC822 headers. Partial-ness comes from the **filename only**.

Because message state lives here, **a raw file copy preserves read/replied/
forwarded/flagged/draft state without the Envelope Index.** That is why the
snapshot step is sufficient for preservation.

For lossless metadata capture, serialize the whole dict rather than enumerating
known keys.

## 4. Hash buckets

```
Data/<d>/<d>/.../Messages/<id>.emlx
```

The directory components are the decimal digits of `id // 1000`, **least
significant first**:

```python
def bucket(msg_id: int) -> list[str]:
    q, out = msg_id // 1000, []
    while q:
        out.append(str(q % 10)); q //= 10
    return out          # [] when id < 1000
```

```
992    -> Data/Messages/992.emlx
1308   -> Data/1/Messages/1308.emlx
79780  -> Data/9/7/Messages/79780.emlx
100186 -> Data/0/0/1/Messages/100186.emlx
```

Verified 39,269 of 39,269 files with zero mismatches, and again across all
90,411 rows of the Envelope Index for every account type.

**The widely-repeated folk rule "reverse all the digits" (`510 -> Data/0/1/5/`)
is wrong.** It includes the low three digits, which are the intra-bucket index.
`510` lives at `Data/Messages/510.emlx`. Depth is `max(0, len(str(id)) - 3)`, and
each `Messages/` holds at most 1000 files.

Use the formula as a `stat()` fast path when you already have an id. For
enumeration, walk the filesystem so the exporter survives a layout change.

`Attachments/` is a **sibling of `Messages/`** in the same bucket:

```
Data/<bucket>/Messages/<id>.partial.emlx
Data/<bucket>/Attachments/<id>/<dotted-part>/<filename>
```

## 5. `.partial.emlx` and attachment reassembly

**`.partial.emlx` does not mean "not downloaded."** It means Mail stripped the
attachment part *bodies* out of the message file. The full MIME skeleton - every
header, boundary, `Content-Type`, `filename`, and `Content-Transfer-Encoding` -
is retained, and the text/plain and text/html bodies are present inline. Each
emptied part gains `X-Apple-Content-Length: N` and has a zero-length payload.

In the reference Exchange account, 18,270 of 18,377 partials had attachment rows,
and 45,491 of 45,505 stub parts had their payload on disk (99.97%). Treating
`.partial` as skippable loses 47% of that corpus.

The inverse also holds: 19 `.partial.emlx` files had *no* stub parts - mostly
`multipart/report` DSNs. So `.partial` does not strictly imply attachments.

### Three payload locations, with opposite handling

**(a) `Attachments/` sibling directory - the dominant path** (45,491 of 45,505).
Content is the **decoded** original (verified: PNG magic, `%PDF`). You must
**re-encode** per the part's declared CTE.

**(b) `.emlxpart` sidecar** - `<bucket>/Messages/<id>.<part>.emlxpart`. Content is
the **still-encoded** part body (verified: `iVBORw0KGgo...`). **Splice verbatim.**

Getting (a) and (b) backwards double-encodes the attachment, producing a file
that looks present and is corrupt. When both exist for one message, prefer the
sidecar - it is closer to the wire form. Unresolved: how a *dotted* part number
is spelled in an `.emlxpart` filename (only flat numbers were observed, and
`63293.1.2.emlxpart` is ambiguous against the message id).

**(c) `~/Library/Containers/com.apple.mail/Data/Library/Mail Downloads/`** -
last-resort fallback by filename, unreliable since there is no message linkage.

### Re-encoding fidelity, measured

| Encoding | Result over 45,302 parts |
|---|---|
| base64 via `base64.encodebytes` (76-col, LF) | **45,296 exact (99.99%)** |
| base64, remaining 6 | off by one trailing newline, all `text/calendar` |
| quoted-printable | 77 match / 43 mismatch - **QP is not reproducible** |
| 7bit / none | 68 exact (splice raw) |
| uuencode | 1, not reproducible from decoded bytes |

So base64 round-trips essentially perfectly. For QP and uuencode the *decoded
content* is still exact; only the wire representation differs.
`export_emlx.py` re-emits uuencode as base64 and rewrites the header, which is
valid MIME and keeps the bytes intact.

`X-Apple-Content-Length` is a **soft check, never an assert**. Its provenance is
inconsistent: on `.emlxpart` sidecars it recorded the CRLF length while the file
stores LF, so it differs by exactly the line count. Warn on a delta > 1; do not
fail.

### Two directory traps

- **56,221 part directories scanned, none contained more than one entry.** "One
  file per part dir" is a safe invariant.
- **One payload was a directory, not a file**: Mail had expanded a `.pages`
  bundle onto disk. `open()` raises `IsADirectoryError` and kills the run near
  the end. Re-zip it; the bytes will not match the original archive, which is
  acceptable and must be logged.

## 6. MIME part numbering

RFC 3501 dotted numbering. The root entity is never numbered; its children are
`1`, `2`, `3`; nested parts are `N.M`. Multipart containers are **not** leaves
but **do** consume an index at their level.

```
ROOT      multipart/mixed
 1        multipart/related
 1.1      multipart/alternative
 1.1.1    text/plain      (inline, full payload)
 1.1.2    text/html       (inline, full payload)
 1.2      image/png       XACL=31234  payload=0  -> Attachments/79995/1.2/image001.png
 2        application/...wordprocessingml.document
                          XACL=107683 payload=0  -> Attachments/79995/2/contract.docx
```

Python's `email` package does not expose these numbers; you must build them while
walking. `walk_numbered()` in `export_emlx.py` is that implementation.

## 7. The `flags` bitfield

64-bit (exceeds 32 bits since 10.11 - a typical value is `8590195713`). jwz's
2005 table still holds for bits 0-25. Cross-validated against the Envelope
Index over 20,000+ messages:

| Bit | Mask | Meaning | Confidence |
|---|---|---|---|
| 0 | `0x1` | read | 100% agreement with `messages.read` |
| 1 | `0x2` | deleted | exact match, but few positive cases |
| 2 | `0x4` | answered / replied | exact match vs `server_messages.replied` |
| 3 | `0x8` | encrypted | not observed |
| 4 | `0x10` | flagged | 100% agreement |
| 5 | `0x20` | recent | not observed |
| 6 | `0x40` | draft | 100% in Drafts, 0% elsewhere |
| 7 | `0x80` | obsolete "initial" | **goes stale on disk - ignore** |
| 8 | `0x100` | forwarded | 21 of 22 |
| 9 | `0x200` | redirected | not observed |
| 10-15 | `0xFC00` | attachment count, 6-bit | **`63` is an "unknown" sentinel** |
| 16-22 | `0x7F0000` | priority (3 = normal) | 99.9% are 3 |
| 24 | `0x1000000` | junk | exact match |
| 25 | `0x2000000` | marked not junk | exact match |
| 33 | `0x200000000` | undocumented, set on ~100% | unknown |
| 34 | `0x400000000` | undocumented, ~2-5% | unknown |

Two practical notes. **Never emit `X-Attachment-Count: 63`** - recount from the
MIME tree instead. And **flag color is not in this bitfield**: bit 4 is a plain
boolean, while the color index lives in `messages.flag_color` in the database.

The on-disk trailer and the database disagree on roughly 4.7% of messages, and
the differing bits are *only* bit 7 and the attachment-count field. Every
semantically meaningful bit agreed in every sampled case, so **the plist bitfield
is trustworthy for export.**

Mapping onto mbox conventions: bit 0 -> `Status: RO`, bit 2 -> `X-Status: A`,
bit 4 -> `F`, bit 6 -> `T`, bit 1 -> `D`, bit 8 -> `X-Keywords: $Forwarded`.
Anything unmappable (priority, conversation-id, remote-id) belongs in
`X-Apple-Mail-*` headers, or base64 the whole plist into one header for true
losslessness.

## 8. The Envelope Index database

`MailData/Envelope Index` is SQLite (261 MB in the reference store). Useful for
metadata enrichment and reconciliation, **but it must not be your enumeration
source** - see `pitfalls.md`.

**Read it safely.** Copy `Envelope Index`, `-wal`, and `-shm` together to scratch,
checkpoint the copy, then query the copy read-only. Do not use `immutable=1`
against a live store: it ignores the WAL and can return torn state. Never write.

```bash
cp "$M/Envelope Index" "$M/Envelope Index-wal" "$M/Envelope Index-shm" scratch/
sqlite3 scratch/"Envelope Index" "PRAGMA wal_checkpoint(TRUNCATE); PRAGMA quick_check;"
```

Key tables: `messages` (one row **per message per mailbox**), `mailboxes`
(`url` = `ews://<AcctUUID>/<Path>` or `imap://...` or `local://...`), `subjects`
(deduped, **minus** the `Re:`/`FW:` prefix), `addresses`, `recipients`
(`type` 0=To, 1=Cc; **no Bcc rows exist anywhere**), `attachments`,
`message_global_data` (holds `message_id_header`, the literal RFC Message-ID).

Three join traps:

- `message_global_data.message_id` joins to `messages.message_id`, **not** to
  `ROWID` and **not** to `global_message_id`. Both alternatives return 0 rows.
- The subject prefix is stored **outside** `subjects`, in
  `messages.subject_prefix`. Concatenate it or you lose `Re:`/`FW:`.
- `messages.size` is the **server-reported** item size, not the local byte size
  (median 0.30x the actual `.emlx` size). Never use it to verify a transfer.

There is **no message body text and no FTS index** in this database. The
`searchable_*` tables are a CoreSpotlight donation queue holding no text;
`summaries` holds preview snippets only. Body text must come from the `.emlx`
files, which is why `export_emlx.py` builds its own FTS5 index.

`ROWID` maps to a file deterministically using the section 4 bucket rule, with
`mailboxes.url` supplying the account UUID and the `.mbox` path segments
(percent-decode, and normalize Unicode NFC vs NFD before matching APFS names).
