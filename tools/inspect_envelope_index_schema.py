#!/usr/bin/env python3
"""Inspect Apple Mail Envelope Index schema without reading message rows.

This helper is intentionally not part of the MCP tool surface. It exists for
the ID-first metadata-index feasibility spike and requires explicit live-read
confirmation before it opens Mail's local SQLite index. The report redacts file
paths and includes only schema metadata: table names, column names/types, index
names/columns, and a stable fingerprint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote


def _json_error(error: str, message: str) -> str:
    return json.dumps({"ok": False, "error": error, "message": message}, indent=2, sort_keys=True)


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _default_envelope_index_path(home: Path | None = None) -> Path | None:
    """Return the newest apparent Mail Envelope Index path, without creating files."""
    root = (Path.home() if home is None else home).expanduser() / "Library" / "Mail"
    if not root.exists():
        return None

    candidates: list[tuple[int, Path]] = []
    for version_dir in root.glob("V*"):
        if not version_dir.is_dir():
            continue
        suffix = version_dir.name[1:]
        if not suffix.isdecimal():
            continue
        path = version_dir / "MailData" / "Envelope Index"
        if path.exists():
            candidates.append((int(suffix), path))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _connect_read_only(path: Path, *, timeout: float = 2.0) -> sqlite3.Connection:
    """Open *path* in SQLite read-only mode with query-only protection."""
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=timeout)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _fetch_table_columns(connection: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    rows = connection.execute(f"PRAGMA table_info({_quote_identifier(table_name)})").fetchall()
    columns: list[dict[str, Any]] = []
    for row in rows:
        columns.append(
            {
                "name": str(row[1]),
                "type": str(row[2] or ""),
                "not_null": bool(row[3]),
                "primary_key_position": int(row[5]),
            }
        )
    return columns


def _fetch_table_indexes(connection: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    rows = connection.execute(f"PRAGMA index_list({_quote_identifier(table_name)})").fetchall()
    indexes: list[dict[str, Any]] = []
    for row in rows:
        index_name = str(row[1])
        column_rows = connection.execute(f"PRAGMA index_info({_quote_identifier(index_name)})").fetchall()
        indexes.append(
            {
                "name": index_name,
                "unique": bool(row[2]),
                "origin": str(row[3]) if len(row) > 3 else "",
                "columns": [str(column_row[2]) for column_row in column_rows if column_row[2] is not None],
            }
        )
    return sorted(indexes, key=lambda item: item["name"])


def inspect_schema(path: Path, *, timeout: float = 2.0) -> dict[str, Any]:
    """Return redacted Envelope Index schema metadata for *path*."""
    resolved = path.expanduser()
    if not resolved.exists():
        raise FileNotFoundError("Envelope Index path does not exist")
    if not resolved.is_file():
        raise ValueError("Envelope Index path must be a file")

    connection = _connect_read_only(resolved, timeout=timeout)
    try:
        rows = connection.execute(
            """
            SELECT name, type
            FROM sqlite_schema
            WHERE type IN ('table', 'view')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()

        objects: list[dict[str, Any]] = []
        for name, object_type in rows:
            object_name = str(name)
            item: dict[str, Any] = {
                "name": object_name,
                "type": str(object_type),
                "columns": _fetch_table_columns(connection, object_name),
            }
            if object_type == "table":
                item["indexes"] = _fetch_table_indexes(connection, object_name)
            objects.append(item)
    finally:
        connection.close()

    fingerprint_source = json.dumps(objects, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "ok": True,
        "live_mail_index": True,
        "privacy": {
            "schema_only": True,
            "reads_message_rows": False,
            "prints_private_content": False,
            "prints_file_path": False,
        },
        "path": "(redacted)",
        "version_hint": _version_hint(resolved),
        "object_count": len(objects),
        "schema_fingerprint_sha256": hashlib.sha256(fingerprint_source).hexdigest(),
        "objects": objects,
    }


def _version_hint(path: Path) -> str | None:
    for parent in path.parents:
        if parent.name.startswith("V") and parent.name[1:].isdecimal():
            return parent.name
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect Apple Mail Envelope Index schema metadata without reading message rows.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        help="Optional Envelope Index path. Defaults to the newest ~/Library/Mail/V*/MailData/Envelope Index.",
    )
    parser.add_argument("--timeout", type=float, default=2.0, help="SQLite connection timeout in seconds")
    parser.add_argument(
        "--confirm-read-only-live-mail-index",
        action="store_true",
        help="Required confirmation. Opens Mail's local index read-only and reads schema metadata only.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the schema-inspection CLI."""
    args = _build_parser().parse_args(argv)
    if not args.confirm_read_only_live_mail_index:
        print(
            _json_error(
                "confirm_read_only_live_mail_index_required",
                "Pass --confirm-read-only-live-mail-index to inspect schema metadata.",
            )
        )
        return 2

    path = args.path or _default_envelope_index_path()
    if path is None:
        print(_json_error("envelope_index_not_found", "No default Envelope Index path was found."))
        return 1

    try:
        payload = inspect_schema(path, timeout=args.timeout)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(_json_error("envelope_index_schema_inspection_failed", str(exc)))
        return 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
