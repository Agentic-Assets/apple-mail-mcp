"""Tests for tools/validate_manifests.py (Phase 1 CI guardrails)."""

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import validate_manifests


class ValidateManifestsTests(unittest.TestCase):
    def test_validate_manifests_passes_on_current_repo(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/validate_manifests.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=result.stdout + result.stderr,
        )
        self.assertIn("validate_manifests: OK", result.stdout)

    def test_compare_zip_members_reports_stale_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.txt"
            archive = tmp_path / "artifact.zip"
            source.write_text("current", encoding="utf-8")
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("payload/source.txt", "old")

            errors = []
            validate_manifests._compare_zip_members(
                archive,
                [(source, "payload/source.txt")],
                "artifact.zip",
                errors,
            )

        self.assertEqual(
            errors,
            ["artifact.zip: stale payload/source.txt; rebuild artifact.zip"],
        )

    def test_compare_zip_members_reports_missing_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.txt"
            archive = tmp_path / "artifact.zip"
            source.write_text("current", encoding="utf-8")
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("payload/other.txt", "current")

            errors = []
            validate_manifests._compare_zip_members(
                archive,
                [(source, "payload/source.txt")],
                "artifact.zip",
                errors,
            )

        self.assertEqual(errors, ["artifact.zip: missing payload/source.txt"])

    def test_compare_zip_members_skips_absent_archive_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.txt"
            source.write_text("current", encoding="utf-8")
            errors = []

            validate_manifests._compare_zip_members(
                Path(tmp) / "missing.zip",
                [(source, "payload/source.txt")],
                "missing.zip",
                errors,
            )

        self.assertEqual(errors, [])

    def test_compare_zip_members_can_require_absent_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.txt"
            source.write_text("current", encoding="utf-8")
            errors = []

            validate_manifests._compare_zip_members(
                Path(tmp) / "missing.zip",
                [(source, "payload/source.txt")],
                "missing.zip",
                errors,
                require_present=True,
            )

        self.assertEqual(
            errors,
            ["missing.zip: missing archive; rebuild missing.zip"],
        )

    def test_check_no_directory_entries_flags_bare_directory_members(self):
        # Regression: raw `zip -r .` emits zero-byte entries whose names end
        # in `/`. `mcpb unpack` (and Claude Desktop's installer) treats those
        # as files and aborts with ENOENT. The MCPB must be built via
        # `mcpb pack`. See apple-mail-mcpb/build-mcpb.sh.
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.mcpb"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("ui/", b"")
                zf.writestr("ui/__init__.py", b"# real file")
                zf.writestr("apple_mail_mcp/", b"")

            errors = []
            validate_manifests._check_no_directory_entries(archive, archive.name, errors)

        self.assertEqual(len(errors), 1)
        msg = errors[0]
        self.assertIn("contains 2 directory entries", msg)
        self.assertIn("ui/", msg)
        self.assertIn("apple_mail_mcp/", msg)
        self.assertIn("mcpb pack", msg)

    def test_check_no_directory_entries_passes_on_clean_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "good.mcpb"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("ui/__init__.py", b"# real file")
                zf.writestr("manifest.json", b"{}")

            errors = []
            validate_manifests._check_no_directory_entries(archive, archive.name, errors)

        self.assertEqual(errors, [])

    def test_check_no_directory_entries_skips_absent_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = []
            validate_manifests._check_no_directory_entries(
                Path(tmp) / "missing.mcpb", "missing.mcpb", errors
            )
        self.assertEqual(errors, [])

    def test_plugin_zip_has_no_directory_entries(self):
        # Regression: zero-byte directory entries (names ending in `/`) broke
        # Cowork's plugin uploader the same way they broke the MCPB
        # extractor. Build script uses `zip -D` to suppress them.
        archive = ROOT / "apple-mail-plugin.zip"
        if not archive.exists():
            self.skipTest("apple-mail-plugin.zip not built; run tools/build-artifacts.sh")
        import zipfile as _zf
        with _zf.ZipFile(archive) as zf:
            offenders = [n for n in zf.namelist() if n.endswith("/")]
        self.assertEqual(
            offenders,
            [],
            msg=(
                f"plugin zip must contain no bare directory entries "
                f"(found {len(offenders)}: {offenders[:3]}); "
                f"rebuild with tools/build-artifacts.sh (uses `zip -D`)"
            ),
        )

    def test_plugin_zip_has_manifest_at_root_not_nested(self):
        # Regression: Cowork (and `claude plugin validate`) look for
        # .claude-plugin/plugin.json at the unzip root. If the zip wraps
        # everything in a `plugin/` prefix, validation fails with
        # "No manifest found in directory". Always zip from inside plugin/.
        archive = ROOT / "apple-mail-plugin.zip"
        if not archive.exists():
            self.skipTest("apple-mail-plugin.zip not built; run tools/build-artifacts.sh")
        import zipfile as _zf
        with _zf.ZipFile(archive) as zf:
            names = zf.namelist()
        self.assertIn(
            ".claude-plugin/plugin.json",
            names,
            msg=(
                "plugin.json must be at zip root for Cowork uploads. "
                "Rebuild with tools/build-artifacts.sh (zips from inside plugin/)."
            ),
        )
        nested = [n for n in names if n.startswith("plugin/")]
        self.assertEqual(
            nested,
            [],
            msg=f"zip must not wrap files under plugin/ — found {len(nested)} such entries",
        )


if __name__ == "__main__":
    unittest.main()
