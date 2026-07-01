"""Characterization tests pinning CLI behavior ahead of the cli/ package split.

These cover branches of non-trivial helpers that the existing test_cli /
test_cli_perf suites exercise only indirectly: the mutually exclusive
--body / --body-file guard (_read_text_arg -> exit 2) and the mcp-config
--unsafe-send path (omits --draft-safe). They must pass against the current
single-module cli.py and after it becomes a package.
"""

import json
import unittest
from unittest.mock import patch

from apple_mail_mcp import cli


class CliCharacterizationTests(unittest.TestCase):
    def test_draft_rejects_body_and_body_file_together(self):
        # _read_text_arg raises ValueError when both are passed; _cmd_draft
        # maps that to exit code 2 without ever calling compose_email.
        with (
            patch("apple_mail_mcp.tools.compose.compose_email") as mock_compose,
            patch("builtins.print"),
        ):
            code = cli.main(
                [
                    "draft",
                    "--account",
                    "Work",
                    "--to",
                    "person@example.com",
                    "--subject",
                    "Subject",
                    "--body",
                    "inline",
                    "--body-file",
                    "/nonexistent/body.txt",
                ]
            )

        self.assertEqual(code, 2)
        mock_compose.assert_not_called()

    def test_mcp_config_unsafe_send_omits_draft_safe(self):
        with patch("builtins.print") as mock_print:
            code = cli.main(
                ["mcp-config", "--repo", "/tmp/apple-mail-mcp", "--unsafe-send"]
            )

        self.assertEqual(code, 0)
        payload = json.loads(mock_print.call_args.args[0])
        args = payload["mcpServers"]["apple-mail"]["args"]
        self.assertEqual(args, ["/tmp/apple-mail-mcp/plugin/start_mcp.sh"])
        self.assertNotIn("--draft-safe", args)


if __name__ == "__main__":
    unittest.main()
