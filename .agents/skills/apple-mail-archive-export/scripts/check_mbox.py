#!/usr/bin/env python3
"""
Prove the mbox files are a faithful, reversible encoding of the .eml archive.

  ./check_mbox.py --out ARCHIVE/10-export

The check is a byte-exact round trip. For every message it strips the fabricated
`From ` envelope line, removes the flag headers the converter injected, reverses
the mboxrd escaping, and compares the result byte-for-byte with the `.eml` file
the mbox was built from. If that matches, everything matches at once: headers,
body, attachments, MIME structure, and the reversibility of the escaping.

Why this exists as its own gate rather than trusting the conversion: mbox is the
one output that has to *mutate* bytes, and a converter can produce files that
pass every count-based check while corrupting headers. That is not theoretical -
an earlier version of make_mbox.py injected the flag headers after the first
physical line instead of the first complete header, which lands inside the folded
`Received:` header Exchange puts on nearly every message. It truncated the
`Received:` value and left `Status:` spanning two lines on 85% of an Exchange
mailbox, and separator counts, attachment byte-comparison and message-ID
multisets all still passed. Only comparing the reconstructed message against its
source caught it.

Exits non-zero if any message fails to round-trip, so it can gate a release.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

# Headers make_mbox.py adds. Anything else appearing before the message's own
# first header means something injected content we cannot account for.
INJECTED = (b"status:", b"x-status:", b"x-keywords:",
            b"x-mozilla-status:", b"x-mozilla-status2:")

SEPARATOR = re.compile(rb"(?m)^From ")
UNESCAPE = re.compile(rb"(?m)^>(>*From )")


def split_records(data: bytes) -> list[bytes]:
    """Split an mbox on its `From ` separator lines.

    Safe because this runs before unescaping: in mboxrd a body line that began
    `From ` is stored as `>From `, so an unindented `^From ` is always a
    separator.
    """
    starts = [m.start() for m in SEPARATOR.finditer(data)]
    return [data[s:e] for s, e in zip(starts, starts[1:] + [len(data)])]


def strip_injected(record: bytes) -> tuple[bytes, list[bytes], str | None]:
    """Drop the envelope line and the injected flag headers.

    Returns (remaining message bytes, injected header lines, error). A folded
    continuation line (leading space or tab) inside the injected block means a
    real header got split, which is the corruption this script exists to catch.
    """
    nl = record.find(b"\n")
    if nl == -1:
        return b"", [], "record has no newline after the envelope line"
    rest = record[nl + 1:]

    injected: list[bytes] = []
    while True:
        end = rest.find(b"\n")
        if end == -1:
            break
        line = rest[:end + 1]
        low = line.lower()
        if low.startswith(INJECTED):
            injected.append(line)
            rest = rest[end + 1:]
            continue
        if injected and line[:1] in (b" ", b"\t"):
            return rest, injected, (
                "a folded continuation line follows the injected flag headers, "
                "so a real header was split: " + line[:70].decode("utf-8", "replace"))
        break
    return rest, injected, None


def check_one(mbox_path: Path, eml_paths: list[Path]) -> dict:
    data = mbox_path.read_bytes()
    records = split_records(data)
    res = {
        "mbox": mbox_path.name,
        "separators": len(records),
        "expected": len(eml_paths),
        "exact": 0,
        "newline_only": 0,
        "escaped_messages": 0,
        "failures": [],
    }
    if len(records) != len(eml_paths):
        res["failures"].append(
            f"separator count {len(records)} != {len(eml_paths)} messages expected")
        return res

    for record, eml in zip(records, eml_paths):
        rest, injected, err = strip_injected(record)
        if err:
            res["failures"].append(f"{eml.name}: {err}")
            continue
        if not injected:
            res["failures"].append(f"{eml.name}: no flag headers were injected")
            continue
        if any(h.count(b"\n") != 1 for h in injected):
            res["failures"].append(f"{eml.name}: a flag header is not a single line")
            continue

        unescaped = UNESCAPE.sub(rb"\1", rest)
        if unescaped != rest:
            res["escaped_messages"] += 1
        original = eml.read_bytes()

        # mbox requires a blank line between messages, and a message not ending
        # in a newline gets one added so that blank line lands correctly. Both
        # are format overhead rather than content, so peel them back before
        # claiming byte-exactness.
        candidates = [unescaped]
        if unescaped.endswith(b"\n"):
            candidates.append(unescaped[:-1])
            if unescaped[:-1].endswith(b"\n"):
                candidates.append(unescaped[:-2])

        if original in candidates:
            res["exact"] += 1
        elif unescaped.rstrip(b"\n") == original.rstrip(b"\n"):
            res["newline_only"] += 1
        else:
            res["failures"].append(
                f"{eml.name}: round trip differs "
                f"({len(unescaped)} bytes reconstructed vs {len(original)} original)")
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path,
                    help="the export dir containing eml/, mbox/ and index.sqlite")
    args = ap.parse_args()

    out: Path = args.out
    db_path = out / "index.sqlite"
    mbox_dir = out / "mbox"
    if not db_path.exists():
        print(f"no index.sqlite in {out}", file=sys.stderr)
        return 2
    if not mbox_dir.is_dir():
        print(f"no mbox/ in {out}; run make_mbox.py first", file=sys.stderr)
        return 2

    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    mailboxes = [r[0] for r in db.execute(
        "SELECT DISTINCT mailbox FROM messages ORDER BY mailbox")]

    print(f"{'mbox file':<46} {'msgs':>7} {'exact':>7} {'nl':>4} {'esc':>4}  result")
    print("-" * 84)

    totals = {"separators": 0, "exact": 0, "newline_only": 0,
              "escaped_messages": 0, "failures": 0}
    missing = []

    for mb in mailboxes:
        # Same ordering make_mbox.py writes with, so records pair 1:1 by position.
        rows = db.execute("SELECT eml_path FROM messages WHERE mailbox=? "
                          "ORDER BY date_epoch", (mb,)).fetchall()
        eml_paths = [out / r[0] for r in rows if (out / r[0]).exists()]
        # An indexed message with no file on disk would otherwise just shrink the
        # list and resurface as a baffling separator-count mismatch. Name it here:
        # the pairing is positional, so a hole in it invalidates every comparison
        # after the hole, not only the missing message.
        absent = len(rows) - len(eml_paths)
        if absent:
            print(f"{mb[:46]:<46} FAIL - {absent} indexed message(s) have no .eml "
                  f"on disk; positional pairing is unsafe")
            totals["failures"] += absent
            continue
        dest = mbox_dir / f"{mb.replace('/', '.') or 'Unfiled'}.mbox"
        if not dest.exists():
            missing.append(dest.name)
            continue

        r = check_one(dest, eml_paths)
        for k in ("separators", "exact", "newline_only", "escaped_messages"):
            totals[k] += r[k]
        totals["failures"] += len(r["failures"])
        verdict = "PASS" if not r["failures"] else f"FAIL ({len(r['failures'])})"
        print(f"{r['mbox'][:46]:<46} {r['separators']:>7,} {r['exact']:>7,} "
              f"{r['newline_only']:>4} {r['escaped_messages']:>4}  {verdict}")
        for f in r["failures"][:3]:
            print(f"    {f}")

    db.close()
    print("-" * 84)
    print(f"messages round-tripped byte-exact : {totals['exact']:,}")
    if totals["newline_only"]:
        print(f"identical but for trailing newlines: {totals['newline_only']:,}")
    print(f"messages needing 'From ' escaping : {totals['escaped_messages']:,} "
          "(reversed successfully)")
    print(f"separators found                  : {totals['separators']:,}")
    if missing:
        print(f"mbox files not built              : {len(missing)} {missing[:5]}")
    print(f"failures                          : {totals['failures']:,}")

    ok = totals["failures"] == 0 and not missing
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
