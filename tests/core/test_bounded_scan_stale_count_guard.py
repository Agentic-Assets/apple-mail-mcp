"""``build_bounded_message_scan`` must not trust ``count of messages``.

PR #90 replaced the unbounded ``messages of MB`` fallback with
``messages 1 thru _mbCount of MB`` and left the count unguarded. That trade was
right about the hang: on a 24K-message Exchange mailbox the unbounded spelling
presents as a hang rather than an error, and a hang is worse than failing. It
was wrong about one arm. ``count of messages`` is a *cached* property, and this
package has already shipped a tool reporting a cached 3,236 against a true
10,016 (``tools/unread_provenance.py``), so:

* a count reading **low** made the slice silently under-scan, and
* a count reading **zero** on a non-empty mailbox bound ``{}`` — a confident
  empty list where the old code enumerated the real contents.

Both sibling call sites already keep a per-slice guard for exactly this reason.
``tools/search/script.py`` wraps the slice in a nested ``try`` and emits an
``ERROR_MAILBOX`` marker when the recovery slice also fails;
``tools/compose/drafts_scripts.py`` says it outright: "``count of messages`` can
still read stale-high, so the slice keeps its own guard."

Two footguns constrain any fix, both recorded from live runs:

1. ``messages 1 thru 0 of MB`` does **not** return an empty list. It silently
   returns the **first** message, verified across all four backends. Clamping
   the slice bound is therefore not sufficient on its own, and a naive
   ``max(1, n)`` clamp trades a false empty for a spurious single row.
2. An out-of-range upper bound raises **-1719**, not -1728, so
   ``messages 1 thru N`` is a usable probe for "does the mailbox hold N?" but
   only when N is known to be ≥ 1.

What this module locks
----------------------
The tests below run the *emitted AppleScript* through a deliberately strict
model of Mail's slice and index semantics (``FakeMailbox`` + ``run_snippet``),
so they assert behaviour rather than substrings: reordering the arms, dropping
the probe, or emitting a zero-bound slice all fail here. The interpreter
refuses any statement shape it does not model, so drift in the emitted script
surfaces as a loud test error instead of a vacuous pass.

Proven vs defended
------------------
The stale-count *reactions* are proven against this model, not against a live
mailbox in a stale state — reproducing a stale Envelope Index on demand needs a
real account in that condition. What the model encodes is documented,
live-observed Mail behaviour (the ``1 thru 0`` result, the -1719 raise, the
"Can't get message 1." throw text that already appears in
``tests/search/test_search_bounded_candidate_binding.py``); what it cannot prove
is that Mail's cached count actually goes stale-low on a *message* count, which
remains inferred from the shipped unread-count defect.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from apple_mail_mcp.bounded_scan import build_bounded_message_scan

MAILBOX_VAR = "inboxMailbox"

_OSACOMPILE_AVAILABLE = shutil.which("osacompile") is not None


# ---------------------------------------------------------------------------
# A model of the Mail.app behaviour the builder has to survive
# ---------------------------------------------------------------------------


class AppleScriptError(Exception):
    """Stands in for a Mail.app AppleScript throw."""


class FakeMailbox:
    """Mail's slice/index semantics, footguns included.

    *true_count* is what the mailbox really holds; *reported_count* is what the
    cached ``count of messages`` property claims. Keeping them separate is the
    whole point: every defect in this module is a disagreement between the two.
    """

    def __init__(self, *, true_count: int, reported_count: int | None = None, name: str = "INBOX") -> None:
        self.name = name
        self.true_count = true_count
        self.reported_count = true_count if reported_count is None else reported_count
        self.count_reads = 0
        self.slice_requests: list[tuple[int, int]] = []
        self.index_probes: list[int] = []

    def count_of_messages(self) -> int:
        self.count_reads += 1
        return self.reported_count

    def messages_slice(self, low: int, high: int) -> list[str]:
        self.slice_requests.append((low, high))
        if self.true_count == 0:
            raise AppleScriptError(f"Can't get messages {low} thru {high} of mailbox.")
        if high == 0:
            # THE footgun: `messages 1 thru 0` returns the FIRST message.
            return ["message-1"]
        if high > self.true_count or low < 1:
            raise AppleScriptError("Invalid index. (-1719)")
        return [f"message-{index}" for index in range(low, high + 1)]

    def message_at(self, index: int) -> str:
        self.index_probes.append(index)
        if index < 1 or index > self.true_count:
            raise AppleScriptError(f"Can't get message {index}.")
        return f"id-{index}"


# ---------------------------------------------------------------------------
# A strict interpreter for the subset of AppleScript this builder emits
# ---------------------------------------------------------------------------

_SET_EMPTY = re.compile(r"^set (\w+) to \{\}$")
_SET_BOOL = re.compile(r"^set (\w+) to (true|false)$")
_SET_SLICE = re.compile(r"^set (\w+) to messages (\d+) thru (.+) of (\w+)$")
_SET_COUNT = re.compile(r"^set (\w+) to count of messages of (\w+)$")
_SET_PROBE = re.compile(r"^set (\w+) to id of message \((.+)\) of (\w+)$")
_IF_OPEN = re.compile(r"^if (.+) then$")
_ON_ERROR = re.compile(r"^on error(?:\s+(\w+))?$")
_RAISE = re.compile(r"^error (.+)$")

_COMPARISON = re.compile(r"^(.+?)\s*(≥|>=|>|<)\s*(.+)$")


def _significant_lines(snippet: str) -> list[str]:
    return [
        stripped
        for stripped in (line.strip() for line in snippet.splitlines())
        if stripped and not stripped.startswith("--")
    ]


def _terminates(line: str, tokens: tuple[str, ...]) -> bool:
    return any(line == token or line.startswith(token + " ") for token in tokens)


def _parse(lines: list[str], index: int = 0, end_tokens: tuple[str, ...] = ()) -> tuple[list[tuple], int]:
    """Parse into nested ``try``/``if``/statement nodes."""
    nodes: list[tuple] = []
    while index < len(lines):
        line = lines[index]
        if _terminates(line, end_tokens):
            return nodes, index
        if line == "try":
            body, index = _parse(lines, index + 1, ("on error", "end try"))
            handler: list[tuple] = []
            error_var = None
            if index < len(lines) and lines[index].startswith("on error"):
                match = _ON_ERROR.match(lines[index])
                assert match is not None, f"Unmodelled `on error` form: {lines[index]}"
                error_var = match.group(1)
                handler, index = _parse(lines, index + 1, ("end try",))
            assert index < len(lines) and lines[index] == "end try", "Unterminated `try` in emitted script."
            nodes.append(("try", body, error_var, handler))
            index += 1
            continue
        # `else` / `else if` are deliberately unmodelled: the count-picked
        # `else if _mbCount > 0 ... else` chain is the shape this module exists
        # to keep out, so reintroducing it must fail loudly here rather than
        # skip a branch the interpreter cannot evaluate.
        assert not line.startswith("else"), f"Unmodelled `else` branch in emitted script: {line!r}"
        if_match = _IF_OPEN.match(line)
        if if_match:
            body, index = _parse(lines, index + 1, ("end if",))
            assert index < len(lines) and lines[index] == "end if", "Unterminated `if` in emitted script."
            nodes.append(("if", if_match.group(1), body))
            index += 1
            continue
        nodes.append(("statement", line))
        index += 1
    return nodes, index


def _eval_int(expr: str, env: dict) -> int:
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        expr = expr[1:-1].strip()
    if "+" in expr:
        left, _, right = expr.partition("+")
        return _eval_int(left, env) + _eval_int(right, env)
    if re.fullmatch(r"-?\d+", expr):
        return int(expr)
    assert expr in env, f"Unmodelled numeric expression: {expr!r}"
    value = env[expr]
    assert isinstance(value, int), f"{expr!r} is not numeric: {value!r}"
    return value


def _split_concatenation(expr: str) -> list[str]:
    """Split an AppleScript ``&`` concatenation, respecting string literals."""
    parts: list[str] = []
    current: list[str] = []
    in_string = False
    for char in expr:
        if char == '"':
            in_string = not in_string
        if char == "&" and not in_string:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _eval_string(expr: str, env: dict, mailbox: FakeMailbox) -> str:
    rendered: list[str] = []
    for term in _split_concatenation(expr):
        if term.startswith('"') and term.endswith('"'):
            rendered.append(term[1:-1])
            continue
        name_match = re.fullmatch(r"\(name of (\w+)\)", term)
        if name_match:
            assert name_match.group(1) == MAILBOX_VAR, f"Unexpected mailbox var: {name_match.group(1)}"
            rendered.append(mailbox.name)
            continue
        cast_match = re.fullmatch(r"\((.+) as string\)", term)
        if cast_match:
            rendered.append(str(_eval_int(cast_match.group(1), env)))
            continue
        if re.fullmatch(r"\w+", term):
            rendered.append(str(env[term]))
            continue
        raise AssertionError(f"Unmodelled string term: {term!r}")
    return "".join(rendered)


def _eval_condition(condition: str, env: dict) -> bool:
    condition = condition.strip()
    negation = re.fullmatch(r"not (\w+)", condition)
    if negation:
        return not env[negation.group(1)]
    comparison = _COMPARISON.match(condition)
    assert comparison is not None, f"Unmodelled condition: {condition!r}"
    left, operator, right = comparison.groups()
    lhs, rhs = _eval_int(left, env), _eval_int(right, env)
    if operator in ("≥", ">="):
        return lhs >= rhs
    if operator == ">":
        return lhs > rhs
    return lhs < rhs


def _run_statement(line: str, env: dict, mailbox: FakeMailbox) -> None:
    empty = _SET_EMPTY.match(line)
    if empty:
        env[empty.group(1)] = []
        return
    boolean = _SET_BOOL.match(line)
    if boolean:
        env[boolean.group(1)] = boolean.group(2) == "true"
        return
    count = _SET_COUNT.match(line)
    if count:
        assert count.group(2) == MAILBOX_VAR
        env[count.group(1)] = mailbox.count_of_messages()
        return
    probe = _SET_PROBE.match(line)
    if probe:
        assert probe.group(3) == MAILBOX_VAR
        env[probe.group(1)] = mailbox.message_at(_eval_int(probe.group(2), env))
        return
    sliced = _SET_SLICE.match(line)
    if sliced:
        assert sliced.group(4) == MAILBOX_VAR
        env[sliced.group(1)] = mailbox.messages_slice(int(sliced.group(2)), _eval_int(sliced.group(3), env))
        return
    raise_match = _RAISE.match(line)
    if raise_match:
        raise AppleScriptError(_eval_string(raise_match.group(1), env, mailbox))
    raise AssertionError(f"Unmodelled AppleScript statement: {line!r}")


def _run(nodes: list[tuple], env: dict, mailbox: FakeMailbox) -> None:
    for node in nodes:
        if node[0] == "try":
            _, body, error_var, handler = node
            try:
                _run(body, env, mailbox)
            except AppleScriptError as exc:
                if error_var:
                    env[error_var] = str(exc)
                _run(handler, env, mailbox)
        elif node[0] == "if":
            _, condition, body = node
            if _eval_condition(condition, env):
                _run(body, env, mailbox)
        else:
            _run_statement(node[1], env, mailbox)


def run_snippet(limit: int, mailbox: FakeMailbox) -> tuple[list[str] | None, str | None]:
    """Execute the emitted snippet, returning ``(candidateMessages, raised)``.

    ``raised`` is the text of an AppleScript error that escaped the snippet —
    i.e. what ``run_applescript`` would surface to the caller as a nonzero
    ``osascript`` exit.
    """
    snippet = build_bounded_message_scan(MAILBOX_VAR, limit)
    nodes, _ = _parse(_significant_lines(snippet))
    env: dict = {}
    try:
        _run(nodes, env, mailbox)
    except AppleScriptError as exc:
        return env.get("candidateMessages"), str(exc)
    return env.get("candidateMessages"), None


# ---------------------------------------------------------------------------
# The failure modes
# ---------------------------------------------------------------------------


class StaleCountGuardTests(unittest.TestCase):
    def test_genuinely_empty_mailbox_binds_zero_rows_and_stays_silent(self):
        """A true empty result is not a failure — and must not be one row."""
        mailbox = FakeMailbox(true_count=0, reported_count=0)
        candidates, raised = run_snippet(50, mailbox)

        self.assertIsNone(raised, "An empty mailbox must not raise; a true empty result is not a failure.")
        self.assertEqual(candidates, [], "An empty mailbox must bind exactly zero rows.")
        self.assertNotIn(
            0,
            [high for _, high in mailbox.slice_requests],
            "`messages 1 thru 0` returns the FIRST message, so a zero upper bound is never safe.",
        )
        self.assertEqual(
            mailbox.index_probes,
            [1],
            "Emptiness must be established by probing `message 1`, not by trusting the count.",
        )

    def test_count_reads_zero_on_a_non_empty_mailbox_raises_instead_of_binding_empty(self):
        """The defect: a zero count used to bind ``{}`` on a mailbox with mail."""
        mailbox = FakeMailbox(true_count=3, reported_count=0)
        candidates, raised = run_snippet(50, mailbox)

        self.assertIsNotNone(raised, "A count of 0 contradicted by `message 1` must not render as an empty inbox.")
        assert raised is not None
        self.assertIn("ERROR_MAILBOX|||", raised)
        self.assertIn(mailbox.name, raised)
        self.assertIn("under-reports", raised)
        self.assertEqual(candidates, [], "Nothing was scanned, so no partial window may be handed back as complete.")

    def test_count_reads_low_on_a_short_mailbox_raises_instead_of_under_scanning(self):
        mailbox = FakeMailbox(true_count=3, reported_count=2)
        _, raised = run_snippet(50, mailbox)

        self.assertIsNotNone(raised)
        assert raised is not None
        self.assertIn("ERROR_MAILBOX|||", raised)
        self.assertIn("under-reports", raised)
        self.assertEqual(
            mailbox.index_probes,
            [3],
            "The probe must look one past what the count admits, so an off-by-one stale read is caught.",
        )

    def test_count_reads_low_on_a_large_mailbox_still_scans_the_full_window(self):
        """The reordering fix: the full window is tried before the count is read.

        A cached count of 5 against a true 10,016 is the shipped
        ``unread count`` defect's shape. Reading the count first bound five
        messages and reported them as the whole window.
        """
        mailbox = FakeMailbox(true_count=10_016, reported_count=5)
        candidates, raised = run_snippet(100, mailbox)

        self.assertIsNone(raised)
        assert candidates is not None
        self.assertEqual(len(candidates), 100, "A stale-low count must not truncate a window the mailbox can serve.")
        self.assertEqual(mailbox.count_reads, 0, "The count is a recovery hint; the happy path must not read it.")

    def test_count_reads_stale_high_surfaces_a_loud_error_not_a_silent_empty(self):
        mailbox = FakeMailbox(true_count=3, reported_count=500)
        candidates, raised = run_snippet(100, mailbox)

        self.assertIsNotNone(raised, "A stale-high count must stay a loud caller-level error.")
        assert raised is not None
        self.assertIn("ERROR_MAILBOX|||", raised)
        self.assertIn("disagree", raised)
        self.assertEqual(candidates, [])
        self.assertEqual(
            mailbox.slice_requests,
            [(1, 100)],
            "The identical failing slice must not be re-issued once the count is known to disagree.",
        )

    def test_count_stale_high_below_the_limit_still_propagates_the_slice_throw(self):
        """A count that lies within the window still fails loudly, never silently."""
        mailbox = FakeMailbox(true_count=3, reported_count=500)
        candidates, raised = run_snippet(1000, mailbox)

        self.assertIsNotNone(raised, "An unbindable recovery slice must reach the caller, not bind {}.")
        assert raised is not None
        self.assertIn("Invalid index", raised)
        self.assertEqual(candidates, [])

    def test_honest_short_mailbox_binds_its_real_contents(self):
        mailbox = FakeMailbox(true_count=3, reported_count=3)
        candidates, raised = run_snippet(50, mailbox)

        self.assertIsNone(raised)
        self.assertEqual(candidates, ["message-1", "message-2", "message-3"])
        self.assertEqual(mailbox.index_probes, [4], "The probe corroborates the count before the short bind.")

    def test_honest_large_mailbox_takes_the_single_slice_fast_path(self):
        mailbox = FakeMailbox(true_count=24_000, reported_count=24_000)
        candidates, raised = run_snippet(500, mailbox)

        self.assertIsNone(raised)
        assert candidates is not None
        self.assertEqual(len(candidates), 500)
        self.assertEqual(mailbox.slice_requests, [(1, 500)])
        self.assertEqual(mailbox.count_reads, 0)
        self.assertEqual(mailbox.index_probes, [], "The 24K path must stay one bounded slice and nothing else.")

    def test_no_emitted_slice_can_have_a_zero_upper_bound(self):
        """``messages 1 thru 0`` silently returns the first message."""
        for limit in (1, 2, 50, 1000):
            snippet = build_bounded_message_scan(MAILBOX_VAR, limit)
            self.assertNotIn("thru 0 of", snippet, f"limit={limit} emitted a zero-bound slice")
        for true_count, reported_count in ((0, 0), (3, 0), (3, 2), (0, 4)):
            mailbox = FakeMailbox(true_count=true_count, reported_count=reported_count)
            run_snippet(50, mailbox)
            self.assertNotIn(
                0,
                [high for _, high in mailbox.slice_requests],
                f"zero-bound slice requested for true={true_count} reported={reported_count}",
            )


class EmittedShapeTests(unittest.TestCase):
    """Static guards on the guard itself, mirroring the sibling call sites."""

    def test_guard_copies_the_sibling_error_marker(self):
        snippet = build_bounded_message_scan(MAILBOX_VAR, 50)
        self.assertEqual(
            snippet.count("ERROR_MAILBOX|||"),
            2,
            "Both contradiction arms (stale-high, stale-low) must carry the sibling marker.",
        )

    def test_guard_never_falls_back_to_the_unbounded_spelling(self):
        snippet = build_bounded_message_scan(MAILBOX_VAR, 50)
        self.assertNotIn(f"messages of {MAILBOX_VAR}", snippet.replace(f"count of messages of {MAILBOX_VAR}", ""))
        self.assertNotIn("every message of", snippet)

    def test_recovery_arm_is_nested_inside_the_slice_try(self):
        lines = _significant_lines(build_bounded_message_scan(MAILBOX_VAR, 50))
        nodes, _ = _parse(lines)
        try_nodes = [node for node in nodes if node[0] == "try"]
        self.assertEqual(len(try_nodes), 1, "The snippet must be one outer `try` around the bounded slice.")
        _, body, error_var, handler = try_nodes[0]
        self.assertEqual(len(body), 1, "Only the bounded slice belongs in the protected body.")
        self.assertEqual(body[0], ("statement", f"set candidateMessages to messages 1 thru 50 of {MAILBOX_VAR}"))
        self.assertIsNotNone(error_var, "The recovery arm must bind the slice error so it can be reported.")
        self.assertTrue(
            any(node[0] == "try" for node in handler),
            "The probe must be a nested `try`, matching tools/search/script.py.",
        )


@unittest.skipUnless(_OSACOMPILE_AVAILABLE, "osacompile not available on this host")
class OsacompileTests(unittest.TestCase):
    def _compile(self, body: str) -> tuple[bool, str]:
        script = f'tell application "Mail"\n    set {MAILBOX_VAR} to mailbox "INBOX" of account "X"\n{body}\nend tell\n'
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "snippet.applescript"
            source.write_text(script, encoding="utf-8")
            result = subprocess.run(
                ["osacompile", "-o", str(Path(tmp) / "snippet.scpt"), str(source)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        return result.returncode == 0, result.stderr.strip()

    def test_emitted_snippet_parses(self):
        for limit in (1, 50, 1000):
            ok, stderr = self._compile(build_bounded_message_scan(MAILBOX_VAR, limit))
            self.assertTrue(ok, f"limit={limit} failed to compile: {stderr}")


if __name__ == "__main__":
    unittest.main()
