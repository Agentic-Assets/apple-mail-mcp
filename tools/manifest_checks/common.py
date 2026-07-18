#!/usr/bin/env python3
"""Shared constants and helpers for the manifest validation checks.

This module is the single source of truth for the repository ``ROOT`` and the
small utilities used by more than one check group. ``tools/validators/validate_manifests.py``
forwards its historical ``ROOT`` attribute to ``common.ROOT`` so tests that
monkeypatch ``validate_manifests.ROOT`` keep steering every check that reads
``common.ROOT`` at call time.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read_marketplace_identity() -> dict:
    path = ROOT / "tools/marketplace_identity.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid marketplace identity contract: {exc}") from exc
    if payload.get("schema_version") != 1:
        raise RuntimeError("unsupported marketplace identity contract schema")
    return payload


MARKETPLACE_IDENTITY = _read_marketplace_identity()

# Per Claude Code marketplace rules: if both marketplace.json plugins[0] and
# plugin.json declare any of these fields, the install errors out unless the
# marketplace entry sets `strict: true`. See _check_marketplace_contract.
MARKETPLACE_COMPONENT_FIELDS = ("commands", "agents", "skills", "hooks", "mcpServers")
CODEX_MARKETPLACE_LABEL = ".agents/plugins/marketplace.json"
CODEX_MANIFEST_LABEL = "plugin/.codex-plugin/plugin.json"
CODEX_MCP_LABEL = "plugin/.mcp.json"
DIRECT_SOURCE_MARKETPLACE_NAME = MARKETPLACE_IDENTITY["standalone_compatibility"]["marketplace_id"]
PRIMARY_MARKETPLACE_NAME = MARKETPLACE_IDENTITY["primary_marketplace"]["id"]
PRIMARY_PLUGIN_SELECTOR = MARKETPLACE_IDENTITY["primary_marketplace"]["selector"]
AGENTIC_ASSETS_MARKETPLACE_DISPLAY_NAME = "Agentic Assets"
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
    # The generated MCPB README (with its "<N> tools" claim) lives in
    # _generated_mcpb_readme(); this entry tracks that the embedded count
    # stays in sync with the registry. It moved here from validate_manifests.py
    # when the checks were split into the manifest_checks package.
    "tools/manifest_checks/artifacts.py",
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


MODULE_LINE_BUDGET_BASELINE = ROOT / "tests" / "fixtures" / "module_line_budget" / "baseline.json"


def _fail(msg: str) -> None:
    print(f"validate_manifests: {msg}", file=sys.stderr)
    sys.exit(1)


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


def _read_json_contract(path: Path, label: str, errors: list[str]) -> dict | None:
    """Read a manifest object while collecting stable validation errors."""
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


def _check_tool_count_claim(text: str | None, source: str, actual: int, errors: list[str]) -> None:
    match = re.search(r"(\d+)\s+(?:MCP\s+)?tools?\b", text or "", re.I)
    if not match:
        errors.append(f"{source}: missing '<N> tools' or '<N> MCP tools' in description")
        return
    claimed = int(match.group(1))
    if claimed != actual:
        errors.append(f"{source}: description claims {claimed} tools, registry has {actual}")


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
