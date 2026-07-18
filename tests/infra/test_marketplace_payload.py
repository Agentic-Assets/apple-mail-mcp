"""Tests for the source-owned marketplace payload contract and inventory."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from marketplace_payload import (  # noqa: E402
    PayloadContractError,
    build_inventory,
    inventory_json,
    load_contract,
)


def _contract() -> dict[str, object]:
    return {
        "schema_version": 1,
        "plugin_id": "apple-mail",
        "source_root": "plugin",
        "marketplace_destination": "plugins/apple-mail",
        "include": ["runtime/**", "start.sh"],
        "required": ["runtime/**"],
        "exclude": ["**/CLAUDE.md", "docs/**"],
        "forbidden": ["provenance.json", "**/provenance.json"],
        "authority": {
            "grants_marketplace_admission": False,
            "statement": "evidence only",
        },
    }


class PayloadRepo:
    def __init__(self, root: Path) -> None:
        self.root = root
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        (root / "distribution").mkdir()
        (root / "plugin").mkdir()
        self.write_contract(_contract())

    def write_contract(self, contract: dict[str, object]) -> None:
        (self.root / "distribution/marketplace-payload.json").write_text(json.dumps(contract), encoding="utf-8")

    def write(self, relative: str, content: bytes = b"payload") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def add(self) -> None:
        subprocess.run(["git", "add", "plugin"], cwd=self.root, check=True)


class TestMarketplacePayload(unittest.TestCase):
    def test_repository_contract_is_valid_and_deterministic(self) -> None:
        contract = load_contract(ROOT)
        first = build_inventory(ROOT, contract)
        second = build_inventory(ROOT, contract)
        self.assertEqual(first, second)
        self.assertEqual(inventory_json(first), inventory_json(second))
        self.assertEqual(first["plugin_id"], "apple-mail")
        self.assertEqual(first["marketplace_destination"], "plugins/apple-mail")
        self.assertFalse(first["grants_marketplace_admission"])
        self.assertEqual(len(first["contract_sha256"]), 64)
        paths = {entry["path"] for entry in first["files"]}
        self.assertIn(".claude-plugin/plugin.json", paths)
        self.assertIn(".codex-plugin/plugin.json", paths)
        self.assertIn(".cursor-plugin/plugin.json", paths)
        self.assertIn("requirements.lock", paths)
        self.assertNotIn("requirements.in", paths)
        self.assertNotIn("requirements.txt", paths)
        self.assertFalse(any(path.endswith("CLAUDE.md") for path in paths))
        self.assertFalse(any(path.startswith("skills/") and path.endswith("/README.md") for path in paths))
        lock = next(entry for entry in first["files"] if entry["path"] == "requirements.lock")
        self.assertEqual(first["requirements_lock_sha256"], lock["sha256"])
        self.assertGreater(first["wheelhouse_file_count"], 0)
        self.assertEqual(len(first["wheelhouse_sha256"]), 64)

    def test_content_and_executable_mode_change_payload_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = PayloadRepo(Path(tmp))
            repo.write("plugin/runtime/nested/a.py", b"one")
            repo.write("plugin/start.sh", b"#!/bin/sh\n")
            (repo.root / "plugin/start.sh").chmod(0o755)
            repo.add()
            first = build_inventory(repo.root)
            repo.write("plugin/runtime/nested/a.py", b"two")
            second = build_inventory(repo.root)
            self.assertNotEqual(first["payload_sha256"], second["payload_sha256"])
            self.assertEqual(second["files"][1]["mode"], "100755")

    def test_explicit_exclusion_wins_inside_included_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = PayloadRepo(Path(tmp))
            repo.write("plugin/runtime/code.py")
            repo.write("plugin/runtime/deep/CLAUDE.md")
            repo.add()
            inventory = build_inventory(repo.root)
            self.assertEqual([entry["path"] for entry in inventory["files"]], ["runtime/code.py"])

    def test_unclassified_tracked_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = PayloadRepo(Path(tmp))
            repo.write("plugin/runtime/code.py")
            repo.write("plugin/surprise.txt")
            repo.add()
            with self.assertRaisesRegex(PayloadContractError, "unclassified tracked source file"):
                build_inventory(repo.root)

    def test_missing_required_tree_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = PayloadRepo(Path(tmp))
            repo.write("plugin/start.sh")
            repo.add()
            with self.assertRaisesRegex(PayloadContractError, "required payload path or tree is missing"):
                build_inventory(repo.root)

    def test_forbidden_provenance_fails_even_below_included_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = PayloadRepo(Path(tmp))
            repo.write("plugin/runtime/code.py")
            repo.write("plugin/runtime/provenance.json")
            repo.add()
            with self.assertRaisesRegex(PayloadContractError, "forbidden tracked source file"):
                build_inventory(repo.root)

    def test_tracked_symlink_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = PayloadRepo(Path(tmp))
            repo.write("plugin/runtime/target.py")
            (repo.root / "plugin/runtime/link.py").symlink_to("target.py")
            repo.add()
            with self.assertRaisesRegex(PayloadContractError, "symlinks and non-regular files"):
                build_inventory(repo.root)

    def test_common_secret_filename_is_forbidden_even_when_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = PayloadRepo(Path(tmp))
            contract = _contract()
            contract["exclude"] = [*contract["exclude"], "**/.env"]
            contract["forbidden"] = [*contract["forbidden"], "**/.env"]
            repo.write_contract(contract)
            repo.write("plugin/runtime/code.py")
            repo.write("plugin/runtime/.env", b"TOKEN=value")
            repo.add()
            with self.assertRaisesRegex(PayloadContractError, "forbidden tracked source file"):
                build_inventory(repo.root)

    def test_secret_content_marker_fails_only_for_included_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = PayloadRepo(Path(tmp))
            repo.write("plugin/runtime/code.py", b"safe = True")
            repo.write("plugin/docs/example.md", b"OPENAI_API_KEY=example")
            repo.add()
            build_inventory(repo.root)
            repo.write("plugin/runtime/code.py", b"OPENAI_API_KEY=secret")
            with self.assertRaisesRegex(PayloadContractError, "secret-like content marker"):
                build_inventory(repo.root)

    def test_compressed_secret_inside_wheel_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = PayloadRepo(Path(tmp))
            contract = _contract()
            contract["include"] = [*contract["include"], "wheelhouse/**"]
            repo.write_contract(contract)
            repo.write("plugin/runtime/code.py")
            wheel = repo.root / "plugin/wheelhouse/demo.whl"
            wheel.parent.mkdir(parents=True)
            with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("package/secrets.json", "OPENAI_API_KEY=hidden")
            repo.add()
            with self.assertRaisesRegex(PayloadContractError, "secret-like filename"):
                build_inventory(repo.root)

    def test_compressed_secret_content_inside_wheel_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = PayloadRepo(Path(tmp))
            contract = _contract()
            contract["include"] = [*contract["include"], "wheelhouse/**"]
            repo.write_contract(contract)
            repo.write("plugin/runtime/code.py")
            wheel = repo.root / "plugin/wheelhouse/demo.whl"
            wheel.parent.mkdir(parents=True)
            with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("package/config.txt", "OPENAI_API_KEY=hidden")
            repo.add()
            with self.assertRaisesRegex(PayloadContractError, "wheel member"):
                build_inventory(repo.root)

    def test_unsafe_contract_path_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = PayloadRepo(Path(tmp))
            contract = _contract()
            contract["source_root"] = "../plugin"
            repo.write_contract(contract)
            with self.assertRaisesRegex(PayloadContractError, "source_root must be a safe"):
                load_contract(repo.root)

    def test_validator_cli_succeeds(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/validators/validate_marketplace_payload.py"), "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["plugin_id"], "apple-mail")
        self.assertGreater(output["file_count"], 0)


if __name__ == "__main__":
    unittest.main()
