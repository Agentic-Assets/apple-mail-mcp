#!/usr/bin/env python3
"""
Split oversized per-mailbox mbox files into per-year files, so a mail client can
actually finish importing them.

  ./split_mbox.py --out ARCHIVE/10-export

Client imports are widely reported to fail on single multi-gigabyte mbox files, and
4 GiB is the threshold usually named, being where 32-bit file offsets stop working.
Treat that number as the default to split at rather than a measured cliff: no client
import has been timed at that size here, and the reason to split anyway is that the
costs are asymmetric. Splitting is under a minute and provably lossless, while a
failed import arrives hours in and leaves a partial mailbox that looks finished.
This archive's Inbox.mbox is 9.0 GB and Sent Items.mbox 8.5 GB.

Record bytes are copied through verbatim: nothing here unescapes mboxrd, reparses
a message, or rewrites a header, since each can corrupt an archive while still
passing count-based checks (check_mbox.py documents the folded-header bug behind
that rule). The only decision made is which output file each record lands in.

The year comes from the record's own `From ` envelope line, which make_mbox.py
writes for every record as `From <sender> %a %b %e %H:%M:%S %Y`. Pairing
index.sqlite's `date_epoch` positionally with the records is the tempting
alternative, rejected deliberately: date_epoch is monotonic, so a pairing that
slipped one row would hand plausible, contiguous, ascending years to the wrong
messages while every check below still passed. Taking the year from bytes inside
the record is what makes those checks test the year assignment itself.

The timezone consequence is the one way this goes wrong legitimately. date_epoch,
which fixed the record order, is UTC (`parsedate_to_datetime(...).timestamp()`);
the envelope stamp is that same Date header as wall clock with the offset
discarded, so it carries the sender's local year. The two disagree within roughly
14 hours of a New Year, so wall-clock year is not guaranteed monotonic in the
ordering key and one straddling pair would break the contiguity everything relies
on. No straddle turned up in the mailboxes split so far, and the checks detect that
case rather than assume it away: a straddle fails loudly with the years named, and
the fix is then to split by UTC year from index.sqlite. A record with an unparseable
Date header carries make_mbox.py's epoch-0 stamp, so it lands in a 1970 file in
plain sight rather than folded into a neighbour.

Four things must hold or the run exits non-zero: per-year runs are contiguous and
strictly ascending, which only date_epoch ordering makes available; therefore the
outputs concatenated in ascending year order reproduce the source byte for byte,
checked as three streamed SHA-256 values with no temp file; records and bytes out
equal records and bytes in; and per-year counts equal index.sqlite's own UTC-year
histogram, catching a systematic year shift the byte checks cannot see. Nothing is
skipped: a record with no plausible envelope year fails its mailbox and is named,
because a check that quietly passes over input it cannot classify reads as a pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SEP, CHUNK, GIB = b"\nFrom ", 4 << 20, 1 << 30
DEFAULT_MAX, MAX_YEAR = 4 * GIB, datetime.now(timezone.utc).year + 1
ENVELOPE_YEAR = re.compile(rb"^From .*[ \t](\d{4})[ \t\r]*$")


class SplitFailure(Exception):
    """A condition that makes this mailbox's output untrustworthy."""


def human(n: float) -> str:
    for unit in ("B", "KiB", "MiB"):
        if abs(n) < 1024:
            return f"{n:.0f} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GiB"


def parse_size(text: str) -> int:
    m = re.fullmatch(r"\s*([\d.]+)\s*([kmgt]?)i?b?\s*", text, re.I)
    if not m:
        raise argparse.ArgumentTypeError(f"cannot read a byte size from {text!r}")
    return int(float(m.group(1)) * 1024 ** " kmgt".index(m.group(2).lower() or " "))


def iter_records(fh):
    """Yield (byte offset, record bytes) for every mbox record, streaming.

    The trap: a `From ` separator can straddle a read boundary, so a scanner that
    matches each chunk independently glues two messages into one record and loses
    one from the count without a word. The separator is matched as b"\\nFrom ",
    and after a refill the search resumes len(SEP)-1 bytes back, exactly the
    longest proper prefix of it that could sit unmatched at the tail. Splitting on
    an unindented `^From ` is safe because these files are mboxrd and nothing has
    been unescaped: a body line that began `From ` is stored as `>From ` (see
    check_mbox.py:split_records).
    """
    # Read at least the envelope prefix, so a short first read cannot fool this.
    buf = bytearray(fh.read(max(CHUNK, 5)))
    if not buf:
        raise SplitFailure("file is empty")
    if not buf.startswith(b"From "):
        raise SplitFailure("file does not begin with a 'From ' envelope line")
    offset, at = 0, 0
    while True:
        i = buf.find(SEP, at)
        if i != -1:
            rec = bytes(buf[:i + 1])
            yield offset, rec
            offset += len(rec)
            del buf[:i + 1]
            at = 0
            continue
        chunk = fh.read(CHUNK)
        if not chunk:
            yield offset, bytes(buf)
            return
        at = max(0, len(buf) - (len(SEP) - 1))
        buf += chunk


def record_year(rec: bytes, offset: int) -> int:
    nl = rec.find(b"\n")
    m = ENVELOPE_YEAR.match(rec[:nl] if nl != -1 else rec)
    year = int(m.group(1)) if m else 0
    if not 1970 <= year <= MAX_YEAR:
        raise SplitFailure(f"record at byte {offset} has no plausible year in its "
                           f"envelope line: {rec[:90]!r}")
    return year


def sha256_stream(paths: list[Path]) -> str:
    """SHA-256 over the concatenation of paths, without materialising it."""
    h = hashlib.sha256()
    for p in paths:
        with p.open("rb") as fh:
            for block in iter(lambda: fh.read(CHUNK), b""):
                h.update(block)
    return h.hexdigest()


def index_year_counts(db, mailbox: str) -> dict[int, int]:
    """index.sqlite's own UTC-year histogram; year 0 means an undated row."""
    return {int(y or 0): c for y, c in db.execute(
        "SELECT strftime('%Y', date_epoch, 'unixepoch'), COUNT(*) FROM messages "
        "WHERE mailbox=? GROUP BY 1", (mailbox,))}


def split_one(src: Path, out_dir: Path, max_bytes: int,
              expected: dict[int, int] | None) -> dict:
    size = src.stat().st_size
    stem = src.name[:-5] if src.name.endswith(".mbox") else src.name
    res: dict = {"source": src.name, "source_bytes": size, "records": 0,
                 "years": [], "over_threshold": [], "failures": []}
    years: dict[int, dict] = {}
    fh_out, cur, n, nbytes, last = None, None, 0, 0, 0
    h_rec, t0 = hashlib.sha256(), time.time()
    try:
        with src.open("rb") as fh:
            for offset, rec in iter_records(fh):
                y = record_year(rec, offset)
                if y != cur:
                    # cur is also the highest year seen, runs so far being ascending,
                    # so one test catches a descent and a year resuming after it ends.
                    if cur is not None and y <= cur:
                        raise SplitFailure(
                            f"record at byte {offset} is year {y} but the run in "
                            f"progress is {cur}; runs must be contiguous and "
                            "ascending or the concatenation stops reproducing the "
                            "source, see the docstring on timezones")
                    if fh_out:
                        fh_out.close()
                    dest = out_dir / f"{stem}-{y}.mbox"
                    fh_out = dest.open("wb", buffering=1 << 20)
                    years[y] = {"year": y, "file": dest.name, "records": 0, "bytes": 0}
                    cur = y
                fh_out.write(rec)
                h_rec.update(rec)
                years[y]["records"] += 1
                years[y]["bytes"] += len(rec)
                n, nbytes = n + 1, nbytes + len(rec)
                if nbytes - last >= 2 * GIB:
                    last = nbytes
                    print(f"    ... {human(nbytes)} of {human(size)}, {n:,} records,"
                          f" in year {y}", flush=True)
    except SplitFailure as e:
        res["failures"].append(str(e))
    finally:
        if fh_out:
            fh_out.close()
    res.update(records=n, bytes_written=nbytes, elapsed_s=round(time.time() - t0, 1))
    if res["failures"]:
        for y in years:
            (out_dir / years[y]["file"]).unlink(missing_ok=True)
        res["failures"].append("this mailbox's partial per-year files were removed "
                               "so a half-written mbox cannot be imported by mistake")
        return res
    res["years"] = [years[y] for y in sorted(years)]
    if nbytes != size:
        res["failures"].append(f"wrote {nbytes} bytes but the source holds {size}; "
                               "record scanning lost or duplicated bytes")
    shas = {"source": sha256_stream([src]), "records": h_rec.hexdigest(),
            "concat": sha256_stream([out_dir / y["file"] for y in res["years"]])}
    res.update(sha256=shas, sha256_match=len(set(shas.values())) == 1)
    if not res["sha256_match"]:
        res["failures"].append("the ascending concatenation is not byte-identical "
                               f"to the source: {shas}")
    got = {y: years[y]["records"] for y in years}
    res["index_expected"] = expected
    if expected is None:
        res["failures"].append("no mailbox in index.sqlite maps to this filename, "
                               "so per-year counts cannot be cross-checked")
    elif got != expected:
        diff = {y: [got.get(y, 0), expected.get(y, 0)]
                for y in sorted(set(got) | set(expected))
                if got.get(y, 0) != expected.get(y, 0)}
        res["failures"].append(f"per-year counts disagree with index.sqlite UTC "
                               f"years {diff} as [got, expected], year 0 being an "
                               "undated row; a New Year wall-clock against UTC "
                               "straddle is the likely cause")
    res["over_threshold"] = [y["file"] for y in res["years"] if y["bytes"] > max_bytes]
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path,
                    help="the export dir containing mbox/ and index.sqlite")
    ap.add_argument("--max-bytes", type=parse_size, default=DEFAULT_MAX,
                    help="split mboxes larger than this, and flag any output year "
                         f"still above it (default {human(DEFAULT_MAX)})")
    ap.add_argument("--all", action="store_true",
                    help="split every mbox regardless of size")
    ap.add_argument("--mailbox", action="append", default=[], metavar="NAME",
                    help="split only this mbox, by filename or stem; repeatable")
    args = ap.parse_args()

    out: Path = args.out
    mbox_dir, db_path = out / "mbox", out / "index.sqlite"
    if not mbox_dir.is_dir() or not db_path.exists():
        print(f"need both {mbox_dir} and {db_path}; run export_emlx.py then "
              "make_mbox.py first", file=sys.stderr)
        return 2
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    # Forward map only: reversing that '/' -> '.' flattening would be ambiguous,
    # real mailbox names here already containing dots.
    by_file = {f"{mb.replace('/', '.') or 'Unfiled'}.mbox": mb for (mb,) in
               db.execute("SELECT DISTINCT mailbox FROM messages")}

    everything = sorted(mbox_dir.glob("*.mbox"))
    if args.mailbox:
        want = {m if m.endswith(".mbox") else f"{m}.mbox" for m in args.mailbox}
        chosen = [p for p in everything if p.name in want]
        if unknown := want - {p.name for p in chosen}:
            print(f"no such mbox in {mbox_dir}: {sorted(unknown)}", file=sys.stderr)
            return 2
    else:
        chosen = [p for p in everything
                  if args.all or p.stat().st_size > args.max_bytes]
    if not chosen:
        print(f"nothing in {mbox_dir} is over {human(args.max_bytes)} (--all forces)")
        return 0

    out_dir = out / "mbox-by-year"
    out_dir.mkdir(parents=True, exist_ok=True)
    results, failures = [], 0
    for src in chosen:
        mailbox = by_file.get(src.name)
        print(f"\n{src.name}  {human(src.stat().st_size)}", flush=True)
        r = split_one(src, out_dir, args.max_bytes,
                      index_year_counts(db, mailbox) if mailbox else None)
        r["mailbox"] = mailbox
        results.append(r)
        failures += len(r["failures"])
        print(f"  {'year':>6} {'records':>9} {'bytes':>12}  file")
        for y in r["years"]:
            over = "  OVER THRESHOLD" if y["file"] in r["over_threshold"] else ""
            print(f"  {y['year']:>6} {y['records']:>9,} {human(y['bytes']):>12}  "
                  f"{y['file']}{over}")
        print(f"  {r['records']:,} records in {r['elapsed_s']}s; source, records and "
              f"concatenation sha256 all equal: {r.get('sha256_match')}")
        for f in r["failures"]:
            print(f"  FAIL {f}")
        for name in r["over_threshold"]:
            print(f"  WARN {name} is still over {human(args.max_bytes)}; split that "
                  "one file by month on the same record boundaries, or import it "
                  "alone and check its count before trusting it")

    db.close()
    # mbox-by-year/ accumulates across runs, so the report has to as well. An
    # earlier version overwrote it, and splitting one more mailbox left a report
    # describing that mailbox alone while the directory held four: exactly the
    # looks-complete-but-isn't reading this skill exists to prevent. Entries whose
    # files are no longer on disk get pruned for the same reason, which also drops
    # a failed run's entry, its partial outputs having been deleted.
    report_path = out_dir / "split-report.json"
    merged: dict[str, dict] = {}
    if report_path.exists():
        try:
            for m in json.loads(report_path.read_text())["mailboxes"]:
                if m["years"] and all((out_dir / y["file"]).exists() for y in m["years"]):
                    merged[m["source"]] = m
        except (ValueError, KeyError, TypeError):
            print(f"  WARN {report_path.name} was unreadable, so it describes only "
                  "this run; re-split the other mailboxes to restore it")
    merged.update({r["source"]: r for r in results})
    described = [merged[k] for k in sorted(merged)]
    described_failures = sum(len(m["failures"]) for m in described)
    report_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "export_dir": str(out.resolve()), "output_dir": str(out_dir.resolve()),
        "max_bytes": args.max_bytes, "mailboxes": described,
        "failures": described_failures, "ok": described_failures == 0}, indent=2) + "\n")
    print(f"\nreport: {out_dir / 'split-report.json'}\nimport from: {out_dir}")
    print("\nPASS" if failures == 0 else f"\nFAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
