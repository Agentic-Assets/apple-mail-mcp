"""Codex plugin-surface checks: marketplace, manifest, and stdio launcher."""

from __future__ import annotations

import json
from pathlib import Path

from manifest_checks import common
from manifest_checks.common import (
    AGENTIC_ASSETS_MARKETPLACE_DISPLAY_NAME,
    AGENTIC_ASSETS_MARKETPLACE_NAME,
    CODEX_MANIFEST_LABEL,
    CODEX_MARKETPLACE_LABEL,
    CODEX_MCP_LABEL,
    CODEX_REQUIRED_FIELDS,
    _check_tool_count_claim,
)


def _append_mismatch(
    errors: list[str],
    label: str,
    actual: object,
    expected: str,
) -> None:
    errors.append(f"{label}: got '{actual}', expected '{expected}'")


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


def _read_json_contract(path: Path, label: str, errors: list[str]) -> dict | None:
    if not path.exists():
        errors.append(f"{label}: missing {path.relative_to(common.ROOT).as_posix()}")
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
    market = _read_json_contract(common.ROOT / market_label, market_label, errors)
    if market is not None:
        if market.get("name") != AGENTIC_ASSETS_MARKETPLACE_NAME:
            _append_mismatch(
                errors,
                f"{market_label} name",
                market.get("name"),
                AGENTIC_ASSETS_MARKETPLACE_NAME,
            )
        interface = market.get("interface") or {}
        if not isinstance(interface, dict):
            errors.append(f"{market_label} interface: expected object")
        elif interface.get("displayName") != AGENTIC_ASSETS_MARKETPLACE_DISPLAY_NAME:
            _append_mismatch(
                errors,
                f"{market_label} interface.displayName",
                interface.get("displayName"),
                AGENTIC_ASSETS_MARKETPLACE_DISPLAY_NAME,
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
    manifest = _read_json_contract(common.ROOT / manifest_label, manifest_label, errors)
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
        elif not (common.ROOT / "plugin/skills").is_dir():
            errors.append(f"{manifest_label} skills: missing plugin/skills")

        if manifest.get("mcpServers") != "./.mcp.json":
            _append_mismatch(
                errors,
                f"{manifest_label} mcpServers",
                manifest.get("mcpServers"),
                "./.mcp.json",
            )
        else:
            mcp_path = common.ROOT / "plugin/.mcp.json"

    mcp_label = CODEX_MCP_LABEL
    mcp = _read_json_contract(mcp_path or (common.ROOT / mcp_label), mcp_label, errors)
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
