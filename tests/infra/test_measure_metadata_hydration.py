"""Tests for the read-only metadata hydration measurement helper."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.probes import measure_metadata_hydration as measurement


class MetadataHydrationMeasurementTests(unittest.TestCase):
    def test_main_requires_explicit_live_mail_confirmation(self) -> None:
        with patch.object(measurement, "run_applescript") as mock_run:
            code = measurement.main(["--account", "Dummy Account", "--message-ids", "101"])

        self.assertEqual(code, 2)
        mock_run.assert_not_called()

    def test_build_script_uses_exact_ids_and_returns_only_aggregate_counts(self) -> None:
        script = measurement._build_measurement_script(
            account="Dummy Account",
            mailbox="INBOX",
            message_ids=["101", "202"],
            measure_headers=True,
            measure_attachments=True,
            timeout=120,
        )

        self.assertIn("set requestedIds to {101, 202}", script)
        self.assertIn("every message of targetMailbox whose id is requestedId", script)
        self.assertIn("all headers of aMessage as string", script)
        self.assertIn("count of mail attachments of aMessage", script)
        self.assertIn("HEADER_CHARS", script)
        self.assertIn("ATTACHMENTS", script)
        self.assertNotIn("subject of aMessage", script)
        self.assertNotIn("sender of aMessage", script)
        self.assertNotIn("content of aMessage", script)
        self.assertNotIn("name of anAttachment", script)
        self.assertNotIn("headerText &", script)

    def test_measurement_redacts_account_and_message_ids(self) -> None:
        perf_counter_values = iter([0.0, 0.1, 1.0, 1.2, 2.0, 2.3])

        with (
            patch.object(
                measurement,
                "run_applescript",
                return_value="FOUND|||2|||MISSING|||0|||HEADER_CHARS|||80|||ATTACHMENTS|||3",
            ),
            patch.object(measurement.time, "perf_counter", side_effect=lambda: next(perf_counter_values)),
        ):
            payload = measurement.measure_metadata_hydration(
                account="Dummy Account",
                mailbox="INBOX",
                message_ids=["101", "202"],
                repeats=1,
            )

        encoded = json.dumps(payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["account"], "(redacted)")
        self.assertEqual(payload["message_id_count"], 2)
        self.assertEqual([case["name"] for case in payload["cases"]], list(measurement.CASES))
        self.assertEqual(payload["cases"][0]["found_count"], 2)
        self.assertEqual(payload["cases"][0]["header_chars_total"], 80)
        self.assertNotIn("Dummy Account", encoded)
        self.assertNotIn("101", encoded)
        self.assertNotIn("202", encoded)

    def test_rejects_invalid_or_empty_ids_before_applescript(self) -> None:
        with patch.object(measurement, "run_applescript") as mock_run:
            code = measurement.main(
                [
                    "--account",
                    "Dummy Account",
                    "--message-ids",
                    "not-a-number",
                    "--confirm-read-only-live-mail",
                ]
            )

        self.assertEqual(code, 1)
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
