# Fast Index-Backed Search: Research, Measurement, and Plan

**Date:** 2026-08-17
**Author:** research agent (read-only investigation lane)
**Status:** research complete, no production code written, no plugin source modified
**Platform measured:** macOS Darwin 25.5 (Tahoe), Mail V10, `Envelope Index` schema `version=4` / `minor_version=84003`
**Live store measured:** 90,420 index rows, 91,408 `.emlx` files, 215 mailboxes, 6 accounts (66 EWS mailboxes, 144 IMAP, 5 local)

Every number in this report is reproducible. Section 9 lists the exact probe scripts and how to re-run them.

---

## 1. Verdict up front

**Yes for metadata. No for bodies. And the current AppleScript search path is not just slow, it is returning wrong answers today.**

Three separate verdicts, in order of confidence:

### 1.1 Metadata search: viable, and the win is enormous

A read-only index-backed path answers every metadata query class in **under 110 ms p95**, against **9 to 18 seconds** for the AppleScript equivalent. Measured end-to-end cost of the whole access pattern, including a fresh crash-safe snapshot of Mail's database, is **0.56 ms p50**.

| Query class | Index-backed | Current AppleScript | Verdict |
|---|---|---|---|
| Newest N in a mailbox | 0.04 to 0.21 ms | 10.05 s | **ship it** |
| Newest N unread | 0.17 ms | 17.66 s | **ship it** |
| Date range | 0.02 to 0.73 ms | n/a (window-capped) | **ship it** |
| Sender exact / sender id | 0.79 ms | ~9 s, 50-msg ceiling | **ship it** |
| Sender domain (suffix) | 21 ms | ~9 s, 50-msg ceiling | **ship it** |
| Subject substring, one mailbox | 6 ms p50 / 105 ms p95 | **returns 0, broken** | **ship it** |
| Read / unread / flagged flags | 0.80 to 4.6 ms | 17.66 s | **ship it** |
| Attachment presence | 0.83 ms | ~9 s | **ship it** |
| Attachment filename | 3.8 ms | not supported | **ship it** |
| Recipient (to/cc) search | 8 to 28 ms | not supported (hangs in Mail) | **ship it** |
| Thread / conversation | 21 to 24 ms | ~9 s, capped 50 | **ship it** |
| Top senders, whole store | 32 ms p50 / 290 ms p95 | ~9 s over a 15-msg sample | **ship it** |
| Unread counts, all 215 mailboxes | 10 to 16 ms | 2.6 s (and disagrees, see 6.4) | **ship with a caveat** |
| Internet Message-ID exact | 11 ms p50 / 333 ms p95 | ~9 s | ship it (unindexed column) |
| **Body text** | **not present in the index** | 25-msg cap | **cannot promise** |

Ordered result sets are **byte-identical** to the AppleScript path where the AppleScript path works (Section 6.1). And critically, **`messages.ROWID` is exactly the AppleScript `message id`** (Section 6.2), so an index-backed discovery tool hands back ids that feed straight into the existing exact-id action tools with no translation layer.

### 1.2 Body text: a genuine dead end via any public API. Stop re-litigating it.

Body text is **not in the Envelope Index at all**. `searchable_messages` is pure bookkeeping (`message_body_indexed`, `transaction_id`, `reindex_type`, no text or blob column anywhere in the 54 tables). The only body-ish text is a `summaries` table of ~1000-character preview snippets covering **23.5%** of messages, heavily recency-biased.

Mail's real body index is CoreSpotlight, and it is closed by code-signing design, not by accident or configuration:

- `Mail.app` declares `com.apple.spotlight.scopes = ["~/Library/Mail"]`, which hands that subtree off from the general file index.
- The writer, `com.apple.mail.SpotlightIndexExtension.appex`, holds `com.apple.private.corespotlight.bundleid = com.apple.mail`, `com.apple.private.corespotlight.internal`, `com.apple.private.email`, `com.apple.private.security.storage.Mail`. Platform-signed, `TeamIdentifier=not set`.
- The only cross-bundle reader, `Spotlight.app`, holds `com.apple.private.corespotlight.search.internal` plus `kTCCServiceSystemPolicyAllFiles`.

Both public APIs were tested to zero **with passing positive controls**, which is what makes this airtight rather than inconclusive:

- `NSMetadataQuery` scoped to `~/Library/Mail`: **0** results for `*.emlx` and for `kMDItemTextContent CONTAINS "the"`. The same predicates against an exported copy directory: **39,269** and **38,551**. A deliberately invalid scope URL returned **78,538**, proving an unrecognised scope silently falls back to broad search. The Mail scope returned 0 *instead of* falling back, so the URL was accepted and the index genuinely holds nothing beneath it.
- `CSSearchQuery` and `CSUserQuery` from a codesigned Swift binary: every query completed with `error == nil` and `foundItemCount == 0`, **including non-Mail control terms that `mdfind` matches 213,466 times**. That is `CSSearchableIndex` per-app donation scoping: a third-party process sees only what it donated itself, and CoreSpotlight never reads the file-metadata index.
- The CoreSpotlight store itself is not SQLite (magic bytes `8tsd`); `sqlite3.connect(..., mode=ro)` fails with `file is not a database`. Reading it would be reverse engineering, not an API.
- `mdfind` exclusion is **subtree-specific, not `~/Library` wholesale**: `~/Library` overall returns 50,069 indexed items, `~/Library/CloudStorage` 43,944, `~/Library/Mail` **0** across 148,231 files. The common phrasing "Spotlight excludes ~/Library" is imprecise and should be corrected in any future note.

**pyobjc is installed on none of the three interpreters** (`/usr/bin/python3`, homebrew `python3`, the repo `.venv`). It would be a new dependency. It is also unnecessary: the ObjC runtime is reachable from stdlib `ctypes`, and JXA drives `NSMetadataQuery` fine. But since CoreSpotlight is closed to all third parties, none of that helps.

One inverse finding worth recording because it will tempt someone: `.emlx` files copied **outside** `~/Library` are automatically full-text body indexed by the stock `Mail.mdimporter` at 100% coverage (39,269 of 39,269 in an existing export tree). That is a property of the archive-export path, not of live search, and it is not a route to the live store.

### 1.3 The hybrid design works, but not in the shape the ask assumed

The suspicion in the brief was: metadata pre-filter in SQLite to cut 24,000 down to a small candidate set, then read bodies from those candidates' `.emlx` files. Measured, that is **half right**, and the wrong half is the part that matters.

Using metadata as a *proxy* for body content has terrible recall. For `invoice` in the 25,012-message Exchange inbox (253 true hits, ground truth established by reading all 25,012 bodies):

| Prefilter | Candidates | Latency | Hits | **Recall** | False negatives |
|---|---|---|---|---|---|
| None (read every body) | 25,012 | 41.5 s serial, **4.33 s at 16 procs** | 253 | 100% | 0 |
| Last 365 days | 8,944 | 17.7 s | 118 | **46.6%** | 135 |
| Subject or 1000-char snippet mentions term | 54 | **135 ms** | 52 | **20.6%** | 201 |

A 135 ms body search that finds 20.6% of the matches is a correctness regression dressed as an optimisation. It is exactly the failure mode the brief warns about.

What *does* work:

1. **Metadata pre-filter on criteria the user actually specified** (mailbox, date range, sender, flags) then read every surviving body. Recall is 100% within the user's stated scope. This is not a proxy, it is scope narrowing.
2. **Parallelism.** Body reads touch no Mail.app and take no `_MAIL_LOCK`, so unlike AppleScript they are embarrassingly parallel: **41.5 s to 4.33 s at 16 processes, 9.6x, identical 253 hits**. A thread pool gives 1.0x, confirming the work is CPU and parse bound rather than I/O bound, so processes are required.
3. **An opt-in FTS5 body index**, which is what `tasks/active/v4-performance-consolidation-2026-05-27/phase-plan.md` Phase 4 already proposed. Measured: **32 s to build the whole 90,420-message store, 827 MB, queries in 0.03 to 0.05 ms.** `sqlite3` ships FTS5 in all three interpreters, so this is **zero new dependencies**.

### 1.4 The finding that outranks all of the above

**`search_emails` subject filtering returns 0 results where Mail itself returns matches, on every account tested.** This is a live bug in v3.11.6, not a performance issue, and it should be fixed before any of this lane ships. Full reproduction in Section 6.3.

---

## 2. Measured evidence: the Envelope Index capability envelope

### 2.1 File and schema facts

```
path        ~/Library/Mail/V10/MailData/Envelope Index
size        266,768,384 bytes (254 MiB), page_size 4096, page_count 65,130
journal     wal   (-wal 1.3 to 1.6 MB live, -shm 32,768 bytes)
tables      54
properties  version = '4'
            minor_version = '84003'
            last_write_version = '4'
            last_write_minor_version = '84003'
            last_write_framework_version = '3864.600.51.1.1'
            WriteTransactionGeneration = 2419934
```

`version=4` / `minor_version=84003` are **identical to the values recorded in `tasks/archive/2026-05/whose-elimination-2026-05-22/01-envelope-index-research.md` in May 2026**. Three months, one macOS point release, no schema minor-version change. That is a real, if small, stability data point. Over the same period the store grew from 104 MB / 44,812 messages / 180 mailboxes to 254 MB / 90,420 / 215.

### 2.2 Row counts (from the snapshot, `count(*)` timing in parentheses)

| Table | Rows | Time | What it gives you |
|---|---|---|---|
| `messages` | 90,420 | 0.2 ms | the spine; `ROWID` == AppleScript `message id` |
| `subjects` | 40,043 | 0.5 ms | `subject TEXT COLLATE RTRIM`, deduplicated |
| `addresses` | 8,700 | 0.2 ms | `address TEXT COLLATE NOCASE`, `comment TEXT` (display name) |
| `recipients` | 181,585 | 0.5 ms | `(message, address, type, position)`, to/cc/bcc |
| `attachments` | 55,445 | 0.2 ms | `(message, attachment_id, name TEXT)` |
| `mailboxes` | 215 | 0.0 ms | `url TEXT`, cached `total_count` / `unread_count` |
| `labels` | 108,283 | 0.3 ms | **Gmail mailbox membership. Load-bearing, see 6.5** |
| `summaries` | 18,751 | 5.3 ms | `summary TEXT COLLATE RTRIM`, ~1000-char snippet |
| `conversations` | 61,938 | 0.1 ms | thread ids |
| `conversation_id_message_id` | 93,741 | 0.4 ms | thread membership |
| `message_references` | 70,272 | 0.4 ms | In-Reply-To / References graph |
| `message_global_data` | 87,409 | 0.2 ms | **`message_id_header` = Internet Message-ID** |
| `server_messages` | 51,629 | 0.1 ms | per-mailbox server flag mirror (IMAP only) |
| `server_labels` | 108,303 | 0.3 ms | server-side label membership |
| `searchable_messages` | 90,419 | 0.3 ms | **bookkeeping only, no content** |
| `duplicates_unread_count` | 50,734 | 0.1 ms | dedup-adjusted unread |
| `events` | 1,403 | 0.0 ms | parsed calendar invitations |
| `generated_summaries` | 6,930 | 0.8 ms | Apple Intelligence summaries (BLOB) |

`messages` columns available: `message_id, global_message_id, remote_id, document_id, sender, subject_prefix, subject, summary, date_sent, date_received, mailbox, remote_mailbox, flags, read, flagged, deleted, size, conversation_id, date_last_viewed, list_id_hash, unsubscribe_type, searchable_message, brand_indicator, display_date, flag_color, is_urgent, color, type, fuzzy_ancestor, automated_conversation, root_status`.

`date_received` and `date_sent` are plain Unix epoch seconds (verified: min 1345691832 = 2012-08-22, max 1786961634 = 2026-08-17). `date_sent` has a sentinel max of 2147483647.

### 2.3 Confirmed: `searchable_messages` holds no body text

```
columns: ['message_id', 'message', 'transaction_id', 'message_body_indexed', 'reindex_type']
message_body_indexed = 1 : 90,417
message_body_indexed = 0 : 2
```

No TEXT or BLOB column exists on the table. `searchable_messages.message` is a foreign key joining `messages.ROWID` 90,417 of 90,417. The flag means "Mail handed this body to its indexer"; that indexer's output is the entitlement-gated CoreSpotlight store from Section 1.2.

### 2.4 The `summaries` table: partial body text, and why it cannot carry a search promise

This is the one thing the brief's ground truth did not have. `messages.summary` references `summaries.summary TEXT`, which is the message-list preview snippet.

```
non-deleted messages          : 90,417
with a summary reference      : 21,236  (23.5%)
distinct summary strings      : 18,751  (deduplicated)
snippet length  min=0  p50=1000  p90=1001  max=1871  mean=813
```

Coverage is strongly recency-biased, so it is worst exactly where "search my old mail" lives:

| Year | Messages | With snippet | Coverage |
|---|---|---|---|
| 2026 | 42,974 | 13,746 | 32.0% |
| 2025 | 31,414 | 5,048 | 16.1% |
| 2024 | 11,478 | 1,720 | 15.0% |
| 2023 | 4,094 | 682 | 16.7% |
| 2015 and earlier | ~350 | ~1 | ~0% |

So: the snippet is a legitimate **ranking and preview** signal, and a legitimate opt-in *cheap* filter if the response says plainly what it did. It is **not** body search. Section 1.3 measured its recall at 20.6%.

### 2.5 Query timings and plans

All on a snapshot clone. 5 to 30 reps, median reported, `EXPLAIN QUERY PLAN` abbreviated. `p95` figures come from the 15-rep worst-case pass in `probe11`, which is the honest number because it includes page-cache misses and concurrent Mail writes.

| # | Query | p50 | p95 | Plan |
|---|---|---|---|---|
| Q1 | mailbox + `date_received DESC` limit 50, join subjects | 0.21 ms | 0.18 ms | `messages_deleted_date_received_index` |
| Q1b | Gmail mailbox via `labels` join, limit 50 | 0.04 ms | 0.66 ms | `labels_mailbox_id_index` covering |
| Q2 | `sender = <address rowid>`, all mailboxes, limit 100 | 0.79 ms | | `messages_deleted_date_received_index` |
| Q2b | sender by literal address string | 1.58 ms | | + `addresses` PK |
| Q3 | `address LIKE '%@domain'` (suffix, unindexable) | 21.2 ms | | `messages_deleted_index` |
| Q4 | `subject LIKE '%term%'`, all mailboxes, limit 100 | 22.4 ms | 104.6 ms | `messages_deleted_date_received_index` + subjects PK |
| Q4b | `subject LIKE` scoped to one mailbox | 32.3 ms | | same |
| Q5 | last 7 days, all mailboxes, count | **0.02 ms** | | **covering index** |
| Q5b | last 30 days one mailbox, limit 200 | 0.73 ms | | `(deleted, date_received)` |
| Q6 | unread count, all mailboxes | 0.80 ms | | **covering partial index** `read=0 AND deleted=0` |
| Q6b | flagged in one mailbox, limit 200 | 4.64 ms | | `messages_deleted_mailbox_index` |
| Q7 | has-attachment, newest 50 in a mailbox | 0.83 ms | | `attachments_message_attachment_id_index` covering EXISTS |
| Q7b | `attachment.name LIKE '%.pdf'`, whole store | 3.77 ms | | `attachments_message_name_index` covering scan |
| Q8 | recipient = address, whole store | 8.03 ms | | `recipients_address_index` |
| Q8b | recipient address `LIKE '%@domain'` | 28.5 ms | 70.0 ms | scan |
| Q9 | top 20 senders, one mailbox, 90 days | 3.85 ms | | + temp b-tree group/order |
| Q9b | top 25 senders, all 215 mailboxes, all time | 32.5 ms | 289.7 ms | `messages_deleted_index` + temp b-trees |
| Q10 | full conversation by `conversation_id` (173 msgs) | 23.8 ms | 22.6 ms | `messages_deleted_date_received_index` |
| Q11 | unread per mailbox, all 215 | 16.3 ms | 29.0 ms | `messages_deleted_mailbox_index` |
| Q11b | cached counts from `mailboxes` (no message scan) | **0.05 ms** | | `SCAN mailboxes` (215 rows) |
| Q12 | lookup by `ROWID` | **0.00 ms** | | INTEGER PRIMARY KEY |
| Q12b | lookup by Internet Message-ID header | 11.1 ms | **332.8 ms** | **`SCAN g`, no index** |
| Q13 | subject OR snippet `LIKE`, all 90,420 msgs | 74.0 ms | | two LEFT JOINs |

Mail ships a rich index set on `messages`: 30+ indexes including `(deleted, date_received)`, `(mailbox, date_received)`, `(mailbox, display_date)`, `(deleted, mailbox)`, `(conversation_id, mailbox, ...)` composites, plus **partial** indexes `WHERE read=0 AND deleted=0` and `WHERE is_urgent=1 AND deleted=0`. Q5 and Q6 hit covering indexes, which is why they are microseconds.

**The two slow spots to design around:**
- `message_id_header` has **no index**: 333 ms p95 full scan of 87,409 rows. Acceptable, but the tool should not advertise Internet-Message-ID lookup as fast.
- Unanchored `LIKE` and whole-store aggregates spike to 100 to 290 ms p95. Still 30x to 100x better than AppleScript, but they need a documented bound, not an unbounded promise.

---

## 3. Measured evidence: safe concurrent read of a live database

This is the section that decides whether the idea is allowed to exist at all, because the constraint is absolute: never write anything under `~/Library/Mail/`.

### 3.1 Open-mode matrix, live database, Mail running (pid 3772, actively writing the WAL)

| Mode | Opens? | `journal_mode` reported | `max(messages.ROWID)` | Verdict |
|---|---|---|---|---|
| `file:...?mode=ro` | yes | `wal` | **100197** | correct data, but see 3.2 |
| `mode=ro` + `PRAGMA query_only=1` | yes | `wal` | **100197** | correct data, same caveat |
| `file:...?immutable=1` | yes | **`delete`** | **100196** | **UNSAFE, silently stale** |
| `mode=ro&nolock=1` | **no** | | | `unable to open database file` |

Writes were attempted under every mode that opened and were correctly rejected with `OperationalError: attempt to write a readonly database`.

### 3.2 `immutable=1` is provably wrong here. Do not use it.

`immutable=1` tells SQLite the file cannot change, so it **skips the WAL entirely** and reports `journal_mode=delete`. Measured divergence at one instant, with a 1.6 MB WAL holding roughly 394 frames:

| Quantity | `immutable=1` | clone including `-wal` | Delta |
|---|---|---|---|
| `count(*) FROM messages` | 90,419 | 90,420 | 1 |
| `max(ROWID)` | 100,196 | 100,197 | 1 |
| unread count | 50,736 | 50,737 | 1 |
| `sum(mailboxes.unread_count)` | 132,447 | 132,450 | 3 |

The deltas are small **right now** because the WAL happened to be small. There is no bound on that. `immutable=1` means "silently return whatever was last checkpointed," which is unbounded staleness dressed as a successful read. This confirms and now quantifies the `mode=ro`-not-`immutable` guidance already recorded in the archived May 2026 research.

### 3.3 A `mode=ro` reader does mutate the `-shm`. Proven.

This is the finding that rules out reading the live file directly, and it needed a clean experiment because Mail writes concurrently and confounds naive before/after hashing.

Method: clone `Envelope Index`, `-wal`, and `-shm` into scratch where nothing else touches them; hash all three; open the **copy** with `mode=ro` + `PRAGMA query_only=1`; run `SELECT count(*) FROM messages`; close; hash again. Any change is attributable to our reader alone.

```
before main : sha256[:16]=d7bd7fe7e710ac4a  size=266768384
before -wal : sha256[:16]=730a4a554a754c4c  size=1297832
before -shm : sha256[:16]=78698815af95a011  size=32768
  journal_mode: wal   msg count: 90420
after  main : d7bd7fe7e710ac4a  SAME
after  -wal : 730a4a554a754c4c  SAME
after  -shm : 14ea113c96bb94aa  *** CHANGED ***
```

This is not a bug, it is the WAL protocol: a reader must register a read mark in the shared-memory WAL index so a concurrent checkpointer does not overwrite frames it is reading. It is by design and it is concurrency-safe. But it **is a write to a file under `~/Library/Mail/`**, and it therefore violates the hard constraint. `mtime` is not a usable detector either, because `-shm` is mmap-backed (live `-shm` mtime was 2 days older than `-wal` mtime).

Two consequences worth stating plainly:

1. Anything in this repo or its skills that opens the **live** `Envelope Index` with `sqlite3.connect(f"file:{db}?mode=ro", uri=True)` is writing to `Envelope Index-shm`. That includes the existing `.agents/skills/apple-mail-archive-export/scripts/` precedent (identified by grep only; I did not read that directory per the lane's exclusion) and `tools/probes/inspect_envelope_index_schema.py`. The blast radius is low, but the claim "read-only" is not literally true and the report should say so rather than let it pass.
2. The theoretical liveness risk is a stale read mark preventing WAL reset and growing Mail's WAL. Not data loss: `-shm` is derived state that SQLite deletes and rebuilds when the last connection closes.

### 3.4 The safe pattern: `clonefile(2)` snapshot, then read the clone

APFS `clonefile(2)` produces a copy-on-write snapshot in a single syscall. Zero bytes of new disk, and **the live database is never opened by SQLite at all**, so there are no locks on it and no `-shm` writes.

```
clonefile x3 (main + -wal + -shm)      p50 = 0.364 ms   p95 = 0.467 ms
sqlite open + PRAGMA query_only + close p50 = 0.044 ms
query: newest 50 in a 25,012-msg mailbox p50 = 0.152 ms
-------------------------------------------------------------
RECOMMENDED PER-CALL PATH               p50 = 0.56 ms
```

Disk cost: none. Five consecutive snapshots left free space unchanged (`df` free blocks went from 967,428,504 to 967,428,504-ish, i.e. within noise and non-decreasing); `du` reports 256 MB logical because blocks are shared. For contrast, `shutil.copyfile` is **not** a clone: it consumed 255 MB of real disk (`df` used blocks 2,912,197,016 to 2,912,457,792) in about 33 to 40 ms with a warm page cache. So the fallback for a non-APFS volume works but costs real bytes.

**Order matters.** Clone `main` first, then `-wal`, then `-shm`. If a checkpoint interleaves, the copied WAL can be inconsistent with the copied main database, which would corrupt the *copy*. The mitigation is an integrity gate plus retry.

### 3.5 Tearing stress test: 200 cycles, zero failures

200 sequential snapshot-plus-query cycles against the live database while Mail was running and writing:

```
cycles = 200, wall = 325.70 s
clone      p50 = 0.28 ms   p95 = 0.52 ms   max = 5.97 ms
query(50)  p50 = 1.54 ms   p95 = 4.22 ms   max = 6.88 ms
integrity failures                    : 0
snapshots that needed a retry         : 0
distinct messages counts observed     : [90420, 90421]
```

Mail committed a write mid-run and the snapshot picked it up correctly on the next cycle, which also demonstrates the staleness semantics: **a snapshot is a consistent point-in-time read as of the clone instant, never older than that.** No torn snapshot occurred in 200 attempts, but the retry path is still required because the failure is possible in principle and must fail loudly rather than return a partial database.

The 325.70 s wall for 200 cycles is **not** the per-call cost. It is dominated by `PRAGMA quick_check(1)`, measured separately at **246 ms p50 / 1,520 ms p95**. That is the single most important implementation detail in this section:

> **Run `quick_check` once at cold start and again only after a `sqlite3.DatabaseError`. Never per call.** Per-call validity uses `PRAGMA user_version` + `PRAGMA schema_version`, measured at **0.002 ms**, plus a `SELECT count(*) FROM messages` at 0.006 ms.

### 3.6 Concurrency: the lock disappears

12 concurrent snapshot-plus-query readers, all succeeded, all returned 50 rows, all integrity-ok, per-call clone p50 1.00 ms. There is no shared lock because there is no shared handle: each reader has its own private clone.

This directly addresses the second half of the original ask. The MCP server serialises all AppleScript behind a single `threading.Lock` with a 300 s acquire timeout (`core/applescript.py`), on top of which each call's own timeout clock only starts after acquisition, so worst-case wall time per AppleScript call is roughly 480 s. An index-backed metadata path **removes those calls from the lock entirely**, which speeds up the AppleScript calls that remain.

### 3.7 Can reading ever block Mail or corrupt anything?

- **Under the recommended clone pattern: no.** SQLite never opens the live file. `clonefile(2)` is a read of the inode extent map. No locks, no `-shm` writes, no possibility of blocking Mail or corrupting the store.
- **Under a direct `mode=ro` open: no corruption, but yes a small liveness risk**, via the `-shm` read mark described in 3.3. Also no data-loss risk, because `-shm` is derived.
- **Under `immutable=1`: no blocking, but silently wrong answers.** Section 3.2.
- Write attempts are rejected by SQLite in every mode tested. That is a real second line of defence, but it is not the primary one; not opening the live file is.

---

## 4. A concrete tool proposal

### 4.1 Design decision: a fast path inside existing tools, plus exactly one new tool

**Argument.** The surface is already 41 tools across 7 modules, and adding a tool costs a module table row, count claims in 11 required docs, an MCPB `tools[]` entry, an annotation name-set entry in `tests/core/test_read_only_registry.py`, a bump to `tools/expected_test_count.txt`, and skill frontmatter updates. The repo's own doctrine, in `tasks/reference/roadmap-2026-07-10.md` and the stalled v4 consolidation phase plan, is to **prefer a parameter on an existing tool** ("Colored flags (a `flag_color` parameter on `update_email_status`) ... Small additive change to an existing tool"; "Reduce registered public tools from 28 to <=18").

So: no new discovery tools. `search_emails`, `list_inbox_emails`, `get_inbox_overview`, `get_top_senders`, `get_statistics`, `get_mailbox_unread_counts`, `get_email_thread` all gain a backend selector. One new tool is justified because it is an operator diagnostic with no natural home and it is the thing that makes the whole lane auditable.

### 4.2 Shared parameter, added to the seven discovery tools

```python
index_backend: str = "auto"
# "auto"        : use the index when the query class is fully supported AND the
#                 schema fingerprint is allowlisted; otherwise AppleScript.
# "index"       : require the index; return INDEX_UNAVAILABLE rather than
#                 silently falling back (this is the fail-closed mode).
# "applescript" : force the legacy path. Escape hatch for divergence reports.
```

Every response gains a provenance block. This is the anti-"silent pass" mechanism and it is mandatory, not optional:

```json
{
  "source": "envelope_index",
  "index_meta": {
    "snapshot_at": "2026-08-17T06:31:04Z",
    "snapshot_age_ms": 3,
    "schema_version": "4.84003",
    "schema_fingerprint": "5b8d986644d65c6606cca63b6a6d1f0a",
    "fingerprint_status": "allowlisted",
    "mailbox_resolution": "labels_union",
    "coverage": {
      "index_rows": 25012,
      "orphan_emlx_not_in_index": 533,
      "body_text_available": false
    },
    "unsupported_filters": [],
    "fell_back_from_index": false
  },
  "warnings": []
}
```

Rules that make this fail closed:

1. **An unsupported filter is never dropped.** If a caller passes `body_text` with `index_backend="index"`, the tool returns `INDEX_CAPABILITY_UNSUPPORTED` listing the offending parameter. It must never run the query without the filter and report success. A check that skips input it does not understand reads as a pass, and that is precisely the bug class this lane must not introduce.
2. **`index_backend="auto"` that falls back must say so** via `fell_back_from_index: true` plus a `warnings[]` entry naming the reason. A silent fallback is indistinguishable from a fast success.
3. **A non-allowlisted schema fingerprint disables the index path in `auto`** and returns `INDEX_SCHEMA_UNKNOWN` in `index` mode. It never guesses.

### 4.3 The one new tool

```python
@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
async def diagnose_mail_index(
    account: Optional[str] = None,
    all_accounts: bool = False,
    verify_integrity: bool = False,   # runs quick_check, ~250ms p50 / 1.5s p95
    reconcile_disk: bool = False,     # walks .emlx, ~3-6s; off by default
    output_format: str = "json",
    timeout: Optional[int] = None,
) -> str:
```

Reports: index presence and readability, `properties` version and minor_version, schema fingerprint and allowlist status, per-mailbox `total_count` and `unread_count` versus derived counts, the `labels`-versus-`messages.mailbox` resolution mode per account, snapshot cost, and when `reconcile_disk=True` the orphan and missing-file counts from Section 6.6. This is what turns "the index disagrees with Mail" from a mystery bug report into a one-command answer, and it is what a user runs when a number looks wrong.

`reconcile_disk` defaults to `False` because a full walk is 6.16 s cold / 2.83 s warm over 91,408 files. `verify_integrity` defaults to `False` because of the p95.

### 4.4 Mode-flag behaviour

Both flags are honest and simple here, because everything proposed is read-only:

- **`--read-only`**: no change. The current implementation removes `SEND_TOOLS` plus calendar write and destructive tools from the registry; it does not gate reads. All seven modified tools and `diagnose_mail_index` stay registered and behave identically. They are annotated `READ_ONLY_TOOL_ANNOTATIONS`.
- **`--draft-safe`**: no change, same reasoning.
- **What both flags MUST additionally guarantee for this lane**, and this needs to be asserted in a test rather than assumed: the index path performs **no writes anywhere**, including no `-shm` write under `~/Library/Mail/`. The enforcement is structural (never `sqlite3.connect` against the live path) and should be locked by a static test in the spirit of `tests/core/test_no_unbounded_whose.py`: a lint that fails if any module under `plugin/apple_mail_mcp/` passes a path under `~/Library/Mail` to `sqlite3.connect`. That test is the durable guard, because the constraint is about Cayman's irreplaceable store and a future refactor could quietly reintroduce a direct open.

If the optional FTS5 body index (Phase 4) is ever built, it writes only under `~/Library/Caches/AgenticAssets/apple-mail-mcp/`, matching the boundary already specified in `metadata_index_contract.py` (`DEFAULT_CACHE_RELATIVE_PATH`). Building it is a local cache write, not a Mail write, but it should still require an explicit opt-in flag and should be blocked under `--read-only` on the principle that read-only means the tool creates nothing.

### 4.5 Structured errors

Reuse `ToolError` / `serialize_tool_error` from `backend/base.py`, wire shape `{"error": true, "code", "message", "remediation": {}}`. New codes, kept few and specific:

| Code | Raised when | `remediation` |
|---|---|---|
| `INDEX_UNAVAILABLE` | `index_backend="index"` but no readable index (missing, no Full Disk Access, clone failed) | `{"fallback": "index_backend=\"applescript\"", "diagnose": "diagnose_mail_index"}` |
| `INDEX_SCHEMA_UNKNOWN` | fingerprint not allowlisted | `{"observed_fingerprint", "observed_version", "fallback", "action": "file an issue in Apple Mail MCP"}` |
| `INDEX_CAPABILITY_UNSUPPORTED` | a requested filter cannot be answered from the index (chiefly `body_text`) | `{"unsupported": ["body_text"], "supported_alternative": "allow_body_scan=True with index_backend=\"applescript\"", "reason": "no body text in Envelope Index"}` |
| `INDEX_INTEGRITY_FAILED` | snapshot failed `quick_check` after N retries | `{"attempts": 3, "fallback": "index_backend=\"applescript\""}` |

Reuse rather than reinvent: `UNBOUNDED_SCAN_REQUIRED` still applies (an index query with no bound at all is still a product decision, not a free lunch), and `BODY_SCAN_DISABLED` keeps its current meaning on the AppleScript path.

### 4.6 What each modified tool gains

| Tool | Index fast path | Notes |
|---|---|---|
| `list_inbox_emails` | full | drops the 50-message `INBOX_HARD_CEILING` because there is no scan cost to bound |
| `search_emails` | metadata filters only | `body_text` returns `INDEX_CAPABILITY_UNSUPPORTED` under `index`; falls back with a warning under `auto`. **Also fixes the Section 6.3 bug by construction, but the bug must be fixed on the AppleScript path too** |
| `get_inbox_overview` | full | replaces the uncapped child-mailbox loop with one grouped query |
| `get_top_senders` | full | replaces a 15-message sample with a true aggregate over the whole mailbox |
| `get_statistics` | full | same |
| `get_mailbox_unread_counts` | full, **with a mandatory divergence warning** | see 6.4; must not silently replace 3,236 with 10,016 |
| `get_email_thread` | full | `conversation_id` plus `message_references` beats the current 50-message cap |

Unchanged and still AppleScript-only: everything in `compose/`, `manage/`, `calendar/`, `get_email_by_id` when full headers or content are needed.

### 4.7 Offset paging

Worth flagging because it interacts. On the AppleScript path, `search_emails` computes `base_cap = limit + 1 + offset` and then clamps it to `SEARCH_HARD_CEILING = 50`, while the script decrements `offsetRemaining` against the match count. For `limit=20, offset=100` the mailbox emits zero rows and reports `has_more: false`. So the "page through bounded calls" remediation the error messages recommend does not actually work past roughly offset 29. The index path has no scan cap, so `LIMIT ? OFFSET ?` is correct and cheap, and paging becomes real for the first time.

---

## 5. Phased plan

Sized so Phase 1 is independently shippable and independently valuable, and so no phase depends on a decision that has not been made.

### Phase 0: fix the shipped bug. Blocking. Not part of this lane's design work.

`search_emails` subject filtering returns 0 results on every account tested (Section 6.3). This must be fixed and regression-tested before an index path lands, for two reasons: users are getting wrong answers today, and if the index path ships first it will mask the bug, making the AppleScript fallback quietly useless.

**Proof gate:** a live test asserting that `search_emails(subject_keyword=<term>)` returns the same ids as a bounded `osascript` loop over the same window, on at least one EWS and one IMAP account. Plus a mocked regression test that would have caught it.

### Phase 1: read-only index reader plus `diagnose_mail_index`. Independently shippable.

Build `plugin/apple_mail_mcp/index/` (snapshot via `clonefile(2)` with `shutil.copyfile` fallback, `mode=ro` + `query_only` on the clone, schema fingerprint allowlist, `properties` version read) and the `diagnose_mail_index` tool. **No existing tool changes behaviour.**

Valuable on its own: it answers "why does the MCP report 3,236 unread when I have 10,016 unread rows," it detects a schema change before anything depends on it, and it gives a reconciliation report for the 988 orphan `.emlx` files.

**Proof gate:**
- Static test: no module passes a `~/Library/Mail` path to `sqlite3.connect`.
- Attribution test: after N snapshot-and-query cycles, `sha256` of the live `Envelope Index`, `-wal`, and `-shm` are unchanged relative to a control interval. This is the constraint-1 gate and it must be in CI as a live-only test, skipped with an explicit `SKIPPED (no live Mail index)` marker that is visible, not silent.
- 200-cycle tearing stress with zero integrity failures.
- Fingerprint mismatch returns `INDEX_SCHEMA_UNKNOWN` and never a guessed answer.
- `immutable=1` is absent from the codebase; a test asserts it.

### Phase 2: metadata fast path on the four safest tools

`list_inbox_emails`, `get_mailbox_unread_counts`, `get_top_senders`, `get_email_thread`. `index_backend="auto"` default. Gmail `labels` union mandatory from day one.

**Proof gate:** an id-level agreement harness. For a matrix of (account scheme x mailbox x filter), assert that the index result set and the AppleScript result set are **ordered-identical**, or that the divergence is one of the enumerated known-divergence classes from Section 6, each with a test naming it. Any unenumerated divergence fails the gate. Plus a `perf-test` case per tool with a threshold in `cli/constants.py`.

### Phase 3: `search_emails` and the analytics tools

Adds subject substring, sender exact and domain, recipient search, attachment name, date range, and true paging. `body_text` returns `INDEX_CAPABILITY_UNSUPPORTED` under `index`.

**Proof gate:** same agreement harness extended, plus an explicit negative test that `body_text` never silently succeeds on the index path.

### Phase 4: opt-in FTS5 body index. Separate decision, separate branch.

Only if Cayman wants body search. Measured cost: 32 s build, 827 MB, 0.03 to 0.05 ms queries, zero new dependencies. This is the resurrection of `v4-performance-consolidation` Phase 4, which already carries the storage-boundary rules and the prior-art attribution requirement (credit `imdinu/apple-mail-mcp`, GPL-3.0, architecture only, no code copied).

**Proof gate:** incremental refresh correctness (an id present on disk but stale in the FTS index must be detected), and a documented recall statement. FTS5 token matching found 224 where substring found 253, so the tool must state which semantics it offers. Prefix search (`invoic*`) found 264, i.e. broader than substring, which is a different answer again and must not be conflated.

### Phase 5 (optional): retire the AppleScript discovery paths

Only after Phases 2 and 3 have run in production with the agreement harness green for a full release cycle. Flip `index_backend` default to `"index"`. This is where the real maintenance win lands, because the bounded-scan machinery, the `whose` allowlist, the pipe-row sanitiser, and the scan-cap arithmetic all exist to work around AppleScript costs that no longer apply.

---

## 6. Correctness and completeness gaps

### 6.1 Result sets agree exactly where both paths work

Ordered id lists, 25,012-message EWS Inbox:

| Test | AppleScript | Index | Ordered identical | Speedup |
|---|---|---|---|---|
| newest 25 | 10.05 s | 0.16 ms | **True** | 62,631x |
| newest 25 unread | 17.66 s | 0.17 ms | **True** | 106,438x |

### 6.2 `messages.ROWID` is the AppleScript `message id`

The linchpin of the whole design. SQLite newest-5 by `date_received DESC` in mailbox 22 gave `99767, 99676, 99675, 99661, 99658`. The `apple-mail inbox` tool returned `message_id` values `'99767', '99676', '99675', '99661', '99658'` with matching timestamps to the second.

So an index-backed discovery tool returns ids that the existing exact-id action tools (`get_email_by_id`, `get_email_by_ids`, `move_email`, `update_email_status`, `manage_trash`) already accept. No translation layer, no new id space, and the ID-first contract is preserved rather than bypassed.

Also confirmed: `ROWID` is `AUTOINCREMENT` with `sqlite_sequence = 100197`, so ids are sparse (min 1, max 100,197, count 90,420, gap 9,777) but **never reused**. Stable identifiers.

### 6.3 `search_emails` subject filtering is broken. Reproduced five ways.

Ground truth from the index: `subject LIKE '%meeting%'` in mailbox 22 has **1,371 all-time matches**, of which **2 fall inside the newest 50** at newest-first ranks 25 and 46 (ids 98968 and 98659, with the term at character offsets 43 and 12 of the base subject).

Mail itself agrees. A bounded `osascript` loop over `messages 1 thru 50` with `ignoring case ... subject contains "meeting"`:

```
COUNT=2 IDS=9896898659 NCAND=50      (6.45 s)
```

`search_emails` does not:

| Invocation | Elapsed | `returned` | `has_more` |
|---|---|---|---|
| `--subject meeting --limit 25` (default 2-day window) | 9.36 s | 0 | false |
| `--subject meeting --date-from 2026-01-01 --limit 25` | 4.73 s | 0 | false |
| `--subject meeting --date-from 2026-01-01 --limit 49` | 8.25 s | 0 | false |
| `--mailbox Inbox --subject meeting --date-from 2026-01-01 --limit 49` | 13.03 s | 0 | false |
| `--subject reminder --limit 10` and `--limit 49` (term at ranks 6, 23, 35) | 3.01 s / 10.95 s | 0 | false |
| **control:** same mailbox, no subject filter, `--limit 5` | 6.32 s | **5** (correct ids) | |

Not Exchange-specific. On a second account, `--subject a` (which should match nearly every subject) returned 0 while the same call without the subject filter returned 5:

```
Cayman - Agentic Assets   --subject a      1.05 s   returned=0
Cayman - Agentic Assets   (no filter)      4.44 s   returned=5
iCloud                    --subject a      0.87 s   returned=0
```

`errors` and `error_details` are both `None`. The failure is silent and `has_more: false` reads as "no such mail exists."

I did not diagnose the root cause because this lane is research-only, and I did not modify the tool. The likely area is the subject-only "needle fast path" in `plugin/apple_mail_mcp/tools/search/script.py` (the branch that reads only `subject of aMessage`), since the control path that skips subject filtering works and the elapsed time drops when the filter is present, which is consistent with the needle branch being taken and matching nothing.

**This is the single highest-priority item in this report.** It is also the cleanest possible illustration of the operating principle: a filter that silently matches nothing reads as a successful empty search.

### 6.4 Unread counts: a large, unreconciled divergence

For the 25,012-message EWS Inbox:

- AppleScript `unread count of mailbox "Inbox"` = **3,236**. The MCP `unread` command reports the same 3,236.
- The Envelope Index says **10,016**, and says it four independent ways: `mailboxes.unread_count` = 10,016, `mailboxes.unseen_count` = 10,016, `count(messages WHERE read=0 AND deleted=0)` = 10,016, `sum(duplicates_unread_count.unread_count)` = 10,016.

I ran a sweep of 16 candidate index quantities looking for 3,236 and **found no match**: last 365 days = 4,848; last 180 days = 1,843; last 90 days = 545; `type=0` = 9,901; `automated_conversation=0` = 4,447; distinct `conversation_id` = 9,447; distinct `global_message_id` = 10,015; `model_category=2` = 3,619. Nothing lands on 3,236.

Mail cannot even compute it the other way: `count of (messages of mb whose read status is false)` **timed out at 90 seconds** and returned the sentinel `-1`, while `unread count of mb` returned instantly. That is itself a useful datum about why the current tools are shaped the way they are.

Most other mailboxes on the same account agree **exactly** between AppleScript and the index (Archive 704, a teaching folder 86, Junk 9, Deleted Items 2, Sent Items 16). One is off by one (Github: 1,506 AppleScript versus 1,507 index). Drafts diverges structurally (261 AppleScript versus 0 index unread, where 261 is the Drafts `total_count`).

**Hypothesis, unverified:** `unread count` may be Mail's cached server-reported figure for the EWS folder, frozen at the last successful server sync, while `messages.read` tracks local flags that have since drifted. That is plausible given this account's server access has ended, but I could not confirm it.

**What I could not measure and why:** which number matches Mail's own sidebar badge. That requires a human looking at Mail.app; there is no read-only API that returns "what the UI displays." **This is an open question for Cayman and a hard blocker on changing `get_mailbox_unread_counts` defaults.**

**Design decision:** `get_mailbox_unread_counts` must not silently swap 3,236 for 10,016. When the index path is used and the two disagree by more than a small tolerance, report both with provenance and a warning, or keep AppleScript as the default for this one tool until the sidebar question is answered. Replacing a familiar number with an unexplained larger one reads as a bug regardless of which is more correct.

### 6.5 The Gmail `labels` indirection: the silent-zero trap

**This is the most dangerous implementation trap in the whole design.** For Gmail-style IMAP accounts, Mail stores each message once (under All Mail) and expresses other mailbox membership through the `labels` table. `messages.mailbox` therefore returns **zero rows** for a Gmail INBOX.

| Mailbox | Scheme | `mailboxes.total_count` | via `messages.mailbox` | via `labels` join | union |
|---|---|---|---|---|---|
| INBOX (a) | imap | 24,207 | **0** | **24,207** | 24,207 |
| INBOX (b) | imap | 16,721 | **0** | **16,721** | 16,721 |
| domain folder | imap | 15,183 | **0** | **15,183** | 15,183 |
| Github | imap | 12,975 | **0** | **12,975** | 12,975 |
| Important | imap | 6,713 | **0** | **6,713** | 6,713 |
| Vercel | imap | 6,648 | **0** | **6,648** | 6,648 |
| university folder | imap | 5,473 | **0** | **5,473** | 5,473 |
| All Mail | imap | 24,883 | 24,883 | 0 | 24,883 |
| Inbox | ews | 25,012 | 25,012 | 0 | 25,012 |
| Sent Items | ews | 9,819 | 9,819 | 0 | 9,819 |

The `labels` counts match the cached `total_count` **exactly** in every case, which is strong evidence the union is the correct resolution.

Aggregate consequence: summing `mailboxes.total_count` across all 215 mailboxes gives **198,703** against only **90,417** non-deleted rows in `messages`, a 108,286 apparent gap that is entirely explained by label-based double counting. Any tool that naively sums cached counts, or that naively filters `messages.mailbox`, will be badly wrong in opposite directions.

Both resolutions are fast: labels-driven 0.04 ms, `OR EXISTS` union 0.07 ms, direct 0.20 ms. So there is no performance reason to get this wrong, only a correctness reason to get it right.

**Design decision:** always resolve mailbox membership as `messages.mailbox = ? OR EXISTS(SELECT 1 FROM labels WHERE message_id = messages.ROWID AND mailbox_id = ?)`. Report which resolution fired in `index_meta.mailbox_resolution`. And add a test that asserts a Gmail INBOX returns a non-zero count, because the naive version returns zero silently and a mocked test on an EWS fixture would pass.

### 6.6 Disk versus index reconciliation: the index is a strict subset

```
Envelope Index rows                     : 90,420
.emlx files on disk                     : 91,408
on disk but NOT in the index (orphans)  :    988
in the index but NOT on disk            :      0
```

Orphans by account directory: 533, 286, 92, 58, 11, 8. **The 533 figure matches the brief's ground truth exactly**, and it belongs to the EWS account.

The direction matters enormously and is good news:

- **Zero index rows lack a file.** Every message the index knows about can have its body read. So the hybrid path never has to say "I found metadata but cannot get the body."
- **988 files are invisible to the index**, and therefore invisible to Mail's UI too (Mail deleted the rows but never unlinked the files). These are ex-messages.

**Design decision:** an index-backed tool should show **exactly what the index shows** and never surface orphans. Surfacing 988 messages that Mail's own UI does not show would be a worse bug than missing them. Orphan discovery belongs in `diagnose_mail_index(reconcile_disk=True)` and in the archive-export skill, where recovering deleted-but-present mail is the actual goal.

Also 27.8% of `.emlx` files are `.partial.emlx` (25,411 of 91,408). Body text is intact in them, verified on 400 of each: full files p50 4,927 chars, partial p50 3,943 chars, with 9 of 400 versus 15 of 400 yielding zero extractable text. So `.partial` costs some average length (detached inline images) but does not lose the text body.

### 6.7 Cross-account differences

| Scheme | Mailboxes | Non-deleted messages | Mailbox resolution | Per-mailbox flags |
|---|---|---|---|---|
| `imap` (Gmail and others) | 144 | 51,629 | **`labels` for INBOX and label folders, `messages.mailbox` for All Mail** | `server_messages` + `server_labels` populated (51,629 rows) |
| `ews` (Exchange) | 66 | 38,787 | `messages.mailbox` | `server_messages` rows = **0** for these mailboxes |
| `local` | 5 | 1 | `messages.mailbox` | n/a |

Two further gotchas:

- Unread via `server_labels` disagrees slightly with `mailboxes.unread_count` on Gmail INBOXes: 17,478 versus 17,473, and 15,583 versus 15,580. So even Mail's own two accounting paths differ by a handful. Any unread number needs a stated source.
- **`messages.remote_id` is declared `INTEGER` but holds base64 TEXT for EWS** (SQLite type affinity, values like `AAMkADZlM2I3YTU4...`). Anything that assumes an integer there will break on Exchange.

### 6.8 Other index facts a tool must handle

- `messages.deleted = 1` on only **3** rows. Trash and Deleted Items are real mailboxes, not a flag. `deleted=0` is still the right predicate but it is not the trash filter.
- **18 messages have `sender IS NULL`.** A `JOIN addresses` silently drops them; use `LEFT JOIN`.
- `message_global_data.ROWID = messages.global_message_id` (joins 90,420 of 90,420). The naive `g.message_id = m.ROWID` join returns **0** rows, another silent-zero trap. `message_id_header` is present for 100% of messages, so the Internet Message-ID is fully available.
- `messages.global_message_id` has only 87,409 distinct values across 90,420 rows, so duplicates across mailboxes share global data.
- `subjects.subject` is the base subject with `Re:` / `Fwd:` split into `messages.subject_prefix`. Subject search must consider both or state that it matches the base subject only.
- `subjects.subject` and `summaries.summary` are `COLLATE RTRIM`; `addresses.address` is `COLLATE NOCASE`; most other TEXT is `COLLATE BINARY`. Case sensitivity is therefore inconsistent across columns and must be normalised deliberately rather than inherited.
- `mailboxes.url` needs percent-decoding (`All%20Mail`). The account UUID is the URL host and maps to `~/Library/Mail/V10/<UUID>/`.
- `.emlx` path resolution: filename is the `ROWID`; the bucket path is the digits of `ROWID // 1000` reversed, one directory per digit (99767 to `Data/9/9/Messages/99767.emlx`). Targeted glob costs 0.60 ms p50 per id, 50 ids in 31.5 ms, 50 of 50 resolved. A full inventory is 6.16 s cold / 2.83 s warm for 91,408 entries and 15.7 MB in memory. **Prefer targeted globs; do not build an inventory for a bounded query.**

---

## 7. Risk register

| # | Risk | Likelihood | Blast radius | Mitigation |
|---|---|---|---|---|
| R1 | **Writing to `~/Library/Mail/`.** A direct `mode=ro` open mutates `Envelope Index-shm` (proven, 3.3). Irreplaceable store, no backup, one account's server is gone. | **Certain** if the live file is opened by SQLite | Low in practice (`-shm` is derived and rebuilt) but the constraint is absolute and the store is unrecoverable | **Never `sqlite3.connect` the live path.** `clonefile(2)` then read the clone. Enforce with a static test. Add a live CI test hashing all three live files across N cycles. Audit the existing skill scripts and `tools/probes/inspect_envelope_index_schema.py`, which currently open the live file directly. |
| R2 | **Silent undercount via the Gmail `labels` trap.** Naive `messages.mailbox` returns 0 rows for a Gmail INBOX (6.5). | **High.** It is the obvious first implementation and it fails silently. An EWS fixture test passes. | Severe: "you have no mail" for 24,207 messages | Mandatory union resolution. A test per account scheme asserting non-zero. Report `mailbox_resolution` in every response. |
| R3 | **Unread-count divergence** (3,236 versus 10,016, unreconciled, 6.4). | **Certain** on this machine | High trust damage: a familiar number changes by 3x with no explanation | Do not change `get_mailbox_unread_counts` defaults until the sidebar question is answered. Report both plus provenance. Human verification needed. |
| R4 | **`immutable=1` staleness.** Silently returns the last checkpoint (3.2, measured 1 to 3 rows behind, unbounded in principle). | Medium (it is the tempting "safest" flag and reads as more read-only) | Wrong answers with no error | Ban it. Assert its absence in a test. Clone includes `-wal`. |
| R5 | **Schema drift on a macOS update.** Private, undocumented schema. | Medium per major release. Evidence: `4.84003` unchanged May to August 2026, so drift is slower than feared, but Tahoe added `protected_message_data`, `generated_summaries`, `brand_indicators`, `root_status`. | Total loss of the fast path, or worse, subtly wrong results | Narrow **contract fingerprint** over only the 29 columns actually read (`5b8d986644d65c6606cca63b6a6d1f0a`), not the whole schema (`2a765dd8...`), so unrelated Apple additions do not false-alarm. Plus `properties.version` / `minor_version`. Non-allowlisted fingerprint disables the index in `auto` and errors in `index`. Never guess. |
| R6 | **Torn snapshot.** Main and `-wal` cloned in separate syscalls; a checkpoint between them can corrupt the copy. | Low (0 of 200 measured) but non-zero | Corrupt copy only, never the original | Clone order main, `-wal`, `-shm`. `quick_check` at cold start and after any `DatabaseError`, then retry up to 3 times, then `INDEX_INTEGRITY_FAILED`. Never return a partial database. |
| R7 | **Result-set divergence from Mail's UI** beyond the enumerated classes. | Medium | Reads as a bug even when the index is more correct | Ordered-id agreement harness in CI as a live gate. Any unenumerated divergence fails. `index_backend="applescript"` escape hatch for user-side comparison. |
| R8 | **Body search oversold.** The cheap metadata prefilter has 20.6% recall (1.3). | **High** if the design is skimmed rather than read | Silently missing 79% of matches, the exact failure mode to design against | `body_text` returns `INDEX_CAPABILITY_UNSUPPORTED` on the index path. Snippet filtering, if offered at all, is a separately named parameter with recall stated in the response. Never call it body search. |
| R9 | **Silent fallback.** `auto` degrading to AppleScript looks like a fast success. | High without explicit handling | Users cannot tell why results changed shape | `fell_back_from_index: true` plus a `warnings[]` reason. `index_backend="index"` is the fail-closed mode. |
| R10 | **Full Disk Access.** Reading `~/Library/Mail` needs it, and the grant attaches to the launching host binary with no API to test it. | High across hosts (Claude Desktop, Cowork, Codex, Cursor all differ) | Feature simply absent, per host | Probe and trap: attempt the clone, catch `PermissionError`, return `INDEX_UNAVAILABLE` with host-specific remediation. `diagnose_mail_index` reports it as the first line. Never silently fall back without saying so. |
| R11 | **Dependency cost.** | **None.** `sqlite3` including FTS5 is stdlib and present in all three interpreters. `clonefile(2)` via `ctypes`. No pyobjc (not installed anywhere, would be new). No new subprocess surface. | | The offline hash-locked 67-wheel wheelhouse is untouched. This is the cheapest possible dependency story and it should be stated in the PR. |
| R12 | **Doctrine conflict.** `plugin/apple_mail_mcp/CLAUDE.md` says "All Mail.app I/O via `core.run_applescript()`", and the roadmap calls `mdfind` "a new subprocess surface outside the `run_applescript`-only doctrine. Out of scope unless that doctrine is revisited." | Certain | A blocked PR | This lane **requires revisiting that doctrine**, and that is a Cayman decision, not an implementer's. Note in mitigation that the index path adds **no subprocess surface at all** (stdlib `sqlite3` plus one `ctypes` syscall), so it is a strictly smaller change than the `mdfind` proposal the roadmap rejected. |
| R13 | **FTS5 index size and staleness** (Phase 4 only): 827 MB, needs incremental refresh. | Medium if built | Stale results, disk pressure | Opt-in only. Store under `~/Library/Caches/AgenticAssets/...` per the existing contract. Refresh watermark plus an explicit staleness field. Blocked under `--read-only`. |
| R14 | **Orphan exposure.** 988 `.emlx` files have no index row; a disk-driven tool would surface mail Mail's UI hides. | Low with the index as the spine | Confusing, potentially privacy-relevant | Index is always the source of truth for what exists. Orphans only via `diagnose_mail_index(reconcile_disk=True)`. |

---

## 8. Relationship to `tasks/active/id-first-search-retirement/`

I read the whole lane: the decision brief, recommendations, completion audit, and `metadata-index-feasibility-spike-2026-06-30.md`. This report is intended to **close that lane's open measurement item**, not to compete with it.

### Where I agree

1. **`mode=ro`, `PRAGMA query_only`, never `immutable=1`.** The spike's helper `tools/probes/inspect_envelope_index_schema.py` already does exactly this, and the archived May 2026 research already called out that `immutable=1` ignores the WAL. Section 3.2 now attaches numbers to that (100,196 versus 100,197).
2. **The spike's risk list was the right list.** It named "Full Disk Access, schema drift, WAL consistency, and fallback behavior risks." All four are real and all four now have measurements and mitigations (R10, R5, R6, R9).
3. **Hydration tiering is the correct shape.** `bulk_metadata` versus `exact_hydrated` in `metadata_index_contract.py` maps almost exactly onto what the index can and cannot answer.
4. **The storage boundary is right.** `~/Library/Caches/AgenticAssets/apple-mail-mcp/` with TTL and provenance, outside the repo and outside packaged artifacts, is the correct home for any Phase 4 FTS index.
5. **Privacy discipline is right and I followed it.** This report contains counts, lengths, timings, ids, and mailbox row numbers. No subjects, no addresses, no body text.
6. **ID-first is preserved, and in fact strengthened.** Section 6.2 shows `messages.ROWID` *is* the AppleScript id, so the index path is natively ID-first. It hands exact ids to the existing exact-id action tools rather than inventing a selector.

### Where I extend it

1. **The spike explicitly did no live measurement** ("No live Mail reads were performed for this spike"; `todo.md`: "metadata-index live measurement not started"). This report is that measurement, for the Envelope Index branch specifically: schema, row counts, 20+ timed query classes with plans, open-mode matrix, tearing stress, and id-level agreement against the live AppleScript path.
2. **`mode=ro` is not sufficient under the never-write constraint.** This is my main substantive extension. The spike treats `mode=ro` + `query_only` as the safe pattern; Section 3.3 proves it mutates `-shm`. The safe pattern is **`clonefile(2)` then read the clone**, at 0.364 ms p50 and zero disk. The spike's own helper, and the archive-export skill scripts, should be updated.
3. **Two new hard correctness findings the spike could not have known**: the Gmail `labels` indirection (6.5) and the unreconciled unread divergence (6.4). Either one, missed, produces a silently wrong tool.
4. **`summaries` exists** and holds ~1000-char snippets for 23.5% of messages. The spike's contract lists "body snippets" only under `exact_hydrated`; some snippet coverage is available in bulk, though at 20.6% recall it must never be presented as body search.
5. **A shipped bug the spike's scope did not cover** (6.3). `search_emails` subject filtering returns 0 everywhere.
6. **Cheaper than the spike assumed.** The spike anticipated a persisted, TTL'd, provenance-tracked cache under `~/Library/Caches`. Measured, **no cache is needed for metadata**: a fresh snapshot plus query is 0.56 ms p50, faster than any cache-invalidation scheme would be worth, and it is always current as of the call. A cache is only needed for Phase 4 body FTS. This meaningfully simplifies the design and removes the whole TTL, invalidation, and staleness-reporting surface for Phases 1 to 3.

### Where I disagree

1. **On sequencing.** The spike's Next Actions put the AppleScript hydration measurement (`tools/probes/measure_metadata_hydration.py`) before Envelope Index work, and Decision 6 recommends "Option 3 before branch review, then Option 1." Given that the index answers the same questions in 0.56 ms versus 9 to 18 s, measuring AppleScript hydration cost more precisely is measuring the path we are trying to leave. I would reorder: run the schema probe and the index measurement first (done, here), and treat hydration measurement as optional.
2. **On the cache.** See extension 6. I disagree with the premise that a persisted metadata cache is the destination. It is unnecessary for metadata and it introduces staleness bugs that the snapshot approach does not have.
3. **A caution on `tools/probes/measure_metadata_hydration.py`.** It carries its own private, **lock-free** copy of `run_applescript` that does not take `_MAIL_LOCK`. Running it while the MCP server is live means two unsynchronised osascript streams into Mail.app. That is worth fixing or documenting before anyone runs it as part of a decision.

### The pending `allow_filter_scan` decision

The lane's Decision 2 asks whether `allow_filter_scan=True` stays as the bulk-action escape hatch on `move_email` / `update_email_status` / `manage_trash`, or moves to separate `bulk_*` tools. The recommendation on record is Option 1 for v3.x, Option 2 for v4.

**This research changes the economics of that decision, and I think it argues for a third option.**

`allow_filter_scan` exists because resolving a filter to a concrete id set through AppleScript is expensive, so the action tools were given a way to do the filtering themselves inside one bounded scan. With an index-backed path, resolving a filter to an exact id set costs **0.2 to 30 ms and is complete rather than capped at 50**. So the escape hatch is no longer buying speed; it is only buying a round trip.

Recommended third option: **keep the action tools purely exact-id, and let index-backed discovery be the filter resolver.** The workflow becomes discover-then-confirm-then-act:

1. `search_emails(..., index_backend="index")` returns the complete matching id set, fast, with provenance.
2. The caller reviews it. This is where a dry-run and a cap belong, and it is inherently safer than a filter evaluated inside a mutation.
3. `move_email(message_ids=[...])` acts on exact ids only.

That gets the safety posture of Option 3 ("remove all filter-scan bulk paths") without the disruption that made Option 3 too costly, and it removes the need to design separate `bulk_*` tools in Option 2. It also fixes a real current gap: `allow_filter_scan` today filters within a 50-message bounded scan, so a bulk archive of a 25,012-message inbox silently operates on 0.2% of it. An index-resolved id set does not have that ceiling.

**This is a product decision for Cayman, and it is now sequenced after Phase 3** rather than being independent, because it depends on index-backed discovery being trustworthy first. I would not close Decision 2 before Phase 3 ships.

The lane's other decisions are unaffected: legacy selector rejection (Decision 1), `mailbox="All"` opt-in (Decision 3), and fuzzy `sender` as discovery-only (Decision 4) all stand. Note that Decision 3 gets cheaper too, since `mailbox="All"` against the index is 74 ms worst case rather than a 10-mailbox-capped enumeration.

### On `tasks/active/v4-performance-consolidation-2026-05-27/`

`todo.md` flags this lane "Stale, confirm resume-vs-archive," and it is stale: it baselines 28 tools, 763 tests, v3.4.0, and single-file tool modules, against today's 41 tools, v3.11.6, and 7 split packages. Its module-split work shipped in v3.9.1.

**But its Phase 4 is not stale, it is unstarted and now validated.** Verbatim: "Add a zero-state default path plus an explicit opt-in SQLite FTS5 body index for full-text search," with the rules "One-shot opt-in required before any body index is created" and "Stop if the index requires storing email bodies outside the approved local index boundary," plus the requirement to credit `imdinu/apple-mail-mcp` (GPL-3.0) as architecture-only prior art.

That is exactly this report's Phase 4, and the measurements support it: 32 s build, 827 MB, 0.03 to 0.05 ms queries, zero new dependencies. **Recommendation: archive the v4 lane's Phases 1 to 3 as superseded, and migrate its Phase 4 text (including the attribution rules, which are a licensing obligation and must not be lost in the move) into this lane's Phase 4.** Its parking-lot note that "Account-list AppleScript exists in both `inbox.py` and `search.py`; centralize around shared helpers before adding cache behavior" also still applies.

---

## 9. Reproducing every number

The probe scripts below were written to a session-local scratch directory
(`<scratch>/fast-search-research/`) and are not committed: every number in this
report came from a live read of one machine's `Envelope Index`, so the scripts
are reproduction recipes rather than portable fixtures. Section 3.4 gives the
snapshot helper in full, and each row states which section its output backs, so
a later phase can rebuild any probe it needs to re-measure.

| Script | Produces | Section |
|---|---|---|
| `probe01_open_modes.py` | open-mode matrix, `immutable=1` staleness | 3.1, 3.2 |
| `probe02_snapshot_attrib.py` | `-shm` mutation attribution on an isolated copy | 3.3 |
| `snaplib.py` | `clonefile(2)` snapshot helper, integrity gate, retry | 3.4 |
| `probe03_capability.py` + `schema_dump.txt` | full schema, row counts, 13 query classes with plans | 2.1 to 2.5 |
| `probe04_correctness.py` | snippet coverage by year, cached-versus-actual counts, flags, ROWID contiguity | 2.4, 6.8 |
| `probe05_out.txt` | `labels` indirection, `message_global_data` join, `server_messages` | 6.5, 6.8 |
| `probe06_agreement.py` | ordered-id agreement, AppleScript versus index, speedups | 6.1, 6.2 |
| `emlxlib.py` | `.emlx` path resolution, body extraction | 6.8 |
| `probe07_hybrid.py` | disk/index reconciliation, prefilter recall | 1.3, 6.6 |
| `probe08b.py` | serial versus parallel body scan, FTS5 build and query | 1.3 |
| `probe10.py` | 200-cycle tearing stress, 12 concurrent readers, cp fallback | 3.5, 3.6 |
| `probe11_out.txt` | per-call cost breakdown, worst-case p95 | 2.5, 3.4 |
| `probe12_out.txt`, `probe13_out.txt` | unread divergence, the 3,236 hunt | 6.4 |
| `spotlight/` | `mdfind`, `NSMetadataQuery`, `CSSearchQuery`, entitlement dumps | 1.2 |

All probes are read-only. `snaplib.py` never calls `sqlite3.connect` on a live Mail path; grep it to confirm. Live AppleScript timings came from `.venv/bin/apple-mail` (one call at a time, bounded limits, no write or send path) and three bounded `osascript` reads.

**Disclosure.** `probe01_open_modes.py` opened the live `Envelope Index` four times with `mode=ro` and `immutable=1`, because that probe is what established the open-mode matrix in the first place. Per the finding in Section 3.3, the two `mode=ro` opens will have written a read mark to `Envelope Index-shm`. The live `-shm` mtime moved from 2026-08-15 16:09 to 2026-08-17 06:35 during this session, consistent with that. No message data was written, `-shm` is derived state that SQLite rebuilds, and the main database and `-wal` were never opened for writing. Every probe after `probe02` used the clone pattern and never opened the live file with SQLite. This is disclosed rather than glossed because the constraint was absolute and the sequencing means the finding could only be established by tripping it once.

**Not measured, and why:**

- **Which unread number matches Mail's sidebar** (6.4). No read-only API exposes what the UI displays. Needs a human.
- **The root cause of the `search_emails` subject bug** (6.3). Out of scope for a research-only lane; would require editing the tool.
- **Schema drift across a macOS major upgrade.** Only one OS version was available. The May-to-August `4.84003` stability is suggestive, not predictive.
- **Behaviour under a cold page cache.** `purge` needs sudo. Reported timings are warm-cache and are therefore optimistic; the `probe11` p95 figures (up to 333 ms) are the closest available proxy.
- **Non-APFS volumes.** `clonefile(2)` will fail there; the `shutil.copyfile` fallback was measured but not on a real non-APFS volume.
- **Hosts other than this one** for Full Disk Access behaviour (R10).

---

## 10. Open questions for Cayman

### Product decisions

1. **Does the `run_applescript`-only doctrine get revisited?** This is the gating question for the entire lane. `plugin/apple_mail_mcp/CLAUDE.md` says all Mail I/O goes through `core.run_applescript()`, and the roadmap rejected `mdfind` as "a new subprocess surface outside the `run_applescript`-only doctrine." The index path adds **no** subprocess surface (stdlib `sqlite3` plus one `ctypes` syscall), so it is a smaller change than the thing already rejected, but it is still a doctrine change and it is yours to make.
2. **Which unread number is right?** Look at Mail's sidebar for the Exchange Inbox. Is it 3,236 or 10,016? This blocks `get_mailbox_unread_counts` and it is a 10-second check that nothing else can substitute for.
3. **Is body search wanted at all?** If yes, Phase 4 costs 32 s of indexing and 827 MB, and is opt-in. If no, Phases 1 to 3 deliver the metadata win and body search stays an explicit AppleScript-only capability with its 25-message cap documented rather than implied.
4. **`allow_filter_scan`: adopt the third option?** Keep action tools exact-id-only and let index-backed discovery resolve filters (Section 8). This supersedes the lane's Options 1 and 2 but should not be decided before Phase 3 ships.
5. **Should the index path be default-on or opt-in?** My recommendation is `index_backend="auto"` default, because the agreement evidence is strong and the provenance block makes every use auditable. The conservative alternative is `"applescript"` default for a release.
6. **Should `search_emails` keep its 50-message ceiling on the AppleScript path?** The index path has no ceiling. Leaving `SEARCH_HARD_CEILING = 50` in place on the fallback means the fallback is close to useless for real search, which is an argument for making the fallback's limits loudly visible rather than quietly capped.

### Technical unknowns

1. **What is the 3,236 figure?** No index quantity matches it across 16 candidates tested. If it is a cached server count on an account whose server is gone, that is worth knowing generally, because it means AppleScript unread counts can be arbitrarily stale.
2. **How fast does the schema actually drift?** `4.84003` held from May to August 2026. One more data point across a macOS major release would set the maintenance budget for the fingerprint allowlist.
3. **Do all five install surfaces get Full Disk Access?** Claude Code, Claude Desktop `.mcpb`, Cowork `.plugin`, Codex, and Cursor launch from different binaries, and the FDA grant attaches to the launching binary with no API to test it. The answer determines whether this is a universal feature or a per-host one.
4. **Is `clonefile(2)` available in every host's sandbox?** It worked here via `ctypes`. A hardened-runtime or seatbelt-profiled host might block it, in which case the `shutil.copyfile` fallback costs 255 MB of disk per snapshot and the design needs a reusable-snapshot strategy instead of a per-call one.
5. **Does `search_emails` subject matching fail on any account anywhere?** I tested three. If it works somewhere, the difference isolates the bug.
6. **Should `tools/probes/inspect_envelope_index_schema.py` and the archive-export skill scripts be migrated off direct live opens?** Both currently write to `Envelope Index-shm` (Section 3.3). Low blast radius, but the "read-only" claim in their docstrings and guard-flag names is not literally accurate.
