"""Regression tests for validate_save_path in core.py.

Covers:
- NUL-byte and other low control characters must return a structured error,
  not raise ValueError (previously a bug — os.path.realpath raised on NUL).
- Happy path: normal paths return None (valid) or the expected error string.
"""

import os

import pytest

from apple_mail_mcp.core import validate_save_path

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_validate_save_path_normal_eml_returns_none() -> None:
    """A normal path under home with no control chars must pass (return None)."""
    home = os.path.expanduser("~")
    result = validate_save_path(os.path.join(home, "tmp", "normal.eml"))
    assert result is None, f"Expected None for normal path, got: {result!r}"


def test_validate_save_path_home_itself_accepted() -> None:
    """Home directory itself is accepted."""
    home = os.path.expanduser("~")
    result = validate_save_path(home)
    assert result is None


# ---------------------------------------------------------------------------
# NUL byte and low control characters — must return structured error, not raise
# ---------------------------------------------------------------------------

_CONTROL_CHARS = [
    "\x00",   # NUL — triggers os.path.realpath ValueError (the original bug)
    "\x01",   # SOH
    "\x1f",   # Unit separator
    "\x7f",   # DEL
    "\r",     # bare CR
    "\n",     # bare LF
]


@pytest.mark.parametrize("ctrl", _CONTROL_CHARS, ids=[repr(c) for c in _CONTROL_CHARS])
def test_validate_save_path_control_char_returns_structured_error(ctrl: str) -> None:
    """validate_save_path must return a structured error string (not raise) for
    any path that contains a control character that would fail os.path.realpath.

    This is a regression test for the NUL-byte bug: previously the function
    raised ValueError instead of returning an error string.
    """
    path = f"/tmp/evil{ctrl}path"
    # Must not raise — any exception is a bug
    try:
        result = validate_save_path(path)
    except Exception as exc:
        pytest.fail(
            f"validate_save_path raised {type(exc).__name__} on path with "
            f"{ctrl!r}: {exc}"
        )
    # Must return a string error, not None (the path is both outside home
    # and contains an invalid character — either guard suffices)
    assert isinstance(result, str), (
        f"validate_save_path({path!r}) returned {result!r}, expected error string"
    )
    assert result.startswith("Error:"), (
        f"Expected 'Error:' prefix in structured error, got: {result!r}"
    )


def test_validate_save_path_null_byte_error_message_mentions_invalid() -> None:
    """The error message for a NUL-byte path should be human-readable."""
    result = validate_save_path("/tmp/evil\x00path")
    assert result is not None
    assert isinstance(result, str)
    # Should mention 'invalid' or 'null' or 'control' to help debuggers
    lower = result.lower()
    assert any(word in lower for word in ("invalid", "null", "control", "character")), (
        f"Error message should mention invalid/null/control/character, got: {result!r}"
    )
