# Reading the reports: which numbers matter and which are noise

Read this when interpreting `export-report.json`, `verify_export.py` output, or
`check_mbox.py` output - especially before telling a user that something looks
wrong. Several fields have alarming names and benign meanings, and the cost of
getting that backwards is either a false alarm or, worse, a shrug at a real
failure.

The general rule: **counts and exit codes cannot detect the dominant failure mode
here.** A full export once reported `39,269 written, 0 parse errors` while
silently emptying every attached email in the corpus. Treat the report as a map of
where to look, and the byte-comparison and round-trip checks as the actual proof.

## Contents

1. `export-report.json`
2. `verify_export.py`
3. `check_mbox.py`
4. Invariants worth asserting
5. Named fields that look wrong but are not

---

## 1. `export-report.json`

| Field | Meaning | What to expect |
|---|---|---|
| `total` | `.emlx` files found by walking the snapshot | Must equal the count from `discover_accounts.py` |
| `written` | `.eml` files produced | Must equal `total`; anything less needs explaining |
| `verbatim` | messages needing no reassembly | Includes `oversize` |
| `reassembled` | messages whose detached payloads were spliced back | Should equal the `.partial.emlx` count |
| `reassembly_failed` | reassembly raised, so the message was written from raw bytes | Should be 0. The message survives, minus whatever was detached |
| `filled_attachments` | parts filled from `Attachments/` (decoded, re-encoded) | Roughly the attachment count |
| `filled_emlxpart` | parts filled from `.emlxpart` sidecars (already encoded, spliced verbatim) | Often 0; sidecars are rare |
| `filled_rfc822` | attached emails rebuilt as nested messages | The class that once failed silently |
| `missing_parts` | stubs with no payload anywhere on disk | Small; each one must be explainable |
| `oversize` | messages streamed without MIME parsing | 0 or a handful of very large drafts |
| `qp_parts` / `uu_parts` | parts re-encoded quoted-printable / from uuencode | Small; uuencode is legacy |
| `xacl_mismatch` | declared `X-Apple-Content-Length` differed from what was spliced | **Usually benign, see section 5** |
| `parse_errors` | messages that hit any error | Must be 0. Equals `reassembly_failed + unreadable` |
| `unreadable` | the `.emlx` could not be split or read, so nothing was written | Must be 0. These are the only genuinely lost messages |
| `invariants_ok` / `invariants_broken` | whether the counters add up | `true` / empty. A violation exits non-zero |

`missing_detail` and `error_detail` carry the specifics. An empty
`missing_detail` alongside a non-zero `missing_parts` would itself be a bug.

**`parse_errors` splits into two very different outcomes**, which is why both are
counted separately. `unreadable` means the message was lost: the `.emlx` would not
split, so no `.eml` exists. `reassembly_failed` means the message was kept but
written from its raw bytes, so it is complete except for anything that had been
detached. Treating those as one number made the report unfalsifiable, because a
reassembly failure incremented neither `verbatim` nor `reassembled` and the sum
quietly came up short with nothing naming the difference.

## 2. `verify_export.py`

This is the gate. It decodes attachments back out of the exported `.eml` files
and byte-compares them with the originals on disk.

| Field | Meaning |
|---|---|
| `messages_verified` / `coverage_pct` | how much of the corpus was actually checked - `--all` for 100% |
| `sampling` | which strata were forced in and why; `strategy: all` when `--all` |
| `attachments_byte_identical` | payloads that matched exactly. The number that matters |
| `attachment_mismatches` | **must be 0.** Any value here is real corruption |
| `leftover_stubs` | `X-Apple-Content-Length` stubs still present where a payload existed. Must be 0 |
| `parse_failures` | exported `.eml` files that would not parse. Must be 0 |
| `unidentified_files` | exported files whose message id could not be recovered from the filename, so they were **not verified**. Must be 0 |
| `originals_indexed` / `originals_by_kind` | payload files found on disk, keyed `(message id, part)`, split into `decoded` (from `Attachments/`) and `encoded` (`.emlxpart` sidecars) |

`originals_indexed` can legitimately exceed `attachments_byte_identical`. Mail
sometimes stores the same attachment under two part numbers for one message -
observed as part `2` and part `2.2` holding the same PDF. Reconcile by hashing:
if the extra payload's bytes already appear in the export, nothing is missing.
Do not report it as a gap without checking.

The `message/rfc822` case is compared by size proximity rather than exact bytes,
because re-serializing a nested message is not byte-stable. That is a deliberate
weakening for one part type, not an oversight.

**Sampling is stratified, and that matters more than the sample size.** Failures
concentrate in structurally rare messages, which is exactly what a uniform draw
misses. On the 39,269-message Exchange account, 26 messages carry a nested email
and a uniform 2,500-message draw at the default seed contained none of them: the
bug that emptied all 61 of those attachments would have passed. So the sampler
forces in every message with a nested-email payload, an `.emlxpart` sidecar, a
re-zipped bundle, a rare payload extension, an unusually high part count, or the
`oversize-` filename shape, then fills the remaining budget uniformly. On that
account 949 of 39,269 messages are forced, 2.4% of the corpus, and selection adds
a few seconds. Every stratum is derived from the snapshot rather than from
anything the exporter recorded, so an exporter that mis-reports cannot steer the
sample away from its own bug.

Nested-email detection sniffs the first 8 KB for the *shape* of a header block
rather than looking for particular header names. Name-based tests fail on real
mail: requiring two recognizable headers within 2 KB found only 12 of those 24
messages, because Microsoft ARC-Seal and DKIM base64 blobs fill the window before
any ordinary header appears. The shape test finds 24 of 24.

## 3. `check_mbox.py`

Reverses the mbox conversion and compares against the `.eml` source.

| Field | Meaning |
|---|---|
| `exact` | messages that round-tripped byte for byte. Should equal the message count |
| `nl` | identical except trailing newlines. Acceptable, but investigate a large count |
| `esc` | messages containing a body line starting `From ` that had to be escaped. Expect ~1% |
| `separators` | `^From ` lines found; must equal the message count per file |
| `failures` | must be 0 |

A failure naming a folded continuation line after the injected flag headers means
a header got split - see the folded-header trap in `pitfalls.md`.

## 4. Invariants worth asserting

Cheap to check, and each one has caught a real bug:

```
verbatim + reassembled + reassembly_failed == written     closed, always
written  + unreadable                      == total       closed, always
reassembly_failed + unreadable             == parse_errors  closed, always

total          == .emlx count from discovery
reassembled    == .partial.emlx count from discovery
parse_errors   == 0   (so unreadable == 0 and reassembly_failed == 0)
missing_parts  == 0        (or every entry individually explained)
attachment_mismatches == 0, leftover_stubs == 0, unidentified_files == 0
check_mbox exact == message count, failures == 0
```

The first three are closed sums rather than expectations: every message takes
exactly one outcome path, so a violation means a path was added without being
accounted for. `export_emlx.py` asserts all three itself via
`Stats.check_invariants()`, reports them as `invariants_ok`, and exits non-zero if
any fails, because a report whose own totals disagree cannot be trusted to
describe the run it summarizes.

The pairing of `reassembled` against the independently counted partial total is
the most useful of the rest: it is the one that fails loudly if detached
attachments were skipped rather than spliced, which is the dominant way these
exports go wrong.

**A note on where these invariants came from.** For a while the documented rule
was `verbatim + reassembled == total`, which was wrong in a way nothing detected:
a message whose reassembly raised was written from raw bytes and counted in
neither, so the sum came up short with no field naming the difference. Splitting
`reassembly_failed` and `unreadable` out of `parse_errors` closed it. The lesson
generalizes past this script - an accounting identity that is *nearly* true is
worse than no identity at all, because it gets asserted, passes on healthy data,
and then explains away the one run where it matters.

Likewise, oversize messages used to be unverifiable. `verify_export.py` recovers
the message id from the filename, and the `oversize-<id>.eml` shape fell through
to an empty id, which matched no original, which silently disabled every check for
that message and let it contribute a pass. Both shapes are now recognized, an
unrecognizable filename is reported as `unidentified_files` and fails the gate
rather than being skipped, and oversize messages are always forced into the
sample. On the 39,269-message Exchange account this changed nothing about the data
- both oversize messages were plain `.emlx` at 868 MB with zero stubs, so nothing
had been missed - but that was luck rather than design, and it took a manual check
to establish. A verifier that cannot name what it declined to examine is not a
verifier.

## 5. Named fields that look wrong but are not

- **`xacl_mismatch` in the hundreds.** `X-Apple-Content-Length` records the
  part's *base64 wire length* as Mail computed it, including its own line
  wrapping. Re-encoding with a different wrap width changes that length without
  changing a single decoded byte. On one 531-message account this fired 317 times
  while byte-comparison found zero mismatches. Check `attachment_mismatches`
  before treating it as a problem; it is the authority.
- **`filled_emlxpart: 0`.** `.emlxpart` sidecars are genuinely rare - 15 files in
  a 91,402-message store. Zero is normal, not a skipped code path.
- **More messages exported than Mail displays.** Files exist with no Envelope
  Index row, because Mail deletes the row without unlinking the file. On one
  account the export held 531 where Mail counted 520; the extra 11 were Drafts
  Mail no longer listed. Enumeration walks the filesystem, so these are
  *recovered*, not spurious. Worth surfacing as a feature.
- **A snapshot manifest that stops matching the live store.** Mail toggles plist
  `flags` bit 7 while running. Compare the RFC-822 message region rather than
  whole files before concluding the snapshot is stale.
- **`esc` greater than zero in `check_mbox.py`.** mbox *must* mutate those bytes;
  reversibility is the property that matters, and the round trip proves it. This
  is exactly why the `.eml` tree stays the archive of record.
