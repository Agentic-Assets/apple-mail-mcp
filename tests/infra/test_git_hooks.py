"""Regression tests for checked-in local Git blockers."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("hook_name", ["pre-commit", "pre-push"])
def test_hook_clears_git_local_environment_before_child_processes(hook_name: str) -> None:
    content = (ROOT / ".githooks" / hook_name).read_text(encoding="utf-8")

    root_index = content.index('ROOT="$(git rev-parse --show-toplevel)"')
    clear_index = content.index("git rev-parse --local-env-vars")
    exec_index = content.index("exec ")

    assert root_index < clear_index < exec_index
    assert 'unset "$variable"' in content
