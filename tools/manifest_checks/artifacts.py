"""Distributable-artifact checks: plugin zip / .plugin parity, MCPB freshness,
archive structural integrity, and stale-artifact pruning."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

from manifest_checks import common


def _is_excluded_payload_file(rel: Path) -> bool:
    excluded_parts = {"venv", "__pycache__"}
    if any(part in excluded_parts for part in rel.parts):
        return True
    if rel.name in {".DS_Store", "CLAUDE.md"}:
        return True
    if rel.name == ".env" or rel.name.startswith(".env."):
        return True
    return rel.suffix in {".pyc", ".log", ".tmp", ".bak", ".swp"}


def _tracked_plugin_files(plugin_root: Path) -> list[Path] | None:
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "plugin",
            ],
            cwd=common.ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    if not result.stdout:
        return None

    rels: list[Path] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = Path(raw.decode())
        try:
            rel = path.relative_to("plugin")
        except ValueError:
            continue
        full_path = plugin_root / rel
        if full_path.is_file():
            rels.append(rel)
    if not rels and plugin_root.exists():
        return None
    return rels


def _iter_plugin_payload_files() -> list[Path]:
    """Return plugin files expected to be byte-identical in apple-mail-plugin.zip."""
    plugin_root = common.ROOT / "plugin"
    tracked = _tracked_plugin_files(plugin_root)
    if tracked is not None:
        return sorted(rel for rel in tracked if not _is_excluded_payload_file(rel))

    files: list[Path] = []
    for path in plugin_root.rglob("*"):
        rel = path.relative_to(plugin_root)
        if path.is_dir():
            continue
        if _is_excluded_payload_file(rel):
            continue
        files.append(rel)
    return sorted(files)


def _compare_zip_members(
    archive: Path,
    expected: list[tuple[Path, str]],
    label: str,
    errors: list[str],
    *,
    require_present: bool = False,
    exact_members: bool = False,
    allowed_extra_members: set[str] | None = None,
) -> None:
    """Compare selected repo files to their distributable archive members."""
    if not archive.exists():
        if require_present:
            errors.append(f"{label}: missing archive; rebuild {archive.name}")
        return

    try:
        with zipfile.ZipFile(archive) as zf:
            all_names = zf.namelist()
            names = set(all_names)
            expected_names = {member for _, member in expected}
            duplicates = sorted(name for name in names if all_names.count(name) > 1)
            for duplicate in duplicates:
                errors.append(f"{label}: duplicate member {duplicate}; rebuild {archive.name}")
            for source, member in expected:
                if member not in names:
                    errors.append(f"{label}: missing {member}")
                    continue
                if source.read_bytes() != zf.read(member):
                    errors.append(f"{label}: stale {member}; rebuild {archive.name}")
            if exact_members:
                allowed = allowed_extra_members or set()
                unexpected = sorted(name for name in names - expected_names - allowed if not name.endswith("/"))
                if unexpected:
                    sample = ", ".join(unexpected[:3]) + (", ..." if len(unexpected) > 3 else "")
                    errors.append(f"{label}: unexpected {sample}; rebuild {archive.name}")
    except zipfile.BadZipFile:
        errors.append(f"{label}: {archive.name} is not a valid zip archive")


def _check_plugin_file_parity(
    root: Path,
    errors: list[str],
    *,
    require_present: bool = False,
) -> None:
    """Enforce that apple-mail.plugin is the byte-identical twin of the zip.

    Cowork's "Customize → Add plugin → Upload plugin" UI accepts the .plugin
    extension; the same payload bytes must ship under both names so the
    Claude Code marketplace zip and the Cowork upload stay in lock-step.
    Drift between the two has been a real installer-confusion bug.
    """
    zip_path = root / "apple-mail-plugin.zip"
    plugin_path = root / "apple-mail.plugin"

    if not plugin_path.exists():
        if require_present:
            errors.append(
                "apple-mail.plugin: missing artifact; rebuild via "
                "tools/gates/build-artifacts.sh (Cowork upload needs the .plugin "
                "extension alongside apple-mail-plugin.zip)"
            )
        return

    _check_no_directory_entries(plugin_path, "apple-mail.plugin", errors)

    if not zip_path.exists():
        errors.append(
            "apple-mail.plugin: present but apple-mail-plugin.zip is missing; "
            "both must ship together — rebuild via tools/gates/build-artifacts.sh"
        )
        return

    if zip_path.read_bytes() != plugin_path.read_bytes():
        errors.append(
            "apple-mail.plugin: bytes diverge from apple-mail-plugin.zip; "
            "rebuild via tools/gates/build-artifacts.sh (the .plugin file must be a "
            "byte-identical copy of the .zip artifact)"
        )


def _check_no_directory_entries(
    archive: Path,
    label: str,
    errors: list[str],
) -> None:
    """Reject zero-byte directory entries — `mcpb unpack` / Claude Desktop choke on them.

    Why: raw `zip -r .` emits entries whose names end in `/`. The MCPB extractor
    treats those as files and aborts with ENOENT. Always build via `mcpb pack`
    (or `zip -X -D` for raw archives).
    """
    if not archive.exists():
        return
    try:
        with zipfile.ZipFile(archive) as zf:
            offenders = [n for n in zf.namelist() if n.endswith("/")]
    except zipfile.BadZipFile:
        return  # already reported by _compare_zip_members
    if offenders:
        sample = ", ".join(offenders[:3]) + (", ..." if len(offenders) > 3 else "")
        errors.append(
            f"{label}: contains {len(offenders)} directory entr"
            f"{'y' if len(offenders) == 1 else 'ies'} ({sample}); "
            f"rebuild without directory entries (`zip -D` or `mcpb pack`; "
            f"Claude/Cowork installers fail on these)"
        )


def _generated_mcpb_readme() -> bytes:
    return """# Apple Mail MCP bundle

Portable Apple Mail MCP server for Claude Desktop **plus** a mirrored **`skills/`** tree copied from [`plugin/skills`](https://github.com/Agentic-Assets/apple-mail-mcp/tree/main/plugin/skills) for Claude Code workflows.

## What is inside this archive

| Path | Role |
|------|------|
| `apple_mail_mcp/` + `apple_mail_mcp.py` | FastMCP tool implementation (**41 tools**) |
| `start_mcp.sh` | Creates `venv/`, installs only bundled hash-checked wheels, execs Python entry |
| `requirements.lock` + `wheelhouse/` | Offline runtime dependency payload for macOS arm64 CPython 3.13 |
| `ui/` | MCP Apps dashboard helpers for `inbox_dashboard` |
| `skills/` | Bundled Claude Code skills (`SKILL.md` per subdirectory) |

For grouped tool summaries, see the upstream [`README`](https://github.com/Agentic-Assets/apple-mail-mcp#readme).

## Claude Desktop install (.mcpb)

1. Claude Desktop → **Settings → Developer → MCP Servers → Install from file** → choose this `.mcpb`.
2. Approve Automation + Mail Data Access prompts when macOS asks.
3. Populate **Default Mail Account** / **Default Mail Signature** / **Email Preferences** in the MCP inspector when available.

Prefer **`--draft-safe`** for shared/agent hosts; manifests typically enable it by default — override only deliberately.

## Claude Code skills (manual sync)

Mirror the bundle's `skills/` directory into Claude Code (`~/.claude/skills`):

```
mkdir -p ~/.claude/skills
cp -a skills/. ~/.claude/skills/
```

Skills included (each subfolder owns a `SKILL.md`):

- `apple-mail-operator` — MCP + Mail navigation bootstrap
- `inbox-triage` — 5–10 minute read-first scan
- `email-management` — sustained Inbox Zero umbrella
- `mailbox-taxonomy` — folder taxonomy + noise diagnosis
- `email-archive-cleanup` — staged archive / bulk move / trash with dry runs
- `mail-rules-advisor` — Mail rule/filter proposals (**Mail UI apply only** — no MCP rule API)
- `email-drafting` — compose/reply drafts (`--draft-safe` aware)
- `email-style-profile` — derive voice prefs from Sent mail + `USER_EMAIL_PREFERENCES`
- `email-attachments` — list/save attachments with path safeguards

Also copies `skills/CLAUDE.md` authoring notes — safe to ignore for runtime.

## Operational notes

- Keep **`DEFAULT_MAIL_ACCOUNT`** set when multiple accounts fan out slowly.
- Set **`DEFAULT_MAIL_SIGNATURE`** to an exact Mail signature name when drafts should include your standard signature.
- Use narrow `recent_days` / caps before escalating cross-account AppleScript workloads.
- `export_emails`, `save_email_attachment`, compose send paths imply disk or dispatch risk — preview + confirm.

Support & source: https://github.com/Agentic-Assets/apple-mail-mcp
""".encode()


def _check_no_stale_distribution_artifacts(expected_version: str, errors: list[str]) -> None:
    expected_name = f"apple-mail-mcp-v{expected_version}.mcpb"
    for path in sorted(common.ROOT.glob("apple-mail-mcp-v*.mcpb")):
        if path.name != expected_name:
            errors.append(f"stale distribution artifact: {path.name}; remove or run tools/gates/build-artifacts.sh")


def _check_artifact_freshness(
    expected_version: str,
    errors: list[str],
    *,
    require_artifacts: bool = False,
) -> None:
    # Zip is built from inside `plugin/` so contents sit at the zip root.
    # Cowork (and `claude plugin validate`) look for `.claude-plugin/plugin.json`
    # at the unzip root — a `plugin/` prefix breaks the upload.
    plugin_expected = [(common.ROOT / "plugin" / rel, rel.as_posix()) for rel in _iter_plugin_payload_files()]
    _compare_zip_members(
        common.ROOT / "apple-mail-plugin.zip",
        plugin_expected,
        "apple-mail-plugin.zip",
        errors,
        require_present=require_artifacts,
        exact_members=True,
    )
    _check_no_directory_entries(common.ROOT / "apple-mail-plugin.zip", "apple-mail-plugin.zip", errors)
    _check_plugin_file_parity(common.ROOT, errors, require_present=require_artifacts)

    mcpb = common.ROOT / f"apple-mail-mcp-v{expected_version}.mcpb"
    mcpb_expected: list[tuple[Path, str]] = [
        (common.ROOT / "apple-mail-mcpb/manifest.json", "manifest.json"),
        (common.ROOT / "plugin/apple_mail_mcp.py", "apple_mail_mcp.py"),
        (common.ROOT / "plugin/requirements.txt", "requirements.txt"),
        (common.ROOT / "plugin/requirements.lock", "requirements.lock"),
        (common.ROOT / "plugin/start_mcp.sh", "start_mcp.sh"),
    ]
    for subdir in ("apple_mail_mcp", "skills", "ui", "wheelhouse"):
        root = common.ROOT / "plugin" / subdir
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if path.is_dir():
                continue
            if "__pycache__" in rel.parts or path.suffix == ".pyc":
                continue
            mcpb_expected.append((path, f"{subdir}/{rel.as_posix()}"))
    _compare_zip_members(
        mcpb,
        mcpb_expected,
        mcpb.name,
        errors,
        require_present=require_artifacts,
        exact_members=True,
        allowed_extra_members={"README.md"},
    )
    _check_no_directory_entries(mcpb, mcpb.name, errors)
    if mcpb.exists():
        try:
            with zipfile.ZipFile(mcpb) as zf:
                actual_readme = zf.read("README.md")
        except KeyError:
            errors.append(f"{mcpb.name}: missing README.md")
        except zipfile.BadZipFile:
            pass
        else:
            if actual_readme != _generated_mcpb_readme():
                errors.append(f"{mcpb.name}: stale README.md; rebuild {mcpb.name}")

    _check_no_stale_distribution_artifacts(expected_version, errors)
