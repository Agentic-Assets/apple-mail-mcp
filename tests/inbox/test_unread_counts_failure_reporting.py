"""Regressions for ``get_mailbox_unread_counts`` nested-path failure reporting.

The nested (``summary_only=False``) path wrapped the per-account block, the
per-mailbox ``unread count`` read, and the child enumeration in bare
``try ... end try`` blocks. Consequences, all of which looked like success:

* an offline or mid-resyncing account contributed **zero rows and no marker**,
  so it vanished from the response entirely;
* a per-mailbox throw dropped that mailbox **and its whole child subtree**,
  because one shared ``try`` wrapped the parent read and the child loop;
* under the default ``include_zero=False`` a dropped mailbox was byte-identical
  to a zero-unread one.

The same file's ``summary_only`` path had done this correctly since it shipped:
its ``on error`` arm appends ``accountName & ":ERROR"`` and the Python side maps
it to ``-1``. These tests pin the nested path onto that same sentinel, plus an
``__errors__`` list for attribution, and pin the structural property the fix
depends on: one ``try`` per read, so a parent failure cannot take its children
with it.

``apple_mail_mcp.tools.inbox.run_applescript`` is the only Mail seam used.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.tools import inbox as inbox_tools
from apple_mail_mcp.tools.inbox.unread_counts import (
    ERRORS_KEY,
    PROVENANCE_KEY,
    UNREAD_COUNT_UNAVAILABLE,
)

# Reduced to the shape that triggers the bug: a Mail throw is just text.
_MAIL_ERROR = "Mail got an error: Can't get unread count of mailbox."

_TRY_OPEN = re.compile(r"^\s*try\s*$")
_TRY_END = re.compile(r"^\s*end\s+try\b")
_ON_ERROR = re.compile(r"^\s*on\s+error\b")


def _capture_nested_script(**kwargs) -> str:
    """Return the AppleScript the nested path sends to ``run_applescript``."""
    captured: dict[str, str] = {}

    def fake_run(script, timeout=120):
        captured["script"] = script
        return ""

    with patch("apple_mail_mcp.tools.inbox.run_applescript", side_effect=fake_run):
        inbox_tools.get_mailbox_unread_counts(**kwargs)
    return captured.get("script", "")


def _bare_try_line_numbers(script: str) -> list[int]:
    """1-based line numbers of ``try`` blocks with no ``on error`` arm.

    A local, deliberately small nesting walk: the point is to assert on the
    *emitted* script rather than on the source module, which is what the
    package-wide ratchet in ``tests/core/test_no_bare_applescript_try.py``
    already does.

    The shared ``core.sanitize_pipe_delimited_field`` fragment (recognizable by
    its ``_amm_parts`` variable) carries its own deliberate bare ``try`` and is
    baselined at its definition site, so it is not counted against callers.
    """
    stack: list[tuple[int, bool, list[str]]] = []
    bare: list[int] = []
    for index, raw_line in enumerate(script.split("\n"), start=1):
        line = raw_line.split("--", 1)[0]
        if _TRY_OPEN.match(line):
            stack.append((index, False, []))
        elif _ON_ERROR.match(line) and stack:
            start, _, body = stack[-1]
            stack[-1] = (start, True, body)
        elif _TRY_END.match(line) and stack:
            start, handled, body = stack.pop()
            if not handled and not any("_amm_parts" in entry for entry in body):
                bare.append(start)
        elif stack:
            stack[-1][2].append(line)
    return bare


class NestedUnreadCountScriptStructureTests(unittest.TestCase):
    """The emitted AppleScript, not the parser, is where the drop happened."""

    def test_nested_script_has_no_bare_try(self):
        script = _capture_nested_script(account="Work")
        self.assertTrue(script, "no script captured")
        self.assertEqual(
            _bare_try_line_numbers(script),
            [],
            "every try in the nested unread-count script must carry a reporting `on error` arm",
        )

    def test_parent_count_read_closes_before_child_enumeration(self):
        """A throwing parent must not take its child subtree with it."""
        script = _capture_nested_script(account="Work")
        parent_read = script.index("set unreadCount to unread count of aMailbox")
        child_bind = script.index("set subMailboxes to")
        between = script[parent_read:child_bind]
        self.assertIn(
            "end try",
            between,
            "the parent `unread count` read must sit in its own try that closes "
            "before child enumeration starts, or one throw drops the whole subtree",
        )

    def test_child_loop_reads_are_individually_guarded(self):
        """One unreadable child must not abort the remaining children."""
        script = _capture_nested_script(account="Work")
        child_loop = script.index("repeat with subBox in subMailboxes")
        loop_body = script[child_loop : script.index("end repeat", child_loop)]
        self.assertIn("try", loop_body)
        self.assertIn("on error", loop_body)

    def test_account_block_reports_its_own_failure(self):
        script = _capture_nested_script(account="Work")
        self.assertIn("__ACCOUNT__|||ERROR:", script)

    def test_error_detail_is_sanitized_before_it_joins_a_pipe_row(self):
        """Untrusted Mail text in a `|||` row can shift later fields."""
        script = _capture_nested_script(account="Work")
        self.assertIn("set _amm_parts to text items of errorDetail", script)


class NestedUnreadCountReportingTests(unittest.TestCase):
    """What the caller actually receives for each failure shape."""

    def _counts(self, raw: str, **kwargs):
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            return inbox_tools.get_mailbox_unread_counts(account="Work", **kwargs)

    def test_failed_mailbox_is_reported_not_dropped(self):
        raw = "\n".join(
            [
                "Work|||INBOX|||4",
                f"Work|||Archive|||ERROR:{_MAIL_ERROR}",
            ]
        )
        result = self._counts(raw)

        self.assertEqual(result["Work"]["INBOX"], 4)
        self.assertEqual(result["Work"]["Archive"], UNREAD_COUNT_UNAVAILABLE)
        self.assertEqual(result["Work"][ERRORS_KEY], [f"Archive: {_MAIL_ERROR}"])

    def test_failed_mailbox_is_distinguishable_from_a_zero_unread_one(self):
        """The whole point: -1 is not 0, and 0 is not "we never read it"."""
        raw = "\n".join(
            [
                "Work|||Quiet|||0",
                f"Work|||Broken|||ERROR:{_MAIL_ERROR}",
            ]
        )
        result = self._counts(raw, include_zero=True)

        self.assertEqual(result["Work"]["Quiet"], 0)
        self.assertEqual(result["Work"]["Broken"], UNREAD_COUNT_UNAVAILABLE)
        self.assertNotIn("Quiet", " ".join(result["Work"][ERRORS_KEY]))

    def test_failed_parent_still_reports_its_children(self):
        """A parent that threw must not erase the child rows that succeeded."""
        raw = "\n".join(
            [
                f"Work|||INBOX|||ERROR:{_MAIL_ERROR}",
                "Work|||INBOX/Team|||2",
                "Work|||INBOX/Alerts|||7",
            ]
        )
        result = self._counts(raw)

        self.assertEqual(result["Work"]["INBOX"], UNREAD_COUNT_UNAVAILABLE)
        self.assertEqual(result["Work"]["INBOX/Team"], 2)
        self.assertEqual(result["Work"]["INBOX/Alerts"], 7)

    def test_whole_account_failure_surfaces_as_an_error_not_an_absence(self):
        raw = "\n".join(
            [
                "Work|||INBOX|||3",
                f"Offline|||__ACCOUNT__|||ERROR:{_MAIL_ERROR}",
            ]
        )
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=raw):
            result = inbox_tools.get_mailbox_unread_counts()

        self.assertIn("Offline", result)
        self.assertEqual(result["Offline"][ERRORS_KEY], [f"account: {_MAIL_ERROR}"])
        # The account contributed no counts, and none were invented for it.
        self.assertEqual([k for k in result["Offline"] if not k.startswith("__")], [])

    def test_child_enumeration_failure_names_the_missing_subtree(self):
        raw = "\n".join(
            [
                "Work|||INBOX|||1",
                f"Work|||INBOX/__CHILDREN__|||ERROR:{_MAIL_ERROR}",
            ]
        )
        result = self._counts(raw)

        self.assertEqual(result["Work"]["INBOX"], 1)
        self.assertEqual(result["Work"][ERRORS_KEY], [f"INBOX/__CHILDREN__: {_MAIL_ERROR}"])
        # A missing subtree is not a mailbox: no invented count key for it.
        self.assertNotIn("INBOX/__CHILDREN__", [k for k in result["Work"] if not k.startswith("__")])

    def test_unnamed_mailbox_failure_reports_without_inventing_a_mailbox(self):
        raw = f"Work|||__UNNAMED__|||ERROR:{_MAIL_ERROR}"
        result = self._counts(raw)

        self.assertEqual(result["Work"][ERRORS_KEY], [f"__UNNAMED__: {_MAIL_ERROR}"])
        self.assertEqual([k for k in result["Work"] if not k.startswith("__")], [])

    def test_detailless_error_row_still_reports(self):
        """Dropping a malformed marker would restore the silent-drop bug."""
        result = self._counts("Work|||Archive|||ERROR")
        self.assertEqual(result["Work"][ERRORS_KEY], ["Archive: unknown error"])
        self.assertEqual(result["Work"]["Archive"], UNREAD_COUNT_UNAVAILABLE)

    def test_unparsable_value_reports_instead_of_raising(self):
        result = self._counts("Work|||Archive|||not-a-number")
        self.assertEqual(result["Work"]["Archive"], UNREAD_COUNT_UNAVAILABLE)
        self.assertEqual(len(result["Work"][ERRORS_KEY]), 1)

    def test_healthy_response_carries_no_errors_key(self):
        """A clean run must not grow a spurious failure list."""
        result = self._counts("Work|||INBOX|||5")
        self.assertEqual(result["Work"], {"INBOX": 5})
        self.assertIn(PROVENANCE_KEY, result)

    def test_truncation_marker_still_works_alongside_errors(self):
        raw = "\n".join(
            [
                "Work|||INBOX|||5",
                f"Work|||Archive|||ERROR:{_MAIL_ERROR}",
                "Work|||__TRUNCATED__|||100",
            ]
        )
        result = self._counts(raw, max_mailboxes=100)

        self.assertTrue(result["Work"]["__truncated__"])
        self.assertEqual(result["Work"][ERRORS_KEY], [f"Archive: {_MAIL_ERROR}"])

    def test_summary_path_sentinel_is_unchanged(self):
        """The nested path copies this arm; it must keep working."""
        with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value="Work:12|Offline:ERROR"):
            result = inbox_tools.get_mailbox_unread_counts(summary_only=True)

        self.assertEqual(result["Work"], 12)
        self.assertEqual(result["Offline"], UNREAD_COUNT_UNAVAILABLE)


@unittest.skipIf(
    shutil.which("osacompile") is None,
    "osacompile not available — AppleScript compile check skipped on this platform",
)
class NestedUnreadCountCompileTests(unittest.TestCase):
    """The reporting arms are spliced AppleScript; prove they still parse."""

    def _assert_compiles(self, script: str, label: str) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as handle:
            handle.write(script)
            src = handle.name
        out = src.replace(".applescript", ".scpt")
        try:
            proc = subprocess.run(["osacompile", "-o", out, src], capture_output=True, text=True, timeout=30)
            self.assertEqual(
                proc.returncode,
                0,
                f"osacompile rejected the {label} unread-count script:\n{proc.stderr or proc.stdout}",
            )
        finally:
            for path in (src, out):
                with contextlib.suppress(OSError):
                    Path(path).unlink()

    def test_every_nested_variant_compiles(self):
        for label, kwargs in (
            ("scoped", {"account": "Work"}),
            ("scoped include_zero", {"account": "Work", "include_zero": True}),
            ("all accounts", {}),
        ):
            with self.subTest(label=label):
                self._assert_compiles(_capture_nested_script(**kwargs), label)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
