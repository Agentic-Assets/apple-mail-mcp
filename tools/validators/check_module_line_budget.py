#!/usr/bin/env python3
"""Module line-count budget scanner for apple-mail-mcp.

Counts physical lines (wc -l semantics) in production package and dev-tool
scripts. The CLI is warn-only (exit 0); CI enforcement lives in
``tests/infra/test_module_line_budget.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MODULE_LINE_BUDGET = 600

ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (
    ROOT / "plugin" / "apple_mail_mcp",
    ROOT / "tools",
)

REFRESH_BASELINE_CMD = (
    "python3 tools/validators/check_module_line_budget.py --write-baseline "
    "tests/fixtures/module_line_budget/baseline.json"
)


def _count_lines(path: Path) -> int:
    """Count physical lines in a file (wc -l semantics)."""
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            files.append(path)
    return files


def _relative_path(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def scan_oversized_modules(threshold: int = MODULE_LINE_BUDGET) -> list[tuple[str, int]]:
    """Return ``(relative_path, line_count)`` for modules over *threshold*, desc by count."""
    oversized: list[tuple[str, int]] = []
    for path in _iter_python_files():
        count = _count_lines(path)
        if count > threshold:
            oversized.append((_relative_path(path), count))
    oversized.sort(key=lambda item: item[1], reverse=True)
    return oversized


def load_baseline(path: Path | str) -> dict[str, int]:
    """Load a baseline JSON fixture; returns ``modules`` path → line count."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    modules = payload.get("modules", {})
    if not isinstance(modules, dict):
        raise ValueError("baseline 'modules' must be a dict")
    return {str(k): int(v) for k, v in modules.items()}


def check_regression(
    baseline: dict[str, int],
    threshold: int = MODULE_LINE_BUDGET,
) -> list[str]:
    """Compare current oversized modules against *baseline*; return error messages."""
    errors: list[str] = []
    current = dict(scan_oversized_modules(threshold))

    for rel, count in current.items():
        if rel not in baseline:
            errors.append(
                f"{rel}: {count} lines (new module over {threshold} LOC, not in baseline)"
            )
        elif count > baseline[rel]:
            errors.append(f"{rel}: {count} lines (grew from baseline {baseline[rel]})")
        elif count < baseline[rel]:
            errors.append(
                f"{rel}: {count} lines (shrunk from baseline {baseline[rel]}; refresh baseline)"
            )

    for rel in baseline:
        if rel not in current:
            path = ROOT / rel
            actual = _count_lines(path) if path.is_file() else 0
            errors.append(
                f"{rel}: {actual} lines (dropped below {threshold} LOC budget; refresh baseline)"
            )

    return errors


def write_baseline(path: Path | str, threshold: int = MODULE_LINE_BUDGET) -> None:
    """Write a baseline JSON snapshot for all modules currently over *threshold*."""
    oversized = scan_oversized_modules(threshold)
    payload = {
        "threshold": threshold,
        "modules": {rel: count for rel, count in oversized},
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def print_report(
    oversized: list[tuple[str, int]],
    threshold: int = MODULE_LINE_BUDGET,
) -> None:
    """Print a human-readable warn-only report."""
    if not oversized:
        print(f"OK: no modules exceed {threshold} LOC budget.", file=sys.stdout)
        return
    print(
        f"WARNING: {len(oversized)} module(s) exceed {threshold} LOC budget:",
        file=sys.stdout,
    )
    for rel, count in oversized:
        print(f"  {rel}: {count} lines", file=sys.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--threshold",
        type=int,
        default=MODULE_LINE_BUDGET,
        help=f"Line-count budget (default: {MODULE_LINE_BUDGET})",
    )
    parser.add_argument(
        "--write-baseline",
        metavar="PATH",
        help="Write baseline JSON snapshot for modules over threshold",
    )
    args = parser.parse_args(argv)

    if args.write_baseline:
        write_baseline(args.write_baseline, args.threshold)
        print(f"Wrote baseline to {args.write_baseline}", file=sys.stderr)
        return 0

    print_report(scan_oversized_modules(args.threshold), args.threshold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
