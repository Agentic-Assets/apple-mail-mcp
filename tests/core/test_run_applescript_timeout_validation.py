"""``run_applescript`` timeout validation (AGENTIC-2369, first half).

Before this guard, ``timeout`` was the one ``run_applescript`` argument nobody
checked. A caller passing ``timeout=-5`` reached two places at once:

* the emitted AppleScript, as ``with timeout of -5 seconds`` — measured to
  compile and run clean on Darwin 25.5, so osascript never objects; and
* ``subprocess.run(timeout=-5)``, which treats the deadline as already expired
  and raises ``TimeoutExpired`` in ~2 ms, which this module then reported as
  ``AppleScriptTimeout("AppleScript execution timed out")``.

The result was a silent error channel: a caller-side argument bug arrived
looking like a slow mailbox. The two timeouts disagreed and the louder one lied.

``timeout`` is now refused, not clamped, before the single-flight lock is
taken. Clamping would replace one misdirection with another (a silently
different deadline) and would relocate the defect into Python where no
AppleScript probe can find it — the same reasoning the bounded-scan bounds use.

Every test here poisons ``subprocess.run`` so an accidental live ``osascript``
call fails loudly instead of touching Mail.app.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.core.applescript import (
    DEFAULT_TIMEOUT_S,
    MAX_TIMEOUT_S,
    run_applescript,
)

_SCRIPT = 'return "ok"'


def _poison_subprocess():
    """Patch ``subprocess.run`` to fail loudly on any live osascript attempt."""
    return patch(
        "subprocess.run",
        side_effect=AssertionError("test attempted a live osascript call; the timeout guard should have refused"),
    )


def _assert_refused(timeout, expected_fragment):
    """Assert *timeout* is refused as INVALID_TIMEOUT without reaching osascript."""
    with _poison_subprocess() as mock_run, pytest.raises(ToolError) as excinfo:
        run_applescript(_SCRIPT, timeout=timeout)
    error = excinfo.value
    assert error.code == "INVALID_TIMEOUT", (
        f"expected code='INVALID_TIMEOUT' for timeout={timeout!r}, got {error.code!r}"
    )
    assert expected_fragment in str(error), f"message for timeout={timeout!r} was {str(error)!r}"
    assert repr(timeout) in str(error), "the refusal must echo the offending value so the caller can see it"
    assert error.remediation, "INVALID_TIMEOUT must carry actionable remediation"
    assert not mock_run.called, (
        f"timeout={timeout!r} must be refused before osascript runs, "
        f"but subprocess.run was called {mock_run.call_count} time(s)."
    )


# ---------------------------------------------------------------------------
# Refusal: non-positive deadlines
# ---------------------------------------------------------------------------


def test_negative_timeout_is_refused_and_never_reaches_osascript():
    _assert_refused(-5, "must be greater than 0")


def test_zero_timeout_is_refused_and_never_reaches_osascript():
    """``0`` is refused for the same reason as a negative value.

    ``0`` is not a spelling of "no deadline" — ``subprocess.run(timeout=0)``
    was measured to raise ``TimeoutExpired`` immediately, and ``timeout=None``
    already owns the "use the default" slot. So ``0`` has no valid meaning.
    """
    _assert_refused(0, "must be greater than 0")


def test_negative_fractional_timeout_is_refused():
    _assert_refused(-0.5, "must be greater than 0")


# ---------------------------------------------------------------------------
# Refusal: absurd deadlines
# ---------------------------------------------------------------------------


def test_timeout_above_maximum_is_refused():
    """A deadline nobody can wait out starves every other Mail call.

    ``_LOCK_WAIT_TIMEOUT`` bounds how long a caller waits for the single-flight
    lock, but nothing bounded how long one call could *hold* it.
    """
    _assert_refused(MAX_TIMEOUT_S + 1, f"at most {MAX_TIMEOUT_S} seconds")


def test_timeout_that_would_overflow_subprocess_is_refused():
    """Above 2_147_483 s ``subprocess.run`` raises a bare ``OverflowError``.

    That is neither ``SubprocessError`` nor ``OSError``, so it escaped
    ``run_applescript``'s handlers unwrapped and surfaced as "timeout is too
    large" — an error naming neither AppleScript nor the offending argument.
    """
    _assert_refused(2**31, f"at most {MAX_TIMEOUT_S} seconds")


def test_non_numeric_timeout_is_refused():
    _assert_refused("120", "must be a number of seconds")


@pytest.mark.parametrize("value", [True, False])
def test_bool_timeout_is_refused(value):
    """``bool`` subclasses ``int``, so a bare isinstance check let it through.

    ``timeout=True`` would have become a 1-second deadline — a near-instant
    ``TimeoutExpired`` reported as ``AppleScriptTimeout``, i.e. the same
    "blame Mail.app for a caller bug" misdirection this guard exists to stop.
    Latent rather than live (the annotation says ``int | None``), but the
    check costs one clause.
    """
    _assert_refused(value, "must be a number of seconds")


# ---------------------------------------------------------------------------
# The mirror image: valid deadlines pass through untouched
# ---------------------------------------------------------------------------


def _run_with_captured_timeout(timeout_kwargs):
    """Run ``run_applescript`` against a stubbed osascript, returning the call."""
    completed = type("_Completed", (), {"returncode": 0, "stdout": b"ok", "stderr": b""})()
    with patch("subprocess.run", return_value=completed) as mock_run:
        result = run_applescript(_SCRIPT, **timeout_kwargs)
    assert mock_run.call_count == 1, f"expected exactly one osascript call, got {mock_run.call_count}"
    return result, mock_run.call_args


@pytest.mark.parametrize("timeout", [1, 30, 120, 300, MAX_TIMEOUT_S])
def test_valid_timeout_passes_through_unchanged(timeout):
    """Load-bearing: this is the path every Mail call in all 41 tools takes."""
    result, call = _run_with_captured_timeout({"timeout": timeout})
    assert call.kwargs["timeout"] == timeout, (
        f"valid timeout={timeout!r} must reach subprocess.run unmodified; got {call.kwargs['timeout']!r}"
    )
    assert call.args[0] == ["osascript", "-"]
    assert call.kwargs["input"] == _SCRIPT.encode("utf-8")
    assert result == "ok"


def test_none_timeout_still_means_the_default():
    result, call = _run_with_captured_timeout({"timeout": None})
    assert call.kwargs["timeout"] == DEFAULT_TIMEOUT_S
    assert result == "ok"


def test_default_argument_is_unchanged_when_timeout_is_omitted():
    result, call = _run_with_captured_timeout({})
    assert call.kwargs["timeout"] == DEFAULT_TIMEOUT_S
    assert result == "ok"


# ---------------------------------------------------------------------------
# The refusal must not disturb the shared lock
# ---------------------------------------------------------------------------


def test_refusal_does_not_leave_the_single_flight_lock_held():
    """A rejected call must not poison Mail access for every later caller."""
    from apple_mail_mcp.core.applescript import _MAIL_LOCK

    with _poison_subprocess(), pytest.raises(ToolError):
        run_applescript(_SCRIPT, timeout=-5)

    assert not _MAIL_LOCK.locked(), "an invalid timeout must be refused before the lock is acquired"
    _, call = _run_with_captured_timeout({"timeout": 30})
    assert call.kwargs["timeout"] == 30, "a valid call must still work after a refused one"
