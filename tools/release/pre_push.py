#!/usr/bin/env python3
"""Require a current local release-gate stamp for sensitive outgoing changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from release import source_release

ROOT = Path(__file__).resolve().parents[2]
POLICY = Path("provenance/source-release-policy.json")
IMMUTABLE_SENSITIVE_PREFIXES = (
    ".agents/plugins/",
    ".claude-plugin/",
    ".githooks/",
    "apple-mail-mcpb/",
    "distribution/",
    "plugin/",
    "provenance/",
    "pyproject.toml",
    "server.json",
    "skills-lock.json",
    "tools/gates/build-artifacts.sh",
    "tools/gates/build-wheelhouse.sh",
    "tools/gates/create-release-tag.sh",
    "tools/gates/dev-check.sh",
    "tools/gates/install-git-hooks.sh",
    "tools/gates/refresh-central-marketplace.sh",
    "tools/gates/refresh-local-plugins.sh",
    "tools/gates/source-release-gate.sh",
    "tools/gates/validate-codex-plugin.sh",
    "tools/manifest_checks/",
    "tools/marketplace_identity.json",
    "tools/marketplace_payload.py",
    "tools/release/",
    "tools/validators/validate_marketplace_payload.py",
    "tools/validators/validate_repo_root.py",
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def blob(root: Path, commit: str, path: Path) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{path.as_posix()}"],
        check=True,
        capture_output=True,
    ).stdout


def policy_digest(root: Path, commit: str = "HEAD") -> str:
    return hashlib.sha256(blob(root, commit, POLICY)).hexdigest()


def tree(root: Path, commit: str = "HEAD") -> str:
    return git(root, "rev-parse", f"{commit}^{{tree}}")


def stamp_path(root: Path) -> Path:
    git_path = git(root, "rev-parse", "--git-path", "aa-gates/source-release.json")
    path = Path(git_path)
    return path if path.is_absolute() else root / path


def write_stamp(root: Path) -> None:
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("refusing to stamp a release gate with tracked or index drift")
    path = stamp_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "head": git(root, "rev-parse", "HEAD"),
        "tree": tree(root),
        "policy_sha256": policy_digest(root, "HEAD"),
    }
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    print(f"source-release gate stamped tree {payload['tree']}")


def require_stamp(root: Path, commit: str) -> None:
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("release-sensitive push refused with tracked or index drift")
    path = stamp_path(root)
    if not path.is_file():
        raise RuntimeError("release-sensitive push requires: bash tools/gates/source-release-gate.sh")
    stamp = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version": 1,
        "head": commit,
        "tree": tree(root, commit),
        "policy_sha256": policy_digest(root, commit),
    }
    if stamp != expected:
        raise RuntimeError("source-release gate stamp is stale; rerun bash tools/gates/source-release-gate.sh")


def policy_at(root: Path, commit: str, *, allow_missing: bool = False) -> dict[str, object] | None:
    try:
        content = blob(root, commit, POLICY)
    except subprocess.CalledProcessError:
        if allow_missing:
            return None
        raise RuntimeError("outgoing commit is missing the source-release policy") from None
    policy = json.loads(content)
    if not isinstance(policy, dict):
        raise RuntimeError("source-release path policy is not an object")
    return policy


def sensitive_prefixes(root: Path, *commits: str) -> list[str]:
    combined = set(IMMUTABLE_SENSITIVE_PREFIXES)
    for index, commit in enumerate(commits):
        policy = policy_at(root, commit, allow_missing=index < len(commits) - 1)
        if policy is None:
            continue
        paths = policy.get("release_sensitive_paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
            raise RuntimeError("release-sensitive path policy is missing or ambiguous")
        combined.update(paths)
    return sorted(combined)


def comparison_base(root: Path, remote_sha: str, local_sha: str) -> str:
    zero = "0" * len(local_sha)
    if remote_sha != zero:
        return remote_sha
    policy = policy_at(root, local_sha)
    assert policy is not None
    default_branch = policy.get("default_branch")
    if not isinstance(default_branch, str) or not default_branch:
        raise RuntimeError("source-release policy has no unambiguous default branch")
    base_lines = git(root, "merge-base", local_sha, f"refs/remotes/origin/{default_branch}").splitlines()
    if len(base_lines) != 1:
        raise RuntimeError("cannot determine one merge-base for a new remote ref")
    return base_lines[0]


def sensitive_paths_for_update(root: Path, base: str, local_sha: str) -> list[str]:
    return sensitive_prefixes(root, base, local_sha)


def changed_paths(root: Path, remote_sha: str, local_sha: str) -> list[str]:
    base = comparison_base(root, remote_sha, local_sha)
    return git(root, "diff", "--name-only", base, local_sha).splitlines()


def is_sensitive(path: str, prefixes: list[str]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def check_updates(root: Path, lines: list[str]) -> None:
    for line in lines:
        fields = line.split()
        if len(fields) != 4:
            raise RuntimeError("ambiguous pre-push update record")
        local_ref, local_sha, remote_ref, remote_sha = fields
        if set(local_sha) == {"0"}:
            if remote_ref.startswith("refs/tags/"):
                raise RuntimeError("refusing to delete an immutable source-release tag")
            continue
        if remote_ref.startswith("refs/tags/"):
            if not local_ref.startswith("refs/tags/") or local_ref != remote_ref:
                raise RuntimeError("ambiguous source-release tag update")
            if set(remote_sha) != {"0"}:
                raise RuntimeError("refusing to update an immutable source-release tag")
            tag = remote_ref.removeprefix("refs/tags/")
            if git(root, "rev-parse", local_ref) != local_sha:
                raise RuntimeError("outgoing source-release tag object is ambiguous")
            bindings = source_release.verify_tag(root, tag)
            require_stamp(root, bindings.commit)
            continue
        if local_ref.startswith("refs/tags/"):
            raise RuntimeError("ambiguous source-release tag destination")
        commit = git(root, "rev-parse", f"{local_sha}^{{commit}}")
        base = comparison_base(root, remote_sha, commit)
        prefixes = sensitive_paths_for_update(root, base, commit)
        paths = git(root, "diff", "--name-only", base, commit).splitlines()
        if any(is_sensitive(path, prefixes) for path in paths):
            require_stamp(root, commit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("stamp", "check"))
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        if args.command == "stamp":
            write_stamp(args.root.resolve())
        else:
            check_updates(args.root.resolve(), sys.stdin.read().splitlines())
    except (RuntimeError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
