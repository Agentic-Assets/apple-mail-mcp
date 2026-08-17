#!/usr/bin/env python3
"""
Inventory every Apple Mail account on this Mac: which UUID directory belongs to
which address, how many messages it holds, how big it is, and whether it is
attachment-detached.

  ./discover_accounts.py            # human table
  ./discover_accounts.py --json     # machine-readable

Run this first, always. The account directories are named by opaque UUID, so the
only way to know which one is the mailbox you care about is to correlate three
sources: Accounts4.sqlite (address + display name), each mailbox's Info.plist
(Exchange vs IMAP markers), and the actual message headers. Guessing from folder
names alone is how you end up exporting the wrong account.

Everything here is read-only. Nothing launches Mail.app.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

MAIL_ROOT = Path.home() / "Library" / "Mail"


def version_dirs() -> list[Path]:
    return sorted(p for p in MAIL_ROOT.glob("V*") if p.is_dir())


def internet_accounts() -> list[tuple[str, str]]:
    """(description, username) from Internet Accounts. Read-only, via a copy."""
    db = Path.home() / "Library" / "Accounts" / "Accounts4.sqlite"
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT ZACCOUNTDESCRIPTION, ZUSERNAME FROM ZACCOUNT"
        ).fetchall()
        con.close()
    except Exception:
        return []
    return [(d or "", u or "") for d, u in rows if (d or u)]


def account_kind(acct_dir: Path) -> str:
    """Exchange / IMAP / local, inferred from any mailbox Info.plist."""
    for info in acct_dir.rglob("Info.plist"):
        try:
            data = plistlib.loads(info.read_bytes())
        except Exception:
            continue
        if "ExchangeFolderId" in data or "ExchangeSyncState" in data:
            return "exchange"
        if "UIDVALIDITY" in data or "IMAPMailboxUnseenCount" in data:
            return "imap"
    return "local"


def sample_addresses(acct_dir: Path, limit: int = 60) -> list[tuple[str, int]]:
    """Most common addresses in From/To headers, to identify the owner.

    Reads only the first 8 KB of each sampled message, and unfolds continuation
    lines first because Exchange wraps long header values.
    """
    counter: Counter = Counter()
    seen = 0
    for root, _dirs, files in os.walk(acct_dir):
        if os.path.basename(root) != "Messages":
            continue
        for fn in files:
            if not fn.endswith(".emlx"):
                continue
            try:
                with open(os.path.join(root, fn), "rb") as fh:
                    head = fh.read(8192).decode("utf-8", "surrogateescape")
            except Exception:
                continue
            head = re.sub(r"\n[ \t]+", " ", head)
            for m in re.finditer(r"[\w.+-]+@[\w.-]+\.\w+", head):
                counter[m.group(0).lower()] += 1
            seen += 1
            if seen >= limit:
                return counter.most_common(6)
    return counter.most_common(6)


def scan(acct_dir: Path) -> dict:
    total = partial = 0
    att_files = 0
    nbytes = 0
    mailboxes = set()
    for root, _dirs, files in os.walk(acct_dir):
        base = os.path.basename(root)
        if base == "Messages":
            for fn in files:
                if fn.endswith(".emlx"):
                    total += 1
                    if fn.endswith(".partial.emlx"):
                        partial += 1
                    try:
                        nbytes += os.stat(os.path.join(root, fn)).st_size
                    except OSError:
                        pass
        elif "Attachments" in Path(root).parts:
            att_files += len(files)
    for p in acct_dir.rglob("*.mbox"):
        mailboxes.add(str(p.relative_to(acct_dir)))
    return {
        "messages": total,
        "partial": partial,
        "full": total - partial,
        "attachment_files": att_files,
        "emlx_bytes": nbytes,
        "mailbox_dirs": len(mailboxes),
    }


def du_h(p: Path) -> str:
    try:
        out = subprocess.run(["du", "-sh", str(p)], capture_output=True, text=True,
                             timeout=600)
        return out.stdout.split("\t")[0].strip() or "?"
    except Exception:
        return "?"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sample", type=int, default=60,
                   help="messages to sample per account for address detection")
    args = ap.parse_args()

    if not MAIL_ROOT.is_dir():
        print(f"no {MAIL_ROOT}", file=sys.stderr)
        return 2

    vdirs = version_dirs()
    if not vdirs:
        print(f"no V* store under {MAIL_ROOT}", file=sys.stderr)
        return 2

    # A read failure here almost always means the terminal lacks Full Disk
    # Access, which is worth saying plainly rather than surfacing as EPERM.
    try:
        next(iter(vdirs[-1].iterdir()))
    except PermissionError:
        print("Cannot read the Mail store. Grant Full Disk Access to this "
              "terminal in System Settings > Privacy & Security > Full Disk "
              "Access, then re-run.", file=sys.stderr)
        return 3

    ia = internet_accounts()
    results = []
    for vdir in vdirs:
        for acct in sorted(p for p in vdir.iterdir()
                           if p.is_dir() and p.name != "MailData"):
            info = scan(acct)
            rec = {
                "store": vdir.name,
                "uuid": acct.name,
                "path": str(acct),
                "kind": account_kind(acct),
                "size_human": du_h(acct),
                **info,
                "top_addresses": sample_addresses(acct, args.sample),
            }
            results.append(rec)

    if args.json:
        print(json.dumps({"accounts": results, "internet_accounts": ia}, indent=2))
        return 0

    print(f"\nApple Mail store: {MAIL_ROOT}  ({', '.join(v.name for v in vdirs)})\n")
    hdr = f"{'UUID':<38}{'kind':<10}{'msgs':>8}{'partial':>9}{'att':>8}{'size':>8}"
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(results, key=lambda x: -x["messages"]):
        print(f"{r['uuid']:<38}{r['kind']:<10}{r['messages']:>8,}"
              f"{r['partial']:>9,}{r['attachment_files']:>8,}{r['size_human']:>8}")
    print("\nLikely owner per account (top addresses in sampled headers):")
    for r in sorted(results, key=lambda x: -x["messages"]):
        if not r["messages"]:
            continue
        tops = ", ".join(f"{a} ({n})" for a, n in r["top_addresses"][:3])
        print(f"  {r['uuid'][:8]}...  {tops}")
    if ia:
        print("\nConfigured Internet Accounts (description | username):")
        for d, u in ia:
            if u:
                print(f"  {d or '(no description)':<32} | {u}")
    print("\nNext: pick the UUID you want, then snapshot it before exporting.")
    print("  ./snapshot_account.sh <UUID> <ARCHIVE_DIR>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
