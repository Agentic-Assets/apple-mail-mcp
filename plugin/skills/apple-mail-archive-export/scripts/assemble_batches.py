#!/usr/bin/env python3
"""Assemble completed Mail plugin export batches into a verified local manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import sys
from pathlib import Path, PurePosixPath

from index_manifest import archive_json, calendar_fields, check, folder_memberships, local_mail_id, verified_file

STAGING_FILES = {"plan.ids.partial", "message.partial", "message.partial.plist"}


def batch_directory(root: Path, name: object) -> Path:
    check(isinstance(name, str) and not any(char in name for char in ("\\", "\0")), "invalid batch path")
    relative = PurePosixPath(name)
    check(
        relative.as_posix() == name
        and not relative.is_absolute()
        and ".." not in relative.parts
        and re.fullmatch(r"batch-[0-9a-f]{32}", relative.name) is not None,
        "invalid batch path",
    )
    directory = root
    for part in relative.parts:
        directory /= part
        check(not directory.is_symlink() and directory.is_dir(), "unsafe or missing batch directory")
    return directory


def read_batch(directory: Path, mailbox: str, memberships: dict[int, set[str]]) -> dict[int, Path]:
    receipt = directory / "plan.ids"
    check(receipt.is_file() and not receipt.is_symlink(), "missing or unsafe plan.ids")
    ids = [local_mail_id(line) for line in receipt.read_text(encoding="utf-8").splitlines()]
    planned = set(ids)
    check(bool(ids) and len(ids) == len(planned), "empty or repeated IDs in plan.ids")
    check(
        all(mailbox in memberships.get(identifier, ()) for identifier in planned), "batch IDs outside snapshot mailbox"
    )
    records: dict[int, Path] = {}
    companions: set[Path] = set()
    for entry in directory.iterdir():
        check(entry.is_file() and not entry.is_symlink(), "unexpected file type in batch")
        if entry.name == "plan.ids" or entry.name in STAGING_FILES:
            continue
        prefix, separator, _ = entry.name.partition("_")
        check(bool(separator), "missing Mail ID prefix on batch file")
        identifier = local_mail_id(prefix)
        check(identifier in planned, "batch file ID not in plan.ids")
        if entry.name.endswith(".ics.plist"):
            companions.add(entry)
            continue
        check(entry.suffix in {".eml", ".ics"}, "unexpected batch file format")
        check(identifier not in records, "duplicate completed ID in batch")
        records[identifier] = entry
    check(
        companions <= {path.with_suffix(".ics.plist") for path in records.values() if path.suffix == ".ics"},
        "orphan calendar companion in batch",
    )
    return records


def copy_artifact(source: Path, target: Path) -> tuple[int, str]:
    checksum = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, target.open("xb") as writer:
        for chunk in iter(lambda: reader.read(1 << 20), b""):
            writer.write(chunk)
            checksum.update(chunk)
            size += len(chunk)
    digest = checksum.hexdigest()
    verified_file(target, size, digest, stream_large=True)
    return size, digest


def write_archive(
    archive: Path, plan: dict, memberships: dict[int, set[str]], completed: dict[int, tuple[str, Path]]
) -> None:
    archive.mkdir(mode=0o700)
    try:
        (archive / "messages").mkdir()
        (archive / "calendar-metadata").mkdir()
        records = []
        for identifier, (mailbox, source) in sorted(completed.items()):
            relative = f"messages/{identifier}{source.suffix}"
            target = archive / relative
            size, digest = copy_artifact(source, target)
            row = {
                "mail_id": str(identifier),
                "folders": [mailbox, *sorted(memberships[identifier] - {mailbox})],
                "format": source.suffix[1:],
                "file": relative,
                "source": "plugin-batch",
                "bytes": size,
                "sha256": digest,
            }
            if source.suffix == ".ics":
                payload = target.read_bytes()
                calendar_fields(payload)
                companion = source.with_suffix(".ics.plist")
                if companion.exists():
                    relative = f"calendar-metadata/{identifier}.plist"
                    trailer_target = archive / relative
                    trailer_size, trailer_digest = copy_artifact(companion, trailer_target)
                    trailer = trailer_target.read_bytes()
                    check(isinstance(plistlib.loads(trailer.strip()), dict), "invalid calendar metadata")
                    row.update(
                        calendar_metadata=relative,
                        calendar_metadata_bytes=trailer_size,
                        calendar_metadata_sha256=trailer_digest,
                        installed_raw_sha256=hashlib.sha256(payload + trailer).hexdigest(),
                    )
            records.append(row)
        result = {"snapshot_folders": plan["snapshot_folders"], "records": records}
        with (archive / "manifest.json").open("x", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except BaseException:
        shutil.rmtree(archive)
        raise


def assemble(export_root: Path, plan_path: Path, output: Path) -> int:
    export_root = export_root.resolve(strict=True)
    check(bool(output.name) and output.name != "..", "invalid archive destination")
    archive = output.parent.resolve(strict=True) / output.name
    plan = archive_json(plan_path.read_bytes())
    memberships = folder_memberships(plan.get("snapshot_folders"))
    batches = plan.get("batches")
    check(isinstance(batches, list) and bool(batches), "expected one or more batches")
    completed: dict[int, tuple[str, Path]] = {}
    directories: set[Path] = set()
    for batch in batches:
        check(isinstance(batch, dict) and isinstance(batch.get("mailbox"), str), "invalid batch definition")
        directory = batch_directory(export_root, batch.get("path"))
        check(
            directory not in directories and not archive.is_relative_to(directory),
            "duplicate batch or overlapping archive",
        )
        directories.add(directory)
        found = read_batch(directory, batch["mailbox"], memberships)
        check(not completed.keys() & found.keys(), "duplicate completed ID across batches")
        completed.update((identifier, (batch["mailbox"], path)) for identifier, path in found.items())
    check(completed.keys() == memberships.keys(), "batch files do not cover the independent snapshot inventory")
    write_archive(archive, plan, memberships, completed)
    return len(completed)


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--export-root", required=True, type=Path, help="directory containing the opened export batches")
    cli.add_argument("--plan", required=True, type=Path, help="local JSON with snapshot_folders and batches")
    cli.add_argument("--archive", required=True, type=Path, help="new archive directory")
    args = cli.parse_args()
    try:
        count = assemble(args.export_root.expanduser(), args.plan.expanduser(), args.archive.expanduser())
    except (OSError, ValueError) as error:
        print(f"archive assembly: {error}", file=sys.stderr)
        return 1
    print(f"Assembled {count:,} verified records in a new archive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
