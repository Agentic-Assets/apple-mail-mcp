"""Tests for the pure JSON perf-result comparison helper."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.compare_perf_results import compare_payloads, load_payload, main


def _payload(*cases: dict, ok: bool = True) -> dict:
    return {
        "ok": ok,
        "account": "Work",
        "profile": "production",
        "cases": list(cases),
    }


def _case(name: str, duration_ms: float, *, passed: bool = True) -> dict:
    return {
        "name": name,
        "category": "perf",
        "duration_ms": duration_ms,
        "threshold_ms": 1500,
        "pass": passed,
        "sample": {},
    }


class ComparePerfPayloadTests(unittest.TestCase):
    def test_compare_payloads_reports_delta_ms_and_pct(self) -> None:
        result = compare_payloads(
            _payload(_case("metadata", 50.0), _case("inbox", 100.0)),
            _payload(_case("inbox", 120.0), _case("metadata", 40.0)),
            max_regression_pct=25.0,
        )

        self.assertTrue(result["ok"])
        inbox = next(case for case in result["cases"] if case["name"] == "inbox")
        metadata = next(case for case in result["cases"] if case["name"] == "metadata")
        self.assertEqual(inbox["delta_ms"], 20.0)
        self.assertEqual(inbox["delta_pct"], 20.0)
        self.assertEqual(metadata["delta_ms"], -10.0)
        self.assertEqual(metadata["delta_pct"], -20.0)

    def test_compare_payloads_fails_when_current_payload_not_ok(self) -> None:
        result = compare_payloads(
            _payload(_case("inbox", 100.0)),
            _payload(_case("inbox", 90.0), ok=False),
        )

        self.assertFalse(result["ok"])
        self.assertIn("current payload ok=false", result["failures"])

    def test_compare_payloads_fails_missing_baseline_case(self) -> None:
        result = compare_payloads(
            _payload(_case("metadata", 100.0)),
            _payload(_case("metadata", 90.0), _case("inbox", 120.0)),
        )

        self.assertFalse(result["ok"])
        self.assertIn("current case missing from baseline: inbox", result["failures"])

    def test_compare_payloads_fails_missing_current_case(self) -> None:
        result = compare_payloads(
            _payload(_case("metadata", 100.0), _case("inbox", 100.0)),
            _payload(_case("metadata", 90.0)),
        )

        self.assertFalse(result["ok"])
        self.assertIn("baseline case missing from current: inbox", result["failures"])

    def test_compare_payloads_fails_when_current_case_failed(self) -> None:
        result = compare_payloads(
            _payload(_case("inbox", 100.0)),
            _payload(_case("inbox", 200.0, passed=False)),
        )

        self.assertFalse(result["ok"])
        self.assertIn("current case failed threshold: inbox", result["failures"])

    def test_compare_payloads_fails_when_current_duration_is_null(self) -> None:
        current_case = _case("inbox", 200.0)
        current_case["duration_ms"] = None

        result = compare_payloads(
            _payload(_case("inbox", 100.0)),
            _payload(current_case),
        )

        self.assertFalse(result["ok"])
        self.assertIn("current case missing duration: inbox", result["failures"])

    def test_compare_payloads_raises_for_non_numeric_duration(self) -> None:
        current_case = _case("inbox", 200.0)
        current_case["duration_ms"] = "fast"

        with self.assertRaisesRegex(ValueError, "current case inbox duration_ms"):
            compare_payloads(
                _payload(_case("inbox", 100.0)),
                _payload(current_case),
            )

    def test_compare_payloads_fails_positive_current_when_baseline_zero(self) -> None:
        result = compare_payloads(
            _payload(_case("inbox", 0.0)),
            _payload(_case("inbox", 1.0)),
        )

        self.assertFalse(result["ok"])
        self.assertIn("case inbox regressed from zero baseline to 1.0ms", result["failures"])

    def test_compare_payloads_fails_when_regression_exceeds_budget(self) -> None:
        result = compare_payloads(
            _payload(_case("inbox", 100.0)),
            _payload(_case("inbox", 130.0)),
            max_regression_pct=10.0,
        )

        self.assertFalse(result["ok"])
        self.assertIn("case inbox regressed 30.0% > 10.0%", result["failures"])


class ComparePerfCliTests(unittest.TestCase):
    def test_load_payload_reads_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payload.json"
            path.write_text(json.dumps(_payload(_case("inbox", 100.0))), encoding="utf-8")

            self.assertEqual(load_payload(path)["cases"][0]["name"], "inbox")

    def test_main_exits_zero_for_within_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.json"
            current = Path(tmp) / "current.json"
            baseline.write_text(json.dumps(_payload(_case("inbox", 100.0))), encoding="utf-8")
            current.write_text(json.dumps(_payload(_case("inbox", 105.0))), encoding="utf-8")

            code = main([str(baseline), str(current), "--max-regression-pct", "10"])

        self.assertEqual(code, 0)

    def test_main_exits_one_for_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.json"
            current = Path(tmp) / "current.json"
            baseline.write_text(json.dumps(_payload(_case("inbox", 100.0))), encoding="utf-8")
            current.write_text(json.dumps(_payload(_case("inbox", 150.0))), encoding="utf-8")

            code = main([str(baseline), str(current), "--max-regression-pct", "10"])

        self.assertEqual(code, 1)

    def test_main_exits_one_for_invalid_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.json"
            current = Path(tmp) / "current.json"
            baseline.write_text(json.dumps(_payload(_case("inbox", 100.0))), encoding="utf-8")
            current.write_text(json.dumps({"ok": True, "cases": "not-a-list"}), encoding="utf-8")

            code = main([str(baseline), str(current)])

        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
