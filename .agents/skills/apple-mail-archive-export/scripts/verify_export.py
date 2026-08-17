#!/usr/bin/env python3
"""
Prove an export is correct by comparing attachment bytes in the exported .eml
files against the original payloads on disk.

  ./verify_export.py --snapshot ARCHIVE/01-raw-snapshot/<UUID> \
                     --out ARCHIVE/10-export [--sample 2500] [--all]

Run this every time. It is not a formality.

Counts do not prove correctness. An export can report "39,269 in, 39,269 out,
zero errors" while silently emptying every attached email in the corpus, because
Python's set_payload() on a message/rfc822 part accepts a string and produces an
empty part without complaining. That exact bug survived a clean full run and was
only caught by decoding attachments back out of the .eml files and diffing them
against the originals. This script is that diff.

What it checks, per verified message:
  1. Every attachment payload present on disk decodes byte-identically out of
     the exported .eml. Both payload sources are covered: decoded payloads under
     Attachments/ and already-encoded .emlxpart sidecars.
  2. No X-Apple-Content-Length stub headers survive where a payload existed
     (a leftover stub means an attachment was never filled).
  3. Re-zipped bundle parts, whose bytes legitimately differ, are at least
     non-empty.
  4. Every .eml parses at all.
  5. Every .eml can be tied back to a source message id. A file that cannot be
     is reported, never skipped: an unidentifiable message is an unverified
     message, and counting it as passed is how a verifier lies.

Sampling is stratified, not uniform - see choose_sample() for why that matters
more than the sample size does.

Exit status is 0 only when there are no mismatches, so this is safe to gate on
in a script.
"""

from __future__ import annotations

import argparse
import email
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from email.message import Message
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_emlx import walk_numbered  # noqa: E402

# An extension seen on fewer messages than this is treated as a rare shape and
# always verified. Set generously: the whole point is that rare shapes are cheap
# to include and are where the bugs live.
RARE_EXT_MESSAGES = 25

# Bytes read from the head of each payload when sniffing for a nested email, and
# how many lines of that to classify. 8 KB rather than 2 KB because Exchange and
# Microsoft 365 messages open with enormous ARC-Seal and DKIM base64 blobs: a 2 KB
# window never reaches a recognizable header and rejected half the real nested
# emails on the Exchange account.
SNIFF_BYTES = 8192
SNIFF_LINES = 60

# Extensions that are a nested message often enough to always be worth checking,
# regardless of how common they are in the corpus. Belt and braces alongside the
# content sniff, because this is the one stratum whose miss already cost data.
MESSAGE_EXTS = {".eml", ".msg", ".emlx", ".mht", ".mhtml"}

_HEADER_START = re.compile(rb"^[A-Za-z][A-Za-z0-9-]*:")


def looks_like_nested_email(path: Path) -> bool:
    """Cheap content sniff for a payload that is itself an RFC822 message.

    Tests the *shape* of the head rather than looking for particular header
    names. A message begins with a run of lines that are either "Name: value" or
    a folded continuation starting with space or tab, and essentially nothing else
    does. Name-based tests fail on real mail: requiring two recognizable headers
    inside 2 KB missed 12 of the 24 nested emails on the Exchange account, because
    ARC-Seal and ARC-Message-Signature blobs filled the whole window before any
    ordinary header appeared.

    This stratum matters more than the others because message/rfc822 is the part
    type that failed silently in production: set_payload() accepted a string and
    produced an empty part, so 61 attached emails were destroyed across 24
    messages while the report said zero errors and every count balanced.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(SNIFF_BYTES)
    except Exception:
        return False
    if not _HEADER_START.match(head):
        return False

    starts = plausible = other = 0
    for line in head.split(b"\n")[:SNIFF_LINES]:
        if not line.strip():
            break  # end of the header block; a real message has a blank line
        if line[:1] in (b" ", b"\t"):
            plausible += 1
        elif _HEADER_START.match(line):
            starts += 1
            plausible += 1
        else:
            other += 1

    # Three or more headers, and almost every line accounted for. A stray
    # "Subject:" inside an HTML or CSV attachment cannot clear this.
    return starts >= 3 and plausible >= 9 * (plausible + other) // 10


def index_originals(snapshot: Path) -> dict[tuple[str, str], tuple[Path, str]]:
    """Map (message_id, dotted_part) -> (payload path, kind).

    Two kinds, because the exporter treats them oppositely and so must the check:

      "decoded"  Attachments/<id>/<part>/<file>   - raw bytes, re-encoded on
                                                    export, compare decoded
      "encoded"  Messages/<id>.<part>.emlxpart    - already MIME-encoded,
                                                    spliced verbatim

    Reversing those two double-encodes an attachment, which is why both have to
    be verified. Indexing only Attachments/ left the encoded path with no check
    at all: an export could mangle every sidecar and still report a clean pass.

    This walk is deliberately independent of anything the exporter wrote. The
    verifier is only worth running because it locates and decodes payloads by its
    own reasoning; sharing that discovery with the exporter would make the two
    agree by construction and catch nothing.
    """
    orig: dict[tuple[str, str], tuple[Path, str]] = {}

    for root, _dirs, files in os.walk(snapshot):
        base = os.path.basename(root)

        if base == "Attachments":
            for mid in os.listdir(root):
                mdir = Path(root) / mid
                if not mdir.is_dir():
                    continue
                for partnum in os.listdir(mdir):
                    pdir = mdir / partnum
                    if not pdir.is_dir():
                        continue
                    entries = sorted(pdir.iterdir())
                    if entries:
                        orig[(mid, partnum)] = (entries[0], "decoded")

        elif base == "Messages":
            for name in files:
                # "<id>.<dotted.part>.emlxpart"
                if not name.endswith(".emlxpart"):
                    continue
                stem = name[: -len(".emlxpart")]
                mid, _, partnum = stem.partition(".")
                if mid.isdigit() and partnum:
                    orig[(mid, partnum)] = (Path(root) / name, "encoded")

    return orig


def message_id_from_name(stem: str) -> str | None:
    """Recover the .emlx id from an exported filename, or None if it cannot be.

    Two shapes exist: "<date>_<id>_<slug>" for normal messages and
    "oversize-<id>" for ones streamed past the size threshold. The oversize shape
    used to fall through to an empty id, which then matched no original, which
    silently disabled every check for that message and let it contribute a pass.
    Returning None makes that condition loud instead of invisible.
    """
    if stem.startswith("oversize-"):
        tail = stem[len("oversize-"):]
        return tail if tail.isdigit() else None
    parts = stem.split("_")
    if len(parts) > 1 and parts[1].isdigit():
        return parts[1]
    return None


def decode_like(part: Message, raw: bytes) -> bytes:
    """Decode `raw` using the transfer encoding declared on `part`.

    Used for .emlxpart sidecars, which are stored already encoded. Comparing the
    decoded forms rather than the raw text makes the check immune to base64 line
    wrapping, which differs harmlessly between Mail and Python and would
    otherwise produce a wall of false mismatches.
    """
    probe = Message()
    cte = part.get("Content-Transfer-Encoding")
    if cte:
        probe["Content-Transfer-Encoding"] = cte
    probe.set_payload(raw.decode("ascii", "surrogateescape"))
    return probe.get_payload(decode=True) or b""


def choose_sample(emls: list[Path], orig: dict[tuple[str, str], tuple[Path, str]],
                  budget: int, seed: int) -> tuple[list[Path], dict]:
    """Pick which messages to verify, forcing structurally unusual ones in.

    Uniform sampling is the wrong default here, and the arithmetic is stark.
    Failures concentrate in rare shapes, and rare shapes are precisely what a
    uniform draw misses. On the 39,269-message Exchange account, 26 messages carry
    a nested email; a uniform 2,500-message draw at the default seed picked up
    exactly none of them, and the message/rfc822 bug that emptied every one of
    those attachments would have sailed through with a confident PASS.

    Each stratum is derived from the snapshot, never from anything the exporter
    recorded, so an exporter that mis-reports cannot steer the sample away from
    its own bug. The strata, in descending order of how much they have actually
    caught:

      nested email      content-sniffed, not guessed from the extension. This is
                        the one that matters; an extension-only rule found 3 of
                        the 26 above, and sniffing finds all 26.
      encoded sidecar   the .emlxpart splice path, rare enough that a uniform
                        draw over a large account will usually contain zero
      bundle directory  re-zipped, so byte equality does not apply and the check
                        degrades to non-emptiness
      rare extension    cheap catch-all for oddities the other strata miss
      many parts        exercises the part walker and dotted numbering

    Everything forced in is verified even if that exceeds the budget. Silently
    dropping the interesting cases to respect a sample size would defeat the
    purpose; the extra count is reported instead.
    """
    by_mid: dict[str, list[tuple[str, Path, str]]] = defaultdict(list)
    for (mid, partnum), (path, kind) in orig.items():
        by_mid[mid].append((partnum, path, kind))

    # How common is each payload extension across messages? Extension is a cheap
    # stand-in for content type, available without parsing a single .eml.
    ext_messages: Counter[str] = Counter()
    for mid, parts in by_mid.items():
        for ext in {p.suffix.lower() for _n, p, _k in parts}:
            ext_messages[ext] += 1
    rare_exts = {e for e, n in ext_messages.items() if n < RARE_EXT_MESSAGES}

    # Part count is the other cheap rarity signal: heavily-nested messages
    # exercise the walker and the numbering in ways a two-part message does not.
    counts = sorted(len(p) for p in by_mid.values())
    busy_threshold = counts[int(len(counts) * 0.99)] if counts else 0

    forced_mids: set[str] = set()
    reasons: Counter[str] = Counter()

    def force(mid: str, why: str) -> None:
        reasons[why] += 1
        forced_mids.add(mid)

    for mid, parts in by_mid.items():
        kinds = {k for _n, _p, k in parts}
        if "encoded" in kinds:
            force(mid, "encoded .emlxpart sidecar")
        if any(p.is_dir() for _n, p, _k in parts):
            force(mid, "re-zipped bundle directory")
        if any(p.suffix.lower() in MESSAGE_EXTS for _n, p, _k in parts) or any(
                not p.is_dir() and looks_like_nested_email(p)
                for _n, p, _k in parts):
            force(mid, "nested email payload")
        if {p.suffix.lower() for _n, p, _k in parts} & rare_exts:
            force(mid, "rare payload extension")
        if busy_threshold and len(parts) >= busy_threshold:
            force(mid, "unusually many detached parts")

    forced: list[Path] = []
    rest: list[Path] = []
    for f in emls:
        # Oversize messages are always verified. They were invisible to this
        # script for its whole life because of the filename shape, so they get no
        # benefit of the doubt now.
        if f.stem.startswith("oversize-"):
            forced.append(f)
            reasons["oversize streamed message"] += 1
            continue
        mid = message_id_from_name(f.stem)
        if mid is None:
            forced.append(f)  # reported as a problem downstream, never skipped
            reasons["unidentifiable filename"] += 1
        elif mid in forced_mids:
            forced.append(f)
        else:
            rest.append(f)

    random.seed(seed)
    remaining = max(0, budget - len(forced))
    filler = random.sample(rest, min(remaining, len(rest)))

    info = {
        "strategy": "stratified",
        "forced": len(forced),
        "forced_reasons": dict(reasons),
        "uniform_filler": len(filler),
        "rare_extensions": sorted(rare_exts),
        "budget_exceeded_by": max(0, len(forced) - budget),
    }
    return forced + filler, info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--sample", type=int, default=2500,
                    help="message budget for the uniform part of the sample "
                         "(rare shapes are always verified on top; default 2500)")
    ap.add_argument("--all", action="store_true",
                    help="verify every message instead of a sample")
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    eml_root = args.out / "eml"
    if not eml_root.is_dir():
        print(f"no exported eml/ under {args.out}", file=sys.stderr)
        return 2

    orig = index_originals(args.snapshot)
    emls = sorted(eml_root.rglob("*.eml"))
    if not emls:
        print("no .eml files found", file=sys.stderr)
        return 2

    if args.all:
        sample, sample_info = emls, {"strategy": "all"}
    else:
        sample, sample_info = choose_sample(emls, orig, args.sample, args.seed)

    ok = bad = stubs = parse_fail = unidentified = 0
    nbytes = 0
    problems: list[dict] = []

    for f in sample:
        try:
            msg = email.message_from_bytes(f.read_bytes())
        except Exception as exc:
            parse_fail += 1
            problems.append({"file": f.name, "error": f"parse: {exc!r}"})
            continue

        mid = message_id_from_name(f.stem)
        if mid is None:
            unidentified += 1
            problems.append({
                "file": f.name,
                "error": "cannot recover message id from filename; NOT verified",
            })
            continue

        for num, part in walk_numbered(msg):
            if part.get_content_maintype() == "multipart":
                continue

            key = (mid, num)
            entry = orig.get(key)

            if part.get("X-Apple-Content-Length"):
                # Only a problem when the payload WAS on disk; a stub with no
                # original is a correctly-preserved known gap.
                if entry is not None:
                    stubs += 1
                    problems.append(
                        {"file": f.name, "part": num, "error": "leftover stub"})
                continue

            if entry is None:
                continue
            src, kind = entry

            if src.is_dir():
                # A bundle re-zipped from a directory: the bytes legitimately
                # differ, so equality is the wrong assertion. Emptiness is not,
                # and an empty part here would otherwise pass unexamined.
                body = part.get_payload(decode=True) or b""
                if body:
                    ok += 1
                    nbytes += len(body)
                else:
                    bad += 1
                    problems.append({
                        "file": f.name, "part": num, "attachment": src.name,
                        "error": "re-zipped bundle part is empty",
                    })
                continue

            try:
                raw = src.read_bytes()
            except Exception as exc:
                bad += 1
                problems.append({"file": f.name, "part": num,
                                 "error": f"unreadable original: {exc!r}"})
                continue

            want = decode_like(part, raw) if kind == "encoded" else raw

            if part.get_content_type() == "message/rfc822":
                # Nested messages are re-serialized, so bytes are not stable.
                # Assert the part is populated and roughly the right size - the
                # failure mode being guarded is an EMPTY part, which this catches.
                pl = part.get_payload()
                got = pl[0].as_bytes() if isinstance(pl, list) and pl else b""
                if got and abs(len(got) - len(want)) / max(len(want), 1) < 0.25:
                    ok += 1
                    nbytes += len(want)
                else:
                    bad += 1
                    problems.append({
                        "file": f.name, "part": num, "type": "message/rfc822",
                        "want_bytes": len(want), "got_bytes": len(got),
                        "error": "nested message empty or wrong size",
                    })
                continue

            try:
                got = part.get_payload(decode=True)
            except Exception as exc:
                bad += 1
                problems.append({"file": f.name, "part": num,
                                 "error": f"decode: {exc!r}"})
                continue

            if got == want:
                ok += 1
                nbytes += len(want)
            else:
                bad += 1
                problems.append({
                    "file": f.name, "part": num, "attachment": src.name,
                    "kind": kind,
                    "want_bytes": len(want), "got_bytes": len(got or b""),
                    "error": "attachment bytes differ",
                })

    total_bad = bad + stubs + parse_fail + unidentified
    kinds = Counter(k for _p, k in orig.values())
    result = {
        "messages_verified": len(sample),
        "of_total": len(emls),
        "coverage_pct": round(100 * len(sample) / len(emls), 1),
        "sampling": sample_info,
        "attachments_byte_identical": ok,
        "bytes_verified": nbytes,
        "attachment_mismatches": bad,
        "leftover_stubs": stubs,
        "parse_failures": parse_fail,
        "unidentified_files": unidentified,
        "originals_indexed": len(orig),
        "originals_by_kind": dict(kinds),
        "passed": total_bad == 0,
        "problems": problems[:50],
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\nverified {len(sample):,} of {len(emls):,} messages "
              f"({result['coverage_pct']}%)")
        if sample_info.get("strategy") == "stratified":
            print(f"  stratified: {sample_info['forced']:,} forced "
                  f"+ {sample_info['uniform_filler']:,} uniform")
            for why, n in sorted(sample_info["forced_reasons"].items()):
                print(f"      {n:>6,}  {why}")
            if sample_info["budget_exceeded_by"]:
                print(f"      (budget exceeded by "
                      f"{sample_info['budget_exceeded_by']:,} to keep every "
                      f"rare shape; nothing was dropped)")
        print(f"  originals indexed          : {len(orig):,} "
              f"({kinds.get('decoded', 0):,} decoded, "
              f"{kinds.get('encoded', 0):,} encoded)")
        print(f"  attachments byte-identical : {ok:,}")
        print(f"  bytes verified             : {nbytes:,} "
              f"({nbytes/1e9:.2f} GB)")
        print(f"  attachment mismatches      : {bad}")
        print(f"  leftover stubs             : {stubs}")
        print(f"  parse failures             : {parse_fail}")
        print(f"  unidentified files         : {unidentified}")
        if problems:
            print("\nfirst problems:")
            for p in problems[:10]:
                print(f"   {p}")
        print("\nPASS" if total_bad == 0 else "\nFAIL")

    return 0 if total_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
