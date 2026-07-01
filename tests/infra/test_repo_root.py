"""Gate repo-root hygiene — no loose reports or scratch artifacts at top level."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.validators.validate_repo_root import (  # noqa: E402
    ALLOWED_ROOT_DIRS,
    ALLOWED_ROOT_FILES,
    validate_repo_root,
)


class TestRepoRoot(unittest.TestCase):
    def test_validate_repo_root_passes(self) -> None:
        errors = validate_repo_root()
        self.assertEqual(errors, [], "\n".join(errors))

    def test_allowed_root_markdown_files_are_exact_navigation_set(self) -> None:
        allowed_md = {name for name in ALLOWED_ROOT_FILES if name.endswith(".md")}
        root_md = {
            p.name
            for p in ROOT.iterdir()
            if p.is_file() and p.suffix == ".md" and not p.name.startswith(".")
        }
        self.assertEqual(root_md, allowed_md)

    def test_required_top_level_directories_exist(self) -> None:
        for name in ALLOWED_ROOT_DIRS:
            self.assertTrue((ROOT / name).is_dir(), f"missing {name}/")

    def test_live_report_at_root_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            for name in ALLOWED_ROOT_FILES:
                (sandbox / name).write_text("# stub\n", encoding="utf-8")
            for name in ALLOWED_ROOT_DIRS:
                (sandbox / name).mkdir()
            (sandbox / "LIVE_MCP_CLI_TESTING_REPORT_2026-05-21.md").write_text(
                "# report\n",
                encoding="utf-8",
            )

            errors = validate_repo_root(sandbox)
            self.assertEqual(len(errors), 1)
            self.assertIn("LIVE_MCP_CLI_TESTING_REPORT_2026-05-21.md", errors[0])

    def test_validate_script_cli_exit_zero(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "validators" / "validate_repo_root.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=proc.stderr or proc.stdout,
        )


if __name__ == "__main__":
    unittest.main()
