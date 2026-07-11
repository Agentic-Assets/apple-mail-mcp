"""Release-contract checks for the self-contained plugin runtime."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugin"


def test_offline_runtime_payload_is_present_and_hash_locked() -> None:
    lock = PLUGIN / "requirements.lock"
    wheels = sorted((PLUGIN / "wheelhouse").glob("*.whl"))

    assert lock.is_file()
    assert "--hash=sha256:" in lock.read_text(encoding="utf-8")
    assert wheels


def test_launcher_has_no_network_install_path() -> None:
    launcher = (PLUGIN / "start_mcp.sh").read_text(encoding="utf-8")

    assert "--no-index" in launcher
    assert "--require-hashes" in launcher
    assert '"${WHEELHOUSE}"' in launcher
    assert "pip install --quiet --upgrade pip" not in launcher
    assert 'pip install --quiet -r "${REQUIREMENTS}"' not in launcher


def test_mcpb_builder_copies_the_offline_payload() -> None:
    builder = (ROOT / "apple-mail-mcpb/build-mcpb.sh").read_text(encoding="utf-8")

    assert 'cp "${SOURCE_DIR}/requirements.lock"' in builder
    assert 'cp -R "${SOURCE_DIR}/wheelhouse"' in builder
