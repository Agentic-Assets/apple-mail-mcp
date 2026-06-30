#!/usr/bin/env python3
"""Validate version sync, tool counts, mcpb parity, and local artifacts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Per Claude Code marketplace rules: if both marketplace.json plugins[0] and
# plugin.json declare any of these fields, the install errors out unless the
# marketplace entry sets `strict: true`. See _check_marketplace_contract.
MARKETPLACE_COMPONENT_FIELDS = ("commands", "agents", "skills", "hooks", "mcpServers")
CODEX_MARKETPLACE_LABEL = ".agents/plugins/marketplace.json"
CODEX_MANIFEST_LABEL = "plugin/.codex-plugin/plugin.json"
CODEX_MCP_LABEL = "plugin/.mcp.json"
CODEX_REQUIRED_FIELDS = (
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "skills",
    "mcpServers",
    "interface",
)
ACTIVE_DOC_TOOL_COUNT_REQUIRED = (
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "docs/CLAUDE.md",
    "plugin/apple_mail_mcp/CLAUDE.md",
    "plugin/apple_mail_mcp/tools/CLAUDE.md",
    "plugin/docs/CLAUDE.md",
    ".claude-plugin/CLAUDE.md",
    "apple-mail-mcpb/CLAUDE.md",
    "apple-mail-mcpb/build-mcpb.sh",
    "tools/validate_manifests.py",
)
ACTIVE_DOC_TOOL_COUNT_SCAN_ONLY = (
    "tools/CLAUDE.md",
    "docs/CLAUDE-conventions.md",
)
TOOL_COUNT_CLAIM_PATTERNS = (
    re.compile(r"\b(\d+)\s+(?:MCP\s+)?tools?\b", re.I),
    re.compile(r"\btool-count claims\b.*?\(\*\*(\d+)\*\*\)", re.I),
    re.compile(r"\bcorrect count\b.*?\(\*\*(\d+)\*\*\)", re.I),
)


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


def _read_project_name() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^\[project\]\s*$([\s\S]*?)(?=^\[|\Z)", text, re.M)
    if not block:
        _fail("pyproject.toml: missing [project] section")
    match = re.search(r'^name\s*=\s*["\']([^"\']+)["\']', block.group(1), re.M)
    if not match:
        _fail("pyproject.toml: missing [project].name")
    return match.group(1)


def _read_pyproject_array(section: str, key: str) -> list[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section_match = re.search(
        rf"^\[{re.escape(section)}\]\s*$([\s\S]*?)(?=^\[|\Z)",
        text,
        re.M,
    )
    if not section_match:
        return []
    key_match = re.search(
        rf"^{re.escape(key)}\s*=\s*\[([\s\S]*?)\]",
        section_match.group(1),
        re.M,
    )
    if not key_match:
        return []
    return re.findall(r'["\']([^"\']+)["\']', key_match.group(1))


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
    for path in sorted((ROOT / "plugin/apple_mail_mcp/tools").glob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
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
        errors.append(f"{source}: description claims {claimed} tools, registry has {actual}")


def _tool_count_claims_in_line(line: str) -> list[int]:
    """Return all tool-count claims in one line of active guidance."""
    claims: list[int] = []
    for pattern in TOOL_COUNT_CLAIM_PATTERNS:
        claims.extend(int(match.group(1)) for match in pattern.finditer(line))
    return claims


def _check_tools_module_count_table(path: Path, actual_count: int, errors: list[str], *, root: Path = ROOT) -> None:
    """Require plugin/apple_mail_mcp/tools/CLAUDE.md module rows to sum to count."""
    if not path.exists():
        return
    total = 0
    seen_rows = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\|\s*`[^`]+\.py`\s*\|\s*(\d+)\s*\|", line)
        if not match:
            continue
        seen_rows += 1
        total += int(match.group(1))
    if seen_rows and total != actual_count:
        errors.append(f"{path.relative_to(root)}: module table sums to {total}, registry has {actual_count}")


def _check_active_doc_tool_count_claims(
    actual_count: int,
    errors: list[str],
    *,
    root: Path = ROOT,
    required_docs: tuple[str, ...] = ACTIVE_DOC_TOOL_COUNT_REQUIRED,
    scan_only_docs: tuple[str, ...] = ACTIVE_DOC_TOOL_COUNT_SCAN_ONLY,
) -> None:
    """Validate tool-count claims in active docs only, preserving historical records."""
    for rel_path in required_docs + scan_only_docs:
        path = root / rel_path
        if not path.exists():
            if rel_path in required_docs:
                errors.append(f"{rel_path}: missing active doc")
            continue
        found_claim = False
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for claimed in _tool_count_claims_in_line(line):
                found_claim = True
                if claimed != actual_count:
                    errors.append(f"{rel_path}:{line_no}: tool-count claim {claimed}, registry has {actual_count}")
        if rel_path in required_docs and not found_claim:
            errors.append(f"{rel_path}: missing active tool-count claim")

    tools_doc = root / "plugin/apple_mail_mcp/tools/CLAUDE.md"
    _check_tools_module_count_table(tools_doc, actual_count, errors, root=root)


def _append_mismatch(
    errors: list[str],
    label: str,
    actual: object,
    expected: str,
) -> None:
    errors.append(f"{label}: got '{actual}', expected '{expected}'")


def _check_mcp_launcher_contract(
    server: object,
    label: str,
    first_arg: str,
    errors: list[str],
) -> None:
    if not isinstance(server, dict):
        errors.append(f"{label}: missing mcpServers.apple-mail")
        return
    if server.get("command") != "/bin/bash":
        errors.append(f"{label} mcpServers.apple-mail.command: expected /bin/bash")

    args = server.get("args")
    if not isinstance(args, list):
        errors.append(f"{label} mcpServers.apple-mail.args: expected list")
        return
    if not args or args[0] != first_arg:
        errors.append(f"{label} mcpServers.apple-mail.args: first arg must be {first_arg}")
    if "--draft-safe" not in args:
        errors.append(f"{label} mcpServers.apple-mail.args: missing --draft-safe")


def _check_codex_mcp_launcher_contract(
    server: object,
    label: str,
    errors: list[str],
) -> None:
    """Validate Codex's stdio launch shape after plugin installation.

    Codex 0.133.0 resolves relative `cwd` values against the installed plugin
    root, but it does not expand `${CLAUDE_PLUGIN_ROOT}` inside argv. Keep this
    separate from the Claude Code contract above.
    """
    if not isinstance(server, dict):
        errors.append(f"{label}: missing mcpServers.apple-mail")
        return
    if server.get("command") != "/bin/bash":
        errors.append(f"{label} mcpServers.apple-mail.command: expected /bin/bash")

    args = server.get("args")
    if not isinstance(args, list):
        errors.append(f"{label} mcpServers.apple-mail.args: expected list")
        return
    if not args or args[0] != "./start_mcp.sh":
        errors.append(f"{label} mcpServers.apple-mail.args: first arg must be ./start_mcp.sh")
    if "--draft-safe" not in args:
        errors.append(f"{label} mcpServers.apple-mail.args: missing --draft-safe")

    cwd = server.get("cwd")
    if cwd != ".":
        errors.append(f"{label} mcpServers.apple-mail.cwd: got '{cwd}', expected '.'")

    values = [server.get("command"), *(args if isinstance(args, list) else []), cwd]
    if any(isinstance(value, str) and "${CLAUDE_PLUGIN_ROOT}" in value for value in values):
        errors.append(
            f"{label} mcpServers.apple-mail: must not contain literal ${{CLAUDE_PLUGIN_ROOT}} in Codex launcher fields"
        )


def _check_plugin_manifest_contract(errors: list[str]) -> None:
    """Validate plugin fields that have caused strict install/runtime failures."""
    plugin = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8"))

    # Cowork/Claude strict validation rejected this field for this plugin.
    # Apple Mail ships workflow entry points as skills only.
    if "commands" in plugin:
        errors.append(
            "plugin.json: unsupported strict-validator field 'commands'; ship workflow entry points as skills only"
        )

    commands_dir = ROOT / "plugin/commands"
    if commands_dir.exists():
        errors.append("plugin/commands: legacy slash commands are retired; ship skills only")

    servers = plugin.get("mcpServers") or {}
    _check_mcp_launcher_contract(
        servers.get("apple-mail"),
        "plugin.json",
        "${CLAUDE_PLUGIN_ROOT}/start_mcp.sh",
        errors,
    )


def _iter_json_strings(value: object) -> Iterator[str]:
    """Yield every string value inside a JSON-like object."""
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_json_strings(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_json_strings(item)


def _check_developer_only_skills_not_packaged(errors: list[str]) -> None:
    """Prevent repo-local development skills from leaking into packaged plugin surfaces."""
    manifest_paths = (
        ROOT / "plugin/.codex-plugin/plugin.json",
        ROOT / "plugin/.claude-plugin/plugin.json",
        ROOT / ".agents/plugins/marketplace.json",
        ROOT / ".claude-plugin/marketplace.json",
    )
    forbidden = (".agents/skills", ".claude/skills")

    for path in manifest_paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for value in _iter_json_strings(payload):
            if any(marker in value for marker in forbidden):
                label = f"{rel} skills" if "skills" in payload else rel
                errors.append(
                    f"{label}: must not reference repo-local developer skills (.agents/skills or .claude/skills)"
                )
                break

    packaged_skills = ROOT / "plugin/skills"
    if packaged_skills.exists():
        resolved = packaged_skills.resolve()
        for dev_dir in (ROOT / ".agents/skills", ROOT / ".claude/skills"):
            if not dev_dir.exists():
                continue
            try:
                resolved.relative_to(dev_dir.resolve())
            except ValueError:
                continue
            errors.append(
                "plugin/skills: must be packaged workflow skills, not a link into "
                f"{dev_dir.relative_to(ROOT).as_posix()}"
            )
            break


def _check_mcpb_runtime_contract(mcpb: dict, errors: list[str]) -> None:
    """Validate the Desktop bundle starts the same safe wrapper as the plugin."""
    server = mcpb.get("server") or {}
    config = server.get("mcp_config") or {}

    if server.get("type") != "python":
        errors.append("mcpb manifest server.type: expected python")

    entry_point = server.get("entry_point")
    if not isinstance(entry_point, str) or not entry_point:
        errors.append("mcpb manifest server.entry_point: expected non-empty string")
    elif not (ROOT / "plugin" / entry_point).exists():
        errors.append(f"mcpb manifest server.entry_point: missing plugin/{entry_point}")

    if config.get("command") != "/bin/bash":
        errors.append("mcpb manifest server.mcp_config.command: expected /bin/bash")

    args = config.get("args")
    if not isinstance(args, list):
        errors.append("mcpb manifest server.mcp_config.args: expected list")
    else:
        if not args or args[0] != "${__dirname}/start_mcp.sh":
            errors.append("mcpb manifest server.mcp_config.args: first arg must be ${__dirname}/start_mcp.sh")
        if "--draft-safe" not in args:
            errors.append("mcpb manifest server.mcp_config.args: missing --draft-safe")

    env = config.get("env") or {}
    for key in (
        "USER_EMAIL_PREFERENCES",
        "DEFAULT_MAIL_ACCOUNT",
        "DEFAULT_MAIL_SIGNATURE",
    ):
        if key not in env:
            errors.append(f"mcpb manifest server.mcp_config.env: missing {key}")
            continue
        value = env.get(key)
        if not isinstance(value, str):
            errors.append(f"mcpb manifest server.mcp_config.env.{key}: expected string")
            continue
        for config_key in re.findall(r"\$\{user_config\.([^}]+)\}", value):
            if config_key not in (mcpb.get("user_config") or {}):
                errors.append(f"mcpb manifest server.mcp_config.env.{key}: unknown user_config.{config_key}")


def _check_marketplace_contract(expected_version: str, errors: list[str]) -> None:
    """Ensure marketplace source and skill pointers resolve to the plugin."""
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    plugins = market.get("plugins") or []
    if not plugins:
        errors.append("marketplace.json: missing plugins[0]")
        return
    plugin_ref = plugins[0]

    source = plugin_ref.get("source")
    source_path: Path | None = None
    source_manifest: dict | None = None
    if not isinstance(source, str):
        errors.append("marketplace.json plugins[0].source: expected string")
    elif not source.startswith("./"):
        errors.append(f"marketplace.json plugins[0].source: path must start with ./ (got {source})")
    else:
        source_path = ROOT / source[2:]
        manifest_path = source_path / ".claude-plugin/plugin.json"
        if not manifest_path.exists():
            errors.append(f"marketplace.json plugins[0].source: missing {source}/.claude-plugin/plugin.json")
        else:
            source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    expected_name = (source_manifest or {}).get("name", "missing")
    if plugin_ref.get("name") != expected_name:
        errors.append(
            "marketplace.json plugins[0].name: got "
            f"'{plugin_ref.get('name')}', expected plugin.json name '{expected_name}'"
        )
    if plugin_ref.get("version") != expected_version:
        errors.append(
            f"marketplace.json plugins[0].version: got '{plugin_ref.get('version')}', expected '{expected_version}'"
        )

    skills = plugin_ref.get("skills") or []
    for skill_path in skills:
        if not isinstance(skill_path, str):
            errors.append("marketplace.json plugins[0].skills: entries must be strings")
            continue
        if not skill_path.startswith("./"):
            errors.append(f"marketplace.json plugins[0].skills: path must start with ./ (got {skill_path})")
            continue
        skill_file = ROOT / skill_path[2:] / "SKILL.md"
        if not skill_file.exists():
            errors.append(f"marketplace.json plugins[0].skills: missing {skill_path}/SKILL.md")

    if source_manifest is not None:
        market_components = [f for f in MARKETPLACE_COMPONENT_FIELDS if plugin_ref.get(f)]
        plugin_components = [f for f in MARKETPLACE_COMPONENT_FIELDS if source_manifest.get(f)]
        if market_components and plugin_components and not plugin_ref.get("strict"):
            errors.append(
                "marketplace.json plugins[0]: component fields "
                f"{market_components} conflict with plugin.json "
                f"components {plugin_components}; remove components from one "
                "manifest or set strict: true "
                "(Claude Code rejects the install otherwise)"
            )


def _read_json_contract(path: Path, label: str, errors: list[str]) -> dict | None:
    if not path.exists():
        errors.append(f"{label}: missing {path.relative_to(ROOT).as_posix()}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{label}: invalid JSON at line {exc.lineno}: {exc.msg}")
        return None
    if not isinstance(data, dict):
        errors.append(f"{label}: expected JSON object")
        return None
    return data


def _check_codex_plugin_contract(
    expected_version: str,
    actual_tool_count: int,
    errors: list[str],
) -> None:
    """Validate the Codex marketplace, manifest, and MCP launcher contract."""
    market_label = CODEX_MARKETPLACE_LABEL
    market = _read_json_contract(ROOT / market_label, market_label, errors)
    if market is not None:
        if market.get("name") != "apple-mail-mcp":
            _append_mismatch(errors, f"{market_label} name", market.get("name"), "apple-mail-mcp")
        interface = market.get("interface") or {}
        if not isinstance(interface, dict):
            errors.append(f"{market_label} interface: expected object")
        elif interface.get("displayName") != "Apple Mail MCP":
            _append_mismatch(
                errors,
                f"{market_label} interface.displayName",
                interface.get("displayName"),
                "Apple Mail MCP",
            )

        plugins = market.get("plugins") or []
        if not plugins:
            errors.append(f"{market_label}: missing plugins[0]")
        else:
            plugin_ref = plugins[0]
            if not isinstance(plugin_ref, dict):
                errors.append(f"{market_label} plugins[0]: expected object")
            else:
                if plugin_ref.get("name") != "apple-mail":
                    _append_mismatch(
                        errors,
                        f"{market_label} plugins[0].name",
                        plugin_ref.get("name"),
                        "apple-mail",
                    )
                expected_source = {"source": "local", "path": "./plugin"}
                if plugin_ref.get("source") != expected_source:
                    errors.append(f"{market_label} plugins[0].source: expected {expected_source}")
                policy = plugin_ref.get("policy") or {}
                if not isinstance(policy, dict):
                    errors.append(f"{market_label} plugins[0].policy: expected object")
                else:
                    if policy.get("installation") != "AVAILABLE":
                        _append_mismatch(
                            errors,
                            f"{market_label} plugins[0].policy.installation",
                            policy.get("installation"),
                            "AVAILABLE",
                        )
                    if policy.get("authentication") != "ON_INSTALL":
                        _append_mismatch(
                            errors,
                            f"{market_label} plugins[0].policy.authentication",
                            policy.get("authentication"),
                            "ON_INSTALL",
                        )
                if plugin_ref.get("category") != "Productivity":
                    _append_mismatch(
                        errors,
                        f"{market_label} plugins[0].category",
                        plugin_ref.get("category"),
                        "Productivity",
                    )

    manifest_label = CODEX_MANIFEST_LABEL
    manifest = _read_json_contract(ROOT / manifest_label, manifest_label, errors)
    mcp_path: Path | None = None
    if manifest is not None:
        for field in CODEX_REQUIRED_FIELDS:
            if field not in manifest:
                errors.append(f"{manifest_label}: missing {field}")

        if manifest.get("name") != "apple-mail":
            _append_mismatch(
                errors,
                f"{manifest_label} name",
                manifest.get("name"),
                "apple-mail",
            )
        if manifest.get("version") != expected_version:
            _append_mismatch(
                errors,
                f"{manifest_label} version",
                manifest.get("version"),
                expected_version,
            )
        _check_tool_count_claim(
            manifest.get("description"),
            f"{manifest_label} description",
            actual_tool_count,
            errors,
        )

        if manifest.get("skills") != "./skills":
            _append_mismatch(
                errors,
                f"{manifest_label} skills",
                manifest.get("skills"),
                "./skills",
            )
        elif not (ROOT / "plugin/skills").is_dir():
            errors.append(f"{manifest_label} skills: missing plugin/skills")

        if manifest.get("mcpServers") != "./.mcp.json":
            _append_mismatch(
                errors,
                f"{manifest_label} mcpServers",
                manifest.get("mcpServers"),
                "./.mcp.json",
            )
        else:
            mcp_path = ROOT / "plugin/.mcp.json"

    mcp_label = CODEX_MCP_LABEL
    mcp = _read_json_contract(mcp_path or (ROOT / mcp_label), mcp_label, errors)
    if mcp is None:
        return

    servers = mcp.get("mcpServers") or {}
    if not isinstance(servers, dict):
        errors.append(f"{mcp_label} mcpServers: expected object")
        return
    _check_codex_mcp_launcher_contract(
        servers.get("apple-mail"),
        mcp_label,
        errors,
    )


def _check_server_json_contract(
    server: dict,
    *,
    expected_version: str,
    project_name: str,
    errors: list[str],
) -> None:
    expected_schema = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
    if server.get("$schema") != expected_schema:
        errors.append(f"server.json $schema: expected {expected_schema}")

    packages = server.get("packages") or []
    if not packages:
        errors.append("server.json: missing packages[0]")
        return

    package = packages[0]
    if package.get("registryType") != "pypi":
        errors.append("server.json packages[0].registryType: expected pypi")
    if package.get("identifier") != project_name:
        errors.append(
            f"server.json packages[0].identifier: got '{package.get('identifier')}', expected '{project_name}'"
        )
    if package.get("version") != expected_version:
        errors.append(f"server.json packages[0].version: got '{package.get('version')}', expected '{expected_version}'")
    transport = package.get("transport") or {}
    if transport.get("type") != "stdio":
        errors.append("server.json packages[0].transport.type: expected stdio")


def _requirement_name(requirement: str) -> str:
    name = re.split(r"[<>=!~;\[]", requirement.strip(), maxsplit=1)[0]
    return name.lower().replace("_", "-")


def _check_python_package_contract(errors: list[str]) -> None:
    """Ensure PyPI package metadata can run the same shipped runtime paths."""
    pyproject_deps = {_requirement_name(dep) for dep in _read_pyproject_array("project", "dependencies")}
    requirements = {
        _requirement_name(line)
        for line in (ROOT / "plugin/requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for dep in sorted(requirements - pyproject_deps):
        errors.append(f"pyproject.toml dependencies: missing runtime dependency {dep} from plugin/requirements.txt")

    packages = set(_read_pyproject_array("tool.hatch.build.targets.wheel", "packages"))
    if "plugin/ui" not in packages:
        errors.append("pyproject.toml wheel packages: missing plugin/ui for inbox_dashboard UI runtime")


def _check_source_syntax(errors: list[str]) -> None:
    """Catch startup syntax failures before a fresh artifact is shipped."""
    start_script = ROOT / "plugin/start_mcp.sh"
    if start_script.exists():
        result = subprocess.run(
            ["bash", "-n", str(start_script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            suffix = f" {detail[-1]}" if detail else ""
            errors.append(f"plugin/start_mcp.sh: shell syntax error:{suffix}")

    for base in ("plugin/apple_mail_mcp.py", "plugin/apple_mail_mcp", "plugin/ui"):
        path = ROOT / base
        paths = [path] if path.is_file() else sorted(path.rglob("*.py")) if path.exists() else []
        for py_file in paths:
            rel = py_file.relative_to(ROOT).as_posix()
            try:
                compile(py_file.read_text(encoding="utf-8"), rel, "exec")
            except SyntaxError as exc:
                errors.append(f"{rel}: python syntax error: {exc.msg} at line {exc.lineno}")


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
            cwd=ROOT,
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
    plugin_root = ROOT / "plugin"
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
                "tools/build-artifacts.sh (Cowork upload needs the .plugin "
                "extension alongside apple-mail-plugin.zip)"
            )
        return

    _check_no_directory_entries(plugin_path, "apple-mail.plugin", errors)

    if not zip_path.exists():
        errors.append(
            "apple-mail.plugin: present but apple-mail-plugin.zip is missing; "
            "both must ship together — rebuild via tools/build-artifacts.sh"
        )
        return

    if zip_path.read_bytes() != plugin_path.read_bytes():
        errors.append(
            "apple-mail.plugin: bytes diverge from apple-mail-plugin.zip; "
            "rebuild via tools/build-artifacts.sh (the .plugin file must be a "
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
| `apple_mail_mcp/` + `apple_mail_mcp.py` | FastMCP tool implementation (**30 tools**) |
| `start_mcp.sh` | Creates `venv/`, installs `requirements.txt`, execs Python entry |
| `requirements.txt` | Runtime Python dependencies |
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


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _check_no_stale_distribution_artifacts(expected_version: str, errors: list[str]) -> None:
    expected_name = f"apple-mail-mcp-v{expected_version}.mcpb"
    for path in sorted(ROOT.glob("apple-mail-mcp-v*.mcpb")):
        if path.name != expected_name:
            errors.append(f"stale distribution artifact: {path.name}; remove or run tools/build-artifacts.sh")


def _check_artifact_freshness(
    expected_version: str,
    errors: list[str],
    *,
    require_artifacts: bool = False,
) -> None:
    # Zip is built from inside `plugin/` so contents sit at the zip root.
    # Cowork (and `claude plugin validate`) look for `.claude-plugin/plugin.json`
    # at the unzip root — a `plugin/` prefix breaks the upload.
    plugin_expected = [(ROOT / "plugin" / rel, rel.as_posix()) for rel in _iter_plugin_payload_files()]
    _compare_zip_members(
        ROOT / "apple-mail-plugin.zip",
        plugin_expected,
        "apple-mail-plugin.zip",
        errors,
        require_present=require_artifacts,
        exact_members=True,
    )
    _check_no_directory_entries(ROOT / "apple-mail-plugin.zip", "apple-mail-plugin.zip", errors)
    _check_plugin_file_parity(ROOT, errors, require_present=require_artifacts)

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


def main() -> None:
    errors: list[str] = []
    expected_version = _read_project_version()
    project_name = _read_project_name()

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
    _check_plugin_manifest_contract(errors)
    _check_developer_only_skills_not_packaged(errors)
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
    _check_marketplace_contract(expected_version, errors)
    _check_codex_plugin_contract(expected_version, actual_count, errors)

    mcpb = json.loads((ROOT / "apple-mail-mcpb/manifest.json").read_text(encoding="utf-8"))
    _check_tool_count_claim(mcpb.get("description"), "mcpb manifest description", actual_count, errors)
    _check_mcpb_runtime_contract(mcpb, errors)

    mcpb_names = [tool["name"] for tool in mcpb.get("tools", [])]
    if len(mcpb_names) != actual_count:
        errors.append(f"tool count mismatch: code={actual_count}, mcpb tools[]={len(mcpb_names)}")

    _check_active_doc_tool_count_claims(actual_count, errors)

    code_set = set(code_names)
    mcpb_set = set(mcpb_names)
    only_code = sorted(code_set - mcpb_set)
    only_mcpb = sorted(mcpb_set - code_set)
    if only_code:
        errors.append("registered in code, missing from mcpb: " + ", ".join(only_code))
    if only_mcpb:
        errors.append("present in mcpb tools[], missing from code: " + ", ".join(only_mcpb))

    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    _check_server_json_contract(
        server,
        expected_version=expected_version,
        project_name=project_name,
        errors=errors,
    )
    _check_python_package_contract(errors)
    _check_source_syntax(errors)

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

    print(f"validate_manifests: OK (version={expected_version}, tools={actual_count})")


if __name__ == "__main__":
    main()
