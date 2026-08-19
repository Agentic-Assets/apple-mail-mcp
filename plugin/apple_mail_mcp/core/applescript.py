"""Low-level osascript execution, the runner Protocol, and the timeout exception."""

import subprocess
import threading
from typing import Protocol

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.core.escaping import _sanitize_for_json

DEFAULT_TIMEOUT_S = 120
# Upper bound on a single call's deadline. Two measured reasons, not style:
# (1) ``subprocess.run`` raises a bare ``OverflowError`` ("timeout is too
#     large") above 2_147_483 s (INT_MAX ms in poll()). That is neither
#     SubprocessError nor OSError, so the handlers below re-raise it unwrapped
#     and the caller sees an error naming neither AppleScript nor the argument.
# (2) ``_LOCK_WAIT_TIMEOUT`` bounds how long a caller *waits* for the
#     single-flight lock but nothing bounds how long one *holds* it. An
#     unbounded deadline lets one call starve every other Mail call in the
#     process. 3600 s is 12x the largest default any tool passes (300).
MAX_TIMEOUT_S = 3600


class AppleScriptRunner(Protocol):
    """Callable shape for injectable AppleScript runners."""

    def __call__(self, script: str, timeout: int | None = DEFAULT_TIMEOUT_S) -> str: ...


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


def _resolve_timeout(timeout: int | None) -> int | float:
    """Return the per-call deadline, refusing values osascript cannot honour.

    ``None`` means "use the default", not "no deadline". Non-positive values
    are refused rather than clamped: ``subprocess.run`` treats them as already
    expired and kills osascript within ~2 ms, then this module reports
    ``AppleScriptTimeout("AppleScript execution timed out")`` — blaming Mail.app
    for what is really a caller bug. Clamping would swap that misdirection for
    a silently different deadline; refusing names the actual cause. AppleScript
    itself never objects: ``with timeout of -5 seconds`` compiles and runs
    clean, so no osascript-side check can ever catch this.
    """
    if timeout is None:
        return DEFAULT_TIMEOUT_S
    # ``bool`` is a subclass of ``int``, so a bare isinstance check would let
    # ``timeout=True`` through as a 1-second deadline — a near-instant, silent
    # timeout blamed on Mail. Reject it as the non-number it is.
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ToolError(
            code="INVALID_TIMEOUT",
            message=f"timeout must be a number of seconds or None; got {timeout!r}.",
            remediation={"hint": f"Pass an integer in (0, {MAX_TIMEOUT_S}], or None for {DEFAULT_TIMEOUT_S}s."},
        )
    if timeout <= 0:
        raise ToolError(
            code="INVALID_TIMEOUT",
            message=f"timeout must be greater than 0 seconds; got {timeout!r}.",
            remediation={"hint": f"Pass a positive number of seconds, or None for the {DEFAULT_TIMEOUT_S}s default."},
        )
    if timeout > MAX_TIMEOUT_S:
        raise ToolError(
            code="INVALID_TIMEOUT",
            message=f"timeout must be at most {MAX_TIMEOUT_S} seconds; got {timeout!r}.",
            remediation={"hint": "Split the work into bounded calls instead of raising the per-call deadline."},
        )
    return timeout


def run_applescript(script: str, timeout: int | None = DEFAULT_TIMEOUT_S) -> str:
    """Execute AppleScript via stdin pipe for reliable multi-line handling.

    Raises ``AppleScriptTimeout`` (subclass of Exception) on per-call timeout
    so callers can isolate slow-account failures without losing siblings'
    partial results.

    Serializes the actual ``osascript`` invocation behind ``_MAIL_LOCK`` so
    only one AppleScript call runs against Mail.app at a time; callers that
    wait longer than ``_LOCK_WAIT_TIMEOUT`` seconds for their turn raise
    ``AppleScriptTimeout`` instead of queuing indefinitely.

    Raises ``ToolError(code="INVALID_TIMEOUT")`` for a non-positive or
    out-of-range ``timeout``, before the lock is taken, so a bad argument
    never queues behind live Mail work and never reaches ``osascript``.
    """
    effective_timeout = _resolve_timeout(timeout)
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
