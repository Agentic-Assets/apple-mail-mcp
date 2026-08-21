"""Static enforcement of the bounded-scan contract, rebuilt on the AST.

Sibling lint to ``tests/core/test_no_bare_applescript_try.py``. That one guards
*whether a failure is allowed to stay invisible*; this one guards *how much Mail
is asked to enumerate*.

Rules
-----
``dangerous_whose``
    ``<message collection> of <mailbox> whose <predicate>`` is allowed only when
    the predicate is ``id is <numeric>``, ``read status is <bool>``, or the
    ``{id_condition}`` interpolation that ``bounded_scan.build_whose_id_list``
    expands into an ``id is X or id is Y`` chain. Those hit Mail.app's indexes.
    Anything else (``subject contains …``, ``date received >= …``) forces Mail to
    materialize the whole remote mailbox and is the hang this contract exists to
    prevent on 24K+ inboxes.

``whose_on_list``
    ``<sliceVar> whose <predicate>`` where ``<sliceVar>`` was bound to a list of
    message refs (``set X to messages 1 thru N of MB``). AppleScript re-resolves
    the predicate against each ref's *underlying physical folder* — on Gmail that
    is ``[Gmail]/All Mail`` — and Mail rejects the call with ``Can't get {message
    id N of mailbox "[Gmail]/All Mail" …} whose …``. Use
    ``bounded_scan.build_bounded_filtered_scan(...)``, which emits an
    in-AppleScript ``repeat … if`` filter by construction.

``raw_every_message`` / ``raw_messages``
    ``every message of <mailbox>`` and ``messages of <mailbox>`` with no ``whose``
    at all. Two spellings of one raw full-mailbox enumeration; kept as separate
    rules because they carry separate baselines.

``bare_property``
    ``contains_any_condition("<bare Mail property>", …)``. That renders
    ``subject contains "…"``, which only binds where an enclosing ``whose``
    supplies the implicit target. Inside ``repeat with aMessage in …`` Mail raises
    -1728 on every message, the loop's ``try`` swallows it, and the tool reports a
    confident empty result — the shipped AGENTIC-2344 bug. The ban is on the
    *argument*: ``contains_any_condition`` is correct against a loop-bound
    variable (``messageSubject``), which is how tools are supposed to use it.

Why this file is AST-based
--------------------------
It used to be a line-by-line regex over raw file text, with no notion of where a
Python string started or ended. A 2026-08 audit drove four evasions end to end
through the real test methods, every one of them something a person or a
formatter produces without trying:

1. **Adjacent-literal concatenation, even on one line.** ``"every message of "
   "targetMailbox whose subject contains …"`` — the mailbox token class ``\\w+``
   cannot match the closing quote, so the line does not match. A ``ruff format``
   reflow of one long line is enough to cause this by accident.
2. **A ``whose`` on a slice variable outside the hardcoded 11-name list.** The
   allowlist of slice-binding variable names was a literal tuple; anything named
   differently was invisible.
3. **``messages `` and ``of MB`` split across two Python lines.** One AppleScript
   line, two Python literals, and a scanner that only ever saw one line at a time.
4. **A ``contains_any_condition(`` call wrapped by a formatter.** The regex
   required the quoted argument on the same physical line as the open paren.

Three further forms — including the most idiomatic spelling of all, ``every
message of mailbox "INBOX" whose …`` — were caught only by the raw-enumeration
rule, which then reported the wrong problem and pointed the fix in the wrong
direction.

So the scanner now parses each module, reconstructs the AppleScript text from
f-string parts and from implicit *and* explicit string concatenation, and matches
against the reconstructed script rather than the Python source line. Python's
parser folds adjacent literals for us, which is precisely why layout and
formatter reflows can no longer hide a violation.

``ClosedEvasionTests`` feeds each of those four samples through the real scanner
and asserts it fires. That regression test is the most valuable thing in this
file: this lint has already gone vacuous once, silently, through the whole
v3.11.6 subject-filter bug (AGENTIC-2344) — the exact defect class it exists to
catch — because a non-recursive ``glob`` narrowed its file set to two flat
leaves and every "no violations found" assertion kept passing.

Scope
-----
Every rule is rooted at ``PACKAGE_DIR``. Three of the five used to root at
``TOOLS_DIR``, which put ``core/``, ``bounded_scan.py`` (the helper every caller
trusts to bound its scan) and ``calendar_core/`` outside their reach entirely.

Known residual limits, stated so nobody mistakes them for coverage: a script
assembled from values bound to *separate Python variables*, or from list elements
later ``"\\n".join``-ed, is reconstructed per fragment, so a violation split
across two such fragments is seen only if one fragment carries it whole. Literals
are never concatenated with each other, because doing so would fabricate
adjacencies that do not exist at runtime.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import re
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = ROOT / "plugin" / "apple_mail_mcp"
TOOLS_DIR = PACKAGE_DIR / "tools"

# ---------------------------------------------------------------------------
# Machinery shared with the sibling lint
# ---------------------------------------------------------------------------
#
# ``tests/core/test_no_bare_applescript_try.py`` already owns a proven,
# well-documented AppleScript comment stripper and package walker. Importing them
# keeps one definition of "what counts as a comment" and "what counts as a module
# in scope" across the two static gates instead of two that can drift apart, and
# reuses its ratchet arithmetic (``regressions``/``stale_entries``), which is
# itself unit-tested over there on synthetic dicts.
#
# The loader is path-based-with-fallback because this module is imported under
# different names depending on how it is run: pytest's ``prepend`` import mode
# gives ``core.test_no_bare_applescript_try`` (``tests/`` is the basedir, since it
# has no ``__init__.py`` while ``tests/core/`` does), while ``python3
# tests/core/test_no_unbounded_whose.py`` has neither name importable at all.
_SIBLING_PATH = Path(__file__).with_name("test_no_bare_applescript_try.py")
_SIBLING_NAMES = ("core.test_no_bare_applescript_try", "tests.core.test_no_bare_applescript_try")


def _load_sibling_lint() -> ModuleType:
    """The bare-``try`` lint, imported as a library however this module was loaded."""
    for name in _SIBLING_NAMES:
        module = sys.modules.get(name)
        if module is not None:
            return module
    for name in _SIBLING_NAMES:
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    spec = importlib.util.spec_from_file_location("_apple_mail_bare_try_lint", _SIBLING_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"Cannot load the sibling lint from {_SIBLING_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BARE_TRY_LINT = _load_sibling_lint()

# ``(* … *)`` blocks plus ``--``/leading-``#`` comments, stripped per literal.
strip_applescript_comments = BARE_TRY_LINT.strip_applescript_comments
# Every module under the package, recursively, ``__pycache__`` excluded.
iter_package_files = BARE_TRY_LINT.iter_package_files
# Two-sided ratchet arithmetic: over baseline is a regression, under it is stale.
regressions = BARE_TRY_LINT.regressions
stale_entries = BARE_TRY_LINT.stale_entries


# ---------------------------------------------------------------------------
# Rule names
# ---------------------------------------------------------------------------

DANGEROUS_WHOSE = "dangerous_whose"
WHOSE_ON_LIST = "whose_on_list"
RAW_EVERY_MESSAGE = "raw_every_message"
RAW_MESSAGES = "raw_messages"
BARE_PROPERTY = "bare_property"

RULES = (DANGEROUS_WHOSE, WHOSE_ON_LIST, RAW_EVERY_MESSAGE, RAW_MESSAGES, BARE_PROPERTY)


# ---------------------------------------------------------------------------
# Script reconstruction
# ---------------------------------------------------------------------------

# Stand-in for an f-string slot whose expression has no usable name, and for the
# non-literal side of a ``"…" + expr`` concatenation. Braces are kept so the slot
# still reads as one mailbox-position token to the patterns below.
OPAQUE_SLOT = "{__expr__}"


def _slot_name(node: ast.AST) -> str:
    """A ``\\w``-safe stand-in for an f-string slot's expression.

    ``{id_condition}`` has to survive verbatim — it is one of the three allowed
    ``whose`` predicates. Anything more complex collapses to its root identifier
    so the reconstructed text stays free of quotes and braces that would confuse
    the token classes below; ``ast.unparse`` would preserve both.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _slot_name(node.func)
    if isinstance(node, ast.Subscript):
        return _slot_name(node.value)
    return "__expr__"


def render_script(node: ast.AST) -> str | None:
    """Reconstructed script text for a string-producing node, else ``None``.

    Handles the three shapes AppleScript is written in here:

    * ``ast.Constant`` — a plain literal. Python's parser has *already* folded
      implicit adjacent concatenation into one Constant, whatever the layout, so
      evasion 1 and evasion 3 both dissolve at this line.
    * ``ast.JoinedStr`` — an f-string, modelled as Constant segments interleaved
      with ``FormattedValue`` slots. Walking Constants alone would yield the
      script chopped at every ``{…}``.
    * ``ast.BinOp`` with ``+`` — explicit concatenation, including the mixed
      ``"head " + var + " tail"`` form where the middle is not a literal.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("{" + _slot_name(value.value) + "}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = render_script(node.left)
        right = render_script(node.right)
        if left is None and right is None:
            return None
        return (left if left is not None else OPAQUE_SLOT) + (right if right is not None else OPAQUE_SLOT)
    return None


def _prose_ids(tree: ast.AST) -> set[int]:
    """Ids of bare string *statements* — docstrings and comment-style strings.

    A string expression statement is never a script; it is prose that documents
    one, and this package documents the banned patterns constantly (``bounded_scan``
    explains ``every message of MB whose <non-id-predicate>`` in order to say it
    refuses to emit one). Structural exclusion replaces the old lint's guesswork
    about RST backtick spans in raw source lines.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, (ast.Constant, ast.JoinedStr))
            and (isinstance(node.value, ast.JoinedStr) or isinstance(node.value.value, str))
        ):
            ids.add(id(node.value))
    return ids


def iter_script_literals(path: Path) -> list[tuple[int, str]]:
    """Every non-prose string-producing expression in *path*, as ``(lineno, text)``.

    Each is reconstructed whole and reported once: a node consumed by an
    enclosing f-string or ``+`` chain is not also yielded on its own, so a
    fragment is never scanned twice under two different spellings.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _prose_ids(tree)
    consumed: set[int] = set()
    # ``ast.walk`` is breadth-first, so an enclosing node is always visited before
    # the nodes it absorbs.
    for node in ast.walk(tree):
        if id(node) in consumed:
            continue
        absorbs = isinstance(node, ast.JoinedStr) or (
            isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add) and render_script(node) is not None
        )
        if absorbs:
            consumed.update(id(sub) for sub in ast.walk(node) if sub is not node)

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if id(node) in consumed or id(node) in skip:
            continue
        text = render_script(node)
        if text:
            found.append((node.lineno, text))
    found.sort(key=lambda item: item[0])
    return found


def script_lines(path: Path) -> list[tuple[int, str]]:
    """``(source_lineno, comment-stripped AppleScript line)`` for every literal."""
    lines: list[tuple[int, str]] = []
    for lineno, text in iter_script_literals(path):
        for offset, line in enumerate(strip_applescript_comments(text)):
            lines.append((lineno + offset, line))
    return lines


# ---------------------------------------------------------------------------
# AppleScript patterns
# ---------------------------------------------------------------------------

# One token of a mailbox reference: a quoted AppleScript string, an f-string slot,
# or a bare identifier. ``whose`` is excluded so a greedy reference cannot swallow
# the keyword that decides whether the enumeration is bounded.
#
# This token class is the whole reason ``every message of mailbox "INBOX" whose …``
# — the most idiomatic spelling there is — used to be misfiled. The old ``\w+``
# matched ``mailbox`` and then demanded ``whose`` immediately, so the quoted name
# broke the match and the line fell through to the raw-enumeration rule, which
# reported "no ``whose`` at all" about a line that had one.
_REF_TOKEN = r'(?!whose\b)(?:"[^"\n]*"|\{[^}\n]*\}|[A-Za-z_]\w*)'
# A whole reference: ``targetMailbox``, ``mailbox "INBOX"``,
# ``mailbox theName of account "Work"``, ``{mailbox_var}``. Intra-reference spacing
# is ``[ \t]`` only — an AppleScript statement ends at the newline, so a reference
# must not be allowed to run across lines.
MAILBOX_REF = _REF_TOKEN + r"(?:[ \t]+" + _REF_TOKEN + r")*"

_WHOSE_TAIL = r"(?:[ \t]+(?P<whose>whose)\b[ \t]*(?P<pred>[^\n]*))?"

# ``every|first|last|some|middle message of <ref>`` / ``messages of <ref>``,
# with the optional ``whose`` tail.
#
# Two spellings are excluded deliberately, because flagging either would make the
# rule noisy enough that people route around it:
#   * ``count of messages of MB`` — a cheap property read, not an enumeration.
#     ``count of (messages of MB …)`` is the same read with a paren, hence the
#     second lookbehind.
#   * ``outgoing messages of application "Mail"`` — compose windows, an entirely
#     different collection from a mailbox's messages, never a scan risk.
# Each lookbehind must be fixed width; keep them that way or split into a
# pre-filter.
ENUMERATION = re.compile(
    r"(?<!count of )(?<!count of \()(?<!outgoing )"
    r"\b(?P<head>(?:every|first|last|some|middle)[ \t]+message|messages) of[ \t]+"
    r"(?P<ref>" + MAILBOX_REF + r")" + _WHOSE_TAIL
)

# ``every message whose …`` / ``messages whose …`` with no ``of`` — the spelling
# used inside a ``tell mailbox`` block, where the target is implicit.
IMPLICIT_TARGET_WHOSE = re.compile(
    r"(?<![\w.])(?P<head>every[ \t]+message|messages)[ \t]+(?P<whose>whose)\b[ \t]*(?P<pred>[^\n]*)"
)

# ``messages 1 thru N of MB whose …`` — a ``whose`` applied to an already-sliced
# list expression rather than to a mailbox. Same Gmail failure as ``<sliceVar>
# whose``, just without the intermediate variable.
SLICED_LIST_WHOSE = re.compile(r"\bmessages[ \t]+[^\n]*?\bthru\b[^\n]*?(?P<whose>whose)\b[ \t]*(?P<pred>[^\n]*)")

# ``<identifier> whose <predicate>`` — the candidate for the slice-variable rule.
# Which identifiers count is decided by ``list_bound_names``, not by this pattern.
IDENTIFIER_WHOSE = re.compile(r"(?<![\w.])(?P<var>[A-Za-z_]\w*)[ \t]+(?P<whose>whose)\b[ \t]*(?P<pred>[^\n]*)")

# The three predicates that stay on a Mail.app index instead of materializing the
# mailbox. ``{id_condition}`` is the f-string slot that
# ``bounded_scan.build_whose_id_list`` expands to an ``id is X or id is Y`` chain.
SAFE_WHOSE_PREDICATE = re.compile(r"(?:id is\b|read status is\b|\{id_condition\})")

# ``set <var> to [(] messages …`` / ``set <var> to [(] every message …`` — the
# binding that makes ``<var>`` a *list of message references* rather than a
# mailbox specifier. ``first|last|some message of …`` is deliberately absent: it
# binds one message, and a ``whose`` on it is not the list bug this rule is about.
#
# Derived from the source instead of hardcoded. The old lint carried a literal
# 11-name tuple, so a slice bound to any twelfth name was invisible to it
# (evasion 2). The package binds 7 names that tuple never listed.
LIST_BINDING = re.compile(r"\bset[ \t]+(?P<var>[A-Za-z_]\w*)[ \t]+to[ \t]+\(?[ \t]*(?:every[ \t]+message|messages)\b")

# Kept as a floor under the derived set. Every name here was in the pre-rebuild
# allowlist; a binding that moves out of scanner reach (into a helper, into a
# ``.join``-ed list) must not silently un-ban the ``whose`` on it.
STATIC_LIST_BOUND_NAMES = frozenset(
    {
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
    }
)

# A name-shaped backstop for a list bound somewhere the scanner cannot follow.
# Requires a prefix, so the bare AppleScript keyword ``messages`` is not swept up
# here — ``messages whose …`` is an enumeration and is reported as one.
LIST_NAME_HINT = re.compile(r"^[A-Za-z_]+[Mm]essages$")

# Mail properties that resolve only where an enclosing ``whose`` supplies the
# implicit target.
BARE_MAIL_PROPERTIES = frozenset(
    {
        "subject",
        "sender",
        "content",
        "all headers",
        "reply to",
        "recipient",
    }
)

CONTAINS_ANY_CONDITION = "contains_any_condition"


def _predicate_is_safe(predicate: str) -> bool:
    return bool(SAFE_WHOSE_PREDICATE.match(predicate.strip()))


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


class Site(NamedTuple):
    """One scanner finding. ``rule`` is ``None`` for an enumeration judged safe."""

    rel: str
    lineno: int
    rule: str | None
    text: str

    def render(self) -> str:
        return f"{self.rel}:{self.lineno} [{self.rule}] {self.text.strip()[:140]}"


def pkg_rel(path: Path) -> str:
    """``PACKAGE_DIR``-relative key, e.g. ``tools/manage/trash.py``.

    Never ``path.name``: ``helpers.py`` exists in four packages and
    ``attachments.py`` in two, so a basename key would let one baseline entry
    exempt a different package's file and would leave the failure message unable
    to say which file to fix. Relative to the package rather than to ``tools/``
    because the scan spans the whole package and the two roots would otherwise
    share a key namespace — ``bounded_scan.py`` and a hypothetical
    ``tools/bounded_scan.py`` must not collide.

    A path outside the package falls back to its basename: the detector tests
    below run the production scanner over synthetic modules in a temp directory,
    so that they exercise the same entry point real modules go through.
    """
    try:
        return path.relative_to(PACKAGE_DIR).as_posix()
    except ValueError:
        return path.name


def list_bound_names(paths: Sequence[Path]) -> frozenset[str]:
    """Variable names bound to a list of message refs anywhere in *paths*."""
    names = set(STATIC_LIST_BOUND_NAMES)
    for path in paths:
        for _, line in script_lines(path):
            names.update(match.group("var") for match in LIST_BINDING.finditer(line))
    return frozenset(names)


def _scan_line(rel: str, lineno: int, line: str, bound_names: frozenset[str]) -> list[Site]:
    """Every finding on one reconstructed AppleScript line, safe ones included.

    A ``whose`` keyword is attributed to exactly one rule. The enumeration
    patterns claim theirs first, so ``every message of inboxMessages whose …`` is
    reported once as a dangerous ``whose`` on a mailbox reference rather than
    twice under two rules with two different remediations.
    """
    sites: list[Site] = []
    claimed: set[int] = set()

    for match in ENUMERATION.finditer(line):
        head = match.group("head")
        if match.group("whose") is not None:
            claimed.add(match.start("whose"))
            if not _predicate_is_safe(match.group("pred")):
                sites.append(Site(rel, lineno, DANGEROUS_WHOSE, line))
            else:
                sites.append(Site(rel, lineno, None, line))
        elif head == "messages":
            sites.append(Site(rel, lineno, RAW_MESSAGES, line))
        elif head.endswith("message") and head.startswith("every"):
            sites.append(Site(rel, lineno, RAW_EVERY_MESSAGE, line))
        # ``first|last|some|middle message of MB`` with no predicate is a single
        # bounded fetch, not an enumeration, so it is deliberately not reported.

    for pattern, rule in ((IMPLICIT_TARGET_WHOSE, DANGEROUS_WHOSE), (SLICED_LIST_WHOSE, WHOSE_ON_LIST)):
        for match in pattern.finditer(line):
            if match.start("whose") in claimed:
                continue
            claimed.add(match.start("whose"))
            if not _predicate_is_safe(match.group("pred")):
                sites.append(Site(rel, lineno, rule, line))

    for match in IDENTIFIER_WHOSE.finditer(line):
        if match.start("whose") in claimed:
            continue
        var = match.group("var")
        if var not in bound_names and not LIST_NAME_HINT.match(var):
            continue
        claimed.add(match.start("whose"))
        if not _predicate_is_safe(match.group("pred")):
            sites.append(Site(rel, lineno, WHOSE_ON_LIST, line))

    return sites


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def bare_property_sites(path: Path) -> list[Site]:
    """``contains_any_condition("<bare Mail property>", …)`` call sites.

    Resolved on the call node, not on a line of text, which is what makes the
    check immune to however the arguments are wrapped (evasion 4). The keyword
    form is covered too, since ``contains_any_condition(field_name="subject", …)``
    is the same call.
    """
    rel = pkg_rel(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sites: list[Site] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != CONTAINS_ANY_CONDITION:
            continue
        field: ast.AST | None = node.args[0] if node.args else None
        for keyword in node.keywords:
            if keyword.arg == "field_name":
                field = keyword.value
        if isinstance(field, ast.Constant) and field.value in BARE_MAIL_PROPERTIES:
            sites.append(Site(rel, node.lineno, BARE_PROPERTY, f"{CONTAINS_ANY_CONDITION}({field.value!r}, …)"))
    return sites


def scan_sites(paths: Sequence[Path] | None = None) -> list[Site]:
    """Every finding across *paths* (the whole package by default), safe ones included."""
    files = list(paths) if paths is not None else list(iter_package_files())
    bound_names = list_bound_names(files)
    sites: list[Site] = []
    for path in files:
        rel = pkg_rel(path)
        for lineno, line in script_lines(path):
            sites.extend(_scan_line(rel, lineno, line, bound_names))
        sites.extend(bare_property_sites(path))
    return sites


Counts = dict[str, dict[str, int]]


def scan(paths: Sequence[Path] | None = None) -> tuple[Counts, list[str]]:
    """``({module: {rule: count}}, rendered_details)`` for the violations only."""
    counts: Counts = {}
    details: list[str] = []
    for site in scan_sites(paths):
        if site.rule is None:
            continue
        counts.setdefault(site.rel, {})[site.rule] = counts.setdefault(site.rel, {}).get(site.rule, 0) + 1
        details.append(site.render())
    return counts, sorted(details)


# ---------------------------------------------------------------------------
# Ratchet baseline
# ---------------------------------------------------------------------------

# Keyed by ``PACKAGE_DIR``-relative path -> rule -> occurrence count.
#
# Deliberately NOT keyed by line number: these files get edited for unrelated
# reasons, and a line-keyed allowlist fails the build every time something above a
# site moves, which trains people to update the allowlist without reading it.
# Counts are stable under that churn and still fail closed on a new site in a new
# file or an extra site in a known file.
#
# The ratchet is two-sided. Lowering a number here is always a valid change and is
# the intended maintenance; raising one is not — fix the call site, or bring a
# reason to AGENTIC review. A count that comes in *under* baseline fails as stale,
# because a one-sided ratchet rots upward: every fix quietly funds the next
# violation in the same file.
#
# Driving this dict to empty is tracked separately; each site needs its own
# decision about whether the honest fallback is "enumerate everything" or "return
# a structured error".
KNOWN_VIOLATIONS: Counts = {
    # Ratcheted 1 -> 0 (entry removed) for ``tools/search/script.py``: its
    # ``on error`` fallback abandoned a bounded slice and enumerated everything
    # precisely when the bound had already failed. It now re-slices against
    # ``count of messages`` and emits an ``ERROR_MAILBOX`` diagnostic if that
    # fails too. ``bounded_scan.py`` never appeared here, being fixed in the same
    # change (AGENTIC-2355) that first brought it into scope.
    #
    # The survivors are the ``else`` arm of an ``if (count of messages of MB) > N``
    # guard — the shape ``bounded_scan.build_bounded_message_scan`` emitted and
    # every copy inherited. Do not go looking for an ``on error`` at them: there
    # isn't one. They stay bounded only because the guard proved the mailbox holds
    # <= N first, so the arm is reachable on a 24K mailbox only when N is itself
    # unclamped. Both remaining sites are clamped by their callers
    # (``export_helpers.py`` to 250, ``trash.py`` to 100).
    #
    # Ratcheted 1 -> 0 (entry removed) for ``tools/inbox/overview.py``: its N was
    # ``max_recent``, a plain tool argument with no clamp, so ``max_recent=50000``
    # against a 25K Exchange inbox made the guard false and took the enumerating
    # arm — the one reachable-from-a-tool-argument instance of this shape. It now
    # clamps to ``SCAN_BOUNDS["INBOX_HARD_CEILING"]`` and emits the slice through
    # ``build_bounded_message_scan``, which has no unbounded arm at all.
    "tools/analytics/export_helpers.py": {RAW_MESSAGES: 1},
    # Ratcheted 2 -> 1: the delete_permanent apply_to_all path no longer hand-rolls
    # a raw ``messages of trashMailbox`` fallback; it resolves ids through the
    # bounded search (which carries the caller's date window) and recurses into the
    # id-direct purge. The remaining site is the empty_trash branch.
    "tools/manage/trash.py": {RAW_MESSAGES: 1},
}

_REMEDIATION = {
    DANGEROUS_WHOSE: (
        "An unbounded `whose` predicate on a mailbox forces Mail to materialize the entire "
        "remote mailbox and hangs on 24K+ inboxes. Allowed predicates are `id is …`, "
        "`read status is …`, and the `{id_condition}` chain from "
        "`bounded_scan.build_whose_id_list`. For anything else use a bounded slice "
        "(`messages 1 thru N of MB`) or `bounded_scan.build_bounded_filtered_scan(...)`, "
        "which emits an in-AppleScript `repeat … if` filter."
    ),
    WHOSE_ON_LIST: (
        "AppleScript `whose` on a list of message refs is forbidden — Mail evaluates the "
        "predicate against each ref's underlying physical folder (on Gmail, "
        "`[Gmail]/All Mail`) and rejects the call with `Can't get {message id N of mailbox "
        '"[Gmail]/All Mail" …} whose …`. Replace with an in-loop `repeat … if` via '
        "`bounded_scan.build_bounded_filtered_scan(...)`."
    ),
    RAW_EVERY_MESSAGE: (
        "Raw `every message of <mailbox>` with no `whose` is a full-mailbox enumeration. "
        "Use `messages 1 thru N of MB` slicing instead."
    ),
    RAW_MESSAGES: (
        "Raw `messages of <mailbox>` is the same full-mailbox enumeration in a different "
        "spelling. Use a bounded slice (`messages 1 thru N of MB`) or "
        "`bounded_scan.build_bounded_filtered_scan(...)`. If a bounded slice already failed, "
        "prefer a structured error over enumerating everything."
    ),
    BARE_PROPERTY: (
        "`contains_any_condition` was passed a bare Mail property. That renders "
        '`subject contains "…"`, which only binds inside a `whose` clause; inside a `repeat` '
        "loop Mail raises -1728 on every message and the loop's `try` hides it, so the tool "
        "returns an empty result with no error (AGENTIC-2344). Pass the loop-bound variable "
        "instead (`messageSubject`), or let the callee build its own bounded predicate."
    ),
}


# ---------------------------------------------------------------------------
# Detector meta-tests — prove the lint cannot go vacuous
# ---------------------------------------------------------------------------


def verdicts_for_source(source: str) -> list[str]:
    """Rule names the real scanner reports for a synthetic module.

    Writes *source* to a temp file and runs the production entry point over it, so
    a test proves the whole pipeline — parse, reconstruct, strip, match, classify —
    and not a regex in isolation.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "synthetic_emitter.py"
        path.write_text(source, encoding="utf-8")
        return sorted(site.rule for site in scan_sites([path]) if site.rule is not None)


class ClosedEvasionTests(unittest.TestCase):
    """The four evasions a 2026-08 audit drove end to end through the old lint.

    Each sample is fed to the real scanner, not to a regex. The old line-by-line
    scanner returned nothing for all four; the last two cases pin the shapes that
    used to be reported under the *wrong* rule.

    Keep these. This lint has already gone vacuous once without anyone noticing,
    and a "no violations found" assertion is indistinguishable from a scanner that
    cannot see anything at all.
    """

    def test_adjacent_literal_concatenation_is_caught(self):
        """Evasion 1: two literals, one line. `\\w+` cannot match a closing quote.

        `ruff format` reflowing one long line produces exactly this, so the old
        lint could be disarmed by a formatting pass nobody reviewed as a change.
        """
        source = 'SCRIPT = "set hits to every message of " \'targetMailbox whose subject contains "urgent"\'\n'
        self.assertEqual(verdicts_for_source(source), [DANGEROUS_WHOSE])

    def test_whose_on_an_unlisted_slice_variable_is_caught(self):
        """Evasion 2: a slice variable outside the old hardcoded 11-name tuple.

        Run twice on purpose. ``pageMessages`` is the audit's shape and is caught
        by either mechanism; ``pageSlice`` matches no name hint at all, so it can
        only be caught by deriving the binding from the script itself. A rebuild
        that quietly reverted to a name list would still pass the first case.
        """
        for slice_var in ("pageMessages", "pageSlice"):
            with self.subTest(slice_var=slice_var):
                source = (
                    "def build(cap: int) -> str:\n"
                    '    return f"""\n'
                    f"    set {slice_var} to messages 1 thru {{cap}} of inboxMailbox\n"
                    f'    set hits to {slice_var} whose subject contains "urgent"\n'
                    '    """\n'
                )
                self.assertEqual(verdicts_for_source(source), [WHOSE_ON_LIST])

    def test_enumeration_split_across_two_python_lines_is_caught(self):
        """Evasion 3: `messages ` and `of MB` in different Python literals."""
        source = 'SCRIPT = (\n    "set mailboxMessages to messages "\n    "of targetMailbox"\n)\n'
        self.assertEqual(verdicts_for_source(source), [RAW_MESSAGES])

    def test_formatter_wrapped_contains_any_condition_is_caught(self):
        """Evasion 4: the quoted argument moved off the open-paren line."""
        source = (
            "def build(subject_terms):\n"
            "    return contains_any_condition(\n"
            '        "subject",\n'
            "        subject_terms,\n"
            "    )\n"
        )
        self.assertEqual(verdicts_for_source(source), [BARE_PROPERTY])

    def test_explicit_plus_concatenation_is_caught(self):
        """The same split as evasion 3, joined with `+` and a non-literal middle."""
        source = (
            "def build(mailbox: str) -> str:\n"
            "    return \"set hits to every message of \" + mailbox + ' whose date received > cutoff'\n"
        )
        self.assertEqual(verdicts_for_source(source), [DANGEROUS_WHOSE])

    def test_idiomatic_mailbox_literal_form_is_named_correctly(self):
        """`every message of mailbox "X" whose …` is a dangerous whose, not a raw scan.

        The old token class stopped at `mailbox` and demanded `whose` immediately,
        so the quoted name broke the match and the line fell through to the
        raw-enumeration rule — which reported "no `whose` at all" about a line
        whose entire problem was the `whose`, and pointed the fix the wrong way.
        """
        source = 'SCRIPT = \'set hits to every message of mailbox "INBOX" whose subject contains "urgent"\'\n'
        self.assertEqual(verdicts_for_source(source), [DANGEROUS_WHOSE])

    def test_nested_mailbox_reference_with_whose_is_caught(self):
        source = "SCRIPT = 'set hits to every message of mailbox \"INBOX\" of acct whose date received > cutoff'\n"
        self.assertEqual(verdicts_for_source(source), [DANGEROUS_WHOSE])

    def test_implicit_target_whose_inside_tell_block_is_caught(self):
        """`messages whose …` with the mailbox supplied by an enclosing `tell`."""
        source = (
            "def build() -> str:\n"
            '    return """\n'
            '    tell mailbox "INBOX"\n'
            '        set hits to messages whose subject contains "urgent"\n'
            "    end tell\n"
            '    """\n'
        )
        self.assertEqual(verdicts_for_source(source), [DANGEROUS_WHOSE])

    def test_whose_on_an_inline_slice_expression_is_caught(self):
        source = "SCRIPT = 'set hits to (messages 1 thru 200 of MB) whose subject contains \"x\"'\n"
        self.assertEqual(verdicts_for_source(source), [WHOSE_ON_LIST])


class SafeSpellingsStayLegalTests(unittest.TestCase):
    """The negative half. A detector that flags everything is as useless as one that flags nothing."""

    def test_bounded_slice_is_legal(self):
        source = "SCRIPT = 'set candidateMessages to messages 1 thru scanUpperBound of currentMailbox'\n"
        self.assertEqual(verdicts_for_source(source), [])

    def test_count_of_messages_is_a_property_read(self):
        source = "SCRIPT = 'set messageCount to count of messages of currentMailbox'\n"
        self.assertEqual(verdicts_for_source(source), [])

    def test_parenthesised_count_of_messages_is_a_property_read(self):
        source = "SCRIPT = 'set n to count of (messages of currentMailbox whose read status is false)'\n"
        self.assertEqual(verdicts_for_source(source), [])

    def test_outgoing_messages_are_compose_windows(self):
        source = "SCRIPT = 'count of outgoing messages of application \"Mail\"'\n"
        self.assertEqual(verdicts_for_source(source), [])

    def test_id_indexed_whose_is_legal(self):
        source = "SCRIPT = 'set targetDrafts to every message of draftsMailbox whose id is 4711'\n"
        self.assertEqual(verdicts_for_source(source), [])

    def test_id_condition_slot_is_legal(self):
        source = (
            'def build(id_condition: str) -> str:\n    return f"every message of targetMailbox whose {id_condition}"\n'
        )
        self.assertEqual(verdicts_for_source(source), [])

    def test_read_status_whose_is_legal(self):
        source = "SCRIPT = 'set unreadOnes to every message of inboxMailbox whose read status is false'\n"
        self.assertEqual(verdicts_for_source(source), [])

    def test_single_message_fetch_is_not_an_enumeration(self):
        source = "SCRIPT = 'set newest to first message of inboxMailbox'\n"
        self.assertEqual(verdicts_for_source(source), [])

    def test_loop_bound_variable_keeps_contains_any_condition_legal(self):
        source = 'X = contains_any_condition("messageSubject", subject_terms)\n'
        self.assertEqual(verdicts_for_source(source), [])

    def test_prose_describing_the_banned_pattern_is_not_a_violation(self):
        """This package documents the banned spellings constantly in order to refuse them."""
        source = (
            '"""Never binds the full ``messages of targetMailbox``.\n\n'
            "Nor ``every message of mailbox whose subject contains x``.\n"
            '"""\n'
            "def helper():\n"
            '    """Refuses to emit ``messages of MB``."""\n'
            "    return None\n"
        )
        self.assertEqual(verdicts_for_source(source), [])

    def test_applescript_comment_is_not_a_violation(self):
        source = 'SCRIPT = """\n-- never do: set x to messages of inboxMailbox\nset x to 1\n"""\n'
        self.assertEqual(verdicts_for_source(source), [])

    def test_whose_on_a_window_or_calendar_is_out_of_scope(self):
        """System Events windows and Calendar events are different collections."""
        source = (
            "SCRIPT = '''\n"
            "set composeWindow to first window whose name contains theMarker\n"
            "set todays to every event of targetCal whose start date >= windowStart\n"
            "'''\n"
        )
        self.assertEqual(verdicts_for_source(source), [])


class ReconstructionTests(unittest.TestCase):
    """The extraction layer, checked directly.

    A regex over raw Python source and a ``Constant``-only AST walk both look
    exactly like a working extractor until you inspect what came out of them.
    """

    def _render_only(self, source: str) -> list[str]:
        tree = ast.parse(source)
        return [text for node in ast.walk(tree) if (text := render_script(node)) is not None]

    def test_adjacent_literals_reconstruct_as_one_string(self):
        self.assertIn("every message of targetMailbox", self._render_only('X = "every message of " "targetMailbox"'))

    def test_fstring_slots_render_as_named_placeholders(self):
        rendered = self._render_only('X = f"every message of {mailbox_var} whose {id_condition}"')
        self.assertIn("every message of {mailbox_var} whose {id_condition}", rendered)

    def test_call_slots_collapse_to_their_root_identifier(self):
        self.assertIn(
            "every message of {resolve}",
            self._render_only('X = f"every message of {resolve(name)}"'),
        )

    def test_plus_chain_with_a_non_literal_middle_keeps_a_placeholder(self):
        rendered = self._render_only('X = "every message of " + mb + " whose subject contains z"')
        self.assertIn("every message of " + OPAQUE_SLOT + " whose subject contains z", rendered)

    def test_a_consumed_fragment_is_not_also_reported_alone(self):
        """Each literal is scanned once, so one violation cannot be counted twice."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.py"
            path.write_text('X = "set hits to messages " "of targetMailbox"\n', encoding="utf-8")
            texts = [text for _, text in iter_script_literals(path)]
            self.assertEqual(texts, ["set hits to messages of targetMailbox"])

    def test_extraction_finds_real_applescript_in_the_known_emitters(self):
        """Anchor on stable script text from modules that actually build scans."""
        for rel, needle in (
            ("tools/search/by_id.py", "every message of targetMailbox whose"),
            # Was anchored on "messages of inboxMailbox", which the v3.11.8
            # `max_recent` clamp removed as a raw enumeration. The needle kept
            # passing by matching the benign `count of messages of ...` read, so
            # the canary was quietly testing nothing it was written to test.
            # Anchored now on text the recent-scan loop cannot lose without the
            # extractor genuinely going blind.
            ("tools/inbox/overview.py", "repeat with aMessage in"),
            ("bounded_scan.py", "messages 1 thru"),
        ):
            with self.subTest(module=rel):
                text = "\n".join(chunk for _, chunk in iter_script_literals(PACKAGE_DIR / rel))
                self.assertIn(
                    needle,
                    text,
                    f"Extraction returned no AppleScript containing {needle!r} from {rel}. "
                    "The extractor is not seeing the scripts, so every count below is fake.",
                )

    def test_reported_line_numbers_point_at_the_real_source_line(self):
        """A finding nobody can locate is a finding nobody fixes."""
        sites = [site for site in scan_sites() if site.rule == RAW_MESSAGES]
        self.assertTrue(sites, "No raw `messages of` sites found at all; the scanner has gone blind.")
        for site in sites:
            with self.subTest(site=site.rel):
                source_line = (PACKAGE_DIR / site.rel).read_text(encoding="utf-8").splitlines()[site.lineno - 1]
                self.assertIn("messages of", source_line, f"{site.rel}:{site.lineno} does not hold the reported text.")


class ListBoundNameTests(unittest.TestCase):
    """The slice-variable rule's input set — the thing evasion 2 walked through."""

    def test_names_are_derived_from_source_not_hardcoded(self):
        derived = list_bound_names(list(iter_package_files()))
        self.assertGreater(
            len(derived - STATIC_LIST_BOUND_NAMES),
            0,
            "No list-bound names were derived from source, so the rule has fallen back to the "
            "hardcoded tuple that evasion 2 walked straight past.",
        )
        # Spot-anchor two the old hardcoded tuple never listed.
        for name in ("matchingMessages", "targetDrafts"):
            self.assertIn(name, derived, f"{name} is bound to a message list in the package but was not derived.")

    def test_the_static_floor_is_kept_under_the_derived_set(self):
        self.assertTrue(list_bound_names([]) >= STATIC_LIST_BOUND_NAMES)

    def test_a_locally_bound_name_is_derived_from_the_module_under_scan(self):
        source = 'X = """\nset weirdlyNamedSlice to messages 1 thru 50 of MB\nset hits to weirdlyNamedSlice whose subject contains "x"\n"""\n'
        self.assertEqual(verdicts_for_source(source), [WHOSE_ON_LIST])

    def test_the_name_hint_backstops_a_binding_the_scanner_cannot_see(self):
        """A list bound in a helper the scanner cannot follow is still not `whose`-able."""
        source = "X = 'set hits to harvestedMessages whose subject contains \"x\"'\n"
        self.assertEqual(verdicts_for_source(source), [WHOSE_ON_LIST])

    def test_an_id_predicate_on_a_list_stays_legal(self):
        source = "X = 'set hits to candidateMessages whose id is 4711'\n"
        self.assertEqual(verdicts_for_source(source), [])


class LintCoverageTests(unittest.TestCase):
    """Guard the scanner's *input set*, not just its verdict.

    Every ratchet test below asserts that nothing new was found. None of them can
    tell "scanned every module and found nothing" apart from "scanned almost
    nothing". When the tool surfaces became packages, this lint's non-recursive
    ``glob`` silently narrowed to two flat leaves and all of its checks began
    passing vacuously — undetected until the AGENTIC-2344 subject-filter bug
    shipped through the exact hole they were meant to close.
    """

    def test_the_shared_machinery_really_came_from_the_sibling_lint(self):
        """A silent fallback to a private copy would let the two gates drift apart."""
        self.assertEqual(BARE_TRY_LINT.PACKAGE_DIR, PACKAGE_DIR)
        self.assertEqual(Path(BARE_TRY_LINT.__file__).resolve(), _SIBLING_PATH.resolve())
        for name in ("strip_applescript_comments", "iter_package_files", "regressions", "stale_entries"):
            self.assertTrue(hasattr(BARE_TRY_LINT, name), f"The sibling lint no longer exports {name}.")

    def test_every_rule_is_rooted_at_the_package(self):
        """``tools/``-only rooting is the AGENTIC-2355 hole, recorded as an assertion.

        Three of the five rules used to root at ``TOOLS_DIR`` on the theory that
        only tool surfaces emit scan loops. That was wrong in the one way that
        mattered: ``bounded_scan.build_bounded_message_scan`` — the helper every
        caller trusts to bound its scan — sits one directory *above* ``tools/``,
        so the bounded-scan lint could not read the bounded-scan builder, and its
        small-mailbox arm emitted a raw ``messages of <mailbox>`` with nothing
        flagging it. Narrowing the root again would restore that hole while every
        other assertion in this module kept passing.
        """
        scanned = {pkg_rel(path) for path in iter_package_files()}
        for required in (
            "bounded_scan.py",
            "core/script_fragments.py",
            "core/normalization.py",
            "calendar_core/scripts_read.py",
            "calendar_core/scripts_write.py",
            "tools/search/script.py",
        ):
            self.assertIn(
                required,
                scanned,
                f"{required} emits AppleScript or defines the banned helper but is out of scope. "
                "Every rule here must root at PACKAGE_DIR, not TOOLS_DIR (AGENTIC-2355).",
            )

    def test_scan_is_recursive_over_every_package(self):
        scanned = {pkg_rel(path) for path in iter_package_files()}
        packages = {
            path.parent.relative_to(PACKAGE_DIR).as_posix()
            for path in PACKAGE_DIR.rglob("*.py")
            if "__pycache__" not in path.parts and path.parent != PACKAGE_DIR
        }
        missing = sorted(pkg for pkg in packages if not any(rel.startswith(pkg + "/") for rel in scanned))
        self.assertFalse(
            missing,
            f"Not scanning these packages at all: {missing}. The file set must come from an "
            "rglob; a non-recursive glob makes every check in this module vacuous.",
        )
        self.assertGreater(len(scanned), 60, f"Only {len(scanned)} module(s) scanned; the file set regressed.")

    def test_every_tool_package_is_represented(self):
        scanned = {pkg_rel(path) for path in iter_package_files()}
        tool_packages = {
            f"tools/{path.parent.relative_to(TOOLS_DIR).as_posix()}"
            for path in TOOLS_DIR.rglob("*.py")
            if "__pycache__" not in path.parts and path.parent != TOOLS_DIR
        }
        missing = sorted(pkg for pkg in tool_packages if not any(rel.startswith(pkg + "/") for rel in scanned))
        self.assertFalse(missing, f"The `whose` lint is not scanning these tool packages at all: {missing}.")
        self.assertIn(
            "tools/search/script.py",
            scanned,
            "search/script.py must be linted: it emits the per-message scan loops.",
        )

    def test_scan_keys_are_unambiguous_across_packages(self):
        """Basename keys would collide; ``helpers.py`` exists in four packages."""
        keys = [pkg_rel(path) for path in iter_package_files()]
        self.assertEqual(len(keys), len(set(keys)), "Scan keys must be unique.")
        self.assertGreater(
            len([key for key in keys if key.endswith("/helpers.py")]),
            1,
            "Expected package-qualified keys like 'tools/manage/helpers.py', not bare basenames.",
        )

    def test_the_scanner_still_sees_the_id_indexed_whose_sites(self):
        """A scanner that reports nothing *and* sees nothing passes every ratchet.

        These are the safe, id-indexed `whose` clauses the package legitimately
        uses. If this count collapses, the extractor stopped reading scripts and
        the clean ratchet below means nothing.
        """
        safe = [site for site in scan_sites() if site.rule is None]
        self.assertGreater(
            len(safe),
            10,
            f"Only {len(safe)} id-indexed `whose` site(s) recognised; the package has many more, "
            "so the extractor or the predicate classifier has regressed.",
        )

    def test_baseline_keys_name_scanned_modules(self):
        """A key that names no module silently retires a grandfathered site."""
        scanned = {pkg_rel(path) for path in iter_package_files()}
        unknown = sorted(set(KNOWN_VIOLATIONS) - scanned)
        self.assertFalse(
            unknown,
            f"KNOWN_VIOLATIONS keys no scanned module: {unknown}. Keys are PACKAGE_DIR-relative, "
            "e.g. 'tools/manage/trash.py'.",
        )
        for rel, rules in KNOWN_VIOLATIONS.items():
            self.assertTrue(set(rules) <= set(RULES), f"Unknown rule key in {rel}: {sorted(rules)}")


class RatchetTests(unittest.TestCase):
    """The package scan against its baseline, one rule at a time."""

    @classmethod
    def setUpClass(cls):
        cls.counts, cls.details = scan()

    def _assert_rule(self, rule: str):
        new = regressions(self.counts, KNOWN_VIOLATIONS, rule)
        self.assertFalse(
            new,
            f"New `{rule}` site(s).\n  - "
            + "\n  - ".join(new)
            + "\n\n"
            + _REMEDIATION[rule]
            + "\n\nAll sites:\n  - "
            + "\n  - ".join(self.details),
        )

    def _assert_not_stale(self, rule: str):
        stale = stale_entries(self.counts, KNOWN_VIOLATIONS, rule)
        self.assertFalse(
            stale,
            f"KNOWN_VIOLATIONS is stale for `{rule}` — these files improved. Lower or remove the "
            "entries so the ratchet cannot silently re-admit a fixed site:\n  - " + "\n  - ".join(stale),
        )

    def test_no_new_dangerous_whose(self):
        self._assert_rule(DANGEROUS_WHOSE)

    def test_no_new_whose_on_a_message_list(self):
        self._assert_rule(WHOSE_ON_LIST)

    def test_no_new_raw_every_message_enumeration(self):
        self._assert_rule(RAW_EVERY_MESSAGE)

    def test_no_new_raw_messages_enumeration(self):
        self._assert_rule(RAW_MESSAGES)

    def test_no_new_bare_property_condition(self):
        self._assert_rule(BARE_PROPERTY)

    def test_dangerous_whose_baseline_is_not_stale(self):
        self._assert_not_stale(DANGEROUS_WHOSE)

    def test_whose_on_list_baseline_is_not_stale(self):
        self._assert_not_stale(WHOSE_ON_LIST)

    def test_raw_every_message_baseline_is_not_stale(self):
        self._assert_not_stale(RAW_EVERY_MESSAGE)

    def test_raw_messages_baseline_is_not_stale(self):
        self._assert_not_stale(RAW_MESSAGES)

    def test_bare_property_baseline_is_not_stale(self):
        self._assert_not_stale(BARE_PROPERTY)

    def test_baseline_has_no_entries_for_clean_files(self):
        orphans = sorted(rel for rel in KNOWN_VIOLATIONS if rel not in self.counts)
        self.assertFalse(
            orphans, "KNOWN_VIOLATIONS lists files with zero remaining sites:\n  - " + "\n  - ".join(orphans)
        )

    def test_the_ratchet_fails_closed_on_an_unlisted_module(self):
        """The direction that matters: a brand-new module gets an allowance of 0."""
        counts: Counts = {"tools/brand_new.py": {RAW_MESSAGES: 1}}
        self.assertEqual(
            regressions(counts, KNOWN_VIOLATIONS, RAW_MESSAGES),
            ["tools/brand_new.py: 1 site(s), baseline allows 0"],
        )

    def test_the_ratchet_is_two_sided(self):
        """A count under baseline is stale, not a pass; one-sided ratchets rot upward.

        Checked against a synthetic baseline so the case keeps its meaning as the
        real one is ratcheted down.
        """
        base: Counts = {"tools/a.py": {RAW_MESSAGES: 2}}
        under: Counts = {"tools/a.py": {RAW_MESSAGES: 1}}
        self.assertEqual(regressions(under, base, RAW_MESSAGES), [])
        self.assertEqual(stale_entries(under, base, RAW_MESSAGES), ["tools/a.py: baseline says 2, found 1"])
        self.assertEqual(stale_entries({}, base, RAW_MESSAGES), ["tools/a.py: baseline says 2, found 0"])

    def test_rules_are_scored_independently(self):
        """A raw-enumeration allowance must not fund a dangerous-`whose` site."""
        counts: Counts = {"tools/manage/trash.py": {RAW_MESSAGES: 1, DANGEROUS_WHOSE: 1}}
        self.assertEqual(regressions(counts, KNOWN_VIOLATIONS, RAW_MESSAGES), [])
        self.assertEqual(len(regressions(counts, KNOWN_VIOLATIONS, DANGEROUS_WHOSE)), 1)


class NoAllowFullScanTests(unittest.TestCase):
    """``allow_full_scan`` was retired in v3.2.0 in favour of structured errors.

    Scoped to the whole package, like every other rule here. This one reads raw
    file text rather than reconstructed scripts on purpose: it bans a Python
    identifier, and a mention in a comment or docstring is exactly the kind of
    half-revert that reintroduces it.
    """

    def test_no_allow_full_scan_in_the_package(self):
        offenders: list[str] = []
        for path in iter_package_files():
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "allow_full_scan" in line:
                    offenders.append(f"{pkg_rel(path)}:{lineno}: {line.strip()}")

        self.assertEqual(
            offenders,
            [],
            "`allow_full_scan` was retired in v3.2.0. Tools must raise `UNBOUNDED_SCAN_REQUIRED` "
            "with a bounded `preferred` fix and must NOT point at `full_inbox_export` (disabled). "
            "Offenders:\n  - " + "\n  - ".join(offenders),
        )

    def test_tool_signatures_have_no_allow_full_scan_param(self):
        # Imported after the static checks so any import-time failure still leaves
        # the file scans above with useful output.
        import apple_mail_mcp  # noqa: F401  (registers tools as a side effect)
        from apple_mail_mcp.server import mcp

        tool_manager = getattr(mcp, "_tool_manager", None)
        self.assertIsNotNone(tool_manager, "FastMCP._tool_manager is missing — has FastMCP changed shape?")

        offenders: list[str] = []
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
