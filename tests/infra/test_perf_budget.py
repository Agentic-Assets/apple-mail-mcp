"""Offline p50/p95 budget fixtures for ID-first hot paths."""

import json
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "perf_budget"
BASELINE_PATH = FIXTURE_DIR / "id_first_baseline.json"
CURRENT_PATH = FIXTURE_DIR / "id_first_current.json"
REQUIRED_CASES = {
    "search_emails.discovery_bounded",
    "get_needs_response.discovery_bounded",
    "get_email_thread.message_id_header_first",
    "get_email_by_id.exact",
    "full_inbox_export.metadata_only",
    "get_email_by_ids.120",
    "verify_drafts.120",
    "list_email_attachments.message_ids_120",
    "export_emails.message_ids_120",
}


def _load_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["suite"] == "id_first_hot_paths"
    assert payload["profile"] == "offline_fixture"
    assert payload["source"] == "mocked_unit_fixture"
    assert payload["live_mail"] is False
    assert isinstance(payload["cases"], list)
    return payload


def _cases_by_name(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for case in payload["cases"]:
        name = case["name"]
        assert name not in cases, f"duplicate perf budget case: {name}"
        cases[name] = case
    return cases


def test_id_first_perf_fixtures_cover_required_hot_paths():
    baseline = _cases_by_name(_load_fixture(BASELINE_PATH))
    current = _cases_by_name(_load_fixture(CURRENT_PATH))

    assert baseline.keys() >= REQUIRED_CASES
    assert current.keys() >= REQUIRED_CASES
    assert set(baseline) == set(current)


def test_id_first_perf_fixtures_have_p50_p95_budget_fields():
    for payload in (_load_fixture(BASELINE_PATH), _load_fixture(CURRENT_PATH)):
        for case in payload["cases"]:
            for field in ("tool", "category", "p50_ms", "p95_ms", "p50_budget_ms", "p95_budget_ms"):
                assert field in case, f"{case['name']} missing {field}"
            assert case["p50_ms"] >= 0
            assert case["p95_ms"] >= case["p50_ms"]
            assert case["p50_budget_ms"] > 0
            assert case["p95_budget_ms"] >= case["p50_budget_ms"]


def test_current_id_first_perf_fixture_stays_within_budget():
    current = _cases_by_name(_load_fixture(CURRENT_PATH))

    for name, case in current.items():
        assert case["p50_ms"] <= case["p50_budget_ms"], f"{name} p50 exceeded budget"
        assert case["p95_ms"] <= case["p95_budget_ms"], f"{name} p95 exceeded budget"


def test_current_id_first_perf_fixture_does_not_regress_against_baseline():
    baseline = _cases_by_name(_load_fixture(BASELINE_PATH))
    current = _cases_by_name(_load_fixture(CURRENT_PATH))

    for name, current_case in current.items():
        baseline_case = baseline[name]
        assert current_case["p50_ms"] <= baseline_case["p50_ms"], f"{name} p50 regressed"
        assert current_case["p95_ms"] <= baseline_case["p95_ms"], f"{name} p95 regressed"
