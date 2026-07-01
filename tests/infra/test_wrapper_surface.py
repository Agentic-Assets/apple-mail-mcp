"""Tests for generated wrapper command-surface checks (mocked, no live wrapper)."""

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECK_WRAPPER_PATH = _REPO_ROOT / "tools" / "validators" / "check_wrapper_surface.py"
_spec = importlib.util.spec_from_file_location("check_wrapper_surface", _CHECK_WRAPPER_PATH)
assert _spec and _spec.loader
check_wrapper_surface = importlib.util.module_from_spec(_spec)
sys.modules["check_wrapper_surface"] = check_wrapper_surface
_spec.loader.exec_module(check_wrapper_surface)

_PATCH_WRAPPER_PATH = _REPO_ROOT / "tools" / "probes" / "patch_mcporter_wrapper.py"
_patch_spec = importlib.util.spec_from_file_location("patch_mcporter_wrapper", _PATCH_WRAPPER_PATH)
assert _patch_spec and _patch_spec.loader
patch_mcporter_wrapper = importlib.util.module_from_spec(_patch_spec)
sys.modules["patch_mcporter_wrapper"] = patch_mcporter_wrapper
_patch_spec.loader.exec_module(patch_mcporter_wrapper)


class WrapperSurfaceTests(unittest.TestCase):
    def test_check_wrapper_surface_all_present(self):
        help_text = "\n".join(check_wrapper_surface.CRITICAL_WRAPPER_COMMANDS)
        with patch.object(
            check_wrapper_surface, "_wrapper_help", return_value=help_text
        ):
            ok, present, missing = check_wrapper_surface.check_wrapper_surface(
                "apple-mail"
            )
        self.assertTrue(ok)
        self.assertEqual(len(present), len(check_wrapper_surface.CRITICAL_WRAPPER_COMMANDS))
        self.assertEqual(missing, [])

    def test_check_wrapper_surface_missing_get_email_by_id(self):
        help_text = "search-emails\nget-email-thread\nlist-inbox-emails\nget-inbox-overview"
        with patch.object(
            check_wrapper_surface, "_wrapper_help", return_value=help_text
        ):
            ok, _present, missing = check_wrapper_surface.check_wrapper_surface(
                "apple-mail"
            )
        self.assertFalse(ok)
        self.assertIn("get-email-by-id", missing)

    def test_check_wrapper_surface_rejects_global_timeout_collision(self):
        help_text = "\n".join(
            list(check_wrapper_surface.CRITICAL_WRAPPER_COMMANDS)
            + ["-t, --timeout <ms>"]
        )
        with patch.object(
            check_wrapper_surface, "_wrapper_help", return_value=help_text
        ):
            ok, _present, missing = check_wrapper_surface.check_wrapper_surface(
                "apple-mail"
            )
        self.assertFalse(ok)
        self.assertIn("request-timeout-ms", missing)

    def test_main_skips_when_no_wrapper_on_path(self):
        with patch("shutil.which", return_value=None):
            code = check_wrapper_surface.main([])
        self.assertEqual(code, 0)

    def test_main_fails_when_commands_missing(self):
        with (
            patch("shutil.which", return_value="/bin/apple-mail"),
            patch.object(
                check_wrapper_surface,
                "check_wrapper_surface",
                return_value=(False, [], ["get-email-by-id"]),
            ),
        ):
            code = check_wrapper_surface.main([])
        self.assertEqual(code, 1)


class PatchMcporterWrapperTests(unittest.TestCase):
    def test_patch_source_renames_global_request_timeout_only(self):
        source = (
            patch_mcporter_wrapper.GLOBAL_TIMEOUT_OPTION
            + "\n"
            + patch_mcporter_wrapper.GLOBAL_TIMEOUT_HELP
            + "\n.option(\"--timeout <timeout>\", \"Set timeout.\")"
            + "\nconst result = await invokeWithTimeout(call, globalOptions.timeout || 12e4);"
        )
        patched, changed = patch_mcporter_wrapper.patch_source(source)
        self.assertTrue(changed)
        self.assertIn("--request-timeout-ms <ms>", patched)
        self.assertNotIn("-t, --timeout <ms>", patched)
        self.assertIn('.option("--timeout <timeout>", "Set timeout.")', patched)
        self.assertIn("globalOptions.requestTimeoutMs || 12e4", patched)
        self.assertNotIn("globalOptions.timeout || 12e4", patched)

    def test_patch_plugin_root_repoints_embedded_start_script(self):
        source = (
            '"args": ["/Users/cayman-mac-mini/Documents/GitHub/'
            'apple-mail-mcp/plugin/start_mcp.sh", "--draft-safe"]'
        )
        patched, changed = patch_mcporter_wrapper.patch_plugin_root(
            source, Path("/tmp/worktree/plugin")
        )
        self.assertTrue(changed)
        self.assertIn('/tmp/worktree/plugin/start_mcp.sh", "--draft-safe"', patched)


if __name__ == "__main__":
    unittest.main()
