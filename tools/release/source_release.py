#!/usr/bin/env python3
"""Create and verify immutable, signed Apple Mail source-release tags."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn
from urllib.parse import unquote, urlsplit

import tomllib

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = Path("provenance/source-release-policy.json")
TRAILERS = (
    "Release-Version",
    "Source-Commit",
    "Payload-Inventory-SHA256",
    "Requirements-Lock-SHA256",
    "Wheelhouse-Inventory-SHA256",
)
HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
FULL_COMMIT = re.compile(r"[0-9a-f]{40,64}")
REGULAR_MODES = {"100644", "100755"}
SECRET_CONTENT_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"AWS_SECRET_ACCESS_KEY=",
    b"ANTHROPIC_API_KEY=",
    b"OPENAI_API_KEY=",
    b"GITHUB_TOKEN=",
)
SECRET_ARCHIVE_NAMES = {".env", "credentials.json", "secrets.json", "id_rsa"}
SECRET_ARCHIVE_SUFFIXES = (".key",)
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_MEMBER_SIZE = 100 * 1024 * 1024
MAX_ARCHIVE_TOTAL_SIZE = 500 * 1024 * 1024


class ReleaseError(RuntimeError):
    """A source release failed closed."""


@dataclass(frozen=True)
class Policy:
    plugin_id: str
    repository: str
    default_branch: str
    tag_prefix: str
    payload_root: str
    payload_contract: str
    version_file: str
    lock_file: str
    wheelhouse_root: str
    trusted_signers: str
    signer_principal: str
    signer_fingerprint: str


@dataclass(frozen=True)
class ReleaseBindings:
    version: str
    commit: str
    payload_inventory_sha256: str
    requirements_lock_sha256: str
    wheelhouse_inventory_sha256: str

    def message(self) -> str:
        return (
            f"Apple Mail MCP v{self.version}\n\n"
            f"Release-Version: {self.version}\n"
            f"Source-Commit: {self.commit}\n"
            f"Payload-Inventory-SHA256: {self.payload_inventory_sha256}\n"
            f"Requirements-Lock-SHA256: {self.requirements_lock_sha256}\n"
            f"Wheelhouse-Inventory-SHA256: {self.wheelhouse_inventory_sha256}\n"
        )


def fail(message: str) -> NoReturn:
    raise ReleaseError(message)


def git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        fail(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def ref_exists(root: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "show-ref", "--verify", "--quiet", ref],
        check=False,
        capture_output=True,
    )
    if result.returncode not in (0, 1):
        fail(f"could not determine whether ref exists: {ref}")
    return result.returncode == 0


def load_policy(root: Path) -> Policy:
    raw = json.loads((root / POLICY_PATH).read_text(encoding="utf-8"))
    return parse_policy(raw)


def load_policy_at(root: Path, commit: str) -> Policy:
    raw = json.loads(blob(root, commit, POLICY_PATH.as_posix()).decode("utf-8"))
    return parse_policy(raw)


def parse_policy(raw: object) -> Policy:
    if not isinstance(raw, dict):
        fail("source-release policy must be a JSON object")
    if raw.get("schema_version") != 1:
        fail("unsupported source-release policy schema")
    return Policy(**{field: raw[field] for field in Policy.__dataclass_fields__})


def canonical_repository(root: Path, repository: str) -> str:
    """Normalize equivalent Git transports to one repository identity."""
    value = repository.strip()
    if not value:
        fail("source-release policy has no canonical repository")
    scp = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", value)
    if scp and "://" not in value:
        host, path = scp.groups()
        return f"network:{host.lower()}/{path.removesuffix('.git').strip('/')}"
    parsed = urlsplit(value)
    if parsed.scheme and parsed.scheme != "file":
        if not parsed.hostname:
            fail(f"repository URL has no unambiguous host: {repository}")
        path = unquote(parsed.path).removesuffix(".git").strip("/")
        if not path:
            fail(f"repository URL has no unambiguous path: {repository}")
        return f"network:{parsed.hostname.lower()}/{path}"
    local = Path(unquote(parsed.path) if parsed.scheme == "file" else value).expanduser()
    if not local.is_absolute():
        local = root / local
    return f"file:{local.resolve()}"


def verify_origin_repository(root: Path, policy: Policy) -> None:
    configured = git(root, "remote", "get-url", "origin")
    if canonical_repository(root, configured) != canonical_repository(root, policy.repository):
        fail("origin does not match the canonical source-release repository")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob(root: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{path}"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        fail(f"release commit is missing required file: {path}")
    return result.stdout


def tree_inventory(root: Path, commit: str, subtree: str) -> tuple[str, list[dict[str, object]]]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "-z", "--full-tree", commit, "--", subtree],
        check=True,
        capture_output=True,
    )
    entries: list[dict[str, object]] = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        metadata, separator, path_bytes = record.partition(b"\t")
        if not separator:
            fail("ambiguous git tree record")
        mode, object_type, object_id = metadata.decode("ascii").split()
        path = path_bytes.decode("utf-8")
        if object_type != "blob" or mode not in REGULAR_MODES:
            fail(f"release payload must contain regular files only: {path}")
        content = blob(root, commit, path)
        entries.append(
            {
                "mode": mode,
                "path": path,
                "git_object": object_id,
                "size": len(content),
                "sha256": sha256(content),
            }
        )
    if not entries:
        fail(f"release inventory is empty: {subtree}")
    canonical = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return sha256(canonical), entries


def glob_matches(path: str, pattern: str) -> bool:
    path_parts = PurePosixPath(path).parts
    pattern_parts = PurePosixPath(pattern).parts

    def match(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        token = pattern_parts[pattern_index]
        if token == "**":
            return match(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and match(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], token)
            and match(path_index + 1, pattern_index + 1)
        )

    return match(0, 0)


def validate_wheel(path: str, content: bytes) -> None:
    """Fail closed on unsafe archive structure, names, or decompressed secrets."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                fail(f"wheel contains too many members: {path}")
            total_size = 0
            for member in members:
                member_path = PurePosixPath(member.filename)
                if (
                    not member.filename
                    or member_path.is_absolute()
                    or "\\" in member.filename
                    or ".." in member_path.parts
                ):
                    fail(f"wheel contains unsafe member path: {path}:{member.filename}")
                total_size += member.file_size
                if member.file_size > MAX_ARCHIVE_MEMBER_SIZE or total_size > MAX_ARCHIVE_TOTAL_SIZE:
                    fail(f"wheel exceeds decompression limits: {path}")
                basename = member_path.name.lower()
                if basename in SECRET_ARCHIVE_NAMES or basename.endswith(SECRET_ARCHIVE_SUFFIXES):
                    fail(f"wheel contains secret-like filename: {path}:{member.filename}")
                if member.is_dir():
                    continue
                try:
                    extracted = archive.read(member)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    fail(f"cannot inspect wheel member {path}:{member.filename}: {exc}")
                if any(marker in extracted for marker in SECRET_CONTENT_MARKERS):
                    fail(f"secret-like content marker in wheel member: {path}:{member.filename}")
    except zipfile.BadZipFile:
        fail(f"payload wheel is not a valid ZIP archive: {path}")


def promoted_inventory(root: Path, commit: str, policy: Policy) -> dict[str, object]:
    """Build the same canonical inventory as tools/marketplace_payload.py, at a commit."""
    contract = json.loads(blob(root, commit, policy.payload_contract).decode("utf-8"))
    if (
        contract.get("schema_version") != 1
        or contract.get("plugin_id") != policy.plugin_id
        or contract.get("source_root") != policy.payload_root
    ):
        fail("marketplace payload contract does not match source-release policy")
    pattern_fields: dict[str, list[str]] = {}
    for field in ("include", "required", "exclude", "forbidden"):
        value = contract.get(field)
        if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
            fail(f"marketplace payload contract has invalid {field} patterns")
        pattern_fields[field] = value
    authority = contract.get("authority")
    if not isinstance(authority, dict) or authority.get("grants_marketplace_admission") is not False:
        fail("source payload contract must not grant marketplace admission")
    if not isinstance(authority.get("statement"), str) or not authority["statement"].strip():
        fail("source payload contract must state its evidence-only authority boundary")

    _, tracked = tree_inventory(root, commit, policy.payload_root)
    prefix = f"{policy.payload_root}/"
    entries: list[dict[str, object]] = []
    for tracked_entry in tracked:
        repo_path = str(tracked_entry["path"])
        if not repo_path.startswith(prefix):
            fail(f"payload inventory escaped source root: {repo_path}")
        path = repo_path.removeprefix(prefix)
        forbidden = any(glob_matches(path, pattern) for pattern in pattern_fields["forbidden"])
        included = any(glob_matches(path, pattern) for pattern in pattern_fields["include"])
        excluded = any(glob_matches(path, pattern) for pattern in pattern_fields["exclude"])
        if forbidden:
            fail(f"forbidden tracked source file: {repo_path}")
        if included and excluded:
            included = False
        if not included and not excluded:
            fail(f"unclassified tracked source file: {repo_path}")
        if included:
            content = blob(root, commit, repo_path)
            if any(marker in content for marker in SECRET_CONTENT_MARKERS):
                fail(f"secret-like content marker in promoted payload: {repo_path}")
            if path.endswith(".whl"):
                validate_wheel(repo_path, content)
            entries.append(
                {
                    "path": path,
                    "sha256": tracked_entry["sha256"],
                    "size": tracked_entry["size"],
                    "mode": tracked_entry["mode"],
                }
            )
    entry_paths = [str(entry["path"]) for entry in entries]
    for pattern in pattern_fields["required"]:
        if not any(glob_matches(path, pattern) for path in entry_paths):
            fail(f"required promoted payload path is missing: {pattern}")
    if not entries:
        fail("promoted payload inventory is empty")
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    wheel_entries = [entry for entry in entries if str(entry["path"]).startswith("wheelhouse/")]
    canonical_wheels = json.dumps(wheel_entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    lock_hashes = [str(entry["sha256"]) for entry in entries if entry["path"] == "requirements.lock"]
    if len(lock_hashes) != 1 or not wheel_entries:
        fail("promoted payload must bind one requirements lock and a non-empty wheelhouse")
    return {
        "payload_sha256": sha256(canonical),
        "requirements_lock_sha256": lock_hashes[0],
        "wheelhouse_sha256": sha256(canonical_wheels),
    }


def version_at(root: Path, commit: str, policy: Policy) -> str:
    parsed = tomllib.loads(blob(root, commit, policy.version_file).decode("utf-8"))
    version = parsed.get("project", {}).get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        fail("release version must be an unambiguous semantic version")
    return version


def bindings_for(root: Path, commit: str, policy: Policy) -> ReleaseBindings:
    commit = git(root, "rev-parse", f"{commit}^{{commit}}")
    inventory = promoted_inventory(root, commit, policy)
    lock_content = blob(root, commit, policy.lock_file)
    if b"--hash=sha256:" not in lock_content:
        fail("requirements lock is not hash-pinned")
    if sha256(lock_content) != inventory["requirements_lock_sha256"]:
        fail("promoted requirements-lock binding drift")
    return ReleaseBindings(
        version=version_at(root, commit, policy),
        commit=commit,
        payload_inventory_sha256=str(inventory["payload_sha256"]),
        requirements_lock_sha256=sha256(lock_content),
        wheelhouse_inventory_sha256=str(inventory["wheelhouse_sha256"]),
    )


def trusted_signers_bytes(root: Path, policy: Policy, commit: str | None = None) -> bytes:
    trusted_path = PurePosixPath(policy.trusted_signers)
    if trusted_path.is_absolute() or ".." in trusted_path.parts or trusted_path.as_posix() in ("", "."):
        fail("trusted signers file must be a safe repository-relative path")
    if commit is not None:
        return blob(root, commit, trusted_path.as_posix())
    allowed = (root / trusted_path.as_posix()).resolve()
    try:
        allowed.relative_to(root.resolve())
    except ValueError:
        fail("trusted signers file must be checked into this repository")
    return allowed.read_bytes()


def validate_trust_root(root: Path, policy: Policy, commit: str | None = None) -> bytes:
    content = trusted_signers_bytes(root, policy, commit)
    lines = [
        line for line in content.decode("utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    prefix = f"{policy.signer_principal} ssh-ed25519 "
    if len(lines) != 1 or not lines[0].startswith(prefix):
        fail("trusted signers file must contain exactly one approved SSH signer")
    with tempfile.NamedTemporaryFile("wb") as allowed:
        allowed.write(content)
        allowed.flush()
        listing = subprocess.run(["ssh-keygen", "-lf", allowed.name], check=True, capture_output=True, text=True).stdout
    fingerprints = re.findall(r"SHA256:[A-Za-z0-9+/=]+", listing)
    if fingerprints != [policy.signer_fingerprint]:
        fail("source-release signer fingerprint is ambiguous or has drifted")
    return content


def tag_message(root: Path, tag: str) -> str:
    return git(root, "for-each-ref", f"refs/tags/{tag}", "--format=%(contents)")


def parse_bindings(message: str) -> dict[str, str]:
    parsed: dict[str, list[str]] = {key: [] for key in TRAILERS}
    for line in message.splitlines():
        key, separator, value = line.partition(":")
        if separator and key in parsed:
            parsed[key].append(value.strip())
    if any(len(values) != 1 for values in parsed.values()):
        fail("release tag has missing or ambiguous binding trailers")
    values = {key: found[0] for key, found in parsed.items()}
    if not FULL_COMMIT.fullmatch(values["Source-Commit"]):
        fail("release tag contains an invalid source commit binding")
    for key in TRAILERS[2:]:
        if not HEX_SHA256.fullmatch(values[key]):
            fail(f"release tag contains an invalid {key} binding")
    return values


def verify_tag(root: Path, tag: str, *, require_remote: bool = False) -> ReleaseBindings:
    ref = f"refs/tags/{tag}"
    if git(root, "cat-file", "-t", ref) != "tag":
        fail("source release must be an annotated tag object")
    tag_object = git(root, "rev-parse", f"{ref}^{{tag}}")
    raw_headers = git(root, "cat-file", "tag", tag_object).split("\n\n", 1)[0].splitlines()
    headers = dict(line.split(" ", 1) for line in raw_headers if " " in line)
    if headers.get("type") != "commit":
        fail("release tag must point directly to a commit, not another tag")
    commit = git(root, "rev-parse", f"{ref}^{{commit}}")
    if headers.get("object") != commit:
        fail("release tag peeled commit is ambiguous")
    policy = load_policy_at(root, commit)
    if require_remote:
        verify_origin_repository(root, policy)
    expected_tag = f"{policy.tag_prefix}{version_at(root, commit, policy)}"
    if tag != expected_tag:
        fail(f"tag name/version mismatch: expected {expected_tag}")
    trusted_signers = validate_trust_root(root, policy, commit)
    with tempfile.NamedTemporaryFile("wb") as allowed:
        allowed.write(trusted_signers)
        allowed.flush()
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "gpg.format=ssh",
                "-c",
                f"gpg.ssh.allowedSignersFile={allowed.name}",
                "verify-tag",
                "--raw",
                tag,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        fail("source release tag signature is not trusted")
    expected = bindings_for(root, commit, policy)
    actual = parse_bindings(tag_message(root, tag))
    expected_values = {
        "Release-Version": expected.version,
        "Source-Commit": expected.commit,
        "Payload-Inventory-SHA256": expected.payload_inventory_sha256,
        "Requirements-Lock-SHA256": expected.requirements_lock_sha256,
        "Wheelhouse-Inventory-SHA256": expected.wheelhouse_inventory_sha256,
    }
    if actual != expected_values:
        fail("release tag bindings do not match the peeled source commit")
    if require_remote:
        direct = git(root, "ls-remote", "origin", ref).splitlines()
        peeled = git(root, "ls-remote", "origin", f"{ref}^{{}}").splitlines()
        if len(direct) != 1 or direct[0].split()[0] != tag_object:
            fail("remote release tag object is missing or ambiguous")
        if len(peeled) != 1 or peeled[0].split()[0] != commit:
            fail("remote release peeled commit is missing or ambiguous")
    return expected


def require_creation_preflight(root: Path, tag: str | None = None) -> tuple[Policy, ReleaseBindings, str]:
    policy = load_policy(root)
    verify_origin_repository(root, policy)
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        fail("release tag creation requires a completely clean checkout")
    branch = git(root, "symbolic-ref", "--short", "HEAD")
    if branch != policy.default_branch:
        fail(f"release tag creation requires branch {policy.default_branch}")
    head = git(root, "rev-parse", "HEAD")
    remote_head = git(root, "rev-parse", f"refs/remotes/origin/{policy.default_branch}")
    if head != remote_head:
        fail("release tag creation requires HEAD to equal the fetched default branch")
    advertised_heads = git(root, "ls-remote", "origin", f"refs/heads/{policy.default_branch}").splitlines()
    if len(advertised_heads) != 1 or advertised_heads[0].split()[0] != head:
        fail("release tag creation requires HEAD to equal the live remote default branch")
    bindings = bindings_for(root, head, policy)
    expected_tag = f"{policy.tag_prefix}{bindings.version}"
    if tag is not None and tag != expected_tag:
        fail(f"requested tag does not match project version: expected {expected_tag}")
    if ref_exists(root, f"refs/tags/{expected_tag}"):
        fail(f"local tag already exists: {expected_tag}")
    remote = git(root, "ls-remote", "origin", f"refs/tags/{expected_tag}*")
    if remote:
        fail(f"remote tag already exists or is ambiguous: {expected_tag}")
    validate_trust_root(root, policy)
    return policy, bindings, expected_tag


def create_tag(root: Path, requested_tag: str | None, confirm: bool) -> str:
    _, bindings, tag = require_creation_preflight(root, requested_tag)
    if not confirm:
        fail("refusing tag creation without --confirm-create")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as message:
        message.write(bindings.message())
        message.flush()
        result = subprocess.run(
            ["git", "-C", str(root), "-c", "gpg.format=ssh", "tag", "-s", "-F", message.name, tag],
            check=False,
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        fail(f"signed tag creation failed: {result.stderr.strip()}")
    try:
        verify_tag(root, tag)
    except ReleaseError:
        subprocess.run(["git", "-C", str(root), "tag", "-d", tag], check=False, capture_output=True)
        raise
    return tag


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    subcommands = parser.add_subparsers(dest="command", required=True)
    verify = subcommands.add_parser("verify-tag")
    verify.add_argument("tag")
    verify.add_argument("--require-remote", action="store_true")
    preflight = subcommands.add_parser("preflight-create")
    preflight.add_argument("tag", nargs="?")
    create = subcommands.add_parser("create-tag")
    create.add_argument("tag", nargs="?")
    create.add_argument("--confirm-create", action="store_true")
    validate = subcommands.add_parser("validate-tree")
    validate.add_argument("commit", nargs="?", default="HEAD")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "verify-tag":
            bindings = verify_tag(root, args.tag, require_remote=args.require_remote)
            print(f"source release verified: v{bindings.version} -> {bindings.commit}")
        elif args.command == "preflight-create":
            _, bindings, tag = require_creation_preflight(root, args.tag)
            print(f"source release preflight passed: {tag} -> {bindings.commit}")
        elif args.command == "create-tag":
            tag = create_tag(root, args.tag, args.confirm_create)
            print(f"created and locally verified signed tag: {tag}")
        else:
            policy = load_policy(root)
            bindings = bindings_for(root, args.commit, policy)
            validate_trust_root(root, policy)
            print(f"source release tree validated: {bindings.commit}")
    except (ReleaseError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
