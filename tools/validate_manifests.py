#!/usr/bin/env python3
"""Validate version sync, tool counts, mcpb parity, and local artifacts."""

from __future__ import annotations

import glob
import json
import os
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fail(msg: str) -> None:
    print(f"validate_manifests: {msg}", file=sys.stderr)
    sys.exit(1)


def _read_project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^\[project\]\s*$([\s\S]*?)(?=^\[|\Z)", text, re.M)
    if not block:
        _fail("pyproject.toml: missing [project] section")
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', block.group(1), re.M)
    if not match:
        _fail("pyproject.toml: missing [project].version")
    return match.group(1)


def _json_field(path: Path, dotted: str):
    data = json.loads(path.read_text(encoding="utf-8"))
    cur = data
    for part in dotted.split("."):
        if "[" in part:
            key, rest = part.split("[", 1)
            idx = int(rest.rstrip("]"))
            cur = cur[key][idx]
        else:
            cur = cur[part]
    return cur


def _extract_registered_tool_names() -> list[str]:
    names: list[str] = []
    for path in sorted(glob.glob(str(ROOT / "plugin/apple_mail_mcp/tools/*.py"))):
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            if re.match(r"^@mcp\.tool", lines[i]):
                j = i + 1
                while j < len(lines) and lines[j].startswith("@"):
                    j += 1
                if j >= len(lines):
                    _fail(f"no function after @mcp.tool in {path}:{i + 1}")
                match = re.match(r"(?:async )?def (\w+)", lines[j])
                if not match:
                    _fail(f"no def after @mcp.tool in {path}:{i + 1}")
                names.append(match.group(1))
                i = j + 1
            else:
                i += 1
    return names


def _check_tool_count_claim(text: str | None, source: str, actual: int, errors: list[str]) -> None:
    match = re.search(r"(\d+)\s+(?:MCP\s+)?tools?\b", text or "", re.I)
    if not match:
        errors.append(f"{source}: missing '<N> tools' or '<N> MCP tools' in description")
        return
    claimed = int(match.group(1))
    if claimed != actual:
        errors.append(
            f"{source}: description claims {claimed} tools, registry has {actual}"
        )


def _iter_plugin_payload_files() -> list[Path]:
    """Return plugin files expected to be byte-identical in apple-mail-plugin.zip."""
    plugin_root = ROOT / "plugin"
    excluded_parts = {"venv", "__pycache__"}
    files: list[Path] = []
    for path in plugin_root.rglob("*"):
        rel = path.relative_to(plugin_root)
        if path.is_dir():
            continue
        if any(part in excluded_parts for part in rel.parts):
            continue
        if path.name in {".DS_Store"} or path.suffix == ".pyc":
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
) -> None:
    """Compare selected repo files to their distributable archive members."""
    if not archive.exists():
        if require_present:
            errors.append(f"{label}: missing archive; rebuild {archive.name}")
        return

    try:
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            for source, member in expected:
                if member not in names:
                    errors.append(f"{label}: missing {member}")
                    continue
                if source.read_bytes() != zf.read(member):
                    errors.append(
                        f"{label}: stale {member}; rebuild {archive.name}"
                    )
    except zipfile.BadZipFile:
        errors.append(f"{label}: {archive.name} is not a valid zip archive")


def _generated_mcpb_readme() -> bytes:
    return """# Apple Mail MCP bundle

Portable Apple Mail MCP server for Claude Desktop **plus** a mirrored **`skills/`** tree copied from [`plugin/skills`](https://github.com/agenticassets/apple-mail-mcp/tree/main/plugin/skills) for Claude Code workflows.

## What is inside this archive

| Path | Role |
|------|------|
| `apple_mail_mcp/` + `apple_mail_mcp.py` | FastMCP tool implementation (**28 tools**) |
| `start_mcp.sh` | Creates `venv/`, installs `requirements.txt`, execs Python entry |
| `requirements.txt` | Runtime Python dependencies |
| `ui/` *(optional)* | MCP Apps dashboard helpers for `inbox_dashboard` |
| `skills/` | Bundled Claude Code skills (`SKILL.md` per subdirectory) |

For grouped tool summaries, see the upstream [`README`](https://github.com/agenticassets/apple-mail-mcp#readme).

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

Support & source: https://github.com/agenticassets/apple-mail-mcp
""".encode()


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _check_artifact_freshness(
    expected_version: str,
    errors: list[str],
    *,
    require_artifacts: bool = False,
) -> None:
    plugin_expected = [
        (ROOT / "plugin" / rel, f"plugin/{rel.as_posix()}")
        for rel in _iter_plugin_payload_files()
    ]
    _compare_zip_members(
        ROOT / "apple-mail-plugin.zip",
        plugin_expected,
        "apple-mail-plugin.zip",
        errors,
        require_present=require_artifacts,
    )

    mcpb = ROOT / f"apple-mail-mcp-v{expected_version}.mcpb"
    mcpb_expected: list[tuple[Path, str]] = [
        (ROOT / "apple-mail-mcpb/manifest.json", "manifest.json"),
        (ROOT / "plugin/apple_mail_mcp.py", "apple_mail_mcp.py"),
        (ROOT / "plugin/requirements.txt", "requirements.txt"),
        (ROOT / "plugin/start_mcp.sh", "start_mcp.sh"),
    ]
    for subdir in ("apple_mail_mcp", "skills", "ui"):
        root = ROOT / "plugin" / subdir
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
    )
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


def main() -> None:
    errors: list[str] = []
    expected_version = _read_project_version()

    version_checks = [
        (ROOT / "plugin/.claude-plugin/plugin.json", "version", "plugin.json"),
        (ROOT / ".claude-plugin/marketplace.json", "plugins[0].version", "marketplace.json"),
        (ROOT / "server.json", "version", "server.json"),
        (ROOT / "server.json", "packages[0].version", "server.json packages[0]"),
        (ROOT / "apple-mail-mcpb/manifest.json", "version", "mcpb manifest.json"),
    ]
    for path, field, label in version_checks:
        actual = _json_field(path, field)
        if actual != expected_version:
            errors.append(f"{label}: got '{actual}', expected '{expected_version}'")

    code_names = _extract_registered_tool_names()
    actual_count = len(code_names)
    if actual_count == 0:
        errors.append("no @mcp.tool registrations found")

    plugin = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
    _check_tool_count_claim(plugin.get("description"), "plugin.json description", actual_count, errors)

    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    plugins = market.get("plugins") or []
    if not plugins:
        errors.append("marketplace.json: missing plugins[0]")
    else:
        _check_tool_count_claim(
            plugins[0].get("description"),
            "marketplace.json plugins[0].description",
            actual_count,
            errors,
        )

    mcpb = json.loads((ROOT / "apple-mail-mcpb/manifest.json").read_text(encoding="utf-8"))
    _check_tool_count_claim(mcpb.get("description"), "mcpb manifest description", actual_count, errors)

    mcpb_names = [tool["name"] for tool in mcpb.get("tools", [])]
    if len(mcpb_names) != actual_count:
        errors.append(
            f"tool count mismatch: code={actual_count}, mcpb tools[]={len(mcpb_names)}"
        )

    code_set = set(code_names)
    mcpb_set = set(mcpb_names)
    only_code = sorted(code_set - mcpb_set)
    only_mcpb = sorted(mcpb_set - code_set)
    if only_code:
        errors.append("registered in code, missing from mcpb: " + ", ".join(only_code))
    if only_mcpb:
        errors.append("present in mcpb tools[], missing from code: " + ", ".join(only_mcpb))

    _check_artifact_freshness(
        expected_version,
        errors,
        require_artifacts=_env_truthy("APPLE_MAIL_REQUIRE_DIST_ARTIFACTS"),
    )

    if errors:
        print("validate_manifests: FAILED", file=sys.stderr)
        for err in errors:
            print(f"  ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    print(
        f"validate_manifests: OK (version={expected_version}, tools={actual_count})"
    )


if __name__ == "__main__":
    main()
