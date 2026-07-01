"""Module line-budget regression check (delegates to check_module_line_budget)."""

from __future__ import annotations

import sys

from manifest_checks import common
from manifest_checks.common import MODULE_LINE_BUDGET_BASELINE


def _check_module_line_budget(errors: list[str]) -> int:
    """Warn on oversized modules; fail on baseline regression. Returns warn count."""
    if not MODULE_LINE_BUDGET_BASELINE.is_file():
        errors.append(
            "module line budget baseline missing: "
            f"{MODULE_LINE_BUDGET_BASELINE.relative_to(common.ROOT)}"
        )
        return 0

    validators_dir = common.ROOT / "tools" / "validators"
    if str(validators_dir) not in sys.path:
        sys.path.insert(0, str(validators_dir))

    from check_module_line_budget import (  # noqa: PLC0415
        MODULE_LINE_BUDGET,
        REFRESH_BASELINE_CMD,
        check_regression,
        load_baseline,
        scan_oversized_modules,
    )

    baseline = load_baseline(MODULE_LINE_BUDGET_BASELINE)
    for msg in check_regression(baseline):
        errors.append(f"module line budget: {msg} (refresh: {REFRESH_BASELINE_CMD})")

    oversized = scan_oversized_modules(MODULE_LINE_BUDGET)
    if oversized:
        print(
            f"validate_manifests: module line budget warning: "
            f"{len(oversized)} module(s) exceed {MODULE_LINE_BUDGET} LOC",
            file=sys.stderr,
        )
        for rel, count in oversized:
            print(f"  WARN: {rel}: {count} lines", file=sys.stderr)

    return len(oversized)
