#!/usr/bin/env python3
"""Build a local search index from a verified EML/ICS archive manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import sqlite3
import stat
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from export_emlx import (
    FLAG_BITS,
    INSERT_MESSAGE,
    INSERT_SEARCH,
    OVERSIZE_BYTES,
    SCHEMA,
    decode_header_value,
    get_header,
    index_text,
    parse_date,
    parse_sender,
    raw_headers,
    unfold,
)

MANIFEST_SCHEMA = """
CREATE TABLE archive_records (
  message_rowid INTEGER PRIMARY KEY REFERENCES messages(id),
  source_format TEXT NOT NULL,
  source TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  calendar_metadata TEXT
);
CREATE TABLE message_mailboxes (
  message_rowid INTEGER NOT NULL REFERENCES messages(id),
  mailbox TEXT NOT NULL,
  PRIMARY KEY (message_rowid, mailbox)
) WITHOUT ROWID;
CREATE INDEX message_mailboxes_by_mailbox ON message_mailboxes(mailbox);
"""
CALENDAR_FIELDS = ("calendar_metadata", "calendar_metadata_bytes", "calendar_metadata_sha256", "installed_raw_sha256")
SHA256 = re.compile(r"[0-9a-f]{64}")


def check(condition: bool, issue: str) -> None:
    if not condition:
        raise ValueError(issue)


def archive_json(contents: bytes) -> dict:
    def unique_keys(fields):
        values = dict(fields)
        check(len(values) == len(fields), "duplicate manifest field")
        return values

    document = json.loads(contents, object_pairs_hook=unique_keys)
    check(isinstance(document, dict), "expected a JSON object")
    return document


def folder_memberships(folders: object) -> dict[int, set[str]]:
    check(isinstance(folders, dict), "expected snapshot_folders")
    memberships: dict[int, set[str]] = defaultdict(set)
    for folder, ids in folders.items():
        check(bool(folder) and "\0" not in folder and isinstance(ids, list), "invalid snapshot folder")
        for value in ids:
            identifier = local_mail_id(value)
            check(folder not in memberships[identifier], "duplicate ID in snapshot folder")
            memberships[identifier].add(folder)
    return memberships


def load_manifest(contents: bytes) -> tuple[list[dict], dict[int, set[str]]]:
    manifest = archive_json(contents)
    records = manifest.get("records")
    check(isinstance(records, list), "expected records")
    return records, folder_memberships(manifest.get("snapshot_folders"))


def local_mail_id(value: object) -> int:
    check(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,18}", value) is not None, "invalid local Mail ID")
    identifier = int(value)
    check(identifier < 2**63, "local Mail ID exceeds SQLite integer range")
    return identifier


def artifact_path(archive: Path, value: object, directory: str, suffix: str) -> Path:
    check(
        isinstance(value, str) and bool(value) and not any(ch in value for ch in ("\0", "\\")), "invalid artifact path"
    )
    relative = PurePosixPath(value)
    check(
        relative.as_posix() == value
        and len(relative.parts) >= 2
        and relative.parts[0] == directory
        and ".." not in relative.parts
        and relative.suffix == suffix,
        "invalid artifact path",
    )
    path = archive
    for part in relative.parts:
        path /= part
        check(not path.is_symlink(), "symlink in artifact path")
    check(stat.S_ISREG(path.lstat().st_mode), "artifact path is not a regular file")
    return path


def verified_file(path: Path, size: object, digest: object, *, stream_large: bool = False) -> bytes | None:
    check(type(size) is int and size > 0, "invalid file byte count")
    check(isinstance(digest, str) and SHA256.fullmatch(digest) is not None, "invalid file SHA-256")
    with path.open("rb") as source:
        check(os.fstat(source.fileno()).st_size == size, "file byte count mismatch")
        if stream_large and size >= OVERSIZE_BYTES:
            checksum = hashlib.sha256()
            for chunk in iter(lambda: source.read(1 << 20), b""):
                checksum.update(chunk)
            payload = None
        else:
            payload = source.read()
            check(len(payload) == size, "file byte count mismatch")
            checksum = hashlib.sha256(payload)
    check(checksum.hexdigest() == digest, "file SHA-256 mismatch")
    return payload


def text_fields(
    path: Path, payload: bytes | None
) -> tuple[str, int | None, str, str, str, str, str, list[str], str, str]:
    if payload is None:
        with path.open("rb") as source:
            header = source.read(65_536)
    else:
        header = payload
    unfolded = unfold(raw_headers(header))
    when, date = parse_date(get_header(unfolded, "Date"), {})
    sender_name, sender_address = parse_sender(decode_header_value(get_header(unfolded, "From")))
    body, attachments = index_text(None, payload, path)
    return (
        get_header(unfolded, "Message-ID"),
        when,
        date,
        sender_name,
        sender_address,
        decode_header_value(get_header(unfolded, "To")),
        decode_header_value(get_header(unfolded, "Cc")),
        attachments,
        decode_header_value(get_header(unfolded, "Subject")),
        body,
    )


def calendar_fields(payload: bytes) -> tuple[str, int | None, str, str]:
    lines = re.sub(r"(?:\r\n|\n)[ \t]", "", payload.decode("utf-8-sig")).splitlines()
    check(
        len(lines) >= 2 and lines[0] == "BEGIN:VCALENDAR" and lines[-1] == "END:VCALENDAR", "invalid calendar framing"
    )
    fields: dict[str, list[str]] = defaultdict(list)
    for line in lines[1:-1]:
        key, separator, value = line.partition(":")
        field = key.partition(";")[0].upper()
        if separator and field in {"SUMMARY", "DESCRIPTION", "LOCATION", "DTSTART"}:
            fields[field].append(value)
    starts = fields["DTSTART"]
    date = ""
    epoch = None
    if starts:
        try:
            date = datetime.strptime(starts[0][:8], "%Y%m%d").date().isoformat()
            if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", starts[0]):
                aware = datetime.strptime(starts[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                date, epoch = aware.isoformat(), int(aware.timestamp())
        except ValueError:
            pass
    return (
        next(iter(fields["SUMMARY"]), "(calendar record)"),
        epoch,
        date,
        " ".join(fields["DESCRIPTION"] + fields["LOCATION"])[:200_000],
    )


def calendar_metadata(archive: Path, record: dict, payload: bytes) -> tuple[str | None, int | None]:
    present = set(record).intersection(CALENDAR_FIELDS)
    if not present:
        return None, None
    check(present == set(CALENDAR_FIELDS), "incomplete calendar metadata contract")
    relative = record["calendar_metadata"]
    path = artifact_path(archive, relative, "calendar-metadata", ".plist")
    data = verified_file(path, record["calendar_metadata_bytes"], record["calendar_metadata_sha256"])
    check(
        hashlib.sha256(payload + data).hexdigest() == record["installed_raw_sha256"], "calendar source SHA-256 mismatch"
    )
    plist = plistlib.loads(data.strip())
    check(isinstance(plist, dict), "calendar metadata is not a plist dictionary")
    return relative, plist.get("flags")


def insert_records(
    db: sqlite3.Connection, archive: Path, records: list[dict], memberships: dict[int, set[str]]
) -> None:
    seen_ids: set[int] = set()
    seen_paths: set[tuple[int, int]] = set()
    for number, row in enumerate(records, 1):
        try:
            check(isinstance(row, dict), "expected a record object")
            identifier = local_mail_id(row.get("mail_id"))
            check(identifier not in seen_ids, "duplicate local Mail ID")
            seen_ids.add(identifier)
            folders = row.get("folders")
            check(
                isinstance(folders, list)
                and bool(folders)
                and all(isinstance(folder, str) for folder in folders)
                and len(folders) == len(set(folders))
                and set(folders) == memberships.get(identifier),
                "record does not match snapshot folder membership",
            )
            kind = row.get("format")
            check(kind in ("eml", "ics"), "unsupported archive record format")
            source = row.get("source")
            check(isinstance(source, str) and bool(source), "missing record source")
            relative = row.get("file")
            path = artifact_path(archive, relative, "messages", f".{kind}")
            info = path.stat()
            identity = (info.st_dev, info.st_ino)
            check(identity not in seen_paths, "duplicate artifact path")
            seen_paths.add(identity)
            payload = verified_file(path, row.get("bytes"), row.get("sha256"), stream_large=kind == "eml")
            metadata_path, flags = None, None
            if kind == "eml":
                check(not set(row).intersection(CALENDAR_FIELDS), "calendar metadata on a mail record")
                message_id, epoch, date, sender, address, to, cc, attachments, subject, body = text_fields(
                    path, payload
                )
            else:
                metadata_path, flags = calendar_metadata(archive, row, payload)
                subject, epoch, date, body = calendar_fields(payload)
                message_id = sender = address = to = cc = ""
                attachments = []
            flags = row.get("flags", flags)
            check(flags is None or (type(flags) is int and 0 <= flags < 2**63), "invalid Mail flags")
            missing = row.get("attachment_placeholders")
            check(
                missing is None or (type(missing) is int and 0 <= missing < 2**63),
                "invalid attachment placeholder count",
            )
            status = [
                None if flags is None else int(bool(flags & FLAG_BITS[key]))
                for key in ("read", "flagged", "answered", "forwarded", "draft")
            ]
            names = " | ".join(attachments)
            cursor = db.execute(
                INSERT_MESSAGE,
                (
                    identifier,
                    folders[0],
                    relative,
                    message_id,
                    date,
                    epoch,
                    sender,
                    address,
                    to,
                    cc,
                    subject,
                    len(attachments),
                    names,
                    row["bytes"],
                    None,
                    None,
                    missing,
                    flags,
                    *status,
                    None,
                ),
            )
            key = cursor.lastrowid
            db.execute(INSERT_SEARCH, (key, subject, body, address, to, names))
            db.execute(
                "INSERT INTO archive_records VALUES (?, ?, ?, ?, ?)", (key, kind, source, row["sha256"], metadata_path)
            )
            db.executemany("INSERT INTO message_mailboxes VALUES (?, ?)", ((key, folder) for folder in folders))
        except (OSError, ValueError, TypeError, plistlib.InvalidFileException) as error:
            raise ValueError(f"record {number}: {error}") from error
    check(seen_ids == memberships.keys(), "snapshot has records missing from manifest")


def build_index(archive: Path) -> int:
    archive = archive.resolve(strict=True)
    manifest_path = archive / "manifest.json"
    check(not manifest_path.is_symlink() and manifest_path.is_file(), "missing or unsafe manifest.json")
    contents = manifest_path.read_bytes()
    records, memberships = load_manifest(contents)
    destination = archive / "index.sqlite"
    check(not destination.is_symlink(), "unsafe index path")
    fd, filename = tempfile.mkstemp(prefix=".archive-index-", suffix=".sqlite", dir=archive)
    os.close(fd)
    temporary = Path(filename)
    try:
        db = sqlite3.connect(temporary)
        try:
            db.executescript(SCHEMA)
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(MANIFEST_SCHEMA)
            with db:
                insert_records(db, archive, records, memberships)
            check(db.execute("PRAGMA integrity_check").fetchone() == ("ok",), "index integrity check failed")
        finally:
            db.close()
        check(manifest_path.read_bytes() == contents, "manifest changed during indexing")
        check(
            not any((archive / f"index.sqlite{suffix}").exists() for suffix in ("-wal", "-shm", "-journal")),
            "existing index has SQLite sidecars",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return len(records)


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--archive", required=True, type=Path, help="archive directory containing manifest.json")
    args = cli.parse_args()
    try:
        count = build_index(args.archive.expanduser())
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"archive index: {error}", file=sys.stderr)
        return 1
    print(f"Indexed {count:,} archive records in index.sqlite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
