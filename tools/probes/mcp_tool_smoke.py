#!/usr/bin/env python3
"""Launch an MCP stdio server and assert that required tools are registered."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


DEFAULT_REQUIRED_TOOLS = (
    "reply_to_email",
    "compose_email",
    "manage_drafts",
    "list_accounts",
    "get_inbox_overview",
)


def _fail(message: str) -> None:
    print(f"mcp_tool_smoke: {message}", file=sys.stderr)
    raise SystemExit(1)


def _load_server_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail(f"server JSON not found: {path}")
    except json.JSONDecodeError as exc:
        _fail(f"invalid server JSON at line {exc.lineno}: {exc.msg}")

    if not isinstance(data, dict):
        _fail("server JSON must be an object")

    if isinstance(data.get("transport"), dict):
        transport = data["transport"]
        if transport.get("type", "stdio") != "stdio":
            _fail(f"unsupported transport type: {transport.get('type')}")
        return transport

    if isinstance(data.get("mcpServers"), dict):
        servers = data["mcpServers"]
        server = servers.get("apple-mail") or next(iter(servers.values()), None)
        if isinstance(server, dict):
            return server

    return data


def _contains_literal(value: object, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, list):
        return any(_contains_literal(item, needle) for item in value)
    if isinstance(value, dict):
        return any(_contains_literal(item, needle) for item in value.values())
    return False


def _server_params(args: argparse.Namespace) -> StdioServerParameters:
    if args.server_json:
        server = _load_server_json(args.server_json)
        command = server.get("command")
        server_args = server.get("args") or []
        cwd = server.get("cwd")
        env = server.get("env")
    else:
        command = args.command
        server_args = args.arg or []
        cwd = args.cwd
        env = None

    if not isinstance(command, str) or not command:
        _fail("server command is missing")
    if not isinstance(server_args, list) or not all(isinstance(arg, str) for arg in server_args):
        _fail("server args must be a list of strings")
    if cwd is not None and not isinstance(cwd, str):
        _fail("server cwd must be a string when provided")
    if env is not None and not isinstance(env, dict):
        _fail("server env must be an object when provided")

    for literal in args.reject_literal:
        if _contains_literal({"command": command, "args": server_args, "cwd": cwd}, literal):
            _fail(f"registered launcher still contains literal {literal!r}")

    return StdioServerParameters(command=command, args=server_args, cwd=cwd, env=env)


async def _list_tools(params: StdioServerParameters, timeout: float) -> list[str]:
    with anyio.fail_after(timeout):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return sorted(tool.name for tool in result.tools)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-json", type=Path, help="JSON output from `codex mcp get NAME --json`")
    parser.add_argument("--command", help="Server command for direct smoke tests")
    parser.add_argument("--arg", action="append", default=[], help="Server arg; repeat for each arg")
    parser.add_argument("--cwd", help="Working directory for direct smoke tests")
    parser.add_argument("--timeout", type=float, default=90.0, help="Handshake timeout in seconds")
    parser.add_argument("--expect-count", type=int, help="Exact expected tool count")
    parser.add_argument(
        "--required-tool",
        action="append",
        default=[],
        help="Required tool name; defaults to the critical Apple Mail tools",
    )
    parser.add_argument(
        "--reject-literal",
        action="append",
        default=[],
        help="Fail before launch if the registered command/args/cwd still contain this literal",
    )
    parser.add_argument("--print-tools", action="store_true", help="Print one tool name per line")
    args = parser.parse_args()
    if bool(args.server_json) == bool(args.command):
        parser.error("pass exactly one of --server-json or --command")
    return args


def main() -> None:
    args = _parse_args()
    required = tuple(args.required_tool) if args.required_tool else DEFAULT_REQUIRED_TOOLS
    params = _server_params(args)

    try:
        tool_names = anyio.run(_list_tools, params, args.timeout)
    except TimeoutError:
        _fail(f"timed out after {args.timeout:g}s waiting for MCP tools")
    except Exception as exc:  # pragma: no cover - exact MCP transport errors vary by host.
        _fail(f"MCP handshake failed: {exc}")

    missing = sorted(set(required) - set(tool_names))
    if missing:
        _fail(f"missing required tools: {', '.join(missing)}")
    if args.expect_count is not None and len(tool_names) != args.expect_count:
        _fail(f"got {len(tool_names)} tools, expected {args.expect_count}")

    if args.print_tools:
        print("\n".join(tool_names))
    print(
        "mcp_tool_smoke: OK "
        f"({len(tool_names)} tools; required: {', '.join(required)})"
    )


if __name__ == "__main__":
    main()
