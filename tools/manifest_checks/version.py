"""Version-sync checks for public release surfaces and changelog state."""

from __future__ import annotations

import re

from manifest_checks import common
from manifest_checks.common import _fail


def _read_project_field(field: str) -> str:
    text = (common.ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^\[project\]\s*$([\s\S]*?)(?=^\[|\Z)", text, re.M)
    if not block:
        _fail("pyproject.toml: missing [project] section")
    match = re.search(rf'^{field}\s*=\s*["\']([^"\']+)["\']', block.group(1), re.M)
    if not match:
        _fail(f"pyproject.toml: missing [project].{field}")
    return match.group(1)


def _read_project_version() -> str:
    return _read_project_field("version")


def _read_project_name() -> str:
    return _read_project_field("name")


def _changelog_sections() -> tuple[str, str]:
    changelog = common.ROOT / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    unreleased = re.search(r"^## Unreleased\s*$([\s\S]*?)(?=^##\s|\Z)", text, re.M)
    first_release = re.search(r"^## (\d+\.\d+\.\d+) - \d{4}-\d{2}-\d{2}\s*$", text, re.M)
    unreleased_body = unreleased.group(1) if unreleased else ""
    release_version = first_release.group(1) if first_release else ""
    return unreleased_body, release_version


def _check_changelog_release_version(expected_version: str, errors: list[str]) -> None:
    unreleased_body, release_version = _changelog_sections()
    if release_version != expected_version:
        errors.append(
            "CHANGELOG.md: latest release heading "
            f"'{release_version or '<missing>'}' must match pyproject.toml version '{expected_version}'"
        )

    release_note_lines = [
        line
        for line in unreleased_body.splitlines()
        if line.lstrip().startswith(("- ", "* "))
    ]
    if release_note_lines:
        errors.append(
            "CHANGELOG.md: Unreleased contains release notes; move them under "
            f"## {expected_version} - YYYY-MM-DD before running the release gate"
        )
