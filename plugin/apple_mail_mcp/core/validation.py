"""Account-name resolution/validation and save-path safety.

The cross-helper calls (``list_mail_account_names``, ``validate_account_name``,
``account_not_found_json``, ``run_applescript``) are routed through the
``apple_mail_mcp.core`` package namespace so the autouse conftest patches
(``patch('apple_mail_mcp.core.<name>')``) reach them at call time. Access is
lazy (inside functions) so partial package init during import is safe.
"""

import json
import os
from pathlib import Path

from apple_mail_mcp import core


def list_mail_account_names(timeout: int | None = 30) -> list[str]:
    """Return configured Mail account names. Cheap (<1s) on any setup."""
    script = """
    tell application "Mail"
        set acctNames to {}
        repeat with anAccount in (every account)
            set end of acctNames to (name of anAccount)
        end repeat
        set AppleScript's text item delimiters to linefeed
        return acctNames as string
    end tell
    """
    raw = core.run_applescript(script, timeout=timeout)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def validate_account_name(account: str, timeout: int | None = 30) -> str | None:
    """Return an error string when *account* is unknown, else None."""
    if not account or not str(account).strip():
        return "Error: account name is required"
    names = core.list_mail_account_names(timeout=timeout)
    if account not in names:
        available = ", ".join(names) if names else "(none configured)"
        return f"Error: account_not_found — '{account}' is not configured in Mail. Available accounts: {available}"
    return None


SENSITIVE_DIRS = (
    ".ssh",
    ".gnupg",
    ".config",
    ".aws",
    ".claude",
    "Library/LaunchAgents",
    "Library/LaunchDaemons",
    "Library/Keychains",
)


def validate_save_path(
    path: str,
    *,
    path_label: str = "Save path",
    sensitive_action: str = "export emails to",
) -> str | None:
    """Return an error string when *path* is outside home or under a sensitive dir."""
    # Guard against NUL bytes and other low control characters that cause
    # os.path.realpath to raise ValueError ("embedded null character in path").
    # All characters in range U+0000–U+001F and U+007F are invalid in filesystem
    # paths and would also break osascript's stdin pipe.
    for ch in path:
        cp = ord(ch)
        if cp <= 0x1F or cp == 0x7F:
            return (
                f"Error: {path_label} contains an invalid control character "
                f"(U+{cp:04X}). Null bytes and control characters are not allowed in paths."
            )
    home_dir = str(Path.home().resolve())
    resolved = str(Path(path).expanduser().resolve())

    if not resolved.startswith(home_dir + os.sep) and resolved != home_dir:
        return f"Error: {path_label} must be under your home directory ({home_dir}). Got: {resolved}"

    for rel in SENSITIVE_DIRS:
        sensitive_dir = str(Path(home_dir) / rel)
        if resolved.startswith(sensitive_dir + os.sep) or resolved == sensitive_dir:
            return f"Error: Cannot {sensitive_action} sensitive directory: {sensitive_dir}"

    return None


def account_not_found_json(account: str, timeout: int | None = 30) -> str:
    """Structured JSON error for unknown account names."""
    names = core.list_mail_account_names(timeout=timeout)
    return json.dumps(
        {
            "error": "account_not_found",
            "account": account,
            "available_accounts": names,
            "emails": [],
        },
        indent=2,
    )


def reject_unknown_account(
    account: str,
    *,
    timeout: int | None = None,
    json_error: bool = False,
) -> str | None:
    """Return an error response string when *account* is unknown, else None."""
    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = core.validate_account_name(account, timeout=validation_timeout)
    if not account_err:
        return None
    if json_error:
        return core.account_not_found_json(account, timeout=validation_timeout)
    return account_err
