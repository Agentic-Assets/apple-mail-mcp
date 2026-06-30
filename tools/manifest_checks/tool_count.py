"""Tool-count checks: registry extraction and active-doc claim validation."""

from __future__ import annotations

import re
from pathlib import Path

from manifest_checks import common
from manifest_checks.common import (
    ACTIVE_DOC_TOOL_COUNT_REQUIRED,
    ACTIVE_DOC_TOOL_COUNT_SCAN_ONLY,
    TOOL_COUNT_CLAIM_PATTERNS,
    _fail,
)


def _extract_registered_tool_names() -> list[str]:
    names: list[str] = []
    for path in sorted((common.ROOT / "plugin/apple_mail_mcp/tools").rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            if re.match(r"^@mcp\.tool", lines[i]):
                j = i + 1
                while j < len(lines) and lines[j].startswith("@"):
                    j += 1
                if j >= len(lines):
                    _fail(f"no function after @mcp.tool in {path}:{i + 1}")
                match = re.match(r"(?:async )?def (\w+)", lines[j])
                if not match:
                    _fail(f"no def after @mcp.tool in {path}:{i + 1}")
                names.append(match.group(1))
                i = j + 1
            else:
                i += 1
    return names


def _tool_count_claims_in_line(line: str) -> list[int]:
    """Return all tool-count claims in one line of active guidance."""
    claims: list[int] = []
    for pattern in TOOL_COUNT_CLAIM_PATTERNS:
        claims.extend(int(match.group(1)) for match in pattern.finditer(line))
    return claims


def _check_tools_module_count_table(path: Path, actual_count: int, errors: list[str], *, root: Path = common.ROOT) -> None:
    """Require plugin/apple_mail_mcp/tools/CLAUDE.md module rows to sum to count."""
    if not path.exists():
        return
    total = 0
    seen_rows = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\|\s*`[^`]+\.py`\s*\|\s*(\d+)\s*\|", line)
        if not match:
            continue
        seen_rows += 1
        total += int(match.group(1))
    if seen_rows and total != actual_count:
        errors.append(f"{path.relative_to(root)}: module table sums to {total}, registry has {actual_count}")


def _check_active_doc_tool_count_claims(
    actual_count: int,
    errors: list[str],
    *,
    root: Path = common.ROOT,
    required_docs: tuple[str, ...] = ACTIVE_DOC_TOOL_COUNT_REQUIRED,
    scan_only_docs: tuple[str, ...] = ACTIVE_DOC_TOOL_COUNT_SCAN_ONLY,
) -> None:
    """Validate tool-count claims in active docs only, preserving historical records."""
    for rel_path in required_docs + scan_only_docs:
        path = root / rel_path
        if not path.exists():
            if rel_path in required_docs:
                errors.append(f"{rel_path}: missing active doc")
            continue
        found_claim = False
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for claimed in _tool_count_claims_in_line(line):
                found_claim = True
                if claimed != actual_count:
                    errors.append(f"{rel_path}:{line_no}: tool-count claim {claimed}, registry has {actual_count}")
        if rel_path in required_docs and not found_claim:
            errors.append(f"{rel_path}: missing active tool-count claim")

    tools_doc = root / "plugin/apple_mail_mcp/tools/CLAUDE.md"
    _check_tools_module_count_table(tools_doc, actual_count, errors, root=root)
