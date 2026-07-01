"""User-string escaping and JSON-safe output sanitizing for AppleScript and MCP stdio."""

import re


def escape_applescript(value: str) -> str:
    """Escape a string for safe injection into AppleScript double-quoted strings.

    Handles backslashes first, then double quotes, then newlines/returns/tabs,
    and Unicode line/paragraph separators to prevent injection and AppleScript
    syntax errors.
    """
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        # Unicode line/paragraph separators can break AppleScript string parsing
        .replace("\u2028", "\\n")
        .replace("\u2029", "\\n")
    )


# Compiled regex for stripping ASCII control characters (except \n and \t)
# from AppleScript output. Covers \x00-\x08, \x0b, \x0c, \x0e-\x1f.
# Runs on every tool output — a compiled sub() is ~6x faster than a
# char-by-char generator join on large outputs (e.g. 24K-row exports).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitize_for_json(text: str) -> str:
    """Sanitize text for safe JSON serialization over MCP stdio transport.

    Preserves Unicode (including Cyrillic, CJK, Arabic, etc.) while
    stripping control characters.
    """
    # Normalize line endings first (AppleScript uses \r)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Strip control characters but keep \n, \t, and all printable Unicode
    return _CONTROL_CHARS_RE.sub("", text)


def sanitize_pipe_delimited_field(var_name: str) -> str:
    """Return an AppleScript snippet that strips ``|||`` and line-breaks from *var_name*.

    Emitted scripts pipe-join per-message fields (``subject|||sender|||...``)
    and the Python parser splits on the same sequence. A subject that
    legitimately contains ``|||`` would shift every subsequent field
    right, mapping the wrong ``message_id`` onto the wrong email — and
    if that corrupted id is later passed to ``manage_trash(action=
    "delete_permanent")``, a different message is permanently deleted.

    This snippet runs in-place on *var_name* (set in caller scope by
    e.g. ``set messageSubject to subject of aMessage``) and replaces:

    * ``|||`` → ``| | |``  (preserves visible content, prevents split corruption)
    * embedded CR/LF/tab → single space  (prevents per-line split corruption)

    The Python parser also defensively validates field counts; this is
    the AppleScript half of a belt-and-suspenders defense.
    """
    return f"""
                            try
                                set AppleScript's text item delimiters to "|||"
                                set _amm_parts to text items of {var_name}
                                set AppleScript's text item delimiters to "| | |"
                                set {var_name} to _amm_parts as string
                                set AppleScript's text item delimiters to {{return, linefeed, tab}}
                                set _amm_parts to text items of {var_name}
                                set AppleScript's text item delimiters to " "
                                set {var_name} to _amm_parts as string
                                set AppleScript's text item delimiters to ""
                            end try"""
