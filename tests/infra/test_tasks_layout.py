"""Gate tasks/ folder layout — agents must use active/reference/archive buckets."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.validators.validate_tasks_layout import (  # noqa: E402
    ALLOWED_ROOT_DIRS,
    ALLOWED_ROOT_FILES,
    TASKS,
    _is_git_ignored,
    validate_tasks_layout,
)

class TestTasksLayout(unittest.TestCase):
    def test_validate_tasks_layout_passes(self) -> None:
        errors = validate_tasks_layout()
        self.assertEqual(errors, [], "\n".join(errors))

    def test_allowed_root_files_are_exact_navigation_set(self) -> None:
        root_md = {
            p.name
            for p in TASKS.iterdir()
            if p.is_file() and p.suffix == ".md" and not _is_git_ignored(p)
        }
        self.assertEqual(root_md, set(ALLOWED_ROOT_FILES))

    def test_ignored_local_task_artifacts_do_not_fail_layout(self) -> None:
        ignored = TASKS / ".validator-local-scratch.tmp"
        ignored.write_text("local scratch\n", encoding="utf-8")
        try:
            self.assertTrue(_is_git_ignored(ignored))
            errors = validate_tasks_layout()
            self.assertEqual(errors, [], "\n".join(errors))
        finally:
            ignored.unlink(missing_ok=True)

    def test_required_subdirectories_exist(self) -> None:
        for name in ALLOWED_ROOT_DIRS:
            self.assertTrue((TASKS / name).is_dir(), f"missing tasks/{name}/")

    def test_claude_documents_agent_requirements(self) -> None:
        text = (TASKS / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("## Agent requirements", text)
        self.assertIn("MUST", text)

    def test_validate_script_cli_exit_zero(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "validators" / "validate_tasks_layout.py")],
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
