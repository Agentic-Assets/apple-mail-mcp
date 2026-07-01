"""Version-sync checks: read the project version and name from pyproject.toml."""

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
