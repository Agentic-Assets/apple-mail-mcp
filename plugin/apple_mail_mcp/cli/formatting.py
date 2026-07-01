"""Output, parsing, and result-classification helpers shared across CLI commands.

No test patch seams live here, so callers import these helpers directly.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from typing import Any


def _version() -> str:
    try:
        return metadata.version("mcp-apple-mail")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def _print_result(result: Any, *, json_mode: bool = False) -> int:
    if json_mode:
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                parsed = {"result": result}
            print(json.dumps(parsed, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if isinstance(result, (dict, list)):
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(result)
    return 0


def _read_text_arg(value: str | None, file_value: str | None) -> str:
    if value is not None and file_value is not None:
        raise ValueError("Use either --body or --body-file, not both")
    if file_value:
        return Path(file_value).expanduser().read_text()
    return value or ""


def _parse_csv_arg(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _run_tool(func: Callable[..., Any], json_mode: bool, **kwargs: Any) -> int:
    try:
        result = func(**kwargs)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        return _print_result(result, json_mode=json_mode)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # pragma: no cover - safety net for CLI UX
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _await_if_coro(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return asyncio.run(value)
    return value


def _redact(value: Any, *, verbose_sensitive: bool = False) -> Any:
    if verbose_sensitive:
        return value
    if isinstance(value, list):
        return {"count": len(value)}
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"accounts", "available_accounts"} and isinstance(item, list):
                redacted[key] = {"count": len(item)}
            elif key == "addresses" and isinstance(item, dict):
                redacted[key] = {"account_count": len(item)}
            elif key in {"emails", "items", "recent", "mailboxes"} and isinstance(item, list):
                redacted[key] = {"count": len(item)}
            elif key == "account" and isinstance(item, str):
                redacted[key] = "(redacted)"
            else:
                redacted[key] = _redact(item, verbose_sensitive=False)
        return redacted
    if isinstance(value, str):
        return {"chars": len(value)}
    return value


def _parse_tool_result(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _result_is_error(value: Any) -> bool:
    parsed = _parse_tool_result(value)
    if isinstance(parsed, str):
        return parsed.startswith("Error:")
    if isinstance(parsed, dict):
        if parsed.get("error"):
            return True
        if parsed.get("errors") and not parsed.get("accounts") and not parsed.get("emails"):
            return True
    return False


def _is_expected_account_not_found(value: Any) -> bool:
    parsed = _parse_tool_result(value)
    if isinstance(parsed, dict):
        return parsed.get("error") == "account_not_found"
    if isinstance(parsed, str):
        return "account_not_found" in parsed
    return False
