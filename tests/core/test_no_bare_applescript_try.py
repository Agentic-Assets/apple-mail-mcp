"""Static ratchet against bare AppleScript ``try`` blocks (silent error channels).

Sibling lint to ``tests/core/test_no_unbounded_whose.py``. That one guards *how
much* Mail is asked to enumerate; this one guards *whether a failure is allowed
to stay invisible*.

Why this class of defect is uniquely dangerous here
---------------------------------------------------
Every AppleScript this package emits runs through
``plugin/apple_mail_mcp/core/applescript.py::run_applescript``, and that
function **raises on a nonzero ``osascript`` exit**. So a script with no ``try``
at all fails loudly, which is correct behaviour. A *bare* ``try`` — one whose
handler is ``end try`` with no ``on error`` arm, or whose ``on error`` arm is
empty — is precisely the construct that converts a loud failure into an exit-0
wrong answer. Shipped examples of what that looked like in production:

* a subject-filter search returning 0 results on every account because a -1728
  unbound-property error was swallowed once per message;
* ``forward_email`` sending mail with the quoted original silently missing;
* ``list_inbox_emails`` in JSON mode returning a payload byte-identical to a
  genuinely empty inbox when the scan threw;
* ``export_emails`` printing "Exported: 47" when the writes had failed;
* calendar enumeration failure rendering as "you have nothing on your calendar".

It compounds: ``plugin/apple_mail_mcp/core/script_fragments.py`` deliberately
raises well-crafted AppleScript errors (``error "No inbox mailbox found…"``,
``error "Mailbox name is ambiguous…"``). Any enclosing script-level bare ``try``
eats them.

The two rules
-------------
``bare``
    A ``try`` with **no** ``on error`` arm, or with an ``on error`` arm whose
    body is empty. There is literally no code that could observe the error, so
    this is decidable from the text and is the hard rule.

``silent``
    A ``try`` whose ``on error`` arm exists and is non-empty but contains no
    observable error signal: no ``error`` re-raise, no reference to the bound
    error variable, no sanctioned marker literal, no error/failure/skip/partial
    token. This tier is a *heuristic*, which is exactly why it is ratcheted
    against a baseline rather than driven to zero: an ``on error`` arm that sets
    a sentinel ``-1`` for an older-OS property probe is legitimate, and a rule
    that hard-failed on every non-reporting arm would be routed around. Existing
    judgement calls live in the baseline; only *new* ones fail.

Both baselines only tighten. Lowering a number is always valid; raising one is
not — fix the call site, or bring the case to review.

Regenerate the baseline with::

    python3 tests/core/test_no_bare_applescript_try.py --write-baseline

Sanctioned replacements for a bare ``try`` (all three already ship here):

P1  in-band marker + per-item counter.
    Producer ``plugin/apple_mail_mcp/tools/search/script.py`` (``ERROR_MAILBOX|||``);
    consumer ``plugin/apple_mail_mcp/tools/search/records.py`` -> ``error_details``
    plus a ``PARTIAL: N mailbox issue(s)`` line.
P2  whole-script sentinel + tri-state.
    Producer ``plugin/apple_mail_mcp/core/reply_state.py`` (``ERROR|||``);
    consumer ``plugin/apple_mail_mcp/tools/reply_state_wiring.py``. Gold
    standard: the blank-row emit keeps the count honest and Python uses a
    tri-state ``None`` rather than fabricating a ``False``.
P3  sentinel + error count.
    Producer ``plugin/apple_mail_mcp/tools/analytics/statistics.py``
    (``__APPLE_MAIL_MCP_ERROR__|||``, ``mboxErrorCount``); consumer
    ``plugin/apple_mail_mcp/tools/analytics/statistics_parsing.py`` ->
    ``MAILBOX SCAN ERRORS``.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tempfile
import unittest
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Scope is the whole package, NOT ``tools/``.
#
# ``test_no_unbounded_whose.py`` scopes most of its rules to ``tools/`` and
# documents the resulting blind spot. That blind spot is fatal for this rule:
# ``end try`` appears in ``core/script_fragments.py``, ``core/reply_state.py``,
# ``core/replied.py``, ``core/escaping.py``, ``bounded_scan.py``,
# ``applescript_snippets.py``, and both ``calendar_core/scripts_*.py`` modules —
# every one of them a shared AppleScript emitter whose bare ``try`` would be
# inherited by many tools at once. A ``tools/``-only scan would miss roughly a
# quarter of the real sites, including the fragment builder that raises the
# deliberate ``error "No inbox mailbox found…"`` this rule exists to protect.
PACKAGE_DIR = ROOT / "plugin" / "apple_mail_mcp"

BASELINE_PATH = ROOT / "tests" / "fixtures" / "bare_applescript_try" / "baseline.json"

REGEN_CMD = "python3 tests/core/test_no_bare_applescript_try.py --write-baseline"

# Stand-in for an f-string's ``{...}`` interpolation. Substituting a token
# rather than dropping the slot keeps the surrounding AppleScript contiguous, so
# a ``try`` before an interpolation still pairs with the ``end try`` after it.
FSTRING_PLACEHOLDER = "__FSTRING_EXPR__"

NESTED_BLOCK_MARKER = "<nested try block>"

# Stands in for an ``on error`` arm that arrives through an f-string slot filled
# by a dangling-arm builder. See ``find_dangling_arm_builders``.
SPLICED_ARM_MARKER = "<spliced on error arm from>"

# ---------------------------------------------------------------------------
# AppleScript tokens
# ---------------------------------------------------------------------------

# AppleScript's ``try`` is a block opener and must stand alone on its line. The
# anchored, whole-line match is what keeps Python's ``try:`` (colon) and every
# prose/identifier use of the word "try" out of the scan.
TRY_OPEN = re.compile(r"^\s*try\s*$")
TRY_END = re.compile(r"^\s*end\s+try\b")
# ``on error [eVar] [number n] [from f] [to t] [partial result l]``
ON_ERROR = re.compile(r"^\s*on\s+error\b")
# ``error "…"`` / ``error errMsg number errNum`` is a *raise*, not a handler.
ERROR_RAISE = re.compile(r"^\s*error\b", re.MULTILINE)

# ``on error`` sub-clause keywords, which are not error-variable names.
_ON_ERROR_KEYWORDS = frozenset({"number", "from", "to", "partial"})
_ON_ERROR_VAR = re.compile(r"^on\s+error\s+([A-Za-z_]\w*)")

# AppleScript comments. ``(* … *)`` is stripped per string literal (never across
# the whole file) so an unbalanced opener cannot swallow an unrelated script.
_AS_BLOCK_COMMENT = re.compile(r"\(\*.*?\*\)", re.DOTALL)

# Whole-script/in-band error channels this package sanctions (P1/P2/P3 above).
SANCTIONED_ERROR_MARKERS = (
    "ERROR|||",
    "ERROR_MAILBOX|||",
    "__APPLE_MAIL_MCP_ERROR__",
)

# Weaker but still-real evidence that a handler does something with the failure.
_ERROR_SIGNAL_WORDS = re.compile(r"[Ee]rror|[Ff]ail|[Ss]kip|[Ww]arn|[Pp]artial|[Uu]navailable")

BARE = "bare"
SILENT = "silent"

_REMEDIATION = (
    "A bare AppleScript `try` turns a loud failure into an exit-0 wrong answer: "
    "`core/applescript.py::run_applescript` raises on a nonzero osascript exit, so a "
    "script with NO `try` is already correct. Swallowing the throw is what produces a "
    "confident empty result, a zero count, or an 'operation succeeded' banner over a "
    "mailbox that threw on every message. It also eats the deliberate "
    '`error "No inbox mailbox found…"` raises in core/script_fragments.py.\n'
    "Use one of the three shipped error channels instead of a bare handler:\n"
    "  P1 in-band marker + per-item counter — producer "
    "plugin/apple_mail_mcp/tools/search/script.py (`ERROR_MAILBOX|||`), consumer "
    "plugin/apple_mail_mcp/tools/search/records.py (`error_details`, `PARTIAL: N mailbox issue(s)`).\n"
    "  P2 whole-script sentinel + tri-state — producer "
    "plugin/apple_mail_mcp/core/reply_state.py (`ERROR|||`), consumer "
    "plugin/apple_mail_mcp/tools/reply_state_wiring.py. Emits a blank row so the count "
    "stays honest and returns tri-state None rather than a fabricated False.\n"
    "  P3 sentinel + error count — producer "
    "plugin/apple_mail_mcp/tools/analytics/statistics.py "
    "(`__APPLE_MAIL_MCP_ERROR__|||`, `mboxErrorCount`), consumer "
    "plugin/apple_mail_mcp/tools/analytics/statistics_parsing.py (`MAILBOX SCAN ERRORS`).\n"
    "If the failure is genuinely fatal, delete the `try` and let run_applescript raise."
)


# ---------------------------------------------------------------------------
# AppleScript extraction
# ---------------------------------------------------------------------------


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Ids of module/class/function docstring nodes, which are prose, not script."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            ids.add(id(first.value))
    return ids


def _literal_text(node: ast.AST) -> str | None:
    """Script text of a string constant or f-string, or ``None`` for anything else."""
    if isinstance(node, ast.JoinedStr):
        return "".join(
            value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else FSTRING_PLACEHOLDER
            for value in node.values
        )
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dangles_an_arm(text: str) -> bool:
    """True when *text* opens an ``on error`` arm at nesting depth 0.

    Such a fragment is not a script; it is a handler for a ``try`` opened by
    whoever splices it in.
    """
    depth = 0
    for line in strip_applescript_comments(text):
        if TRY_OPEN.match(line):
            depth += 1
        elif TRY_END.match(line):
            depth = max(0, depth - 1)
        elif ON_ERROR.match(line) and depth == 0:
            return True
    return False


def _returned_scripts(node: ast.AST) -> list[str]:
    """Script-bearing values a definition can hand back, one per ``return``."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        values = [sub.value for sub in ast.walk(node) if isinstance(sub, ast.Return) and sub.value is not None]
    else:
        values = [node]
    texts = [_literal_text(value) for value in values]
    return [text for text in texts if text and _carries_try_tokens(strip_applescript_comments(text))]


def find_dangling_arm_builders() -> dict[str, str]:
    """Names of helpers that return a *dangling* ``on error`` arm, mapped to their module.

    ``plugin/apple_mail_mcp/tools/analytics/export_failure_reporting.py::export_failure_arm``
    and ``tools/search/script.py::_SCAN_FAILURE_ARM`` deliberately emit an arm
    that opens with ``on error`` and does **not** close the ``try``; callers
    splice it in immediately before their loop body's own ``end try``. Both feed
    the sanctioned P1 channel (``exportFailureCount`` / ``ERROR_MAILBOX|||`` ->
    ``PARTIAL:``), so a call site using one is the *correct* pattern.

    Without this resolution those call sites read as bare — the arm arrives
    through an f-string slot the scanner replaced with an opaque placeholder —
    and the lint would fail people for adopting the very fix it recommends.

    A name qualifies only when **every** script it can return dangles. Requiring
    all branches matters: ``applescript_snippets.py``'s ``recipient_addresses_block``
    and ``thread_headers_block`` each have one early-return branch that happens to
    contain a depth-0 ``on error``, and crediting them would whitewash real call
    sites that receive their *other*, self-contained branch.
    """
    builders: dict[str, str] = {}
    for path in iter_package_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name: str | None = node.name
                target: ast.AST = node
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                target = node.value
            else:
                continue
            scripts = _returned_scripts(target)
            if scripts and all(_dangles_an_arm(script) for script in scripts) and name is not None:
                builders[name] = _rel(path)
    return builders


_DANGLING_ARM_CACHE: dict[str, str] | None = None


def dangling_arm_builders() -> dict[str, str]:
    global _DANGLING_ARM_CACHE
    if _DANGLING_ARM_CACHE is None:
        _DANGLING_ARM_CACHE = find_dangling_arm_builders()
    return _DANGLING_ARM_CACHE


def _slot_identifier(node: ast.AST) -> str | None:
    """Root name of an f-string slot: ``f(x)`` -> ``f``, ``m.f(x)`` -> ``f``, ``NAME`` -> ``NAME``."""
    if isinstance(node, ast.Call):
        return _slot_identifier(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _joined_str_text(node: ast.JoinedStr, builders: dict[str, str]) -> str:
    """Reconstruct an f-string, substituting each ``{expr}`` slot.

    An ordinary slot becomes ``FSTRING_PLACEHOLDER``: a token rather than a
    deletion, so a ``try`` before the slot still pairs with the ``end try``
    after it. A slot filled by a dangling-arm builder instead becomes a real
    ``on error`` line, because that is what it expands to at runtime.
    """
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
            continue
        name = _slot_identifier(value.value) if isinstance(value, ast.FormattedValue) else None
        if name in builders:
            parts.append(f"\non error {SPLICED_ARM_MARKER} {name}\n    {SPLICED_ARM_MARKER} {name}\n")
        else:
            parts.append(FSTRING_PLACEHOLDER)
    return "".join(parts)


def iter_string_literals(path: Path, *, include_fstrings: bool = True) -> list[tuple[int, str]]:
    """Every non-docstring string literal in *path*, as ``(lineno, text)``.

    Most AppleScript in this package is written as an f-string, which the AST
    models as ``ast.JoinedStr`` holding ``ast.Constant`` *segments*. A plain
    ``ast.Constant`` walk therefore yields those segments individually, chopped
    at every ``{...}`` — nesting analysis over the pieces is meaningless, and
    ``include_fstrings=False`` reproduces exactly that failure mode for
    ``test_naive_constant_walk_would_go_vacuous``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstring_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            skip.update(id(value) for value in node.values if isinstance(value, ast.Constant))

    builders = dangling_arm_builders()
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if include_fstrings and isinstance(node, ast.JoinedStr):
            found.append((node.lineno, _joined_str_text(node, builders)))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skip:
                continue
            found.append((node.lineno, node.value))
    found.sort(key=lambda item: item[0])
    return found


def strip_applescript_comments(text: str) -> list[str]:
    """Drop ``(* … *)`` blocks and ``--`` / leading ``#`` comments; return lines.

    Truncating at ``--`` can only remove text, never synthesize a token, so a
    ``--`` inside an AppleScript string literal is harmless here.
    """
    lines: list[str] = []
    for line in _AS_BLOCK_COMMENT.sub(" ", text).split("\n"):
        marker = line.find("--")
        if marker != -1:
            line = line[:marker]
        if line.lstrip().startswith("#"):
            line = ""
        lines.append(line)
    return lines


def _carries_try_tokens(lines: Iterable[str]) -> bool:
    return any(TRY_OPEN.match(line) or TRY_END.match(line) or ON_ERROR.match(line) for line in lines)


def script_lines(path: Path, *, include_fstrings: bool = True) -> list[str]:
    """Comment-stripped AppleScript lines for *path*, concatenated in source order.

    Concatenation (rather than one pass per literal) is deliberate: ``compose/
    forward.py`` and ``compose/send.py`` each assemble one script from a head
    literal and a tail literal, so a ``try`` opened in the head closes in the
    tail. Analyzed literal-by-literal those blocks are unmatched on both sides
    and silently drop out of the scan. Only literals that actually carry a
    ``try``/``end try``/``on error`` token are joined, which keeps unrelated
    strings off the nesting stack.
    """
    lines: list[str] = []
    for _, text in iter_string_literals(path, include_fstrings=include_fstrings):
        stripped = strip_applescript_comments(text)
        if _carries_try_tokens(stripped):
            lines.extend(stripped)
    return lines


# ---------------------------------------------------------------------------
# try-block analysis
# ---------------------------------------------------------------------------


class TryBlock:
    """One ``try … end try`` block, with its body and handler arm separated."""

    __slots__ = ("body", "end", "handler", "on_error_line", "on_error_text", "start")

    def __init__(self, start: int) -> None:
        self.start = start
        self.end = -1
        self.on_error_line: int | None = None
        self.on_error_text = ""
        self.body: list[str] = []
        self.handler: list[str] = []

    def _error_variable(self) -> str | None:
        """Name bound by ``on error <var>``, or ``None``.

        ``on error number -1728`` binds nothing: ``number``/``from``/``to``/
        ``partial`` are sub-clause keywords, and treating one as a variable name
        would let ``set errNum to 0`` masquerade as consuming the error.
        """
        match = _ON_ERROR_VAR.match(self.on_error_text)
        if match is None or match.group(1) in _ON_ERROR_KEYWORDS:
            return None
        return match.group(1)

    def classify(self) -> str | None:
        """``BARE``, ``SILENT``, or ``None`` when the handler observes the error."""
        if self.on_error_line is None:
            return BARE
        handler = [line for line in self.handler if line.strip()]
        if not handler:
            return BARE
        joined = "\n".join(handler)
        if SPLICED_ARM_MARKER in joined:
            # Arm supplied by a dangling-arm builder (the sanctioned P1 splice);
            # its body is scanned where the builder is defined.
            return None
        if ERROR_RAISE.search(joined):
            return None  # re-raise
        if any(marker in joined for marker in SANCTIONED_ERROR_MARKERS):
            return None  # P1/P2/P3 channel
        err_var = self._error_variable()
        if err_var and re.search(rf"\b{re.escape(err_var)}\b", joined):
            return None  # arm consumes the bound error variable
        if _ERROR_SIGNAL_WORDS.search(joined):
            return None  # counter / flag / message naming the failure
        return SILENT


def analyze_try_blocks(lines: list[str]) -> tuple[list[TryBlock], int, int]:
    """Return ``(closed_blocks, unclosed_try_count, unmatched_end_try_count)``.

    ``try`` blocks nest, so the handler arm has to be attributed to the *correct*
    ``try``: a flat "does this script contain ``on error`` anywhere" check is
    defeated by one good handler plus five bare ones, which is the common real
    shape here. A closed inner block is recorded in its parent's current arm as
    ``NESTED_BLOCK_MARKER`` so a parent whose entire handler is a nested
    fallback ``try`` (the ``Sent Messages`` -> ``Sent`` -> ``Sent Items`` chain in
    ``core/replied.py``) is not misread as an empty arm.
    """
    stack: list[TryBlock] = []
    closed: list[TryBlock] = []
    unmatched_end = 0

    for index, line in enumerate(lines):
        if TRY_OPEN.match(line):
            stack.append(TryBlock(index))
            continue
        if ON_ERROR.match(line):
            if stack and stack[-1].on_error_line is None:
                stack[-1].on_error_line = index
                stack[-1].on_error_text = line.strip()
            continue
        if TRY_END.match(line):
            if not stack:
                unmatched_end += 1
                continue
            block = stack.pop()
            block.end = index
            closed.append(block)
            if stack:
                _current_arm(stack[-1]).append(NESTED_BLOCK_MARKER)
            continue
        if stack:
            _current_arm(stack[-1]).append(line)

    return closed, len(stack), unmatched_end


def _current_arm(block: TryBlock) -> list[str]:
    return block.handler if block.on_error_line is not None else block.body


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def iter_package_files() -> Iterable[Path]:
    """Every module in the package, recursively.

    This MUST stay ``rglob``. ``tools/`` is a tree of packages
    (``compose/``, ``search/``, ``manage/``, ``inbox/``, ``analytics/``,
    ``calendar/``, ``smart_inbox/``) and so are ``core/`` and ``calendar_core/``;
    a non-recursive ``glob("*.py")`` matches a handful of flat leaves and every
    assertion below passes against a file set holding almost no AppleScript. A
    green run over the wrong file set is indistinguishable from a green run over
    the right one, which is what ``LintCoverageTests`` exists to prevent.
    """
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _rel(path: Path) -> str:
    """Repo-relative POSIX key, e.g. ``plugin/apple_mail_mcp/tools/manage/trash.py``.

    Deliberately not ``path.name``: ``helpers.py`` exists under ``calendar/``,
    ``compose/``, ``manage/``, and ``smart_inbox/``, and ``attachments.py`` under
    ``analytics/`` and ``manage/``, so basename keys would let one baseline entry
    exempt a different package's file and would leave the failure message unable
    to say which file to fix. Repo-relative also matches the sibling fixture
    ``tests/fixtures/module_line_budget/baseline.json`` and stays safe to publish
    (no absolute user paths).
    """
    return path.relative_to(ROOT).as_posix()


def scan() -> tuple[dict[str, dict[str, int]], list[str], int, int]:
    """Scan the package; return ``(counts, details, unclosed_try, unmatched_end)``."""
    counts: dict[str, dict[str, int]] = {}
    details: list[str] = []
    unclosed_total = 0
    unmatched_total = 0

    for path in iter_package_files():
        lines = script_lines(path)
        if not lines:
            continue
        blocks, unclosed, unmatched = analyze_try_blocks(lines)
        unclosed_total += unclosed
        unmatched_total += unmatched
        rel = _rel(path)
        for block in blocks:
            verdict = block.classify()
            if verdict is None:
                continue
            rules = counts.setdefault(rel, {})
            rules[verdict] = rules.get(verdict, 0) + 1
            details.append(f"{rel} [{verdict}] script-line {block.start}")

    return counts, details, unclosed_total, unmatched_total


def load_baseline() -> dict[str, dict[str, int]]:
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    modules = payload["modules"]
    return {str(k): {str(rule): int(n) for rule, n in v.items()} for k, v in modules.items()}


def write_baseline() -> None:
    counts, _, _, _ = scan()
    payload = {
        "_comment": (
            "Ratchet baseline for tests/core/test_no_bare_applescript_try.py. Counts only "
            "ever go down: lowering or removing an entry is always valid, raising one is not. "
            "Keys are repo-relative module paths."
        ),
        "_regenerate": REGEN_CMD,
        "rules": {
            BARE: (
                "AppleScript `try` with no `on error` arm, or with an empty `on error` arm. "
                "The thrown error is provably unobservable."
            ),
            SILENT: (
                "`on error` arm present and non-empty but with no observable error signal "
                "(no re-raise, no bound error variable use, no sanctioned marker, no "
                "error/fail/skip/partial token). Heuristic tier: a sentinel-setting probe "
                "arm can be legitimate, so existing cases are recorded here rather than "
                "hard-failed."
            ),
        },
        "modules": {rel: dict(sorted(rules.items())) for rel, rules in sorted(counts.items())},
    }
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    total = sum(n for rules in counts.values() for n in rules.values())
    print(f"Wrote {BASELINE_PATH.relative_to(ROOT)}: {len(counts)} module(s), {total} site(s).")


# ---------------------------------------------------------------------------
# Detector meta-tests — prove the lint cannot go vacuous
# ---------------------------------------------------------------------------


def _blocks(script: str) -> list[TryBlock]:
    lines = strip_applescript_comments(script)
    blocks, _, _ = analyze_try_blocks(lines)
    return blocks


def _verdicts(script: str) -> list[str | None]:
    return [block.classify() for block in _blocks(script)]


class DetectorTests(unittest.TestCase):
    """Hand-built AppleScript fed straight to the detector.

    Every assertion below is two-sided on purpose. A detector that returns
    ``BARE`` for everything and a detector that returns nothing both make the
    ratchet meaningless, and only paired positive/negative cases tell them apart.
    """

    def test_bare_try_is_flagged(self):
        self.assertEqual(_verdicts("try\n    set x to subject of m\nend try"), [BARE])

    def test_reporting_handler_is_clean(self):
        script = 'try\n    set x to subject of m\non error errMsg\n    return "ERROR|||" & errMsg\nend try'
        self.assertEqual(_verdicts(script), [None])

    def test_reraise_handler_is_clean(self):
        script = 'try\n    set x to subject of m\non error errMsg\n    error "read failed: " & errMsg\nend try'
        self.assertEqual(_verdicts(script), [None])

    def test_empty_handler_arm_is_bare(self):
        """`on error` + nothing is the same silence as no arm at all."""
        self.assertEqual(_verdicts("try\n    set x to subject of m\non error\nend try"), [BARE])

    def test_nested_bare_inside_handled_is_flagged(self):
        script = (
            "try\n"
            "    try\n"
            "        set x to subject of m\n"
            "    end try\n"
            "on error errMsg\n"
            '    set end of out to "ERROR_MAILBOX|||" & errMsg\n'
            "end try"
        )
        # Inner closes first, so ordering is inner-then-outer.
        self.assertEqual(_verdicts(script), [BARE, None])

    def test_nested_handled_inside_bare_is_flagged(self):
        script = (
            "try\n"
            "    try\n"
            "        set x to subject of m\n"
            "    on error errMsg\n"
            '        error "inner: " & errMsg\n'
            "    end try\n"
            "end try"
        )
        self.assertEqual(_verdicts(script), [None, BARE])

    def test_flat_on_error_check_would_be_defeated(self):
        """One good handler plus several bare ones is the common real shape."""
        script = (
            "try\n"
            '    set a to "x"\n'
            "end try\n"
            "try\n"
            '    set b to "x"\n'
            "end try\n"
            "try\n"
            '    set c to "x"\n'
            "on error errMsg\n"
            '    return "ERROR|||" & errMsg\n'
            "end try"
        )
        self.assertIn("on error", script)  # a substring check would pass this script
        self.assertEqual(_verdicts(script), [BARE, BARE, None])

    def test_try_inside_line_comment_is_not_a_block(self):
        self.assertEqual(_verdicts("-- try\n-- end try\nset x to 1"), [])

    def test_try_inside_block_comment_is_not_a_block(self):
        self.assertEqual(_verdicts("(* try\n   end try *)\nset x to 1"), [])

    def test_trailing_comment_does_not_hide_a_block(self):
        self.assertEqual(_verdicts("try -- best effort\n    set x to 1\nend try -- done"), [BARE])

    def test_error_raise_statement_is_not_a_handler(self):
        """`error "…"` is a raise. A try whose body raises is still bare."""
        script = 'try\n    error "No inbox mailbox found for this account"\nend try'
        self.assertEqual(_verdicts(script), [BARE])
        # And the raise on its own, with no enclosing try, is not a block at all.
        self.assertEqual(_verdicts('error "No inbox mailbox found for this account"'), [])

    def test_python_try_colon_is_not_applescript(self):
        self.assertEqual(_verdicts("try:\n    pass\n"), [])

    def test_sentinel_arm_is_silent_not_bare(self):
        """The older-OS property probe: legitimate, so it must not be a hard failure.

        Recorded in the `silent` baseline instead, where it is visible and cannot
        multiply, rather than exempted by a blanket carve-out.
        """
        script = "try\n    set v to background color of m\non error\n    set v to -1\nend try"
        self.assertEqual(_verdicts(script), [SILENT])

    def test_counter_arm_is_clean(self):
        script = "try\n    set s to subject of m\non error\n    set scanReadFailures to scanReadFailures + 1\nend try"
        self.assertEqual(_verdicts(script), [None])

    def test_on_error_number_clause_has_no_error_variable(self):
        """`on error number -1728` binds nothing; `number` must not count as a var."""
        script = "try\n    set s to subject of m\non error number errNum\n    set v to 0\nend try"
        self.assertEqual(_verdicts(script), [SILENT])

    def test_spliced_arm_is_clean_but_an_opaque_slot_is_not(self):
        """The two shapes differ only in what the f-string slot expands to.

        A slot filled by a dangling-arm builder really does supply the handler,
        so flagging it would fail people for adopting the sanctioned P1 splice.
        Every other slot stays opaque and cannot excuse a missing arm.
        """
        spliced = (
            "try\n    set s to subject of m\n"
            f"on error {SPLICED_ARM_MARKER} export_failure_arm\n    {SPLICED_ARM_MARKER} export_failure_arm\nend try"
        )
        self.assertEqual(_verdicts(spliced), [None])
        opaque = f"try\n    set s to subject of m\n    {FSTRING_PLACEHOLDER}\nend try"
        self.assertEqual(_verdicts(opaque), [BARE])

    def test_fstring_placeholder_does_not_break_nesting(self):
        script = f"try\n    {FSTRING_PLACEHOLDER}\n    set x to 1\nend try"
        self.assertEqual(_verdicts(script), [BARE])

    def test_unbalanced_blocks_are_reported_not_guessed(self):
        _, unclosed, unmatched = analyze_try_blocks(strip_applescript_comments("try\n    set x to 1"))
        self.assertEqual((unclosed, unmatched), (1, 0))
        _, unclosed, unmatched = analyze_try_blocks(strip_applescript_comments("end try"))
        self.assertEqual((unclosed, unmatched), (0, 1))


class ExtractionTests(unittest.TestCase):
    """Prove the AppleScript actually reaches the detector.

    A regex over raw Python source, or an ``ast.Constant``-only walk, both look
    exactly like a working extractor until you check what came out.
    """

    def test_extraction_finds_known_applescript_fragments(self):
        """Anchor on real, stable AppleScript from the three sanctioned producers."""
        cases = [
            ("plugin/apple_mail_mcp/core/reply_state.py", "ERROR|||"),
            ("plugin/apple_mail_mcp/tools/search/script.py", "ERROR_MAILBOX|||"),
            ("plugin/apple_mail_mcp/tools/analytics/statistics.py", "mboxErrorCount"),
        ]
        for rel, needle in cases:
            with self.subTest(module=rel):
                text = "\n".join(chunk for _, chunk in iter_string_literals(ROOT / rel))
                self.assertIn(
                    needle,
                    text,
                    f"Extraction returned no AppleScript containing {needle!r} from {rel}. "
                    "The extractor is not seeing the scripts, so every count below is fake.",
                )

    def test_extraction_reaches_inside_fstrings(self):
        """Those markers live in f-strings, not plain string constants."""
        for rel, needle in [
            ("plugin/apple_mail_mcp/core/reply_state.py", "ERROR|||"),
            ("plugin/apple_mail_mcp/tools/analytics/statistics.py", "mboxErrorCount"),
        ]:
            with self.subTest(module=rel):
                tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
                in_fstring = any(
                    isinstance(value, ast.Constant) and isinstance(value.value, str) and needle in value.value
                    for node in ast.walk(tree)
                    if isinstance(node, ast.JoinedStr)
                    for value in node.values
                )
                self.assertTrue(
                    in_fstring,
                    f"{needle!r} is no longer inside an f-string in {rel}; pick a new anchor "
                    "for the f-string coverage assertion.",
                )

    def test_naive_constant_walk_would_go_vacuous(self):
        """An ``ast.Constant``-only walk misses most AppleScript in this package.

        Most scripts here are f-strings. The AST models one as ``JoinedStr``
        holding ``Constant`` *segments*, so a plain ``Constant`` walk yields the
        script chopped at every ``{...}`` — ``try`` and ``end try`` land in
        different fragments and the nesting analysis finds far fewer blocks. This
        assertion fails the moment someone "simplifies" the extractor that way.
        """
        real = 0
        naive = 0
        for path in iter_package_files():
            real += len(analyze_try_blocks(script_lines(path))[0])
            naive += len(analyze_try_blocks(script_lines(path, include_fstrings=False))[0])
        self.assertGreater(real, 0, "No try blocks found at all — the extractor is broken.")
        self.assertGreater(
            real,
            naive * 2,
            f"An f-string-blind walk found {naive} block(s) vs {real} real — the gap collapsed, "
            "so either the package stopped using f-strings for AppleScript or the extractor "
            "silently lost its JoinedStr handling.",
        )

    def test_script_assembly_is_balanced(self):
        """Split-literal scripts must still pair up, or blocks drop out unseen.

        ``compose/forward.py`` and ``compose/send.py`` build one script from a
        head literal and a tail literal. Analyzed per literal they leave an
        unclosed ``try`` on one side and an orphan ``end try`` on the other, and
        both vanish from the scan. Concatenating in source order fixes that; if
        this count climbs, the scan is losing coverage silently.
        """
        _, _, unclosed, unmatched = scan()
        self.assertEqual(
            (unclosed, unmatched),
            (0, 0),
            f"{unclosed} unclosed `try` and {unmatched} orphan `end try` across the package. "
            "Those blocks are invisible to this lint. Keep a script's `try` and `end try` in "
            "the same Python string, or extend script_lines() to join the pieces.",
        )


class LintCoverageTests(unittest.TestCase):
    """Guard the scanner's *input set*, not just its verdict.

    Every ratchet test asserts "no new violations". None of them can tell
    "scanned everything, found nothing new" apart from "scanned nothing". When
    the tool surfaces became packages, the sibling ``whose`` lint's
    non-recursive ``glob`` narrowed to two flat leaves and passed vacuously
    through the exact bug it existed to catch.
    """

    def test_scan_is_recursive_over_every_package(self):
        scanned = {_rel(path) for path in iter_package_files()}
        packages = {
            _rel(path.parent) + "/"
            for path in PACKAGE_DIR.rglob("*.py")
            if "__pycache__" not in path.parts and path.parent != PACKAGE_DIR
        }
        missing = sorted(pkg for pkg in packages if not any(rel.startswith(pkg) for rel in scanned))
        self.assertFalse(
            missing,
            f"Not scanning these packages at all: {missing}. iter_package_files() must use "
            "rglob; a non-recursive glob makes every check in this module vacuous.",
        )
        self.assertGreater(len(scanned), 60, f"Only {len(scanned)} module(s) scanned; the file set regressed.")

    def test_scope_spans_core_and_tools(self):
        """`tools/`-only would miss the shared AppleScript emitters."""
        scanned = {_rel(path) for path in iter_package_files()}
        for rel in (
            "plugin/apple_mail_mcp/core/script_fragments.py",
            "plugin/apple_mail_mcp/core/reply_state.py",
            "plugin/apple_mail_mcp/bounded_scan.py",
            "plugin/apple_mail_mcp/applescript_snippets.py",
            "plugin/apple_mail_mcp/calendar_core/scripts_read.py",
            "plugin/apple_mail_mcp/tools/search/script.py",
        ):
            self.assertIn(rel, scanned, f"{rel} emits AppleScript and must be in scope.")

    def test_scan_finds_try_blocks_in_the_known_emitters(self):
        """A file set that reaches the emitters but extracts nothing is still vacuous."""
        for rel in (
            "plugin/apple_mail_mcp/core/script_fragments.py",
            "plugin/apple_mail_mcp/tools/search/script.py",
            "plugin/apple_mail_mcp/calendar_core/scripts_read.py",
        ):
            with self.subTest(module=rel):
                blocks, _, _ = analyze_try_blocks(script_lines(ROOT / rel))
                self.assertGreater(len(blocks), 0, f"No AppleScript try blocks extracted from {rel}.")

    def test_dangling_arm_builders_are_discovered(self):
        """The splice resolution must find the two real arm builders."""
        builders = dangling_arm_builders()
        self.assertEqual(
            builders.get("export_failure_arm"),
            "plugin/apple_mail_mcp/tools/analytics/export_failure_reporting.py",
        )
        self.assertEqual(
            builders.get("_SCAN_FAILURE_ARM"),
            "plugin/apple_mail_mcp/tools/search/script.py",
        )

    def test_dangling_arm_resolution_cannot_go_broad(self):
        """A wide builder set would whitewash real bare `try` blocks wholesale.

        Every name here silences the bare rule at each call site that splices it,
        so the set must stay tiny and deliberate. If this fails because a genuine
        new arm builder landed, add it knowingly; if it fails because the
        every-branch requirement in `find_dangling_arm_builders` was relaxed,
        that is the regression.
        """
        builders = dangling_arm_builders()
        self.assertLess(
            len(builders),
            8,
            f"{len(builders)} dangling-arm builders detected: {sorted(builders)}. The "
            "detection has gone broad and is now excusing bare `try` blocks.",
        )
        for name in ("recipient_addresses_block", "thread_headers_block"):
            self.assertNotIn(
                name,
                builders,
                f"{name} has a self-contained return branch as well as a dangling one, so "
                "crediting it would whitewash call sites that receive the self-contained one. "
                "find_dangling_arm_builders must require *every* returned script to dangle.",
            )

    def test_baseline_keys_are_repo_relative_and_real(self):
        baseline = load_baseline()
        self.assertTrue(baseline, "Baseline is empty; regenerate with: " + REGEN_CMD)
        for rel, rules in baseline.items():
            with self.subTest(module=rel):
                self.assertTrue(
                    rel.startswith("plugin/apple_mail_mcp/"),
                    f"Baseline key {rel!r} must be a repo-relative path, not a bare basename: "
                    "helpers.py and attachments.py each exist in several packages.",
                )
                self.assertTrue((ROOT / rel).is_file(), f"Baseline names a missing module: {rel}")
                self.assertTrue(set(rules) <= {BARE, SILENT}, f"Unknown rule key in {rel}: {sorted(rules)}")

    def test_remediation_message_names_the_sanctioned_channels(self):
        """The failure has to teach the fix, not just refuse the change."""
        for token in (
            "plugin/apple_mail_mcp/tools/search/script.py",
            "plugin/apple_mail_mcp/tools/search/records.py",
            "plugin/apple_mail_mcp/core/reply_state.py",
            "plugin/apple_mail_mcp/tools/reply_state_wiring.py",
            "plugin/apple_mail_mcp/tools/analytics/statistics.py",
            "plugin/apple_mail_mcp/tools/analytics/statistics_parsing.py",
            "ERROR_MAILBOX|||",
            "ERROR|||",
            "__APPLE_MAIL_MCP_ERROR__",
        ):
            self.assertIn(token, _REMEDIATION)
        for rel in (
            "plugin/apple_mail_mcp/tools/search/records.py",
            "plugin/apple_mail_mcp/tools/reply_state_wiring.py",
            "plugin/apple_mail_mcp/tools/analytics/statistics_parsing.py",
        ):
            self.assertTrue((ROOT / rel).is_file(), f"Remediation points at a missing file: {rel}")


# ---------------------------------------------------------------------------
# Ratchet
# ---------------------------------------------------------------------------


Counts = dict[str, dict[str, int]]


def regressions(counts: Counts, baseline: Counts, rule: str) -> list[str]:
    """Files exceeding their allowance. A file absent from *baseline* allows 0."""
    return [
        f"{rel}: {rules[rule]} site(s), baseline allows {baseline.get(rel, {}).get(rule, 0)}"
        for rel, rules in sorted(counts.items())
        if rules.get(rule, 0) > baseline.get(rel, {}).get(rule, 0)
    ]


def stale_entries(counts: Counts, baseline: Counts, rule: str) -> list[str]:
    """Baseline entries that are now too generous, i.e. sites that got fixed."""
    return [
        f"{rel}: baseline says {rules[rule]}, found {counts.get(rel, {}).get(rule, 0)}"
        for rel, rules in sorted(baseline.items())
        if rule in rules and counts.get(rel, {}).get(rule, 0) < rules[rule]
    ]


class RatchetArithmeticTests(unittest.TestCase):
    """The comparison itself, on synthetic dicts.

    The scans below all assert "nothing new was found", which stays green if the
    comparison is broken in the permissive direction. These cases pin the three
    behaviours the ratchet depends on.
    """

    BASE: Counts = {"plugin/apple_mail_mcp/tools/a.py": {BARE: 2}}

    def test_a_file_absent_from_the_baseline_fails_closed(self):
        """The important direction: a brand-new module gets an allowance of 0."""
        counts: Counts = {"plugin/apple_mail_mcp/tools/brand_new.py": {BARE: 1}}
        self.assertEqual(
            regressions(counts, self.BASE, BARE),
            ["plugin/apple_mail_mcp/tools/brand_new.py: 1 site(s), baseline allows 0"],
        )

    def test_at_baseline_is_clean_and_over_baseline_is_not(self):
        at_baseline: Counts = {"plugin/apple_mail_mcp/tools/a.py": {BARE: 2}}
        over: Counts = {"plugin/apple_mail_mcp/tools/a.py": {BARE: 3}}
        self.assertEqual(regressions(at_baseline, self.BASE, BARE), [])
        self.assertEqual(len(regressions(over, self.BASE, BARE)), 1)

    def test_under_baseline_is_stale_not_a_pass(self):
        """Ratchets that only check the upper bound rot upward, one fix at a time."""
        under: Counts = {"plugin/apple_mail_mcp/tools/a.py": {BARE: 1}}
        self.assertEqual(regressions(under, self.BASE, BARE), [])
        self.assertEqual(
            stale_entries(under, self.BASE, BARE),
            ["plugin/apple_mail_mcp/tools/a.py: baseline says 2, found 1"],
        )
        self.assertEqual(
            stale_entries({}, self.BASE, BARE), ["plugin/apple_mail_mcp/tools/a.py: baseline says 2, found 0"]
        )

    def test_rules_are_scored_independently(self):
        """A silent-handler entry must not fund a bare-`try` allowance."""
        base: Counts = {"plugin/apple_mail_mcp/tools/a.py": {SILENT: 5}}
        counts: Counts = {"plugin/apple_mail_mcp/tools/a.py": {BARE: 1, SILENT: 5}}
        self.assertEqual(len(regressions(counts, base, BARE)), 1)
        self.assertEqual(regressions(counts, base, SILENT), [])


class EndToEndDetectionTests(unittest.TestCase):
    """File on disk -> verdict, so the pipeline is proven past the string level."""

    def test_bare_try_in_an_fstring_module_is_detected(self):
        module = (
            "def build(mailbox: str) -> str:\n"
            '    """Docstring mentioning try and end try, which must be ignored."""\n'
            "    return f'''\n"
            'tell application "Mail"\n'
            "    try\n"
            "        set target to mailbox {mailbox}\n"
            "    end try\n"
            "end tell\n"
            "'''\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic_emitter.py"
            path.write_text(module, encoding="utf-8")
            blocks, unclosed, unmatched = analyze_try_blocks(script_lines(path))
            self.assertEqual((unclosed, unmatched), (0, 0))
            self.assertEqual([block.classify() for block in blocks], [BARE])

    def test_docstring_prose_alone_yields_no_blocks(self):
        module = '"""A module docstring that says try on its own line:\n\ntry\nend try\n"""\n'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prose_only.py"
            path.write_text(module, encoding="utf-8")
            self.assertEqual(script_lines(path), [])


class BareTryRatchetTests(unittest.TestCase):
    def setUp(self):
        self.counts, self.details, _, _ = scan()
        self.baseline = load_baseline()

    def _regressions(self, rule: str) -> list[str]:
        return regressions(self.counts, self.baseline, rule)

    def _stale(self, rule: str) -> list[str]:
        return stale_entries(self.counts, self.baseline, rule)

    def test_no_new_bare_try(self):
        regressions = self._regressions(BARE)
        self.assertFalse(
            regressions,
            "New bare AppleScript `try` block(s) — a `try` with no `on error` arm, or with an "
            "empty one.\n  - " + "\n  - ".join(regressions) + "\n\n" + _REMEDIATION,
        )

    def test_bare_baseline_is_not_stale(self):
        """A fixed site must leave the baseline, so the ratchet only tightens.

        Without this, every fix quietly raises the headroom for the next bare
        `try` in the same file and the lint goes soft one commit at a time.
        Lowering these numbers is the intended maintenance; regenerate with the
        command in the module docstring.
        """
        stale = self._stale(BARE)
        self.assertFalse(
            stale,
            "Bare-`try` baseline is stale — these files improved. Lower or remove the entries "
            f"so the ratchet cannot silently re-admit a fixed site ({REGEN_CMD}):\n  - " + "\n  - ".join(stale),
        )

    def test_no_new_silent_handler(self):
        regressions = self._regressions(SILENT)
        self.assertFalse(
            regressions,
            "New AppleScript `on error` arm(s) with no observable error signal: no re-raise, no "
            "use of the bound error variable, no sanctioned marker, no error/fail/skip/partial "
            "token. If this is a deliberate sentinel probe (e.g. `set v to -1` for a property "
            "missing on older macOS), it belongs in the baseline with a note, not in a blanket "
            "exemption.\n  - " + "\n  - ".join(regressions) + "\n\n" + _REMEDIATION,
        )

    def test_silent_baseline_is_not_stale(self):
        stale = self._stale(SILENT)
        self.assertFalse(
            stale,
            "Silent-handler baseline is stale — lower or remove these entries "
            f"({REGEN_CMD}):\n  - " + "\n  - ".join(stale),
        )

    def test_baseline_has_no_entries_for_clean_files(self):
        orphans = sorted(rel for rel in self.baseline if rel not in self.counts)
        self.assertFalse(
            orphans,
            "Baseline lists files with zero remaining sites. Remove them "
            f"({REGEN_CMD}):\n  - " + "\n  - ".join(orphans),
        )


if __name__ == "__main__":
    if "--write-baseline" in sys.argv:
        write_baseline()
    else:
        unittest.main()
