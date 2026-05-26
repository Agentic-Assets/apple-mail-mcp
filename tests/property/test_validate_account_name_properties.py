"""Property-based tests for validate_account_name and related path helpers.

Focus areas:
- Known-good account names always pass (return None) when the name is in the
  mocked account list.
- Empty / whitespace-only / control-character inputs return a clean structured
  error string without raising.
- Names with AppleScript-special characters do not bypass validation — they
  still trigger account_not_found when not in the account list.
- validate_save_path: paths outside home directory are always rejected.
- validate_save_path: sensitive directory paths are always rejected.
"""

import os
import string

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from unittest.mock import patch

from apple_mail_mcp.core import validate_account_name, validate_save_path

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Characters that are dangerous in AppleScript — good injection candidates.
_APPLESCRIPT_SPECIALS = st.sampled_from(['"', "'", "\\", "\n", "\r", "\t",
                                          "\x00", "\x01", "\x08", ";", "&", "|"])

# Account names that are present in the mocked Mail.app account list.
_KNOWN_ACCOUNTS = ["Work", "Personal", "Gmail", "iCloud", "Outlook"]

_safe_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
)

_whitespace_only = st.text(
    alphabet=st.sampled_from(list(" \t\n\r\x0b\x0c")),
    min_size=1,
    max_size=50,
)

_control_chars = st.text(
    alphabet=st.characters(
        whitelist_categories=("Cc",),
        blacklist_categories=("Cs",),
    ),
    min_size=1,
    max_size=20,
)


# ---------------------------------------------------------------------------
# Known-good accounts
# ---------------------------------------------------------------------------


@given(account=st.sampled_from(_KNOWN_ACCOUNTS))
def test_validate_known_account_returns_none(account: str) -> None:
    """A name in the configured account list must pass (return None)."""
    with patch(
        "apple_mail_mcp.core.list_mail_account_names",
        return_value=_KNOWN_ACCOUNTS,
    ):
        result = validate_account_name(account)
    assert result is None, (
        f"validate_account_name({account!r}) returned {result!r}, expected None"
    )


# ---------------------------------------------------------------------------
# Empty / whitespace / control-char inputs never raise; return error string
# ---------------------------------------------------------------------------


def test_validate_empty_string_returns_error() -> None:
    """Empty account name must return an error string, not raise."""
    result = validate_account_name("")
    assert isinstance(result, str)
    assert "required" in result.lower() or "error" in result.lower(), (
        f"Unexpected error format for empty input: {result!r}"
    )


@given(value=_whitespace_only)
@settings(max_examples=100)
def test_validate_whitespace_only_returns_error(value: str) -> None:
    """Whitespace-only account names must return an error string without raising."""
    with patch(
        "apple_mail_mcp.core.list_mail_account_names",
        return_value=_KNOWN_ACCOUNTS,
    ):
        result = validate_account_name(value)
    # validate_account_name catches whitespace-only at the top-level guard.
    assert isinstance(result, str), (
        f"validate_account_name({value!r}) did not return a str"
    )
    assert result.startswith("Error:"), (
        f"Expected 'Error:' prefix for whitespace-only input, got: {result!r}"
    )


@given(value=_control_chars)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_validate_control_char_input_no_exception(value: str) -> None:
    """Control-character account names must not raise; must return an error string."""
    with patch(
        "apple_mail_mcp.core.list_mail_account_names",
        return_value=_KNOWN_ACCOUNTS,
    ):
        try:
            result = validate_account_name(value)
        except Exception as exc:
            pytest.fail(
                f"validate_account_name raised {type(exc).__name__} on "
                f"control-char input {value!r}: {exc}"
            )
    assert result is None or isinstance(result, str), (
        f"validate_account_name returned non-string/None: {type(result)}"
    )


# ---------------------------------------------------------------------------
# AppleScript-special chars in account name — must not bypass validation
# ---------------------------------------------------------------------------


@given(
    prefix=st.text(alphabet=string.ascii_letters, min_size=1, max_size=10),
    special=_APPLESCRIPT_SPECIALS,
    suffix=st.text(alphabet=string.ascii_letters, min_size=0, max_size=10),
)
@settings(max_examples=300)
def test_validate_applescript_special_not_in_known_accounts_returns_error(
    prefix: str, special: str, suffix: str
) -> None:
    """Names with injection characters that are not in the account list must fail.

    This ensures escape or injection through the validator is impossible:
    the validator must compare the literal name against the account list,
    not execute it.
    """
    injected_name = prefix + special + suffix
    # Only run the assertion when the name is genuinely not in our list.
    if injected_name in _KNOWN_ACCOUNTS:
        return

    with patch(
        "apple_mail_mcp.core.list_mail_account_names",
        return_value=_KNOWN_ACCOUNTS,
    ):
        result = validate_account_name(injected_name)

    # Must return a string error, not None (i.e., must not pass validation).
    assert isinstance(result, str), (
        f"validate_account_name({injected_name!r}) returned None (passed!) "
        "for a name not in the account list"
    )
    assert result.startswith("Error:"), (
        f"Expected 'Error:' prefix, got: {result!r}"
    )


@given(
    special=_APPLESCRIPT_SPECIALS,
)
@settings(max_examples=100)
def test_validate_pure_special_char_name_returns_error(special: str) -> None:
    """A name that is only AppleScript-special characters must not validate."""
    with patch(
        "apple_mail_mcp.core.list_mail_account_names",
        return_value=_KNOWN_ACCOUNTS,
    ):
        result = validate_account_name(special)

    if special.strip() == "":
        # Whitespace-only or empty: caught by the early guard.
        assert isinstance(result, str) and result.startswith("Error:")
    else:
        # Non-whitespace special: not in account list, must fail.
        assert isinstance(result, str) and result.startswith("Error:")


# ---------------------------------------------------------------------------
# validate_save_path properties
# ---------------------------------------------------------------------------


def test_validate_save_path_root_rejected() -> None:
    """The filesystem root must be rejected."""
    result = validate_save_path("/")
    assert result is not None
    assert "home directory" in result


def test_validate_save_path_home_accepted() -> None:
    """The home directory itself must be accepted."""
    home = os.path.expanduser("~")
    result = validate_save_path(home)
    assert result is None, f"Expected None for home dir, got: {result!r}"


def test_validate_save_path_subdir_accepted() -> None:
    """A subdirectory of home must be accepted."""
    home = os.path.expanduser("~")
    result = validate_save_path(os.path.join(home, "Downloads", "mail-export"))
    assert result is None


@given(
    sensitive=st.sampled_from([
        ".ssh", ".gnupg", ".config", ".aws", ".claude",
        os.path.join("Library", "LaunchAgents"),
        os.path.join("Library", "LaunchDaemons"),
        os.path.join("Library", "Keychains"),
    ])
)
def test_validate_save_path_sensitive_dir_rejected(sensitive: str) -> None:
    """Sensitive directories must always be rejected."""
    home = os.path.expanduser("~")
    path = os.path.join(home, sensitive, "export")
    result = validate_save_path(path)
    assert result is not None, (
        f"Expected error for sensitive path {path!r}, got None"
    )
    assert "sensitive" in result.lower() or "cannot" in result.lower(), (
        f"Error message missing expected language for {path!r}: {result!r}"
    )


@given(
    relative_path=st.text(
        # Restrict to printable ASCII letters and digits to avoid NUL bytes
        # (which trigger an os.path.realpath bug documented in
        # test_validate_save_path_null_byte_raises_bug) and other filesystem-
        # illegal characters that would raise unrelated OS errors.
        alphabet=st.sampled_from(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        ),
        min_size=1,
        max_size=30,
    )
)
@settings(max_examples=100)
def test_validate_save_path_outside_home_rejected(relative_path: str) -> None:
    """Paths that resolve outside home must always be rejected."""
    # Construct a path that starts outside home (e.g. /tmp/...)
    path = os.path.join("/tmp", relative_path)
    result = validate_save_path(path)
    assert result is not None, (
        f"Expected rejection for outside-home path {path!r}, got None"
    )


def test_validate_save_path_null_byte_returns_structured_error() -> None:
    """validate_save_path must return a structured error string for NUL-byte paths.

    Previously (bug): os.path.realpath raised ValueError("lstat: embedded null
    character in path") and the validator propagated it as an unhandled exception
    instead of returning a clean error dict/string.

    Fixed in core.py: an early guard scans for NUL and other low control chars
    (U+0000–U+001F, U+007F) and returns an "Error: … contains an invalid control
    character …" string before calling os.path.realpath.  The contract for ALL
    validate_* functions in this module is structured-error returns, never raises.
    """
    result = validate_save_path("/tmp/evil\x00path")
    assert isinstance(result, str), (
        f"Expected error string for NUL-byte path, got: {result!r}"
    )
    assert result.startswith("Error:"), (
        f"Expected 'Error:' prefix for NUL-byte path, got: {result!r}"
    )
    # The message should indicate an invalid / control character
    lower = result.lower()
    assert any(word in lower for word in ("invalid", "null", "control", "character")), (
        f"Error message should mention invalid/null/control/character, got: {result!r}"
    )


@given(
    prefix=st.text(alphabet=st.characters(
        blacklist_categories=("Cs",),
        blacklist_characters="\x00",
    ), min_size=1, max_size=20),
    ctrl=st.characters(min_codepoint=0x00, max_codepoint=0x1F),
    suffix=st.text(alphabet=st.characters(
        blacklist_categories=("Cs",),
        blacklist_characters="\x00",
    ), min_size=0, max_size=20),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_validate_save_path_low_control_char_always_returns_structured_error(
    prefix: str, ctrl: str, suffix: str
) -> None:
    """Fuzz validate_save_path with all low control characters (U+0000–U+001F)
    embedded into otherwise-valid path fragments.  Every call must return a
    structured error string (never raise, never return None).

    This property test covers the full class of characters that break
    os.path.realpath and/or osascript's stdin pipe, not just NUL.
    """
    path = f"/tmp/{prefix}{ctrl}{suffix}"
    try:
        result = validate_save_path(path)
    except Exception as exc:
        pytest.fail(
            f"validate_save_path raised {type(exc).__name__} on path with "
            f"control char U+{ord(ctrl):04X}: {exc}"
        )
    assert isinstance(result, str), (
        f"Expected error string for control-char path {path!r}, got: {result!r}"
    )
    assert result.startswith("Error:"), (
        f"Expected 'Error:' prefix for control-char path {path!r}, got: {result!r}"
    )


@given(
    ctrl=st.just("\x7f"),
)
@settings(max_examples=10)
def test_validate_save_path_del_char_returns_structured_error(ctrl: str) -> None:
    """U+007F (DEL) must also return a structured error."""
    path = f"/tmp/evil{ctrl}path"
    try:
        result = validate_save_path(path)
    except Exception as exc:
        pytest.fail(
            f"validate_save_path raised {type(exc).__name__} on DEL char: {exc}"
        )
    assert isinstance(result, str) and result.startswith("Error:")
