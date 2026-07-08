#!/usr/bin/env python3
"""Sync canonical plugin skill references into each skill's references/ folder.

Canonical sources live under plugin/skills/references/. Packaged Claude/Codex
skills only expose files inside each skill directory, so shared references are
copied per skill. Edit the canonical files, then run this script (or let
dev-check / pytest enforce parity via --check).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_DIR = ROOT / "plugin" / "skills" / "references"
SKILLS_ROOT = ROOT / "plugin" / "skills"

# skill directory name -> filenames under plugin/skills/references/
SYNC_MAP: dict[str, list[str]] = {
    "apple-mail-operator": [
        "large-inbox-rules.md",
        "pre-draft-verification.md",
        "recent-first-triage.md",
        "exchange-account-patterns.md",
    ],
    "inbox-triage": [
        "large-inbox-rules.md",
        "pre-draft-verification.md",
        "recent-first-triage.md",
        "exchange-account-patterns.md",
        "research-project-tracking.md",
    ],
    "email-management": [
        "large-inbox-rules.md",
        "pre-draft-verification.md",
        "recent-first-triage.md",
        "exchange-account-patterns.md",
        "research-project-tracking.md",
    ],
    "email-drafting": ["pre-draft-verification.md", "recent-first-triage.md"],
    "email-archive-cleanup": ["large-inbox-rules.md", "exchange-account-patterns.md"],
    "email-style-profile": ["large-inbox-rules.md"],
    "email-attachments": ["large-inbox-rules.md", "research-project-tracking.md"],
    "mail-rules-advisor": ["large-inbox-rules.md"],
    "mailbox-taxonomy": ["large-inbox-rules.md"],
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync_references(*, check_only: bool) -> list[str]:
    """Return human-readable error lines; empty means success."""
    errors: list[str] = []

    for skill_name, filenames in sorted(SYNC_MAP.items()):
        skill_dir = SKILLS_ROOT / skill_name
        if not skill_dir.is_dir():
            errors.append(f"missing skill directory: {skill_dir.relative_to(ROOT)}")
            continue

        ref_dir = skill_dir / "references"
        for filename in filenames:
            src = CANONICAL_DIR / filename
            dst = ref_dir / filename
            if not src.is_file():
                errors.append(f"missing canonical reference: {src.relative_to(ROOT)}")
                continue
            if dst.is_file() and _sha256(src) == _sha256(dst):
                continue
            if check_only:
                rel = dst.relative_to(ROOT)
                errors.append(
                    f"{rel}: out of sync with {src.relative_to(ROOT)} "
                    "(run: python3 tools/validators/sync_skill_references.py)"
                )
                continue
            ref_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 when any per-skill copy drifts from canonical sources.",
    )
    args = parser.parse_args()
    errors = sync_references(check_only=args.check)
    if errors:
        for line in errors:
            print(line, file=sys.stderr)
        return 1
    if args.check:
        print("skill reference sync: OK")
    else:
        print("skill reference sync: updated copies where needed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
