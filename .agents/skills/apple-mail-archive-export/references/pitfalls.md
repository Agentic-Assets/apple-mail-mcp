# Pitfalls: every trap hit during a real 39,269-message recovery

Ordered by how much damage each one does. The recurring theme is that **the
dangerous failures are silent** - the export reports success and the archive
looks fine until someone needs a specific attachment years later.

## Contents

1. Silent data-loss traps
2. Tooling that will lose your mail
3. Copy and metadata traps
4. Parsing traps
5. Mail.app operations that destroy data
6. Verification anti-patterns
7. Environment gotchas

---

## 1. Silent data-loss traps

### `message/rfc822` attachments emptied by `set_payload()`

**The worst bug in this whole workflow, and it passed a clean full run.**

For a `message/rfc822` part (a forwarded email attached as a file), Python's
`part.set_payload("<string>")` does **not** populate the part. The payload of a
`message/rfc822` entity is a *list containing a Message object*, so assigning a
string yields an empty part - with no exception, no warning, nothing in the logs.

The first full export reported `39,269 written, 0 parse errors` while destroying
all 61 attached emails across 24 messages. It was caught only by decoding
attachments back out of the exported `.eml` files and diffing them against the
originals.

Correct handling:

```python
if part.get_content_type() == "message/rfc822":
    nested = email.message_from_bytes(data)
    part.set_payload([nested])          # a LIST, containing a Message
    del part["Content-Transfer-Encoding"]
```

The general lesson: **a run that reports success is not evidence of correctness.**
Always run `verify_export.py`.

### A detached body with no `X-Apple-Content-Length` stub

`.partial.emlx` normally marks each emptied part with `X-Apple-Content-Length`, so
the obvious reassembly loop is "for every part carrying that header, find its
payload on disk." That is what the exporter did, and it was wrong in one case that
produces a completely empty message.

When Mail detaches the body of a **single-part** message - a bare `text/calendar`
meeting reply, not a multipart with attachments - it can leave **no marker at
all**. The `.partial.emlx` holds the headers, a blank line, and nothing else. The
payload sits at `Attachments/<id>/1/Mail Attachment.ics` as usual, but with no
stub header there is nothing for a marker-driven loop to match, so the exported
`.eml` ends after its headers and the entire message body is gone.

This is nastier than the `rfc822` bug in one respect: there is no leftover stub, so
the "no `X-Apple-Content-Length` survived" check passes, `missing_parts` stays 0,
and the message looks structurally valid in a mail client. It is just blank.

The fix is to stop treating the marker as the only evidence:

```python
if part.get("X-Apple-Content-Length") is None:
    # An empty part with a payload on disk at its own part number is a
    # detached body too, marker or not. The emptiness test keeps this safe:
    # a part that already holds content is never touched.
    if not (has_payload and not _stub_has_content(part)):
        continue
```

Reported as `filled_unmarked` so the case stays visible rather than becoming
invisible good luck. On the 39,269-message Exchange account exactly one message
was affected, 2,267 bytes of calendar data. Small, and it was found by a stratified
verification sample rather than by any count in the report.

**A caution on measuring this**, learned the embarrassing way. The first attempt
scanned each `.partial.emlx` for the absence of `X-Apple-Content-Length` in its
first 1 MB, and reported 51 affected messages. That was wrong: large messages carry
their stubs well past the 1 MB mark, so 50 healthy messages were misclassified. The
honest test is to parse the message and check, per part, whether it is empty while a
payload exists at its part number. When you bound a scan for speed, the bound
becomes part of the claim.

### Injecting a header between existing headers

Converting to mbox means adding flag headers (`Status:`, `X-Mozilla-Status:`).
The obvious implementation splits the message at the first newline and inserts
after "the first header line" - and that is wrong, because RFC 5322 folds long
values onto continuation lines beginning with space or tab, and Exchange folds
the leading `Received:` header on essentially every message. Splitting at the
first *physical* newline lands **inside** that fold: it truncates the `Received:`
value and leaves `Status:` spanning two lines.

This corrupted **85% of a real 39,269-message Exchange archive** (Inbox 47/47,
one folder 282/282), while messages authored locally - Sent, Drafts - came out
clean, because those have no folded leading `Received:`. Every count-based check
still passed: separator counts, attachment byte-comparison, Message-ID multisets.

Header order is not significant, so the fix is to stop inserting between headers
and prepend the flag headers immediately after the `From ` envelope line. That
removes the failure mode instead of navigating it. `check_mbox.py` catches the
whole class by reconstructing each message and comparing it to its `.eml`.

### Treating `.partial.emlx` as "incomplete, skip it"

`.partial` means the attachment *bodies* were moved to a sibling `Attachments/`
directory, not that the message failed to download. Skipping partials lost 47% of
messages and all 45,501 attachments in the reference corpus. See
`emlx-format.md` section 5.

### Reversing the two payload sources

Payloads under `Attachments/` are **decoded** and must be **re-encoded**.
`.emlxpart` sidecars are **already encoded** and must be **spliced verbatim**.
Swap them and every attachment is double-encoded: present, correctly named, and
corrupt.

### Filling a stub with zero bytes when the payload is absent

14 of 45,505 parts had no payload on disk. Writing an empty attachment makes a
permanent gap look like a successfully exported file. Leave the
`X-Apple-Content-Length` stub in place and record the gap in the report, so it
stays auditable.

### Driving enumeration from the Envelope Index

Hundreds of `.emlx` files exist on disk with **no database row** - 533 in the
reference account, still accumulating (Mail deletes the row but never unlinks the
file). A database-driven export silently skips them. **Walk the filesystem;** use
the database only for enrichment and reconciliation. The upside: those orphans are
often deleted mail the user would want back.

### The parent mailbox absorbing its children's messages

Child mailboxes nest *inside* the parent `.mbox`. A recursive glob from the parent
attributes every descendant's mail to it, so folder structure silently collapses
and per-folder counts are wrong in a way that still sums to the right total.

## 2. Tooling that will lose your mail

### Apple Mail's own File > Export Mailbox

Two documented defects, both of which apply exactly to a dead-account recovery:

- **It omits attachments when the account is offline**, even though the bytes are
  on local disk ([Apple Support 8246891](https://discussions.apple.com/thread/8246891)).
- **It mishandles `From ` line escaping**
  ([Michael Tsai](https://mjtsai.com/blog/2019/10/11/mail-data-loss-in-macos-10-15/)),
  and was still producing 0 KB attachments on Exchange imports as of Jan 2026.

Combined with a mailbox that is ~47% attachment-detached, it produces a large,
plausible, quietly incomplete `.mbox`. Use it only as a cross-check.

### Popular third-party emlx converters

Several widely-recommended tools contain **zero references** to `partial` or
`X-Apple-Content-Length`, meaning they drop attachments on every detached
message: `pip install emlx` (`mikez/emlx`), `terhechte/emlx`,
`terhechte/postsack` (569 stars). Relativity documents `.partial.emlx` as
unsupported; `mac_apt` has no Apple Mail plugin.

Worse: `Crosten/emlx2mbox` flat-globs the attachment directory, so two parts both
named `image001.png` collide and it inlines **the wrong bytes** - corruption
rather than absence.

Reasonable references if you want to compare implementations:
`qqilihq/partial-emlx-converter` (MIT, actively maintained, fails loud),
`LRGH/emlx2eml` (Apache-2.0), `hexrw/mailgrep` (good auditor - distinguishes
`NOT_DOWNLOADED` from `NOT_EXTRACTABLE`).

### The Apple Mail MCP for bulk extraction

Do not use it, and this is settled by the plugin's own source rather than
opinion. `plugin/apple_mail_mcp/tools/analytics/full_export.py` is a **disabled
refusal shim**: `full_inbox_export` returns `code: UNBOUNDED_EXPORT_DISABLED`,
**no AppleScript runs**, and every parameter is marked "Unused. Retained for
signature compatibility." The stated reason is that full-mailbox walks are too
heavy on large Exchange/Gmail accounts and spike Mail.app CPU. Callers are capped
at `max_emails=50`.

At that ceiling a 39,269-message mailbox is **786 serialized AppleScript round
trips** through a live Mail instance, each one a chance to wedge Mail. Filesystem
extraction did the same job in **188 seconds** without launching Mail.

The MCP's legitimate role here is narrow: read-only metadata cross-checks
(`list_mailboxes`, `get_mailbox_unread_counts`) after the export, to sanity-check
per-folder counts.

**Never call `synchronize_account` on a decommissioned account** - it is the one
tool that actively contacts the server. Leave its `confirm_sync` gate in place.

### Where Mail.app's AppleScript `source` genuinely wins

Worth stating plainly rather than pretending filesystem extraction dominates
everywhere. For a **small, still-live** account, reading each message's
`source` property has a real advantage: Mail assembles the complete message and
can fetch parts the local cache never stored. On a reference store, 2,316 of
25,412 detached messages had **no payload on disk at all** - no filesystem method
can recover those, and a live `source` read can.

It was measured byte-identical to the raw `.emlx` payload on sampled messages, so
it is lossless where the cache is complete and strictly better where it is not.

The limits are what make it the wrong default:

- It needs a live account and a responsive Mail. In the dead-account case this
  skill exists for, the server is gone and the advantage evaporates.
- It does not scale. One AppleScript round trip per message means ~786 batched
  calls at 39,000 messages, each a chance to wedge Mail. Filesystem extraction
  did the same corpus in 188 seconds.
- **It is not read-only.** Serving those reads made Mail rewrite 21 of its own
  cache files (store 25 MB -> 26 MB) on a 51-message account.

So: use it as a targeted repair for the specific messages whose payloads are
missing, or as an independent cross-check on a handful of messages. Do not use it
as the extraction path for a whole mailbox.

## 3. Copy and metadata traps

### `/usr/bin/rsync` on modern macOS is openrsync, and it lies

Measured on real Mail data:

| Tool | Data | APFS compression | xattrs |
|---|---|---|---|
| `ditto --rsrc --extattr` | identical | **preserved** | preserved |
| `rsync -aE` | identical | **lost** (25M -> 46M) | lost on paths > 245 chars |
| `rsync -a` | identical | lost | **ALL xattrs dropped, exit code 0** |

`-a` expands to only `-Dgloprt` - no `-E`, no `-H`. The total silent xattr loss
with a success exit code is the trap. **Use `ditto`.**

Also: Mail files are mode `0600`. A copy without permission preservation exposes
mail to other local accounts.

### The live store drifts under you while Mail runs

Re-hash the source after a snapshot and a few files will differ even though you
never wrote to them. Measured on a 106-file account, two `.emlx` files changed
within ten minutes. The difference was confined to the plist trailer's `flags`
value, bit 7 only, with the declared length and the entire RFC-822 message region
byte-identical.

So a later manifest diff is not evidence the snapshot is bad. Compare the
*message* region rather than the whole file before concluding anything: hash the
bytes between the length header and the plist trailer. Content drift is a real
problem; flag-bit churn is Mail bookkeeping. This is also a second argument for
quitting Mail first - not because `.emlx` files are at risk of a torn write, but
because it makes the proof clean.

### Do not copy loose files over SMB/NFS

Every xattr becomes an extra round trip, and NFS strips them entirely. Make a
`.dmg` or tarball first, then move one large file.

### Spotlight will index your archive copy

Suffix the destination directory `.noindex`. The old
`.metadata_never_index` marker no longer works on current macOS despite
widespread advice to the contrary.

## 4. Parsing traps

### Folded headers

Exchange folds as `Message-ID:\n\t<...>`. A same-line regex missed the
Message-ID on **42%** of the reference corpus and initially produced a false
"16,698 messages have no Message-ID" alarm. **Unfold RFC 5322 continuation lines
before matching anything:** `re.sub(r"\n[ \t]+", " ", headers)`.

### Filenames containing newlines

Three attachment filenames contained literal `\n` (e.g.
`Outlook-Logo\n\nDesc.png`). Any line-delimited pipeline miscounts them - a
`find | wc -l` reported 45,953 where the true count was 45,947. Use `-print0` /
`os.walk`, and NUL-delimited counting in shell.

### Python's strict email parser crashes on real Message-IDs

`email.policy.strict` raises on at least one Microsoft-generated Message-ID with
a bracketed local part. Parse tolerantly (`compat32`) or extract with a regex.

### Duplicate Message-IDs are normal, not corruption

1,285 IDs appeared more than once - the same message filed in Inbox + Archive, or
Sent + Drafts. Key on `(mailbox, emlx_id)`, **never** on Message-ID alone. Note
that this also means **counts are not a completeness check**: compare Message-ID
*multisets*, not sets.

### The same payload stored twice for one message

Mail sometimes writes one attachment to two payload paths for the same message -
observed as part `2` and part `2.2` holding the same PDF, byte-identical. A
verifier that expects every payload file on disk to map to a distinct exported
MIME part will report unmatched leftovers and look like it found missing data.
Reconcile by content hash, not by path count: if the unmatched payload's bytes
already appear in the export, nothing is missing.

### mbox escaping

Use **mboxrd** (`^>*From ` -> `>&`), which is reversible. Never mboxo. In the
reference corpus 372 messages (0.9%) contained lines needing escaping.

### Missing `From:` headers

16 messages had none. `.emlx` stores no envelope sender, so the mbox `From ` line
is **always fabricated**. Synthesize `MAILER-DAEMON` and move on.

### Oversized messages

One 910 MB draft. Parsing it costs multiple GB of RAM. Stream anything over a few
hundred MB straight through without MIME parsing.

## 5. Mail.app operations that destroy data

For an account whose server is gone, these are irreversible:

- **`Mailbox > Rebuild`** - the single most dangerous menu item. Its model is
  "discard local, re-fetch from server." With no reachable server that is a
  one-way delete. It is also the folk remedy for "Mail is acting weird," and a
  broken account *is* acting weird, so the temptation is real.
- **Deleting the account** from Mail Settings or System Settings > Internet
  Accounts. Prefer unchecking **"Enable this account"** - reversible, scoped, and
  it removes the nagging that provokes deletion.
- **`Erase Deleted Items` / `Erase Junk Mail`** - Deleted Items held 1.6 GB in the
  reference account, so "it is just trash" is wrong.
- **A macOS upgrade migrating the store.** Have an off-machine copy first; there
  is no older version directory to fall back to.

An account can sit safely at `ZACTIVE=1, ZAUTHENTICATED=0` (enabled but unable to
log in) indefinitely. No evidence was found that Mail purges a cached Exchange
store because auth failed - but that is an absence of evidence, which is another
argument for snapshotting immediately.

**Quit Mail before snapshotting.** `.emlx` files are written whole and are not at
risk, but Mail holds read/write handles on the Envelope Index and its WAL, and
keeps rewriting `Info.plist` and `.mboxCache.plist` even while idle. Quit
normally (Cmd-Q); never force-quit, which can damage the index.

## 6. Verification anti-patterns

**Counts prove nothing.** "39,269 in, 39,269 out" passed while every attached
email was empty. Duplicate Message-IDs also make naive count comparisons
meaningless.

**Exit code 0 proves nothing.** `rsync -a` drops all xattrs and exits 0. The
export reported 0 errors while corrupting parts.

**Checking only what you fixed proves nothing.** After fixing the
`message/rfc822` bug, re-verification used a *larger* sample and a different
random seed, not the same 900 messages.

**Checking only the payloads proves nothing about the envelope.** The
folded-header corruption above survived separator counts, full attachment
byte-comparison, and Message-ID multiset equality. Every check was green and 85%
of the headers were mangled. Verify each derived format against the format it was
derived *from*, not against a summary of it.

**A uniform random sample proves less than its size suggests.** Failures cluster
in structurally rare messages, which is exactly what a uniform draw misses. On the
39,269-message account, 26 messages carry a nested email; a uniform 2,500-message
draw at the default seed contained *none* of them, so the `message/rfc822` bug
would have passed at 6.4% coverage looking perfectly healthy. Stratify instead:
force in every message with a nested-email payload, an `.emlxpart` sidecar, a
re-zipped bundle, a rare payload extension, an unusual part count, or the oversize
filename shape, then fill the rest uniformly. On that account the forced set is 949
messages, 2.4% of the corpus, and it is what surfaced the unmarked-body bug above.

**A check that silently skips is worse than no check.** `verify_export.py` recovered
the message id from the exported filename and fell back to an empty string when the
shape did not match. Oversize messages are named `oversize-<id>.eml`, so they got an
empty id, matched no original, and every check for them became a no-op that
contributed a pass. Derive the strata and the identifiers so that an unrecognized
case is *reported*, never skipped: unnameable inputs now fail the gate as
`unidentified_files`. Ask of any verifier, "what does this do with an input it does
not understand?" If the answer is "nothing, quietly", it is not a gate.

What actually constitutes proof, in order:

1. **Snapshot fidelity**: SHA-256 every file on both sides and diff the manifests.
2. **Structural integrity**: every `.emlx` parses - header, exact-length body,
   well-formed plist trailer.
3. **Semantic completeness**: per-mailbox counts plus Message-ID multiset compare.
4. **Attachment fidelity**: decode attachments back out of the exported `.eml`
   and byte-compare against the originals on disk. This is the one that catches
   real payload corruption; `verify_export.py` does it.
5. **Derivative fidelity**: reverse each derived format back to its source and
   compare bytes. For mbox that means stripping the envelope line and injected
   flag headers, un-escaping `>From `, and expecting the original `.eml` byte for
   byte; `check_mbox.py` does it. A round trip is strictly stronger than any
   count, because it can only pass if every byte was preserved or reversibly
   transformed.

## 7. Environment gotchas

- **Full Disk Access** is required to read `~/Library/Mail`. Without it every
  read fails with `EPERM`, which reads like corruption. Grant it to the terminal
  in System Settings > Privacy & Security > Full Disk Access.
- **`~/Library/Mail` and the Mail sandbox container path are the same inodes**
  (firmlinked). There is only one physical copy; you cannot hide a backup by
  putting it in the other path. Just do not place a copy *inside*
  `~/Library/Mail`.
- **iCloud offloading is not a risk here.** `find -flags +dataless` returned 0
  files, and `~/Library/Mail` is not an iCloud-managed location. Optimized
  Storage governs iCloud Drive only.
- **Check the destination for cloud sync before writing tens of GB.** Verify
  `FXICloudDriveDesktop` / `FXICloudDriveDocuments` are 0, and avoid Dropbox and
  Google Drive folders unless upload is the explicit goal. Google Drive
  File Stream also enforces the account's quota - a free `@gmail.com` tier is
  15 GB, which an 18 GB archive silently stalls against.
- **`shuf` and `timeout` do not exist on macOS.** Use `sort -R` or Python's
  `random` instead of `shuf`; rely on your harness's own timeout rather than
  wrapping commands in `timeout`.
- **`du --apparent-size` is GNU-only.** BSD `du` reports physical blocks, so APFS
  cloning can make an archive look smaller than the sum of its files.
