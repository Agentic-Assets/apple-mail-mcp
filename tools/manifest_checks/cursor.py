"""Cursor plugin-surface checks for the local draft-safe MCP adapter."""

from __future__ import annotations

from manifest_checks import common
from manifest_checks.common import _check_tool_count_claim, _read_json_contract

CURSOR_MANIFEST_LABEL = "plugin/.cursor-plugin/plugin.json"
CURSOR_MCP_LABEL = "plugin/mcp.json"


def _check_cursor_mcp_launcher_contract(
    server: object,
    label: str,
    errors: list[str],
) -> None:
    """Validate Cursor's plugin-root-aware stdio launcher."""
    if not isinstance(server, dict):
        errors.append(f"{label}: missing mcpServers.apple-mail")
        return
    if server.get("command") != "/bin/bash":
        errors.append(f"{label} mcpServers.apple-mail.command: expected /bin/bash")

    args = server.get("args")
    if not isinstance(args, list):
        errors.append(f"{label} mcpServers.apple-mail.args: expected list")
        return
    expected_launcher = "${CURSOR_PLUGIN_ROOT}/start_mcp.sh"
    if not args or args[0] != expected_launcher:
        errors.append(f"{label} mcpServers.apple-mail.args: first arg must be {expected_launcher}")
    if "--draft-safe" not in args:
        errors.append(f"{label} mcpServers.apple-mail.args: missing --draft-safe")

    if "cwd" in server:
        errors.append(f"{label} mcpServers.apple-mail.cwd: omit cwd for Cursor plugins")

    values = [server.get("command"), *args, server.get("cwd")]
    if any(isinstance(value, str) and "${CLAUDE_PLUGIN_ROOT}" in value for value in values):
        errors.append(f"{label} mcpServers.apple-mail: must not use ${{CLAUDE_PLUGIN_ROOT}} in Cursor launcher fields")


def _check_cursor_plugin_contract(
    expected_version: str,
    actual_tool_count: int,
    errors: list[str],
) -> None:
    """Keep Cursor's manifest and local launcher distinct from Codex's adapter."""
    manifest = _read_json_contract(
        common.ROOT / CURSOR_MANIFEST_LABEL,
        CURSOR_MANIFEST_LABEL,
        errors,
    )
    mcp_path = common.ROOT / CURSOR_MCP_LABEL
    if manifest is not None:
        if manifest.get("name") != "apple-mail":
            errors.append(f"{CURSOR_MANIFEST_LABEL} name: got '{manifest.get('name')}', expected 'apple-mail'")
        if manifest.get("version") != expected_version:
            errors.append(
                f"{CURSOR_MANIFEST_LABEL} version: got '{manifest.get('version')}', expected '{expected_version}'"
            )
        if manifest.get("mcpServers") != "./mcp.json":
            errors.append(
                f"{CURSOR_MANIFEST_LABEL} mcpServers: got '{manifest.get('mcpServers')}', expected './mcp.json'"
            )
        description = manifest.get("description")
        if description is not None:
            _check_tool_count_claim(description, f"{CURSOR_MANIFEST_LABEL} description", actual_tool_count, errors)

    mcp = _read_json_contract(mcp_path, CURSOR_MCP_LABEL, errors)
    if mcp is None:
        return
    servers = mcp.get("mcpServers") or {}
    if not isinstance(servers, dict):
        errors.append(f"{CURSOR_MCP_LABEL} mcpServers: expected object")
        return
    _check_cursor_mcp_launcher_contract(
        servers.get("apple-mail"),
        CURSOR_MCP_LABEL,
        errors,
    )
