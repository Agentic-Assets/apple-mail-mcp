#!/usr/bin/env python3
"""Build deterministic evidence for a source-owned marketplace payload."""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

CONTRACT_PATH = Path("distribution/marketplace-payload.json")
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


class PayloadContractError(ValueError):
    """Raised when the payload contract or source tree is unsafe or incomplete."""


@dataclass(frozen=True)
class TrackedFile:
    path: str
    mode: str


def _inspect_wheel(path: str, content: bytes) -> list[str]:
    """Return fail-closed errors for unsafe names or content inside a wheel."""
    errors: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                return [f"wheel contains too many members: {path}"]
            total_size = 0
            for member in members:
                member_path = PurePosixPath(member.filename)
                if (
                    not member.filename
                    or member_path.is_absolute()
                    or "\\" in member.filename
                    or ".." in member_path.parts
                ):
                    errors.append(f"wheel contains unsafe member path: {path}:{member.filename}")
                    continue
                total_size += member.file_size
                if member.file_size > MAX_ARCHIVE_MEMBER_SIZE or total_size > MAX_ARCHIVE_TOTAL_SIZE:
                    errors.append(f"wheel exceeds decompression limits: {path}")
                    break
                basename = member_path.name.lower()
                if basename in SECRET_ARCHIVE_NAMES or basename.endswith(SECRET_ARCHIVE_SUFFIXES):
                    errors.append(f"wheel contains secret-like filename: {path}:{member.filename}")
                    continue
                if member.is_dir():
                    continue
                try:
                    extracted = archive.read(member)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    errors.append(f"cannot inspect wheel member {path}:{member.filename}: {exc}")
                    continue
                markers = [marker.decode("ascii") for marker in SECRET_CONTENT_MARKERS if marker in extracted]
                if markers:
                    errors.append(
                        f"secret-like content marker in wheel member {path}:{member.filename}: " + ", ".join(markers)
                    )
    except zipfile.BadZipFile:
        errors.append(f"payload wheel is not a valid ZIP archive: {path}")
    return errors


def _is_safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and "\\" not in value and ".." not in path.parts


def _glob_matches(path: str, pattern: str) -> bool:
    """Match slash-separated globs, with ``**`` consuming any path segments."""
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


def _validate_patterns(name: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise PayloadContractError(f"{name} must be a non-empty array")
    if any(not isinstance(item, str) or not _is_safe_relative(item) for item in value):
        raise PayloadContractError(f"{name} contains an unsafe or non-string pattern")
    if len(value) != len(set(value)):
        raise PayloadContractError(f"{name} contains duplicate patterns")
    return value


def load_contract(repo_root: Path, contract_path: Path = CONTRACT_PATH) -> dict[str, Any]:
    """Load and structurally validate the source payload contract."""
    path = repo_root / contract_path
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PayloadContractError(f"cannot read {contract_path}: {exc}") from exc
    if not isinstance(contract, dict) or contract.get("schema_version") != 1:
        raise PayloadContractError("schema_version must be 1")
    for field in ("plugin_id", "source_root", "marketplace_destination"):
        value = contract.get(field)
        if not isinstance(value, str) or not _is_safe_relative(value):
            raise PayloadContractError(f"{field} must be a safe relative path or identifier")
    if "/" in contract["plugin_id"]:
        raise PayloadContractError("plugin_id must be one path segment")
    for field in ("include", "required", "exclude", "forbidden"):
        contract[field] = _validate_patterns(field, contract.get(field))
    authority = contract.get("authority")
    if not isinstance(authority, dict) or authority.get("grants_marketplace_admission") is not False:
        raise PayloadContractError("authority.grants_marketplace_admission must be false")
    if not isinstance(authority.get("statement"), str) or not authority["statement"].strip():
        raise PayloadContractError("authority.statement must explain the evidence boundary")
    return contract


def _tracked_files(repo_root: Path, source_root: str) -> list[TrackedFile]:
    result = subprocess.run(
        ["git", "ls-files", "--stage", "-z", "--", source_root],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    prefix = f"{source_root}/"
    tracked: list[TrackedFile] = []
    for record in result.stdout.decode("utf-8", errors="strict").split("\0"):
        if not record:
            continue
        metadata, repo_path = record.split("\t", 1)
        mode = metadata.split(" ", 1)[0]
        if not repo_path.startswith(prefix):
            raise PayloadContractError(f"git returned path outside source_root: {repo_path}")
        relative = repo_path.removeprefix(prefix)
        if not _is_safe_relative(relative):
            raise PayloadContractError(f"unsafe tracked path: {repo_path}")
        tracked.append(TrackedFile(relative, mode))
    return sorted(tracked, key=lambda item: item.path.encode("utf-8"))


def build_inventory(repo_root: Path, contract: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate classification and return a deterministic content inventory."""
    contract = contract or load_contract(repo_root)
    source_root = contract["source_root"]
    includes = contract["include"]
    required = contract["required"]
    excludes = contract["exclude"]
    forbidden = contract["forbidden"]
    entries: list[dict[str, Any]] = []
    errors: list[str] = []

    for tracked in _tracked_files(repo_root, source_root):
        path = tracked.path
        if any(_glob_matches(path, pattern) for pattern in forbidden):
            errors.append(f"forbidden tracked source file: {source_root}/{path}")
            continue
        included = any(_glob_matches(path, pattern) for pattern in includes)
        excluded = any(_glob_matches(path, pattern) for pattern in excludes)
        if included and excluded:
            # Explicit exclusions win for files nested below an included runtime tree.
            included = False
        if not included and not excluded:
            errors.append(f"unclassified tracked source file: {source_root}/{path}")
            continue
        if excluded:
            continue
        full_path = repo_root / source_root / path
        relative_to_repo = PurePosixPath(source_root) / path
        cursor = repo_root
        has_symlink_component = False
        for component in relative_to_repo.parts:
            cursor /= component
            if cursor.is_symlink():
                has_symlink_component = True
                break
        if tracked.mode not in REGULAR_MODES or has_symlink_component:
            errors.append(f"symlinks and non-regular files are forbidden: {source_root}/{path}")
            continue
        try:
            content = full_path.read_bytes()
        except OSError as exc:
            errors.append(f"cannot read payload file {source_root}/{path}: {exc}")
            continue
        markers = [marker.decode("ascii") for marker in SECRET_CONTENT_MARKERS if marker in content]
        if markers:
            errors.append(f"secret-like content marker in payload file {source_root}/{path}: " + ", ".join(markers))
            continue
        if path.endswith(".whl"):
            wheel_errors = _inspect_wheel(path, content)
            if wheel_errors:
                errors.extend(wheel_errors)
                continue
        entries.append(
            {
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "mode": tracked.mode,
            }
        )

    entry_paths = [entry["path"] for entry in entries]
    for pattern in required:
        if not any(_glob_matches(path, pattern) for path in entry_paths):
            errors.append(f"required payload path or tree is missing: {source_root}/{pattern}")
    if not entries:
        errors.append("payload contains no promotable files")
    if errors:
        raise PayloadContractError("\n".join(errors))

    canonical_entries = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    canonical_contract = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    wheelhouse_entries = [entry for entry in entries if entry["path"].startswith("wheelhouse/")]
    canonical_wheelhouse = json.dumps(wheelhouse_entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    requirements_lock = next(
        (entry["sha256"] for entry in entries if entry["path"] == "requirements.lock"),
        None,
    )
    return {
        "schema_version": 1,
        "plugin_id": contract["plugin_id"],
        "source_root": source_root,
        "marketplace_destination": contract["marketplace_destination"],
        "grants_marketplace_admission": False,
        "contract_sha256": hashlib.sha256(canonical_contract).hexdigest(),
        "file_count": len(entries),
        "payload_sha256": hashlib.sha256(canonical_entries).hexdigest(),
        "requirements_lock_sha256": requirements_lock,
        "wheelhouse_file_count": len(wheelhouse_entries),
        "wheelhouse_sha256": hashlib.sha256(canonical_wheelhouse).hexdigest(),
        "files": entries,
    }


def inventory_json(inventory: dict[str, Any]) -> str:
    """Serialize inventory in the one canonical human-readable form."""
    return json.dumps(inventory, indent=2, sort_keys=True) + "\n"
