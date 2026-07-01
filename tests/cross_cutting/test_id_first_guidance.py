"""Static guidance checks for ID-first action-tool targeting."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

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
    "Never pass",
    "never pass",
    "no longer",
    "deprecated",
    "schema-compatible",
    "schema-compat",
    "returns `TARGET_SELECTOR_DEPRECATED`",
    "returns TARGET_SELECTOR_DEPRECATED",
    "flag action calls",
    "Discovery-only",
    "**Discovery-only:**",
)

# Action-tool call blocks that misuse discovery param names or reply body param.
ACTION_SENDER_EXACT_RE = re.compile(
    r"\b(" + "|".join(ACTION_TOOLS) + r")\([^`\n]*sender_exact"
)
ACTION_SENDER_DOMAIN_RE = re.compile(
    r"\b(" + "|".join(ACTION_TOOLS) + r")\([^`\n]*sender_domain"
)
REPLY_BODY_PARAM_RE = re.compile(r"\breply_to_email\([^`\n]*\bbody\s*=")
SAVE_ATTACHMENT_MESSAGE_ID_RE = re.compile(
    r"\bsave_email_attachment\([^`\n]*\bmessage_id\s*="
)
BARE_MOVE_EMAIL_RE = re.compile(
    r"\bmove_email\(\s*(?:account\s*=\s*[^,\n]+,\s*)?"
    r"(?:to_mailbox|from_mailbox)\s*="
)

README_FALLBACK_RE = re.compile(
    r"subject_keyword.*fallback|Cayman-approved",
    re.IGNORECASE,
)
README_FILTER_SCAN_WITHOUT_DEPRECATION_RE = re.compile(
    r"allow_filter_scan=True.*FILTER_SCAN_DISABLED",
    re.IGNORECASE,
)
MANIFEST_SUBJECT_KEYWORD_FALLBACK_RE = re.compile(
    r"or subject keyword",
    re.IGNORECASE,
)
SEARCH_EMAILS_WRONG_JSON_KEY_RE = re.compile(r'\[["\']emails["\']\]')
LIST_INBOX_EMAILS_MARKER = "list_inbox_emails"
SEARCH_EMAILS_MARKER = "search_emails"


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

    def _collect_legacy_selector_violations(self, path: Path) -> list[str]:
        rel = path.relative_to(ROOT)
        lines = path.read_text(encoding="utf-8").splitlines()
        violations: list[str] = []
        for line_no, line in enumerate(lines, start=1):
            if ACTION_SELECTOR_RE.search(line) and not any(
                marker in line for marker in NEGATIVE_GUIDANCE_MARKERS
            ):
                violations.append(f"{rel}:{line_no}: {line.strip()}")
        for line_no, block in self._action_call_blocks(lines):
            if not LEGACY_ACTION_SELECTOR_RE.search(block):
                continue
            if any(marker in block for marker in NEGATIVE_GUIDANCE_MARKERS):
                continue
            first_line = block.splitlines()[0].strip()
            violations.append(f"{rel}:{line_no}: {first_line}")
        return violations

    def _collect_template_trap_violations(self, path: Path) -> list[str]:
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        violations: list[str] = []

        for line_no, line in enumerate(lines, start=1):
            if ACTION_SENDER_EXACT_RE.search(line):
                violations.append(f"{rel}:{line_no}: sender_exact on action tool")
            if ACTION_SENDER_DOMAIN_RE.search(line):
                violations.append(f"{rel}:{line_no}: sender_domain on action tool")
            if REPLY_BODY_PARAM_RE.search(line):
                violations.append(f"{rel}:{line_no}: reply_to_email uses body= not reply_body=")
            if SAVE_ATTACHMENT_MESSAGE_ID_RE.search(line):
                violations.append(f"{rel}:{line_no}: save_email_attachment uses message_id= not message_ids=")

        for line_no, block in self._action_call_blocks(lines):
            if "move_email(" not in block:
                continue
            if "message_ids" in block or "allow_filter_scan" in block:
                continue
            if BARE_MOVE_EMAIL_RE.search(block.replace("\n", " ")):
                first_line = block.splitlines()[0].strip()
                violations.append(f"{rel}:{line_no}: bare move_email without message_ids or allow_filter_scan")

        return violations

    def _collect_search_items_json_violations(self, path: Path) -> list[str]:
        """search_emails JSON uses 'items'; list_inbox_emails uses 'emails'."""
        rel = path.relative_to(ROOT)
        lines = path.read_text(encoding="utf-8").splitlines()
        violations: list[str] = []
        for line_no, line in enumerate(lines, start=1):
            if not SEARCH_EMAILS_WRONG_JSON_KEY_RE.search(line):
                continue
            lookback = lines[max(0, line_no - 12) : line_no]
            has_search = any(SEARCH_EMAILS_MARKER in prior for prior in lookback)
            has_list = any(LIST_INBOX_EMAILS_MARKER in prior for prior in lookback)
            if has_search and not has_list:
                violations.append(
                    f"{rel}:{line_no}: use ['items'] after search_emails, not ['emails']"
                )
        return violations

    def test_packaged_guidance_does_not_teach_legacy_action_selectors(self):
        docs = [ROOT / "docs" / "CLAUDE-conventions.md"]
        docs.extend((ROOT / "plugin" / "skills").rglob("*.md"))

        violations: list[str] = []
        for path in sorted(docs):
            violations.extend(self._collect_legacy_selector_violations(path))

        self.assertEqual(violations, [])

    def test_common_workflows_does_not_teach_copy_paste_traps(self):
        path = ROOT / "plugin" / "skills" / "email-management" / "templates" / "common-workflows.md"
        violations = self._collect_template_trap_violations(path)
        self.assertEqual(violations, [])

    def test_example_docs_do_not_teach_copy_paste_traps(self):
        examples_dir = ROOT / "plugin" / "skills" / "email-management" / "examples"
        violations: list[str] = []
        for path in sorted(examples_dir.glob("*.md")):
            violations.extend(self._collect_template_trap_violations(path))
            violations.extend(self._collect_search_items_json_violations(path))
        self.assertEqual(violations, [])

    def test_email_archive_cleanup_skill_json_keys(self):
        path = ROOT / "plugin" / "skills" / "email-archive-cleanup" / "SKILL.md"
        violations = self._collect_search_items_json_violations(path)
        self.assertEqual(violations, [])

    def test_readme_does_not_advertise_subject_keyword_fallback_on_actions(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        violations: list[str] = []
        for line_no, line in enumerate(readme.splitlines(), start=1):
            if README_FALLBACK_RE.search(line) and "TARGET_SELECTOR_DEPRECATED" not in line:
                violations.append(f"README.md:{line_no}: {line.strip()}")
            if README_FILTER_SCAN_WITHOUT_DEPRECATION_RE.search(line):
                if "TARGET_SELECTOR_DEPRECATED" not in line and "subject_keyword" not in line:
                    violations.append(
                        f"README.md:{line_no}: filter-scan row missing subject/sender deprecation"
                    )
        self.assertEqual(violations, [])

    def test_manifest_action_tools_do_not_imply_working_subject_keyword_fallback(self):
        manifest_path = ROOT / "apple-mail-mcpb" / "manifest.json"
        text = manifest_path.read_text(encoding="utf-8")
        violations: list[str] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            if MANIFEST_SUBJECT_KEYWORD_FALLBACK_RE.search(line):
                violations.append(f"apple-mail-mcpb/manifest.json:{line_no}: {line.strip()}")
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
