"""Tests for the schema-only Envelope Index inspection helper."""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import inspect_envelope_index_schema as inspector


def _create_dummy_index(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE messages (
                ROWID INTEGER PRIMARY KEY,
                message_id TEXT,
                subject TEXT,
                sender TEXT,
                date_received REAL
            )
            """
        )
        connection.execute("CREATE INDEX messages_message_id_index ON messages(message_id)")
        connection.execute("CREATE INDEX messages_sender_date_index ON messages(sender, date_received)")
        connection.execute(
            "INSERT INTO messages(message_id, subject, sender, date_received) VALUES (?, ?, ?, ?)",
            ("row_value_message_id", "row_value_subject", "row_value_sender", 1.0),
        )
        connection.commit()
    finally:
        connection.close()


class EnvelopeIndexSchemaInspectionTests(unittest.TestCase):
    def test_main_requires_explicit_confirmation_before_opening_sqlite(self) -> None:
        with patch.object(inspector, "_connect_read_only") as mock_connect:
            code = inspector.main([])

        self.assertEqual(code, 2)
        mock_connect.assert_not_called()

    def test_inspect_schema_reports_schema_without_rows_or_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Envelope Index"
            _create_dummy_index(path)

            payload = inspector.inspect_schema(path)

        encoded = json.dumps(payload, sort_keys=True)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["path"], "(redacted)")
        self.assertEqual(payload["object_count"], 1)
        self.assertTrue(payload["privacy"]["schema_only"])
        self.assertFalse(payload["privacy"]["reads_message_rows"])
        self.assertEqual(payload["objects"][0]["name"], "messages")
        self.assertIn("message_id", [column["name"] for column in payload["objects"][0]["columns"]])
        self.assertIn("messages_message_id_index", [index["name"] for index in payload["objects"][0]["indexes"]])
        self.assertNotIn(str(path), encoded)
        self.assertNotIn("row_value_subject", encoded)
        self.assertNotIn("row_value_sender", encoded)
        self.assertNotIn("row_value_message_id", encoded)

    def test_main_prints_redacted_json_for_confirmed_schema_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Envelope Index"
            _create_dummy_index(path)

            with patch("builtins.print") as mock_print:
                code = inspector.main(["--path", str(path), "--confirm-read-only-live-mail-index"])

        self.assertEqual(code, 0)
        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn('"ok": true', printed)
        self.assertIn('"path": "(redacted)"', printed)
        self.assertNotIn(str(path), printed)
        self.assertNotIn("row_value_subject", printed)

    def test_default_path_prefers_newest_mail_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            older = home / "Library" / "Mail" / "V9" / "MailData"
            newer = home / "Library" / "Mail" / "V10" / "MailData"
            older.mkdir(parents=True)
            newer.mkdir(parents=True)
            (older / "Envelope Index").write_text("", encoding="utf-8")
            (newer / "Envelope Index").write_text("", encoding="utf-8")

            self.assertEqual(inspector._default_envelope_index_path(home), newer / "Envelope Index")


if __name__ == "__main__":
    unittest.main()
