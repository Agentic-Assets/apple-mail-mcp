#!/usr/bin/env python3
"""
Build one mboxrd file per mailbox from an exported .eml tree, so the archive can
be imported into a real mail client and browsed with a UI.

  ./make_mbox.py --out ARCHIVE/10-export

mboxrd, not mboxo: it escapes `^>*From ` reversibly, so the original body is
always recoverable. Apple Mail's own export gets this wrong, which is a
documented source of corruption in mail imported from it.

The .eml tree stays the archive of record. mbox mutates body bytes by necessity
(every line beginning "From " gains a ">"), so this output is a convenience copy.
Expect it to roughly double the archive's disk usage.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

FROM_LINE = re.compile(rb"^(>*From )", re.M)


def esc(body: bytes) -> bytes:
    """mboxrd escaping: prefix '>' to any line matching ^>*From """
    return FROM_LINE.sub(rb">\1", body)


def envelope(raw: bytes) -> bytes:
    """Synthesize the mbox 'From ' separator line.

    .emlx stores no envelope sender, so this line is always fabricated. That is
    expected and standard for converted archives; mail clients only use it as a
    record separator.
    """
    head = raw[:65536].decode("utf-8", "surrogateescape")
    head = re.sub(r"\n[ \t]+", " ", head)  # unfold before matching
    m = re.search(r"^From:.*?<([^>]+)>", head, re.I | re.M)
    if not m:
        m = re.search(r"^From:[ \t]*(\S+@\S+)", head, re.I | re.M)
    sender = m.group(1).strip() if m else "MAILER-DAEMON"
    d = re.search(r"^Date:[ \t]*(.+)$", head, re.I | re.M)
    when = None
    if d:
        try:
            when = parsedate_to_datetime(d.group(1).strip())
        except Exception:
            when = None
    stamp = (when.strftime("%a %b %e %H:%M:%S %Y") if when
             else time.strftime("%a %b %e %H:%M:%S %Y", time.gmtime(0)))
    return f"From {sender} {stamp}\n".encode("utf-8", "surrogateescape")


def status_headers(row) -> bytes:
    """Render read/flagged/replied state as headers mail clients actually read.

    Two conventions, because no single one is universal and they do not
    conflict. Status/X-Status is what mutt, Evolution and MailMate read;
    X-Mozilla-Status is what Thunderbird's own mbox parser reads, and
    Thunderbird ignores Status entirely. Emitting both means the state survives
    regardless of where the archive is imported.
    """
    read, flagged, answered, forwarded, draft = row

    st = ("R" if read else "") + "O"
    xst = ("A" if answered else "") + ("F" if flagged else "") + ("T" if draft else "")
    out = f"Status: {st}\n"
    if xst:
        out += f"X-Status: {xst}\n"
    if forwarded:
        out += "X-Keywords: $Forwarded\n"

    # Thunderbird nsMsgMessageFlags: Read 0x1, Replied 0x2, Marked 0x4,
    # Forwarded 0x1000. Status2 carries the high 16 bits and stays 0 here.
    moz = (0x0001 if read else 0) | (0x0002 if answered else 0) \
        | (0x0004 if flagged else 0) | (0x1000 if forwarded else 0)
    out += f"X-Mozilla-Status: {moz:04x}\nX-Mozilla-Status2: 00000000\n"
    return out.encode()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path,
                    help="the export dir containing eml/ and index.sqlite")
    args = ap.parse_args()

    out: Path = args.out
    db_path = out / "index.sqlite"
    if not db_path.exists():
        print(f"no index.sqlite in {out}; run export_emlx.py first", file=sys.stderr)
        return 2

    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    mbox_dir = out / "mbox"
    mbox_dir.mkdir(parents=True, exist_ok=True)

    mailboxes = [r[0] for r in db.execute(
        "SELECT DISTINCT mailbox FROM messages ORDER BY mailbox")]
    total = 0
    t0 = time.time()

    for mb in mailboxes:
        rows = db.execute(
            "SELECT eml_path, read, flagged, answered, forwarded, draft "
            "FROM messages WHERE mailbox=? ORDER BY date_epoch", (mb,)).fetchall()
        if not rows:
            continue
        # Flatten nested mailbox paths into one filename so clients see a flat
        # set of folders rather than failing on missing intermediate dirs.
        dest = mbox_dir / f"{mb.replace('/', '.') or 'Unfiled'}.mbox"
        n = 0
        with dest.open("wb") as fh:
            for eml_path, *flags in rows:
                p = out / eml_path
                if not p.exists():
                    continue
                raw = p.read_bytes()
                fh.write(envelope(raw))
                # Flag headers go directly after the envelope line, ahead of the
                # message's own headers. Header order is not significant, and
                # injecting *between* existing headers is a trap: RFC 5322 folds
                # long values onto continuation lines, Exchange folds the leading
                # Received: on nearly every message, and splitting at the first
                # physical newline lands inside that fold - truncating Received:
                # and leaving Status: multi-line. Prepending sidesteps it.
                fh.write(status_headers(flags))
                fh.write(esc(raw))
                if not raw.endswith(b"\n"):
                    fh.write(b"\n")
                fh.write(b"\n")                  # blank line between messages
                n += 1
        total += n
        print(f"  {mb:<48} {n:>6} -> {dest.name}", flush=True)

    print(f"\n{total:,} messages written to {len(mailboxes)} mbox files "
          f"in {time.time()-t0:.1f}s")
    print(f"import from: {mbox_dir}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
