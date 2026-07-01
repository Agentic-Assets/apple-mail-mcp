"""Packaged plugin skill path constraints."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = ROOT / "plugin" / "skills"

# Maintainer-only files under a skill tree; agents load SKILL.md + references/examples/templates.
MAINTAINER_ONLY_NAMES = frozenset({"README.md"})

MARKDOWN_LINK_RE = re.compile(r"\]\(([^)]+)\)")
BACKTICK_PATH_RE = re.compile(r"`(\.\./[^`]+)`")


def packaged_skill_dirs() -> list[Path]:
    return sorted(
        path
        for path in SKILLS_ROOT.iterdir()
        if path.is_dir() and path.name != "references" and (path / "SKILL.md").is_file()
    )


def agent_visible_markdown(skill_dir: Path) -> list[Path]:
    paths: list[Path] = [skill_dir / "SKILL.md"]
    for sub in ("references", "examples", "templates"):
        base = skill_dir / sub
        if base.is_dir():
            paths.extend(sorted(base.rglob("*.md")))
    return paths


def link_escapes_skill(skill_dir: Path, target: str) -> bool:
    stripped = target.strip()
    if not stripped or stripped.startswith(("http://", "https://", "mailto:", "#")):
        return False
    if stripped.startswith("/"):
        return True
    if ".." in Path(stripped).parts:
        return True
    resolved = (skill_dir / stripped).resolve()
    try:
        resolved.relative_to(skill_dir.resolve())
    except ValueError:
        return True
    return False


class PackagedSkillPathTests(unittest.TestCase):
    def test_packaged_skills_do_not_link_outside_skill_directory(self):
        violations: list[str] = []
        for skill_dir in packaged_skill_dirs():
            for md_path in agent_visible_markdown(skill_dir):
                if md_path.name in MAINTAINER_ONLY_NAMES:
                    continue
                rel = md_path.relative_to(ROOT)
                text = md_path.read_text(encoding="utf-8")
                for line_no, line in enumerate(text.splitlines(), start=1):
                    for match in MARKDOWN_LINK_RE.finditer(line):
                        target = match.group(1).strip()
                        if link_escapes_skill(skill_dir, target):
                            violations.append(f"{rel}:{line_no}: link escapes skill dir -> {target}")
                    for match in BACKTICK_PATH_RE.finditer(line):
                        target = match.group(1).strip()
                        if link_escapes_skill(skill_dir, target):
                            violations.append(f"{rel}:{line_no}: path escapes skill dir -> {target}")
        self.assertEqual(violations, [])

    def test_synced_skill_references_match_canonical_sources(self):
        proc = subprocess.run(
            ["python3", "tools/validators/sync_skill_references.py", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=proc.stderr.strip() or proc.stdout.strip() or "sync_skill_references --check failed",
        )


if __name__ == "__main__":
    unittest.main()
