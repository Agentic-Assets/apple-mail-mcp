"""Static checks for the inbox dashboard's ID-first action contract."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_TEMPLATE = ROOT / "plugin" / "ui" / "templates" / "dashboard.html"


class DashboardIdFirstTests(unittest.TestCase):
    def test_quick_actions_use_message_ids(self):
        template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")

        self.assertNotIn("subject_keyword: email.subject", template)
        self.assertIn("message_ids: target.message_ids", template)
        self.assertIn("function requireMessageId(email)", template)


if __name__ == "__main__":
    unittest.main()
