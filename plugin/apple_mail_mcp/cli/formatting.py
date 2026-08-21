"""Output, parsing, and result-classification helpers shared across CLI commands.

No test patch seams live here, so callers import these helpers directly.

``_structured_error_envelope`` + ``_emit_tool_result`` are the CLI's error
contract: one JSON envelope (the ``ToolError.to_dict()`` shape from
``backend/base.py``) and a non-zero exit for every structured tool error.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from typing import Any

#: Exit status for a tool that answered with a structured error.
#:
#: Distinct from the argparse/usage exits (2) the command handlers already
#: return and from the ``130`` interrupt code, so a shell caller can tell
#: "you asked for something impossible" from "the request was valid and Mail
#: refused it".
TOOL_ERROR_EXIT_CODE = 1

#: ``code`` used when a tool reports failure without a machine-readable one.
#:
#: The CLI deliberately does not mint codes the tool layer never emits — the
#: catalogue in ``tools/CLAUDE.md`` § Structured error codes stays the single
#: vocabulary. An error whose text carries no code is reported honestly as
#: generic, with the tool's own wording preserved in ``message``.
GENERIC_TOOL_ERROR_CODE = "TOOL_ERROR"

_ERROR_TEXT_PREFIX = "Error:"

#: Keys the canonical envelope owns; everything else is preserved under ``details``.
_ENVELOPE_KEYS = ("error", "code", "message", "remediation")

# A leading identifier carrying at least one underscore, followed by a
# separator or end of string. Matches the codes tools already embed in
# otherwise-unstructured error text (``account_not_found — '...'``,
# ``FORWARD_DRAFT_ID_MISMATCH\n...``) without misreading an ordinary English
# first word ("Mailbox not found: ...", "offset must be >= 0") as a code.
_EMBEDDED_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?=$|[\s:.,;—-])")


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


def _error_code_from_text(text: str) -> str:
    """Return the code embedded at the start of *text*, else the generic code."""
    match = _EMBEDDED_CODE_RE.match(text.strip())
    return match.group(0).upper() if match else GENERIC_TOOL_ERROR_CODE


def _structured_error_envelope(value: Any) -> dict[str, Any] | None:
    """Return the canonical error envelope for *value*, or ``None`` if it is not an error.

    The CLI wraps the same tool functions the MCP server registers, and those
    answer failures in three different ways: the canonical
    ``ToolError.to_dict()`` envelope (``backend/base.py``), an ad-hoc dict
    whose ``error`` key is a snake_case token beside success-shaped keys
    (``search_emails``' ``{"results": [], "total": 0, "error":
    "account_not_found", ...}``), or a bare ``"Error: ..."`` string that
    ``_print_result`` used to hand back as ``{"result": "Error: ..."}`` with
    no code at all. A ``--json`` consumer needs one code path, so every one of
    those is normalized here to the shape agents already get at the MCP
    boundary: ``{"error": true, "code", "message", "remediation"}``.

    Whole-result errors only. A payload that carries results *and* a plural
    ``errors``/``error_details`` list is a partial failure (one mailbox of
    many timed out) and is deliberately left alone — it is a success with
    caveats, and ``_result_is_error`` keeps owning that classification for
    the perf battery.

    Keys the tool sent alongside the error (``available_accounts``,
    ``account``, ...) survive under ``details`` so normalizing the shape never
    costs the caller its recovery information.
    """
    parsed = _parse_tool_result(value)

    if isinstance(parsed, str):
        stripped = parsed.strip()
        if not stripped.startswith(_ERROR_TEXT_PREFIX):
            return None
        detail = stripped[len(_ERROR_TEXT_PREFIX) :].strip()
        return {
            "error": True,
            "code": _error_code_from_text(detail),
            "message": detail or stripped,
            "remediation": {},
        }

    if not isinstance(parsed, dict):
        return None
    flag = parsed.get("error")
    if not flag:
        return None

    code = parsed.get("code")
    if not isinstance(code, str) or not code:
        code = _error_code_from_text(flag) if isinstance(flag, str) else GENERIC_TOOL_ERROR_CODE

    message = parsed.get("message")
    if not isinstance(message, str) or not message:
        message = flag if isinstance(flag, str) else str(flag)

    remediation = parsed.get("remediation")
    envelope: dict[str, Any] = {
        "error": True,
        "code": code,
        "message": message,
        "remediation": remediation if isinstance(remediation, dict) else {},
    }
    details = {key: item for key, item in parsed.items() if key not in _ENVELOPE_KEYS}
    if details:
        envelope["details"] = details
    return envelope


def _emit_tool_result(result: Any, *, json_mode: bool) -> int:
    """Print *result* and return the exit status it deserves.

    Success prints unchanged. A structured error prints the canonical envelope
    under ``--json`` (text mode keeps the tool's human wording) and exits
    non-zero, so ``if ! apple-mail ...`` finally detects the failures that
    used to arrive as exit 0.
    """
    envelope = _structured_error_envelope(result)
    if envelope is None:
        return _print_result(result, json_mode=json_mode)
    if json_mode:
        print(json.dumps(envelope, indent=2, ensure_ascii=False))
    else:
        _print_result(result, json_mode=False)
    return TOOL_ERROR_EXIT_CODE


def _run_tool(func: Callable[..., Any], json_mode: bool, **kwargs: Any) -> int:
    try:
        result = func(**kwargs)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        return _emit_tool_result(result, json_mode=json_mode)
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
