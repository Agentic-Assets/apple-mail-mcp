"""Cursor plugin-surface checks for the local draft-safe MCP adapter."""

from __future__ import annotations

from manifest_checks import common
from manifest_checks.codex import _check_codex_mcp_launcher_contract, _read_json_contract
from manifest_checks.common import _check_tool_count_claim

CURSOR_MANIFEST_LABEL = "plugin/.cursor-plugin/plugin.json"
CURSOR_MCP_LABEL = "plugin/mcp.json"


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
            errors.append(
                f"{CURSOR_MANIFEST_LABEL} name: got '{manifest.get('name')}', expected 'apple-mail'"
            )
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
    _check_codex_mcp_launcher_contract(servers.get("apple-mail"), CURSOR_MCP_LABEL, errors)
