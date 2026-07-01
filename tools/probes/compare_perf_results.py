#!/usr/bin/env python3
"""Compare two apple-mail perf-test JSON payloads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_payload(path: Path) -> dict[str, Any]:
    """Read a perf-test JSON payload from *path*."""
    with path.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _cases_by_name(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = payload.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError("payload cases must be a list")

    by_name: dict[str, dict[str, Any]] = {}
    for item in cases:
        if not isinstance(item, dict):
            raise ValueError("each perf case must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("each perf case must include a non-empty string name")
        if name in by_name:
            raise ValueError(f"duplicate perf case name: {name}")
        by_name[name] = item
    return by_name


def _duration(case: dict[str, Any], *, label: str) -> float | None:
    value = case.get("duration_ms")
    if value is None:
        return None
    if not isinstance(value, int | float):
        raise ValueError(f"{label} duration_ms must be numeric or null")
    return float(value)


def _delta_pct(baseline_ms: float | None, current_ms: float | None) -> float | None:
    if baseline_ms is None or current_ms is None or baseline_ms <= 0:
        return None
    return round(((current_ms - baseline_ms) / baseline_ms) * 100.0, 1)


def compare_payloads(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    max_regression_pct: float = 10.0,
) -> dict[str, Any]:
    """Compare perf cases by name and return a structured pass/fail report."""
    failures: list[str] = []
    baseline_cases = _cases_by_name(baseline)
    current_cases = _cases_by_name(current)

    if current.get("ok") is False:
        failures.append("current payload ok=false")

    for name in sorted(current_cases.keys() - baseline_cases.keys()):
        failures.append(f"current case missing from baseline: {name}")
    for name in sorted(baseline_cases.keys() - current_cases.keys()):
        failures.append(f"baseline case missing from current: {name}")

    comparisons: list[dict[str, Any]] = []
    for name in sorted(baseline_cases.keys() & current_cases.keys()):
        baseline_case = baseline_cases[name]
        current_case = current_cases[name]
        baseline_ms = _duration(baseline_case, label=f"baseline case {name}")
        current_ms = _duration(current_case, label=f"current case {name}")
        delta_ms = None if baseline_ms is None or current_ms is None else round(current_ms - baseline_ms, 1)
        delta_pct = _delta_pct(baseline_ms, current_ms)

        if current_case.get("pass") is False:
            failures.append(f"current case failed threshold: {name}")
        if current_ms is None:
            failures.append(f"current case missing duration: {name}")
        if baseline_ms == 0 and current_ms is not None and current_ms > 0:
            failures.append(f"case {name} regressed from zero baseline to {current_ms:.1f}ms")
        if delta_pct is not None and delta_pct > max_regression_pct:
            failures.append(f"case {name} regressed {delta_pct:.1f}% > {max_regression_pct:.1f}%")

        comparisons.append(
            {
                "name": name,
                "baseline_ms": baseline_ms,
                "current_ms": current_ms,
                "delta_ms": delta_ms,
                "delta_pct": delta_pct,
                "baseline_pass": baseline_case.get("pass"),
                "current_pass": current_case.get("pass"),
            }
        )

    return {
        "ok": not failures,
        "max_regression_pct": max_regression_pct,
        "failures": failures,
        "cases": comparisons,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two apple-mail perf-test --json payloads by case name.",
    )
    parser.add_argument("baseline", type=Path, help="Baseline perf-test JSON file")
    parser.add_argument("current", type=Path, help="Current perf-test JSON file")
    parser.add_argument(
        "--max-regression-pct",
        type=float,
        default=10.0,
        help="Maximum allowed per-case duration regression percentage",
    )
    parser.add_argument("--json", action="store_true", help="Print the full comparison report as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the perf comparison CLI."""
    args = _build_parser().parse_args(argv)
    try:
        result = compare_payloads(
            load_payload(args.baseline),
            load_payload(args.current),
            max_regression_pct=args.max_regression_pct,
        )
    except (OSError, ValueError) as exc:
        print(f"perf comparison: FAILED\n- {exc}")
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["ok"]:
        print("perf comparison: OK")
    else:
        print("perf comparison: FAILED")
        for failure in result["failures"]:
            print(f"- {failure}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
