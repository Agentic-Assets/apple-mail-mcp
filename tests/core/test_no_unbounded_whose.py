"""Static enforcement tests for the bounded-scan contract.

These tests guard the Phase A whose-elimination invariants documented in
``tasks/archive/2026-05/whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md`` and the
post-Phase-A Gmail-whose-on-list fix (commit f96b44c, 2026-05-27).

Key rules they enforce (per
``tasks/archive/2026-05/whose-elimination-2026-05-22/05-codebase-whose-map.md`` § 1):

* ``every message of <mailbox> whose <predicate>`` is only allowed when
  the predicate is ``id is <numeric>`` or ``read status is <bool>`` — both
  hit Mail.app's fast indexes or are filtered against an already-sliced
  in-memory list. Anything else (``subject contains ...``, ``date
  received >= ...``) forces Mail to materialize the entire remote mailbox
  and is regression-prone on 24K+ inboxes.
* ``<sliceVar> whose <predicate>`` (where ``<sliceVar>`` is bound via
  ``messages 1 thru N``) is FORBIDDEN. AppleScript's ``whose`` over a
  list-typed value re-resolves the predicate against each ref's
  underlying physical folder — on Gmail that's ``[Gmail]/All Mail`` and
  Mail rejects the call with
  ``Can't get {message id N of mailbox "[Gmail]/All Mail" ...} whose ...``.
  Use ``bounded_scan.build_bounded_filtered_scan(...)`` which emits an
  in-AppleScript ``repeat ... if`` filter by construction.
* ``every message of <mailbox>`` with no ``whose`` clause at all is a
  raw enumeration and is banned outright.
* ``allow_full_scan`` was retired in v3.2.0 in favor of structured
  ``UNBOUNDED_SCAN_REQUIRED`` errors whose remediation must NOT point at
  ``full_inbox_export`` (that tool is disabled) and must instead carry an
  actionable bounded ``preferred`` fix. No tool may reintroduce the boolean
  kwarg.
"""

from __future__ import annotations

import inspect
import re
import unittest
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "plugin" / "apple_mail_mcp" / "tools"
# THE SCOPE DECISION for the two package-wide rules, stated once here.
#
# Root for BARE_PROPERTY_CONDITION (its fragment builder lives in ``core/``, so a
# scan rooted at ``tools/`` would miss a new caller added beside it) and, since
# AGENTIC-2355, for RAW_MESSAGES_ENUMERATION. That rule was scoped to TOOLS_DIR on
# the theory that only tool surfaces emit scan loops, which was wrong in the one
# way that mattered: ``bounded_scan.build_bounded_message_scan`` — the helper
# every caller trusts to bound its scan — sits one directory ABOVE ``tools/``, so
# the bounded-scan lint could not read the bounded-scan builder, and its
# small-mailbox arm emitted a raw ``messages of <mailbox>`` with nothing flagging
# it.
#
# Rooting at the package rather than a named list also reaches the other
# AppleScript-emitting modules outside ``tools/`` (``core/script_fragments.py``,
# ``calendar_core/scripts_read.py``, ``calendar_core/scripts_write.py``) plus any
# future one. Widening cost nothing: ``bounded_scan.py`` held the only hit outside
# ``tools/`` and was fixed in the same change, so nothing needed grandfathering.
#
# DANGEROUS_WHOSE / WHOSE_ON_SLICE_VAR / RAW_ENUMERATION stay scoped to
# TOOLS_DIR; widening those is a separate decision with its own baseline.
PACKAGE_DIR = ROOT / "plugin" / "apple_mail_mcp"

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

# Same failure, different spelling. AppleScript treats `messages of MB` as
# identical to `every message of MB`: both materialize the entire mailbox, and on
# a 24K+ Exchange mailbox that is the exact hang the bounded-scan contract exists
# to prevent. The rule above only ever matched the `every message of` form, so
# this spelling went unlinted while seven live sites used it.
#
# Two shapes, and the difference matters when you go to fix one (AGENTIC-2355):
#
#   * ONE `on error` fallback (`search/script.py`), which abandoned a bounded
#     slice and enumerated everything precisely when the bound had already failed.
#     Fixed: it now re-slices against `count of messages` and emits an
#     `ERROR_MAILBOX` diagnostic if that fails too.
#   * SIX `else` arms of an `if (count of messages of MB) > N` guard — the shape
#     `bounded_scan.build_bounded_message_scan` emitted and every copy inherited.
#     These stay bounded only because the guard proved the mailbox holds ≤ N
#     first, so the arm is reachable on a 24K mailbox only when N is itself
#     unclamped. `bounded_scan.py` is fixed (all arms slice; 0 binds `{}`); the
#     five remaining sites below are that same `else` arm.
#
# So do not go looking for an `on error` at the five sites in the baseline: there
# isn't one. Their real exposure is the unclamped cap that makes the arm
# reachable, which is a separate change from the spelling.
#
# Two spellings are excluded deliberately, because flagging either would make the
# rule noisy enough that people route around it:
#   * `count of messages of MB` — a cheap property read, not an enumeration.
#   * `outgoing messages of application "Mail"` — compose windows, an entirely
#     different collection from a mailbox's messages, and never a scan risk.
# Both lookbehinds are exactly 9 characters, which Python's fixed-width lookbehind
# requires; keep them that way or split into a pre-filter.
RAW_MESSAGES_ENUMERATION = re.compile(r"(?<!count of )(?<!outgoing )\bmessages of (?:\w+|__VAR__)\b")

# Ratchet baseline, keyed by package-relative path -> occurrence count.
#
# Keys are relative to PACKAGE_DIR (``tools/manage/trash.py``), not TOOLS_DIR,
# because the scan now spans the whole package and the two roots would otherwise
# share a key namespace — ``bounded_scan.py`` and a hypothetical
# ``tools/bounded_scan.py`` must not collide.
#
# Deliberately NOT keyed by line number: these files get edited for unrelated
# reasons, and a line-keyed allowlist would fail the build every time something
# above a site moved, which trains people to update the allowlist without reading
# it. Counts are stable under that churn and still fail closed on a new site in a
# new file or an extra site in a known file.
#
# Lowering a number here is always a valid change. Raising one is not: fix the
# call site instead, or bring a reason to AGENTIC review. Driving this dict to
# empty is tracked separately; each site needs its own decision about whether the
# honest fallback is "enumerate everything" or "return a structured error".
#
# AGENTIC-2355 removed ``search/script.py`` (fixed — see shape 1 above);
# ``bounded_scan.py`` never appeared here, being fixed in the same change that
# brought it into scope.
KNOWN_RAW_MESSAGES_ENUMERATION: dict[str, int] = {
    "tools/analytics/dashboard.py": 1,
    "tools/analytics/export_helpers.py": 1,
    "tools/inbox/overview.py": 1,
    "tools/manage/trash.py": 2,
}

# Mail properties that resolve only where an enclosing `whose` clause supplies the
# implicit target. `core.normalization.contains_any_condition(field, values)` renders
# `field contains "…"`, so passing one of these names produces exactly the
# AGENTIC-2344 fragment: unbound inside `repeat with aMessage in …`, so Mail raises
# -1728 on every message, the loop's `try` swallows it, and the tool reports a
# confident empty result.
#
# The ban is on the *argument*, not the helper. `contains_any_condition` is correct
# and safe against a loop-bound variable (`messageSubject`, `messageSender`), which
# is how the tools use it. That is also why deleting the helper would be the weaker
# fix: it would leave the next hand-rolled `f'{field} contains "…"'` uncaught, while
# this rule covers both routes. As of this commit the helper has no production
# caller at all — `manage/trash.py` was the last one, and it computed the fragment
# only to test its truthiness before discarding it.
BARE_MAIL_PROPERTIES = (
    "subject",
    "sender",
    "content",
    "all headers",
    "reply to",
    "recipient",
)

BARE_PROPERTY_CONDITION = re.compile(
    r"contains_any_condition\(\s*[\"'](?:" + "|".join(BARE_MAIL_PROPERTIES) + r")[\"']"
)

# Normalize Python f-string placeholders so the static scan also catches
# `every message of {mailbox_var} whose ...` patterns — the original
# `\w+` token class never matched the curly-brace prefix. Only normalize
# the mailbox-position placeholder (i.e. an f-string brace immediately
# preceded by "every message of "). Other `{...}` substitutions (notably
# `{id_condition}` after `whose`) must remain so the allowlist regex
# negative lookahead still matches.
_MAILBOX_FSTRING = re.compile(r"(?<=every message of )\{[^}]+\}")

# Same normalization for the `messages of {mailbox_var}` spelling, so an f-string
# mailbox cannot slip a raw enumeration past RAW_MESSAGES_ENUMERATION.
_MESSAGES_OF_FSTRING = re.compile(r"(?<=messages of )\{[^}]+\}")


def _normalize_line(line: str) -> str:
    """Replace mailbox-position `{...}` placeholders with `__VAR__`."""
    return _MESSAGES_OF_FSTRING.sub("__VAR__", _MAILBOX_FSTRING.sub("__VAR__", line))


# Slice-binding variable names used across tool source. When code does
# ``set X to messages 1 thru N of MB`` (or ``set X to messages of MB``)
# the resulting AppleScript value is a *list* of message references —
# NOT a mailbox specifier. A subsequent ``X whose <predicate>`` is the
# Gmail crash: Mail evaluates the predicate against each ref's underlying
# physical folder (``[Gmail]/All Mail`` for Gmail accounts) and fails
# with ``Can't get {message id N of mailbox "[Gmail]/All Mail" ...}
# whose ...``. The lint forbids this construct globally — use
# ``bounded_scan.build_bounded_filtered_scan(...)`` which emits the safe
# in-loop ``repeat ... if`` pattern instead.
SLICE_BIND_VARS = (
    "candidateMessages",
    "mailboxMessages",
    "inboxMessages",
    "draftMessages",
    "sentMessages",
    "sourceMessages",
    "trashMessages",
    "batchMessages",
    "recentMessages",
    "targetMessages",
    "matchedMessages",
)

# ``<sliceVar> whose <predicate>`` — except when the predicate is an
# ``id is`` lookup (safe by Mail's id index) or the
# ``{id_condition}`` Python f-string interpolation produced by
# ``build_whose_id_list`` (which expands to ``id is X or id is Y ...``).
WHOSE_ON_SLICE_VAR = re.compile(
    r"\b(?:" + "|".join(SLICE_BIND_VARS) + r")\s+whose\s+"
    r"(?!id is\b|\{id_condition\}|\")"
)

# Known offenders that pre-date this enforcement and whose fix lives in a
# follow-on PR. Each entry is (path_relative_to_tools, line_number,
# tracking_note). The test asserts the *exact* set so adding new offenders
# fails CI and removing a fixed offender also fails CI (prompting cleanup).
#
# Empty as of 2026-05-27: the last entry (`compose.py:141` —
# `_build_draft_lookup`'s `every message of draftsMailbox whose subject
# contains`) was migrated to `build_bounded_filtered_scan` in the same
# commit that introduced the slice-var lint below.
KNOWN_DANGEROUS_WHOSE: set[tuple[str, int]] = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_tool_files() -> Iterable[Path]:
    """Yield every AppleScript-emitting tool module, recursively.

    This MUST stay ``rglob``. Every tool surface is a package now
    (``search/``, ``inbox/``, ``compose/``, ``manage/``, ``analytics/``,
    ``smart_inbox/``, ``calendar/``), so a non-recursive ``glob("*.py")``
    matches only the two flat leaves left at this level and every check below
    passes against a file set that contains no AppleScript at all. That is not
    a hypothetical: this lint sat disarmed through the v3.11.6 ``search_emails``
    subject-filter bug (AGENTIC-2344), which is exactly the class it exists to
    catch. A green run over the wrong file set looks identical to a green run
    over the right one, so the coverage assertion in
    ``test_lint_scans_every_tool_package`` is the only thing standing between
    this helper and silently going vacuous again.
    """
    for path in sorted(TOOLS_DIR.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        yield path


def _iter_package_files() -> Iterable[Path]:
    """Yield every module in the package, recursively.

    The file set for the two package-wide rules (RAW_MESSAGES_ENUMERATION and
    BARE_PROPERTY_CONDITION) — see the scope decision at ``PACKAGE_DIR``.

    ``__init__.py`` is NOT skipped here, unlike in ``_iter_tool_files()``: the
    package facades are ordinary modules that could grow a script builder, and
    excluding them would be a hole with nothing behind it.
    """
    return sorted(PACKAGE_DIR.rglob("*.py"))


def _pkg_rel(path: Path) -> str:
    """PACKAGE_DIR-relative key, e.g. ``tools/manage/trash.py``.

    Distinct from ``_rel`` on purpose: the package-wide scans must not key a
    module above ``tools/`` by a path that a module inside ``tools/`` could also
    produce.
    """
    return path.relative_to(PACKAGE_DIR).as_posix()


def _rel(path: Path) -> str:
    """Tools-relative key, e.g. ``manage/helpers.py``.

    Never ``path.name``: ``helpers.py`` exists in ``calendar/``, ``compose/``,
    ``manage/``, and ``smart_inbox/``, and ``attachments.py`` in ``analytics/``
    and ``manage/``. Keyed by bare basename, one allowlist entry would exempt
    the same line number in a *different* package's file, and a failure message
    would not say which file to fix.
    """
    return path.relative_to(TOOLS_DIR).as_posix()


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
    # RST code spans in prose. ``messages of`` is here because docstrings legitimately
    # describe the banned pattern in order to say they avoid it — e.g.
    # export_helpers' "Never binds the full ``messages of targetMailbox``", which is a
    # promise not to do the thing, not the thing.
    return any(token in line for token in ("``every message", "``set ", "``messages of", "``count of"))


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class LintCoverageTests(unittest.TestCase):
    """Guard the scanner's *input set*, not just its verdict.

    Every other test in this module asserts that no violation was found. None of
    them can tell the difference between "scanned every module and found nothing"
    and "scanned almost nothing". When the tool surfaces became packages, the
    scanner's non-recursive ``glob`` silently narrowed to two flat leaves and all
    four checks began passing vacuously — undetected until the AGENTIC-2344
    subject-filter bug shipped through the exact hole they were meant to close.
    These assertions fail loudly on that regression instead of going quiet.
    """

    def test_lint_scans_every_tool_package(self):
        scanned = {_rel(path) for path in _iter_tool_files()}

        # Every package that holds a real module must be represented. Derived
        # from the tree rather than hardcoded, so a newly added tool surface is
        # covered the moment it exists instead of when someone remembers to
        # update a list here.
        packages_with_modules = {
            path.parent.relative_to(TOOLS_DIR).as_posix()
            for path in TOOLS_DIR.rglob("*.py")
            if path.name != "__init__.py" and path.parent != TOOLS_DIR
        }
        scanned_packages = {rel.rsplit("/", 1)[0] for rel in scanned if "/" in rel}
        missing = packages_with_modules - scanned_packages
        self.assertFalse(
            missing,
            f"The `whose` lint is not scanning these tool packages at all: {sorted(missing)}. "
            "`_iter_tool_files()` must use rglob; a non-recursive glob makes every check below vacuous.",
        )

        # Spot-anchor the module that actually shipped the bug this lint missed.
        self.assertIn(
            "search/script.py",
            scanned,
            "search/script.py must be linted: it emits the per-message scan loops.",
        )

        # Floor check. The exact count moves with refactors, but a collapse back
        # to a handful of files means the scanner has been disarmed again.
        self.assertGreater(
            len(scanned),
            30,
            f"Only {len(scanned)} tool module(s) linted; the tool tree is far larger, "
            "so the scanner's file set has regressed.",
        )

    def test_raw_messages_lint_reaches_applescript_modules_outside_tools(self):
        """The bounded-scan rule must be able to read the bounded-scan builder.

        The scope decision at ``PACKAGE_DIR``, recorded as an assertion because a
        comment cannot fail. Narrowing the root back to TOOLS_DIR would restore
        exactly the AGENTIC-2355 hole while every other assertion in this module
        kept passing — the same failure mode as the non-recursive glob described
        in this class's docstring.
        """
        scanned = {_pkg_rel(path) for path in _iter_package_files()}

        # The AppleScript-emitting modules outside ``tools/``. Spot-anchored
        # rather than derived: these are the exact files the widening was for, so
        # a regression must name them.
        for required in (
            "bounded_scan.py",
            "core/script_fragments.py",
            "calendar_core/scripts_read.py",
            "calendar_core/scripts_write.py",
        ):
            self.assertIn(
                required,
                scanned,
                f"{required} emits AppleScript but is outside the raw-enumeration scan. "
                "That scan must root at PACKAGE_DIR, not TOOLS_DIR (AGENTIC-2355).",
            )

        # Widening must not have dropped anything the tools-only scan covered.
        tool_keys = {f"tools/{_rel(path)}" for path in _iter_tool_files()}
        missing_tools = tool_keys - scanned
        self.assertFalse(
            missing_tools,
            f"The package-wide scan no longer covers tool modules it used to: {sorted(missing_tools)}.",
        )
        self.assertGreater(
            len(scanned),
            len(tool_keys),
            "The package-wide scan must cover strictly more modules than the tools-only scan; "
            f"got {len(scanned)} vs {len(tool_keys)}.",
        )

        # A baseline key that names no scanned module silently retires a
        # grandfathered site (e.g. after a rename, or after a key-namespace
        # change like the TOOLS_DIR -> PACKAGE_DIR move).
        unknown = set(KNOWN_RAW_MESSAGES_ENUMERATION) - scanned
        self.assertFalse(
            unknown,
            f"KNOWN_RAW_MESSAGES_ENUMERATION keys no scanned module: {sorted(unknown)}. "
            "Keys are PACKAGE_DIR-relative, e.g. 'tools/manage/trash.py'.",
        )

    def test_rel_keys_are_unambiguous_across_packages(self):
        """Basename keys would collide; ``helpers.py`` exists in four packages."""
        keys = [_rel(path) for path in _iter_tool_files()]
        self.assertEqual(
            len(keys),
            len(set(keys)),
            "Scan keys must be unique. Keyed by bare basename, one KNOWN_DANGEROUS_WHOSE "
            "entry would exempt the same line in a different package's file.",
        )
        self.assertTrue(
            any(key.endswith("/helpers.py") for key in keys),
            "Expected package-qualified keys like 'manage/helpers.py', not bare basenames.",
        )


class NoRawMessagesEnumerationTests(unittest.TestCase):
    """Ban `messages of <mailbox>`, the unlinted twin of `every message of`."""

    def _scan(self) -> tuple[dict[str, int], list[str]]:
        counts: dict[str, int] = {}
        details: list[str] = []
        for path in _iter_package_files():
            rel = _pkg_rel(path)
            with path.open() as fh:
                for lineno, line in enumerate(fh, 1):
                    if _is_docstring_or_comment_line(line):
                        continue
                    if RAW_MESSAGES_ENUMERATION.search(_normalize_line(line)):
                        counts[rel] = counts.get(rel, 0) + 1
                        details.append(f"{rel}:{lineno}: {line.strip()}")
        return counts, details

    def test_no_new_raw_messages_enumeration(self):
        counts, details = self._scan()

        regressions = [
            f"{rel}: {count} occurrence(s), baseline allows {KNOWN_RAW_MESSAGES_ENUMERATION.get(rel, 0)}"
            for rel, count in sorted(counts.items())
            if count > KNOWN_RAW_MESSAGES_ENUMERATION.get(rel, 0)
        ]
        self.assertFalse(
            regressions,
            "New raw `messages of <mailbox>` enumeration(s). This materializes the whole "
            "mailbox and hangs on 24K+ inboxes — use a bounded slice "
            "(`messages 1 thru N of MB`) or `bounded_scan.build_bounded_filtered_scan(...)`. "
            "If a bounded slice already failed, prefer a structured error over enumerating "
            "everything.\n  - " + "\n  - ".join(regressions) + "\n\nAll sites:\n  - " + "\n  - ".join(details),
        )

    def test_raw_messages_enumeration_baseline_is_not_stale(self):
        """A fixed site must be removed from the baseline, so the ratchet only tightens."""
        counts, _ = self._scan()
        stale = [
            f"{rel}: baseline says {expected}, found {counts.get(rel, 0)}"
            for rel, expected in sorted(KNOWN_RAW_MESSAGES_ENUMERATION.items())
            if counts.get(rel, 0) < expected
        ]
        self.assertFalse(
            stale,
            "KNOWN_RAW_MESSAGES_ENUMERATION is stale — lower or remove these entries so the "
            "ratchet cannot silently re-admit a fixed site:\n  - " + "\n  - ".join(stale),
        )

    def test_count_of_messages_is_not_flagged(self):
        """`count of messages of MB` is a property read, not an enumeration.

        Flagging it would make the rule noisy enough that people route around it.
        """
        self.assertIsNone(
            RAW_MESSAGES_ENUMERATION.search("set messageCount to count of messages of currentMailbox"),
        )
        self.assertIsNotNone(
            RAW_MESSAGES_ENUMERATION.search("set mailboxMessages to messages of currentMailbox"),
        )
        # Bounded slices must stay legal — they are the prescribed fix.
        self.assertIsNone(
            RAW_MESSAGES_ENUMERATION.search(
                "set candidateMessages to messages 1 thru scanUpperBound of currentMailbox"
            ),
        )
        # An f-string mailbox must not slip through.
        self.assertIsNotNone(
            RAW_MESSAGES_ENUMERATION.search(_normalize_line("set x to messages of {mailbox_var}")),
        )
        # `outgoing messages` are compose windows, not a mailbox scan.
        self.assertIsNone(
            RAW_MESSAGES_ENUMERATION.search('count of outgoing messages of application "Mail"'),
        )
        # Docstring prose describing the banned pattern is not a violation.
        self.assertTrue(
            _is_docstring_or_comment_line("    Never binds the full ``messages of targetMailbox``."),
        )


class NoBarePropertyConditionTests(unittest.TestCase):
    """Ban `contains_any_condition("<bare Mail property>", …)`.

    No allowlist: there are zero production call sites, so the honest baseline is
    zero and any hit is new. An empty ratchet is the only kind that cannot rot.
    """

    def _scan(self) -> list[str]:
        hits: list[str] = []
        for path in _iter_package_files():
            with path.open() as fh:
                for lineno, line in enumerate(fh, 1):
                    if _is_docstring_or_comment_line(line):
                        continue
                    if BARE_PROPERTY_CONDITION.search(line):
                        hits.append(f"{_pkg_rel(path)}:{lineno}: {line.strip()}")
        return hits

    def test_no_bare_property_contains_any_condition(self):
        hits = self._scan()
        self.assertFalse(
            hits,
            "`contains_any_condition` was passed a bare Mail property. That renders "
            '`subject contains "…"`, which only binds inside a `whose` clause; inside a '
            "`repeat` loop Mail raises -1728 on every message and the loop's `try` hides "
            "it, so the tool returns an empty result with no error (AGENTIC-2344). Pass the "
            "loop-bound variable instead (`messageSubject`), or let the callee build its own "
            "bounded predicate.\n  - " + "\n  - ".join(hits),
        )

    def test_bare_property_rule_has_teeth(self):
        """Verified in both directions: the safe spelling must stay legal."""
        self.assertIsNotNone(
            BARE_PROPERTY_CONDITION.search('condition = contains_any_condition("subject", subject_terms)'),
        )
        self.assertIsNotNone(
            BARE_PROPERTY_CONDITION.search("contains_any_condition( 'sender', [sender])"),
        )
        # The correct use — a variable the enclosing loop binds — must not trip.
        self.assertIsNone(
            BARE_PROPERTY_CONDITION.search('contains_any_condition("messageSubject", subject_terms)'),
        )
        self.assertIsNone(
            BARE_PROPERTY_CONDITION.search('contains_any_condition("messageSender", [sender])'),
        )
        # Prose describing the banned call is not a violation; `manage/trash.py`
        # documents it in a comment to explain why it no longer builds one.
        self.assertTrue(
            _is_docstring_or_comment_line('        # `contains_any_condition("subject", ...)` -> unbound'),
        )

    def test_bare_property_scan_covers_core_and_tools(self):
        """The rule's file set must span both packages, or it goes vacuous."""
        scanned = {_pkg_rel(path) for path in _iter_package_files()}
        self.assertIn(
            "core/normalization.py",
            scanned,
            "The scan must reach core/, where contains_any_condition is defined.",
        )
        self.assertIn("tools/manage/trash.py", scanned)
        self.assertGreater(len(scanned), 60, f"Only {len(scanned)} module(s) scanned; the file set regressed.")


class NoDangerousWhoseTests(unittest.TestCase):
    """Static scan of ``plugin/apple_mail_mcp/tools/**/*.py``."""

    def test_no_dangerous_whose_in_tools(self):
        found: set[tuple[str, int]] = set()
        details: list[str] = []
        for path in _iter_tool_files():
            rel = _rel(path)
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

    def test_no_whose_on_slice_bound_list(self):
        """``<sliceVar> whose <predicate>`` is forbidden globally.

        This is the regression scanner for the 2026-05-27 Gmail crash:
        AppleScript's ``whose`` clause is unreliable on a list of message
        refs bound by ``messages 1 thru N``. Mail evaluates the predicate
        against each ref's underlying physical folder, which on Gmail is
        ``[Gmail]/All Mail`` — Mail then rejects the call with
        ``Can't get {message id N of mailbox "[Gmail]/All Mail" ...}
        whose ...``. The only safe pattern is an in-AppleScript
        ``repeat ... if`` loop; see
        ``bounded_scan.build_bounded_filtered_scan``.
        """
        offenders: list[str] = []
        for path in _iter_tool_files():
            rel = _rel(path)
            with path.open() as fh:
                for lineno, line in enumerate(fh, 1):
                    if _is_docstring_or_comment_line(line):
                        continue
                    if WHOSE_ON_SLICE_VAR.search(line):
                        offenders.append(f"{rel}:{lineno}: {line.rstrip()}")

        self.assertEqual(
            offenders,
            [],
            "AppleScript `whose` on a slice-bound variable is forbidden — "
            "it crashes on Gmail because Mail evaluates the predicate "
            "against the underlying physical folder (e.g. "
            "`[Gmail]/All Mail`), not the bound list. Replace with an "
            "in-loop `repeat ... if` via "
            "`bounded_scan.build_bounded_filtered_scan(...)`. "
            "Offenders:\n  - " + "\n  - ".join(offenders),
        )

    def test_no_raw_every_message_enumeration_in_tools(self):
        offenders: list[str] = []
        for path in _iter_tool_files():
            rel = _rel(path)
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
            "use `messages 1 thru N` slicing instead. Offenders:\n  - " + "\n  - ".join(offenders),
        )

    def test_no_allow_full_scan_in_tools(self):
        offenders: list[str] = []
        for path in _iter_tool_files():
            rel = _rel(path)
            with path.open() as fh:
                for lineno, line in enumerate(fh, 1):
                    if "allow_full_scan" in line:
                        offenders.append(f"{rel}:{lineno}: {line.rstrip()}")

        self.assertEqual(
            offenders,
            [],
            "`allow_full_scan` was retired in v3.2.0. Tools must raise "
            "`UNBOUNDED_SCAN_REQUIRED` with a bounded `preferred` fix and must "
            "NOT point at `full_inbox_export` (disabled). "
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
            "Tool signatures must not expose `allow_full_scan` (retired in v3.2.0):\n  - " + "\n  - ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
