"""Static enforcement tests for the bounded-scan contract.

These tests guard the Phase A whose-elimination invariants documented in
``tasks/whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md``.

Key rules they enforce (per
``tasks/whose-elimination-2026-05-22/05-codebase-whose-map.md`` § 1):

* ``every message of <mailbox> whose <predicate>`` is only allowed when
  the predicate is ``id is <numeric>`` or ``read status is <bool>`` — both
  hit Mail.app's fast indexes or are filtered against an already-sliced
  in-memory list. Anything else (``subject contains ...``, ``date
  received >= ...``) forces Mail to materialize the entire remote mailbox
  and is regression-prone on 24K+ inboxes.
* ``every message of <mailbox>`` with no ``whose`` clause at all is a
  raw enumeration and is banned outright.
* ``allow_full_scan`` was retired in v3.2.0 in favor of structured
  ``UNBOUNDED_SCAN_REQUIRED`` errors that point at ``full_inbox_export``.
  No tool may reintroduce the boolean kwarg.
"""

from __future__ import annotations

import inspect
import re
import unittest
from pathlib import Path
from typing import Iterable, Tuple

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "plugin" / "apple_mail_mcp" / "tools"

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Bans `every message of <mailbox> whose <X>` unless <X> is one of:
#   * ``id is ...``                   (Mail.app id index — O(1))
#   * ``read status is ...``          (boolean index, safe against pre-slice)
#   * ``{id_condition}``              (Python f-string interpolation that, by
#                                      convention in tools/manage.py, expands
#                                      from ``build_whose_id_list`` to an
#                                      ``id is X or id is Y`` chain — see
#                                      ``bounded_scan.build_whose_id_list``)
#   * ``"`` immediately after whose   (multi-line Python string assembly that
#                                      concatenates the predicate on the next
#                                      line; we validate the continuation
#                                      separately via the multi-line check)
DANGEROUS_WHOSE = re.compile(
    r"\bevery message of (?:\w+|__VAR__)\s+whose\s+(?!id is\b|read status is\b|\{id_condition\}|\")"
)

# Bans `every message of <mailbox>` with NO `whose` at all — that is a raw
# full-mailbox enumeration. Mailbox identifiers may end with ``Mailbox`` or
# be bare; in either case the next token must be ``whose``.
RAW_ENUMERATION = re.compile(r"\bevery message of (?:\w+|__VAR__)\b(?!\s+whose)")

# Normalize Python f-string placeholders so the static scan also catches
# `every message of {mailbox_var} whose ...` patterns — the original
# `\w+` token class never matched the curly-brace prefix. Only normalize
# the mailbox-position placeholder (i.e. an f-string brace immediately
# preceded by "every message of "). Other `{...}` substitutions (notably
# `{id_condition}` after `whose`) must remain so the allowlist regex
# negative lookahead still matches.
_MAILBOX_FSTRING = re.compile(r"(?<=every message of )\{[^}]+\}")


def _normalize_line(line: str) -> str:
    """Replace mailbox-position `{...}` placeholders with `__VAR__`."""
    return _MAILBOX_FSTRING.sub("__VAR__", line)

# Known offenders that pre-date this enforcement and whose fix lives in a
# follow-on PR. Each entry is (path_relative_to_tools, line_number,
# tracking_note). The test asserts the *exact* set so adding new offenders
# fails CI and removing a fixed offender also fails CI (prompting cleanup).
KNOWN_DANGEROUS_WHOSE: set[Tuple[str, int]] = {
    # `manage_drafts` subject lookup: `items 1 thru DRAFT_LIST_CAP of
    # (every message of draftsMailbox whose subject contains "...")` still
    # materializes the full Drafts whose-result before slicing. Drafts
    # mailbox is local and typically small, so the regression window is
    # narrow — but the pattern is the exact one Phase A is meant to ban.
    # Tracked for Phase B follow-up.
    ("compose.py", 142),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_tool_files() -> Iterable[Path]:
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        yield path


def _is_docstring_or_comment_line(line: str) -> bool:
    """Skip Python comment lines and prose lines inside RST docstrings.

    The docstring detector is intentionally narrow: it only excludes lines
    where the ``every message`` token appears inside RST ``\\`\\`...\\`\\```
    code spans, which is the only flavor of docstring quoting that appears
    in the current tool files. Adding new docstring patterns is fine — the
    regex won't match without `whose` immediately following, and prose
    that quotes the dangerous pattern uses backticks today.
    """
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return True
    if "``every message" in line or "``set " in line:
        return True
    return False


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class NoDangerousWhoseTests(unittest.TestCase):
    """Static scan of ``plugin/apple_mail_mcp/tools/*.py``."""

    def test_no_dangerous_whose_in_tools(self):
        found: set[Tuple[str, int]] = set()
        details: list[str] = []
        for path in _iter_tool_files():
            rel = path.name
            with path.open() as fh:
                for lineno, line in enumerate(fh, 1):
                    if _is_docstring_or_comment_line(line):
                        continue
                    if DANGEROUS_WHOSE.search(_normalize_line(line)):
                        key = (rel, lineno)
                        found.add(key)
                        details.append(f"{rel}:{lineno}: {line.rstrip()}")

        unexpected = found - KNOWN_DANGEROUS_WHOSE
        stale = KNOWN_DANGEROUS_WHOSE - found

        msg_parts = []
        if unexpected:
            msg_parts.append(
                "New dangerous `whose` patterns detected in tools/. Replace "
                "with `messages 1 thru N` slicing or id-filtered whose:\n  - "
                + "\n  - ".join(
                    line for line in details if (line.split(":", 2)[0], int(line.split(":", 2)[1])) in unexpected
                )
            )
        if stale:
            msg_parts.append(
                "KNOWN_DANGEROUS_WHOSE references lines that no longer "
                "match — remove these entries from the allowlist:\n  - "
                + "\n  - ".join(f"{p}:{n}" for p, n in sorted(stale))
            )

        self.assertFalse(msg_parts, "\n\n".join(msg_parts))

    def test_no_raw_every_message_enumeration_in_tools(self):
        offenders: list[str] = []
        for path in _iter_tool_files():
            rel = path.name
            with path.open() as fh:
                for lineno, line in enumerate(fh, 1):
                    if _is_docstring_or_comment_line(line):
                        continue
                    if RAW_ENUMERATION.search(_normalize_line(line)):
                        offenders.append(f"{rel}:{lineno}: {line.rstrip()}")

        self.assertEqual(
            offenders,
            [],
            "Raw `every message of <mailbox>` (no `whose`) is banned — "
            "use `messages 1 thru N` slicing instead. Offenders:\n  - "
            + "\n  - ".join(offenders),
        )

    def test_no_allow_full_scan_in_tools(self):
        offenders: list[str] = []
        for path in _iter_tool_files():
            rel = path.name
            with path.open() as fh:
                for lineno, line in enumerate(fh, 1):
                    if "allow_full_scan" in line:
                        offenders.append(f"{rel}:{lineno}: {line.rstrip()}")

        self.assertEqual(
            offenders,
            [],
            "`allow_full_scan` was retired in v3.2.0. Tools must raise "
            "`UNBOUNDED_SCAN_REQUIRED` and point at `full_inbox_export`. "
            "Offenders:\n  - " + "\n  - ".join(offenders),
        )

    def test_tool_signatures_have_no_allow_full_scan_param(self):
        # Import after the static checks so any import-time failures still
        # produce useful output on the file scans above.
        import apple_mail_mcp  # noqa: F401  (registers tools as side effect)
        from apple_mail_mcp.server import mcp

        offenders: list[str] = []
        # FastMCP exposes the registered tool functions via _tool_manager.
        tool_manager = getattr(mcp, "_tool_manager", None)
        self.assertIsNotNone(
            tool_manager,
            "FastMCP._tool_manager is missing — has FastMCP changed shape?",
        )
        for name, tool in tool_manager._tools.items():
            fn = getattr(tool, "fn", None)
            if fn is None:
                continue
            sig = inspect.signature(fn)
            if "allow_full_scan" in sig.parameters:
                offenders.append(f"{name}({', '.join(sig.parameters)})")

        self.assertEqual(
            offenders,
            [],
            "Tool signatures must not expose `allow_full_scan` (retired in "
            "v3.2.0):\n  - " + "\n  - ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
