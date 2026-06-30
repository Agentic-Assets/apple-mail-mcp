"""Static guidance checks for ID-first action-tool targeting."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ACTION_TOOLS = (
    "reply_to_email",
    "forward_email",
    "move_email",
    "update_email_status",
    "manage_trash",
    "manage_drafts",
    "list_email_attachments",
    "save_email_attachment",
    "export_emails",
)

ACTION_SELECTOR_RE = re.compile(
    r"\b(" + "|".join(ACTION_TOOLS) + r")\([^`\n]*(subject_keyword|subject_keywords|sender=|draft_subject)"
)
MAILBOX_ALL_RE = re.compile(r"\bmailbox\s*=\s*['\"]All['\"]")
LEGACY_ACTION_SELECTOR_RE = re.compile(r"\b(subject_keyword|subject_keywords|sender\s*=|draft_subject)\b")

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
    def _action_call_blocks(self, lines: list[str]) -> list[tuple[int, str]]:
        blocks: list[tuple[int, str]] = []
        line_index = 0
        while line_index < len(lines):
            line = lines[line_index]
            if not any(f"{tool}(" in line for tool in ACTION_TOOLS):
                line_index += 1
                continue

            block = [line]
            start_line = line_index + 1
            depth = line.count("(") - line.count(")")
            next_index = line_index + 1
            while depth > 0 and next_index < len(lines) and len(block) < 40:
                block.append(lines[next_index])
                depth += lines[next_index].count("(") - lines[next_index].count(")")
                next_index += 1
            blocks.append((start_line, "\n".join(block)))
            line_index = max(next_index, line_index + 1)
        return blocks

    def test_packaged_guidance_does_not_teach_legacy_action_selectors(self):
        docs = [ROOT / "docs" / "CLAUDE-conventions.md"]
        docs.extend((ROOT / "plugin" / "skills").rglob("*.md"))

        violations: list[str] = []
        for path in sorted(docs):
            rel = path.relative_to(ROOT)
            lines = path.read_text(encoding="utf-8").splitlines()
            for line_no, line in enumerate(lines, start=1):
                if ACTION_SELECTOR_RE.search(line) and not any(marker in line for marker in NEGATIVE_GUIDANCE_MARKERS):
                    violations.append(f"{rel}:{line_no}: {line.strip()}")
            for line_no, block in self._action_call_blocks(lines):
                if not LEGACY_ACTION_SELECTOR_RE.search(block):
                    continue
                if any(marker in block for marker in NEGATIVE_GUIDANCE_MARKERS):
                    continue
                first_line = block.splitlines()[0].strip()
                violations.append(f"{rel}:{line_no}: {first_line}")

        self.assertEqual(violations, [])

    def test_packaged_guidance_does_not_teach_copyable_mailbox_all(self):
        docs = (ROOT / "plugin" / "skills").rglob("*.md")

        violations: list[str] = []
        for path in sorted(docs):
            rel = path.relative_to(ROOT)
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if MAILBOX_ALL_RE.search(line):
                    violations.append(f"{rel}:{line_no}: {line.strip()}")

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
