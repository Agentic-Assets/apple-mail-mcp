"""Low-level osascript execution, the runner Protocol, and the timeout exception."""

import subprocess
import threading
from typing import Protocol

from apple_mail_mcp.core.escaping import _sanitize_for_json


class AppleScriptRunner(Protocol):
    """Callable shape for injectable AppleScript runners."""

    def __call__(self, script: str, timeout: int | None = 120) -> str: ...


class AppleScriptTimeout(Exception):
    """Raised when an AppleScript invocation exceeds its per-call timeout."""


# Mail.app's AppleScript bridge is effectively single-threaded: concurrent
# osascript invocations (from parallel tool calls or internal fan-out) thrash
# Mail.app instead of running in parallel, causing CPU spin and timeouts.
# This lock makes every subprocess.run(["osascript", ...]) call single-flight
# across the whole process. It is a plain threading.Lock (not RLock, not an
# asyncio primitive) because run_applescript is a synchronous function called
# both from asyncio.to_thread worker threads and from the plain-sync CLI; a
# blocking, thread-safe mutex is what both call paths need.
_MAIL_LOCK = threading.Lock()
_LOCK_WAIT_TIMEOUT = 300


def run_applescript(script: str, timeout: int | None = 120) -> str:
    """Execute AppleScript via stdin pipe for reliable multi-line handling.

    Raises ``AppleScriptTimeout`` (subclass of Exception) on per-call timeout
    so callers can isolate slow-account failures without losing siblings'
    partial results.

    Serializes the actual ``osascript`` invocation behind ``_MAIL_LOCK`` so
    only one AppleScript call runs against Mail.app at a time; callers that
    wait longer than ``_LOCK_WAIT_TIMEOUT`` seconds for their turn raise
    ``AppleScriptTimeout`` instead of queuing indefinitely.
    """
    effective_timeout = 120 if timeout is None else timeout
    if not _MAIL_LOCK.acquire(timeout=_LOCK_WAIT_TIMEOUT):
        raise AppleScriptTimeout("AppleScript queued too long waiting for Mail.app to become available")
    try:
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
    finally:
        _MAIL_LOCK.release()
