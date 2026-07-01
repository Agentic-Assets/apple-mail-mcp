"""Module line-count budget enforcement for apple-mail-mcp.

Guards against unbounded module growth in ``plugin/apple_mail_mcp/`` and
``tools/``. The 600 LOC target aligns with repo agent guidance (see
``tools/CLAUDE.md``, code-simplifier triggers) and the
``python-project-structure`` skill for split guidance.
"""

from __future__ import annotations

import sys
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.validators.check_module_line_budget import (  # noqa: E402
    MODULE_LINE_BUDGET,
    REFRESH_BASELINE_CMD,
    check_regression,
    load_baseline,
    scan_oversized_modules,
)

BASELINE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "module_line_budget" / "baseline.json"
)

REMEDIATION_HINT = (
    "Split oversized modules per tools/CLAUDE.md and the python-project-structure "
    "skill; run code-simplifier after refactors."
)


class ModuleLineBudgetTests(unittest.TestCase):
    """Static scan of production package and dev-tool scripts."""

    def test_oversized_modules_warn(self) -> None:
        oversized = scan_oversized_modules(MODULE_LINE_BUDGET)
        if not oversized:
            return
        lines = [
            f"{rel}: {count} lines (budget {MODULE_LINE_BUDGET})"
            for rel, count in oversized
        ]
        warnings.warn(
            f"{len(oversized)} module(s) exceed {MODULE_LINE_BUDGET} LOC budget:\n"
            + "\n".join(f"  - {line}" for line in lines)
            + f"\n{REMEDIATION_HINT}",
            UserWarning,
            stacklevel=1,
        )

    def test_module_line_count_regression(self) -> None:
        baseline = load_baseline(BASELINE_PATH)
        errors = check_regression(baseline, MODULE_LINE_BUDGET)
        self.assertEqual(
            errors,
            [],
            "Module line-count regression detected:\n  - "
            + "\n  - ".join(errors)
            + f"\n\nRefresh baseline after intentional splits:\n  {REFRESH_BASELINE_CMD}",
        )


if __name__ == "__main__":
    unittest.main()
