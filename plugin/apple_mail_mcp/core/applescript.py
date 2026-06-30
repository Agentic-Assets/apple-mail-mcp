"""Low-level osascript execution, the runner Protocol, and the timeout exception."""

import subprocess
from typing import Protocol

from apple_mail_mcp.core.escaping import _sanitize_for_json


class AppleScriptRunner(Protocol):
    """Callable shape for injectable AppleScript runners."""

    def __call__(self, script: str, timeout: int | None = 120) -> str: ...


class AppleScriptTimeout(Exception):
    """Raised when an AppleScript invocation exceeds its per-call timeout."""


def run_applescript(script: str, timeout: int | None = 120) -> str:
    """Execute AppleScript via stdin pipe for reliable multi-line handling.

    Raises ``AppleScriptTimeout`` (subclass of Exception) on per-call timeout
    so callers can isolate slow-account failures without losing siblings'
    partial results.
    """
    effective_timeout = 120 if timeout is None else timeout
    try:
        result = subprocess.run(
            ["osascript", "-"],
            input=script.encode("utf-8"),
            capture_output=True,
            timeout=effective_timeout,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            if stderr:
                raise Exception(f"AppleScript error: {stderr}")
            raise Exception(f"AppleScript exited with code {result.returncode} (no stderr)")
        output = result.stdout.decode("utf-8", errors="replace").strip()
        return _sanitize_for_json(output)
    except subprocess.TimeoutExpired as exc:
        raise AppleScriptTimeout("AppleScript execution timed out") from exc
    except AppleScriptTimeout:
        raise
    except (subprocess.SubprocessError, OSError) as exc:
        raise Exception(f"AppleScript execution failed: {exc}") from exc
    except Exception:
        raise
