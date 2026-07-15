from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from marketplace_payload import build_inventory  # noqa: E402
from release import pre_push, source_release  # noqa: E402


def run(root: Path, *command: str) -> str:
    return subprocess.run(command, cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def git(root: Path, *args: str) -> str:
    return run(root, "git", *args)


@pytest.fixture
def release_repo(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "source"
    remote = tmp_path / "remote.git"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Release Test")
    git(root, "config", "user.email", "release@example.test")
    key = tmp_path / "release-key"
    run(tmp_path, "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key))
    fingerprint_output = run(tmp_path, "ssh-keygen", "-lf", str(key.with_suffix(".pub")))
    fingerprint = re.search(r"SHA256:[A-Za-z0-9+/=]+", fingerprint_output)
    assert fingerprint is not None

    (root / "plugin/wheelhouse").mkdir(parents=True)
    (root / "distribution").mkdir()
    (root / "provenance").mkdir()
    (root / "plugin/start_mcp.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "plugin/requirements.lock").write_text(
        "demo==1 \\\n+    --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8"
    )
    with zipfile.ZipFile(root / "plugin/wheelhouse/demo-1-py3-none-any.whl", "w") as wheel:
        wheel.writestr("demo/__init__.py", "")
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "1.2.3"\n', encoding="utf-8")
    payload_contract = {
        "schema_version": 1,
        "plugin_id": "apple-mail",
        "source_root": "plugin",
        "marketplace_destination": "plugins/apple-mail",
        "include": ["start_mcp.sh", "requirements.lock", "wheelhouse/**"],
        "required": ["start_mcp.sh", "requirements.lock", "wheelhouse/**"],
        "exclude": ["docs/**"],
        "forbidden": ["**/.env"],
        "authority": {
            "grants_marketplace_admission": False,
            "statement": "Source evidence does not grant marketplace admission.",
        },
    }
    (root / "distribution/marketplace-payload.json").write_text(
        json.dumps(payload_contract, indent=2) + "\n", encoding="utf-8"
    )
    public_key = key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    (root / "provenance/source-release.allowed_signers").write_text(
        "release-test " + " ".join(public_key) + "\n", encoding="utf-8"
    )
    policy = {
        "schema_version": 1,
        "plugin_id": "apple-mail",
        "repository": str(remote),
        "default_branch": "main",
        "tag_prefix": "v",
        "payload_root": "plugin",
        "payload_contract": "distribution/marketplace-payload.json",
        "version_file": "pyproject.toml",
        "lock_file": "plugin/requirements.lock",
        "wheelhouse_root": "plugin/wheelhouse",
        "trusted_signers": "provenance/source-release.allowed_signers",
        "signer_principal": "release-test",
        "signer_fingerprint": fingerprint.group(0),
        "release_sensitive_paths": ["plugin/", "pyproject.toml", "provenance/source-release-policy.json"],
    }
    (root / "provenance/source-release-policy.json").write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "release fixture")
    git(tmp_path, "init", "--bare", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-u", "origin", "main")
    git(root, "config", "gpg.format", "ssh")
    git(root, "config", "user.signingkey", str(key))
    return root, key


def test_signed_release_tag_verifies_all_bindings(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    tag = source_release.create_tag(root, "v1.2.3", confirm=True)

    bindings = source_release.verify_tag(root, tag)

    assert tag == "v1.2.3"
    assert bindings.version == "1.2.3"
    assert bindings.commit == git(root, "rev-parse", "HEAD")


def test_source_tag_payload_binding_matches_promotion_contract(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    policy = source_release.load_policy(root)

    bindings = source_release.bindings_for(root, "HEAD", policy)
    promoted = build_inventory(root)

    assert bindings.payload_inventory_sha256 == promoted["payload_sha256"]
    assert bindings.requirements_lock_sha256 == promoted["requirements_lock_sha256"]
    assert bindings.wheelhouse_inventory_sha256 == promoted["wheelhouse_sha256"]


def test_lightweight_release_tag_is_rejected(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    git(root, "tag", "v1.2.3")

    with pytest.raises(source_release.ReleaseError, match="annotated tag object"):
        source_release.verify_tag(root, "v1.2.3")


def test_signed_tag_with_drifted_binding_is_rejected(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    bindings = source_release.bindings_for(root, "HEAD", source_release.load_policy(root))
    message = bindings.message().replace(bindings.payload_inventory_sha256, "0" * 64)
    message_file = root / "tag-message"
    message_file.write_text(message, encoding="utf-8")
    git(root, "add", "tag-message")
    git(root, "commit", "-m", "make tag message tracked")
    # Bind to the new commit but preserve the intentionally bad payload digest.
    current = source_release.bindings_for(root, "HEAD", source_release.load_policy(root))
    message = current.message().replace(current.payload_inventory_sha256, "0" * 64)
    message_file.write_text(message, encoding="utf-8")
    git(root, "tag", "-s", "-F", str(message_file), "v1.2.3")

    with pytest.raises(source_release.ReleaseError, match="bindings do not match"):
        source_release.verify_tag(root, "v1.2.3")


def test_dirty_policy_and_signer_substitution_cannot_trust_attacker_tag(
    release_repo: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _ = release_repo
    attacker_key = tmp_path / "attacker-key"
    run(tmp_path, "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(attacker_key))
    attacker_public = attacker_key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    fingerprint_output = run(tmp_path, "ssh-keygen", "-lf", str(attacker_key.with_suffix(".pub")))
    attacker_fingerprint = re.search(r"SHA256:[A-Za-z0-9+/=]+", fingerprint_output)
    assert attacker_fingerprint is not None

    policy_path = root / "provenance/source-release-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["signer_fingerprint"] = attacker_fingerprint.group(0)
    policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    (root / "provenance/source-release.allowed_signers").write_text(
        "release-test " + " ".join(attacker_public) + "\n", encoding="utf-8"
    )

    bindings = source_release.bindings_for(root, "HEAD", source_release.load_policy_at(root, "HEAD"))
    message_file = tmp_path / "attacker-tag-message"
    message_file.write_text(bindings.message(), encoding="utf-8")
    run(
        root,
        "git",
        "-c",
        "gpg.format=ssh",
        "-c",
        f"user.signingkey={attacker_key}",
        "tag",
        "-s",
        "-F",
        str(message_file),
        "v1.2.3",
    )

    with pytest.raises(source_release.ReleaseError, match="signature is not trusted"):
        source_release.verify_tag(root, "v1.2.3")


def test_non_head_tag_uses_policy_and_signers_from_tag_commit(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    tag = source_release.create_tag(root, "v1.2.3", confirm=True)
    policy_path = root / "provenance/source-release-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["tag_prefix"] = "release-"
    policy["signer_fingerprint"] = "SHA256:" + "A" * 43
    policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    git(root, "add", str(policy_path.relative_to(root)))
    git(root, "commit", "-m", "change future release policy")

    bindings = source_release.verify_tag(root, tag)

    assert bindings.commit == git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}")


def test_creation_preflight_requires_clean_default_branch(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    (root / "untracked").write_text("dirty", encoding="utf-8")

    with pytest.raises(source_release.ReleaseError, match="completely clean"):
        source_release.require_creation_preflight(root)


def test_creation_preflight_rejects_repointed_origin(release_repo: tuple[Path, Path], tmp_path: Path) -> None:
    root, _ = release_repo
    git(root, "remote", "set-url", "origin", str(tmp_path / "attacker.git"))

    with pytest.raises(source_release.ReleaseError, match="canonical source-release repository"):
        source_release.require_creation_preflight(root)


def test_remote_tag_verification_rejects_repointed_origin(release_repo: tuple[Path, Path], tmp_path: Path) -> None:
    root, _ = release_repo
    tag = source_release.create_tag(root, "v1.2.3", confirm=True)
    git(root, "remote", "set-url", "origin", str(tmp_path / "attacker.git"))

    with pytest.raises(source_release.ReleaseError, match="canonical source-release repository"):
        source_release.verify_tag(root, tag, require_remote=True)


def test_pre_push_stamp_must_match_sensitive_outgoing_commit(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    base = git(root, "rev-parse", "HEAD")
    (root / "plugin/start_mcp.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    git(root, "add", "plugin/start_mcp.sh")
    git(root, "commit", "-m", "sensitive change")
    head = git(root, "rev-parse", "HEAD")
    pre_push.write_stamp(root)

    pre_push.check_updates(root, [f"refs/heads/main {head} refs/heads/main {base}"])

    (root / "plugin/start_mcp.sh").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    git(root, "add", "plugin/start_mcp.sh")
    git(root, "commit", "-m", "later sensitive change")
    stale_head = git(root, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError, match="stamp is stale"):
        pre_push.check_updates(root, [f"refs/heads/main {stale_head} refs/heads/main {base}"])


def test_pre_push_refuses_sensitive_push_with_post_stamp_tracked_drift(
    release_repo: tuple[Path, Path],
) -> None:
    root, _ = release_repo
    base = git(root, "rev-parse", "HEAD")
    script = root / "plugin/start_mcp.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    git(root, "add", str(script.relative_to(root)))
    git(root, "commit", "-m", "sensitive change")
    head = git(root, "rev-parse", "HEAD")
    pre_push.write_stamp(root)
    script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="tracked or index drift"):
        pre_push.check_updates(root, [f"refs/heads/main {head} refs/heads/main {base}"])


def test_pre_push_ignores_non_sensitive_outgoing_commit(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    base = git(root, "rev-parse", "HEAD")
    (root / "notes.txt").write_text("not a release surface\n", encoding="utf-8")
    git(root, "add", "notes.txt")
    git(root, "commit", "-m", "non-sensitive change")
    head = git(root, "rev-parse", "HEAD")

    pre_push.check_updates(root, [f"refs/heads/main {head} refs/heads/main {base}"])


def test_pre_push_refuses_release_tag_deletion(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    deleted = "0" * 40
    remote = git(root, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="refusing to delete"):
        pre_push.check_updates(root, [f"(delete) {deleted} refs/tags/v1.2.3 {remote}"])


def test_pre_push_refuses_existing_remote_tag_update(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    tag = source_release.create_tag(root, "v1.2.3", confirm=True)
    tag_object = git(root, "rev-parse", f"refs/tags/{tag}")

    with pytest.raises(RuntimeError, match="immutable source-release tag"):
        pre_push.check_updates(root, [f"refs/tags/{tag} {tag_object} refs/tags/{tag} {tag_object}"])


def test_pre_push_rejects_unverified_new_tag(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    git(root, "tag", "notes-tag")
    tag_sha = git(root, "rev-parse", "refs/tags/notes-tag")

    with pytest.raises(source_release.ReleaseError, match="tag name/version mismatch|annotated tag object"):
        pre_push.check_updates(root, [f"refs/tags/notes-tag {tag_sha} refs/tags/notes-tag {'0' * 40}"])


def test_pre_push_validates_new_signed_release_tag_and_stamp(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    tag = source_release.create_tag(root, "v1.2.3", confirm=True)
    tag_object = git(root, "rev-parse", f"refs/tags/{tag}")
    update = f"refs/tags/{tag} {tag_object} refs/tags/{tag} {'0' * 40}"

    with pytest.raises(RuntimeError, match="release-sensitive push requires"):
        pre_push.check_updates(root, [update])

    pre_push.write_stamp(root)
    pre_push.check_updates(root, [update])


def test_pre_push_uses_base_policy_when_outgoing_policy_shrinks(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    policy_path = root / "provenance/source-release-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["release_sensitive_paths"].append("legacy-control/")
    (root / "legacy-control").mkdir()
    (root / "legacy-control/state.txt").write_text("safe\n", encoding="utf-8")
    policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "add legacy release control")
    git(root, "push", "origin", "main")
    base = git(root, "rev-parse", "HEAD")

    policy["release_sensitive_paths"].remove("legacy-control/")
    policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    (root / "legacy-control/state.txt").write_text("bypassed\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "shrink outgoing policy")
    head = git(root, "rev-parse", "HEAD")

    prefixes = pre_push.sensitive_paths_for_update(root, base, head)
    assert "legacy-control/" in prefixes
    with pytest.raises(RuntimeError, match="release-sensitive push requires"):
        pre_push.check_updates(root, [f"refs/heads/main {head} refs/heads/main {base}"])


def test_sensitive_prefixes_allow_legacy_base_without_policy(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    empty_tree = git(root, "mktree")
    legacy_base = git(root, "commit-tree", empty_tree, "-m", "legacy base")

    prefixes = pre_push.sensitive_paths_for_update(root, legacy_base, git(root, "rev-parse", "HEAD"))

    assert "plugin/" in prefixes
    assert "tools/gates/dev-check.sh" in prefixes


def test_new_docs_only_branch_does_not_require_release_stamp(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    (root / "notes.txt").write_text("docs only\n", encoding="utf-8")
    git(root, "add", "notes.txt")
    git(root, "commit", "-m", "docs only")
    head = git(root, "rev-parse", "HEAD")

    pre_push.check_updates(root, [f"refs/heads/docs/test {head} refs/heads/docs/test {'0' * 40}"])


def test_stamp_refuses_tracked_build_drift(release_repo: tuple[Path, Path]) -> None:
    root, _ = release_repo
    (root / "plugin/start_mcp.sh").write_text("changed\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="tracked or index drift"):
        pre_push.write_stamp(root)


def test_pre_push_direct_entry_point_loads_release_package() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/release/pre_push.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Require a current local release-gate stamp" in result.stdout
