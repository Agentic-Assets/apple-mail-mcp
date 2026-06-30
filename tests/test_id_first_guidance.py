"""Static guidance checks for ID-first action-tool targeting."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ACTION_SELECTOR_RE = re.compile(
    r"\b("
    r"reply_to_email|forward_email|move_email|update_email_status|manage_trash|"
    r"manage_drafts|list_email_attachments|save_email_attachment|export_emails"
    r")\([^`\n]*(subject_keyword|subject_keywords|sender=|draft_subject)"
)
MAILBOX_ALL_RE = re.compile(r"\bmailbox\s*=\s*['\"]All['\"]")

NEGATIVE_GUIDANCE_MARKERS = (
    "Do not",
    "do not",
    "not pass",
    "no longer",
    "deprecated",
    "schema-compatible",
    "returns `TARGET_SELECTOR_DEPRECATED`",
    "flag action calls",
)


class IdFirstGuidanceTests(unittest.TestCase):
    def test_packaged_guidance_does_not_teach_legacy_action_selectors(self):
        docs = [ROOT / "docs" / "CLAUDE-conventions.md"]
        docs.extend((ROOT / "plugin" / "skills").rglob("*.md"))

        violations: list[str] = []
        for path in sorted(docs):
            rel = path.relative_to(ROOT)
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not ACTION_SELECTOR_RE.search(line):
                    continue
                if any(marker in line for marker in NEGATIVE_GUIDANCE_MARKERS):
                    continue
                violations.append(f"{rel}:{line_no}: {line.strip()}")

        self.assertEqual(violations, [])

    def test_search_patterns_do_not_teach_default_mailbox_all(self):
        path = ROOT / "plugin" / "skills" / "email-management" / "templates" / "search-patterns.md"

        violations = [
            f"{path.relative_to(ROOT)}:{line_no}: {line.strip()}"
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
            if MAILBOX_ALL_RE.search(line)
        ]

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
