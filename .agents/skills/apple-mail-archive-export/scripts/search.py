#!/usr/bin/env python3
"""
Search an exported mail archive.

  ./search.py --archive ARCHIVE/10-export --stats
  ./search.py --archive ARCHIVE/10-export gilcrease
  ./search.py --archive ARCHIVE/10-export "tenure AND review"
  ./search.py --archive ARCHIVE/10-export --from meagan --mailbox "Sent Items"
  ./search.py --archive ARCHIVE/10-export --year 2024 --has-attachment tax
  ./search.py --archive ARCHIVE/10-export --open 3

If ARCHIVE_DIR is exported as an environment variable, --archive can be omitted.

Queries hit a SQLite FTS5 index over decoded subject, body, sender, recipients,
and attachment names. That decoding is the point: bodies are base64 or
quoted-printable on disk, so ripgrep over the raw files under-recalls badly.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path


def resolve_archive(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser()
    env = os.environ.get("ARCHIVE_DIR")
    if env:
        return Path(env).expanduser()
    # Convenience: running from inside the skill's own archive layout.
    here = Path.cwd()
    for cand in (here / "10-export", here):
        if (cand / "index.sqlite").exists():
            return cand
    print("no --archive given and ARCHIVE_DIR unset", file=sys.stderr)
    raise SystemExit(2)


def run_query(db: sqlite3.Connection, archive: Path, args) -> None:
    where, params = [], []

    if args.terms:
        where.append("m.id IN (SELECT rowid FROM search WHERE search MATCH ?)")
        params.append(" ".join(args.terms))
    if args.sender:
        where.append("m.from_addr LIKE ?")
        params.append(f"%{args.sender}%")
    if args.mailbox:
        where.append("m.mailbox LIKE ?")
        params.append(f"%{args.mailbox}%")
    if args.year:
        where.append("m.date_sent LIKE ?")
        params.append(f"{args.year}%")
    if args.has_attachment:
        where.append("m.n_attachments > 0")

    sql = ("SELECT m.id, m.date_sent, m.from_addr, m.subject, m.mailbox, "
           "m.n_attachments, m.eml_path FROM messages m")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY m.date_epoch DESC LIMIT ?"
    params.append(args.limit)

    rows = db.execute(sql, params).fetchall()
    if not rows:
        print("no matches")
        return

    for i, (_mid, date, frm, subj, mb, natt, path) in enumerate(rows, 1):
        att = f" [{natt} att]" if natt else ""
        print(f"\n\033[1m{i:>3}.\033[0m {(date or '')[:10]}  "
              f"\033[36m{mb}\033[0m{att}")
        print(f"     from: {frm}")
        print(f"     {textwrap.shorten(subj or '(no subject)', 92)}")
        if args.paths:
            print(f"     {archive / path}")
    print(f"\n{len(rows)} shown. --open N opens one, --paths shows file paths.")

    if args.open:
        idx = args.open - 1
        if 0 <= idx < len(rows):
            target = archive / rows[idx][6]
            subprocess.run(["open", str(target)])
            print(f"opened {target.name}")
        else:
            print(f"--open {args.open} is out of range", file=sys.stderr)


def show_stats(db: sqlite3.Connection) -> None:
    g = lambda s: db.execute(s).fetchone()[0]  # noqa: E731
    lo = g("SELECT min(date_sent) FROM messages WHERE date_epoch IS NOT NULL") or "?"
    hi = g("SELECT max(date_sent) FROM messages WHERE date_epoch IS NOT NULL") or "?"
    print(f"""
  Mail archive
  ------------
  messages          {g('SELECT count(*) FROM messages'):,}
  unique message-id {g("SELECT count(DISTINCT message_id) FROM messages WHERE message_id!=''"):,}
  mailboxes         {g('SELECT count(DISTINCT mailbox) FROM messages'):,}
  with attachments  {g('SELECT count(*) FROM messages WHERE n_attachments>0'):,}
  attachments       {g('SELECT COALESCE(sum(n_attachments),0) FROM messages'):,}
  date range        {lo[:10]} .. {hi[:10]}
  unread            {g('SELECT count(*) FROM messages WHERE read=0'):,}
  flagged           {g('SELECT count(*) FROM messages WHERE flagged=1'):,}
""")
    print("  top folders")
    for mb, n in db.execute("SELECT mailbox, count(*) c FROM messages "
                            "GROUP BY mailbox ORDER BY c DESC LIMIT 10"):
        print(f"    {n:>7,}  {mb}")
    print("\n  top correspondents")
    for a, n in db.execute("SELECT from_addr, count(*) c FROM messages "
                           "WHERE from_addr!='' GROUP BY from_addr "
                           "ORDER BY c DESC LIMIT 10"):
        print(f"    {n:>7,}  {a}")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Search an exported mail archive",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("terms", nargs="*", help="full-text query (FTS5 syntax)")
    p.add_argument("--archive", default=None,
                   help="export dir containing index.sqlite (or set ARCHIVE_DIR)")
    p.add_argument("--from", dest="sender", help="filter by sender substring")
    p.add_argument("--mailbox", help="filter by folder")
    p.add_argument("--year", help="filter by year, e.g. 2024")
    p.add_argument("--has-attachment", action="store_true")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--paths", action="store_true", help="show .eml file paths")
    p.add_argument("--open", type=int, metavar="N", help="open result N")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    archive = resolve_archive(args.archive)
    db_path = archive / "index.sqlite"
    if not db_path.exists():
        print(f"index not found: {db_path}", file=sys.stderr)
        return 2
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    if args.stats:
        show_stats(db)
        return 0
    if not (args.terms or args.sender or args.mailbox or args.year
            or args.has_attachment):
        p.print_help()
        return 1
    run_query(db, archive, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
