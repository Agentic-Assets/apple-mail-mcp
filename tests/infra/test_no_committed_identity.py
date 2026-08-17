"""Gate committed personal/machine identity in this public repo.

Root ``AGENTS.md`` § "This repo is PUBLIC" used to describe a manual
``git diff --cached | grep`` before every commit. It was prose, so it got
skipped exactly when someone was moving fast — which is exactly when a live-test
address or an absolute home path lands in a doc or a fixture.
``tools/validators/validate_no_committed_identity.py`` makes it a real gate; this
module is its regression suite.

Three things are tested, and the third is the one that actually keeps the gate
honest:

1. **Ratchet** — no file may carry more identity hits than
   ``KNOWN_IDENTITY_HITS`` grandfathers, and an unlisted file fails on its first
   hit (``.get(rel, 0)`` defaults to zero).
2. **Anti-staleness** — a baseline entry that over-claims must be lowered, so
   the ratchet can only tighten and cannot leave slack for the next leak to hide
   inside.
3. **Coverage** — assertions on the scanner's *input set*, not just its verdict.
   See ``IdentityScanCoverageTests``.

Bootstrap note for anyone editing this file: it is scanned by the gate it tests,
so every deliberate offender below is assembled at runtime from fragments, or
written into a ``tempfile`` sandbox. Do not "fix" a self-hit by adding this file
to ``KNOWN_IDENTITY_HITS`` or to a skip list. A gate that exempts its own tests
can be disarmed by editing a test.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.validators.validate_no_committed_identity import (  # noqa: E402
    ALLOWED_EMAIL_DOMAINS,
    KNOWN_IDENTITY_HITS,
    PLACEHOLDER_USER_SEGMENTS,
    SKIP_PREFIXES,
    identity_hits_in_line,
    iter_tracked_text_files,
    ratchet_regressions,
    scan_identity,
    stale_baseline_entries,
    validate_no_committed_identity,
)

VALIDATOR = ROOT / "tools" / "validators" / "validate_no_committed_identity.py"

# Extensions that are unambiguously text. Used to derive an expected file set
# from the tree independently of the validator's own binary sniff, so a bug in
# that sniff shows up as missing coverage rather than as a quiet pass.
TEXT_SUFFIXES = frozenset(
    {".applescript", ".cfg", ".ini", ".json", ".md", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml"}
)


# ---------------------------------------------------------------------------
# Runtime offender construction (see the bootstrap note in the module docstring)
# ---------------------------------------------------------------------------


def _address(local: str, domain: str) -> str:
    """Assemble an email address without writing one into this file.

    The ``@`` is only ever adjacent to ``}`` and ``{`` in the source, and the
    validator's local-part class excludes both, so this line cannot match its
    own pattern.
    """
    return f"{local}@{domain}"


def _home_path(segment: str, *rest: str) -> str:
    """Assemble an absolute home-directory path from segments."""
    return "/".join(("", "Users", segment, *rest))


def _uppercase_uuid() -> str:
    """An obviously-fake uppercase UUID, assembled from repeated characters."""
    return "-".join(("A" * 8, "1" * 4, "A" * 4, "1" * 4, "A" * 12))


class IdentityRatchetTests(unittest.TestCase):
    """The ratchet itself: nothing new, and no slack left behind."""

    def test_no_new_identity_hits(self) -> None:
        regressions = ratchet_regressions(scan_identity())
        self.assertFalse(
            regressions,
            "New committed identity. This repo is PUBLIC and a force-push does not "
            "unpublish a commit — redact before committing, do not raise the baseline. "
            "Use a synthetic address, an elided path, or a placeholder UUID; see "
            "AGENTS.md § This repo is PUBLIC.\n  - " + "\n  - ".join(regressions),
        )

    def test_identity_baseline_is_not_stale(self) -> None:
        """A redacted hit must leave the baseline, so the ratchet only tightens."""
        stale = stale_baseline_entries(scan_identity())
        self.assertFalse(
            stale,
            "KNOWN_IDENTITY_HITS is stale — lower or remove these entries. Leftover "
            "slack is exactly where the next leak hides, because the count never "
            "exceeds the baseline:\n  - " + "\n  - ".join(stale),
        )

    def test_validator_reports_clean_tree(self) -> None:
        errors = validate_no_committed_identity()
        self.assertEqual(errors, [], "\n".join(errors))


class IdentityScanCoverageTests(unittest.TestCase):
    """Guard the scanner's *input set*, not just its verdict.

    Every other test here asserts that no violation was found, and none of them
    can tell "scanned the whole tree and found nothing" apart from "scanned
    almost nothing". This repo has already shipped that exact failure: when the
    tool surfaces became packages, the bounded-scan lint's non-recursive glob
    silently narrowed from 79 modules to 2, and all four of its checks passed
    vacuously for weeks — until the bug class they existed to catch shipped
    anyway. A green run over the wrong file set looks identical to a green run
    over the right one, so these assertions are the only thing standing between
    the enumeration and going quiet.

    ``scanned`` and ``tracked`` are computed once for the class: every test below
    needs the same two sets, and ``iter_tracked_text_files()`` reads the contents
    of every tracked text file in the repo to produce its half.
    """

    scanned: set[str]
    tracked: list[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.scanned = {rel for rel, _ in iter_tracked_text_files()}
        # Derived from the tree, not a hardcoded list: a file added tomorrow is
        # covered the moment it exists, not when someone remembers to list it.
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        cls.tracked = [
            rel
            for rel in result.stdout.decode("utf-8", errors="replace").split("\0")
            if rel and not rel.startswith(SKIP_PREFIXES)
        ]

    def test_scan_covers_every_tracked_text_file(self) -> None:
        expected = {rel for rel in self.tracked if Path(rel).suffix in TEXT_SUFFIXES}
        missing = sorted(expected - self.scanned)
        self.assertFalse(
            missing,
            f"The identity gate is not scanning {len(missing)} tracked text file(s) at all, "
            "so any hit in them passes silently. Check the enumeration in "
            "iter_tracked_text_files():\n  - " + "\n  - ".join(missing[:20]),
        )

    def test_scan_covers_every_top_level_tree(self) -> None:
        def top(rel: str) -> str:
            return rel.split("/", 1)[0] if "/" in rel else "<root>"

        expected_trees = {top(rel) for rel in self.tracked}
        scanned_trees = {top(rel) for rel in self.scanned}
        missing = sorted(expected_trees - scanned_trees)
        self.assertFalse(
            missing,
            f"Entire top-level tree(s) are unscanned: {missing}. A prefix skip or a "
            "narrowed enumeration has made the gate blind to them.",
        )

    def test_scan_includes_spot_anchors(self) -> None:
        scanned = self.scanned

        # The rulebook must be inside the rules. AGENTS.md documents a bare
        # `/Users/` grep pattern; the gate stays clean on it because rule 2
        # requires a following path segment, NOT because AGENTS.md is exempt.
        self.assertIn("AGENTS.md", scanned)
        self.assertIn("CLAUDE.md", scanned)

        # Packaged skill docs ship to users and are full of example addresses and
        # paths, so they are a prime leak site.
        self.assertIn("plugin/skills/email-attachments/SKILL.md", scanned)
        self.assertTrue(
            any(rel.startswith("plugin/skills/") for rel in scanned),
            "No plugin/skills/ file scanned; packaged skill docs are a prime leak site.",
        )

        # Live-verification instructions and dated task handoffs are where
        # real mailbox output gets written down.
        self.assertIn("docs/AGENT_LIVE_TESTING.md", scanned)
        self.assertIn("tests/infra/test_wrapper_surface.py", scanned)

        # `.agents/skills/` holds this repo's OWN agent skills, and it was a skip
        # prefix in the first version of this gate. These skills get hand-edited
        # while someone is looking at live mailbox output, so they are authored
        # content, not vendored, and they must be in scope.
        self.assertNotIn(".agents/", SKIP_PREFIXES)
        self.assertIn(".agents/skills/finalize-apple-mail-mcp/SKILL.md", scanned)
        self.assertIn(".agents/skills/mail-scripting-dictionary/SKILL.md", scanned)

        # `.claude/skills/*` are tracked *directory* symlinks into
        # `.agents/skills/`. They must contribute no entries of their own —
        # otherwise every first-party skill file is scanned and counted twice,
        # and a per-file ratchet count would mean two different things.
        self.assertFalse(
            [rel for rel in scanned if rel.startswith(".claude/skills/")],
            "Tracked directory symlinks under .claude/skills/ leaked into the scan "
            "set; their targets are already covered at their .agents/skills/ paths.",
        )

    def test_scan_file_count_floor(self) -> None:
        # 583 scanned as of 2026-08-17: 667 tracked, minus 68 under the two skip
        # prefixes (67 `plugin/wheelhouse/` + 1 `archive/`), 1 binary suffix,
        # 1 `.coverage`, and 14 tracked directory symlinks under
        # `.claude/skills/` whose targets are counted at their real
        # `.agents/skills/` paths instead.
        #
        # The floor is 520 rather than a round fraction of 583, and the reason is
        # the specific regression this suite just fixed. `.agents/` used to be a
        # skip prefix, hiding 68 first-party files; scanning it took the set from
        # 515 to 583. A floor below 515 could not tell the two states apart, so
        # re-adding that prefix — or any change that re-blinds a subtree that
        # size — would pass silently. 520 fails on it while still leaving 63
        # files of headroom for ordinary deletions, the same slack the previous
        # 450/515 pair carried. Lower it only alongside a real shrink of the
        # tree, never to make a red run go green.
        self.assertGreater(
            len(self.scanned),
            520,
            f"Only {len(self.scanned)} file(s) scanned; the tracked tree is far larger, "
            "so the identity scan's file set has regressed.",
        )


class IdentityPatternTests(unittest.TestCase):
    """Bidirectional checks: each rule must fire, and must not over-fire."""

    def test_patterns_have_teeth(self) -> None:
        # --- Rule 1: email -------------------------------------------------
        # Reserved documentation domains stay legal.
        self.assertEqual(identity_hits_in_line(_address("someone", "example.com")), [])
        self.assertEqual(identity_hits_in_line(_address("someone", "example.invalid")), [])
        # Subdomain of a reserved name is also legal.
        self.assertEqual(identity_hits_in_line(_address("someone", "mail.example.com")), [])
        # A discovered synthetic placeholder stays legal.
        self.assertEqual(identity_hits_in_line(_address("boss", "company.com")), [])

        # Anything else fires, including real mail providers and the company
        # domain. These assertions are also a guard on the allowlist itself:
        # adding one of these domains to ALLOWED_EMAIL_DOMAINS breaks this test.
        for domain in ("notallowlisted.com", "gmail.com", "google.com", "agenticassets.ai", "someschool.edu"):
            with self.subTest(domain=domain):
                hits = identity_hits_in_line(_address("person", domain))
                self.assertEqual(len(hits), 1, f"{domain} must fire")
                self.assertIn("email address", hits[0])
                self.assertNotIn(domain, ALLOWED_EMAIL_DOMAINS)

        # --- Rule 2: absolute /Users paths ---------------------------------
        # A bare mention with no following segment is prose, not identity. This
        # is what keeps the gate off its own rulebook in AGENTS.md.
        self.assertEqual(identity_hits_in_line("never commit an absolute /Users/ path"), [])
        self.assertEqual(identity_hits_in_line("grep -nE '/Users/|secret'"), [])
        # An angle-bracket stand-in is also not a real segment.
        self.assertEqual(identity_hits_in_line("absolute /Users/<name>/... paths"), [])

        # Placeholder segments stay legal.
        for segment in sorted(PLACEHOLDER_USER_SEGMENTS):
            with self.subTest(segment=segment):
                self.assertEqual(identity_hits_in_line(_home_path(segment, "Library", "Mail")), [])

        # A real-looking username segment fires.
        hits = identity_hits_in_line(_home_path("canaryuser", "Library", "Mail"))
        self.assertEqual(len(hits), 1)
        self.assertIn("absolute /Users path", hits[0])
        # The username itself must never reach the report.
        self.assertNotIn("canaryuser", hits[0])
        # A hostname-shaped segment (hyphens) fires too.
        self.assertEqual(len(identity_hits_in_line(_home_path("some-host-name", "Documents"))), 1)

        # --- Rule 3: account / calendar UUID -------------------------------
        # Lowercase is every plugin id, bundle id, and MCPB identifier in the
        # repo; matching it case-insensitively would make the rule unkeepable.
        self.assertEqual(identity_hits_in_line(_uppercase_uuid().lower()), [])
        uuid_hits = identity_hits_in_line(_uppercase_uuid())
        self.assertEqual(len(uuid_hits), 1)
        self.assertIn("UUID", uuid_hits[0])
        self.assertNotIn(_uppercase_uuid(), uuid_hits[0])

    def test_occurrences_are_counted_not_lines(self) -> None:
        """Two hits on one line count twice, so redacting one registers as progress."""
        line = f"{_address('a', 'notallowlisted.com')} {_address('b', 'notallowlisted.com')}"
        self.assertEqual(len(identity_hits_in_line(line)), 2)

    def test_new_gate_files_are_clean_under_their_own_rules(self) -> None:
        """The gate and its tests must not trip the gate.

        Checked directly against the files on disk rather than through
        ``scan_identity``, because ``git ls-files`` only sees the index and these
        files may not be staged yet in a working session.
        """
        for path in (VALIDATOR, Path(__file__)):
            with self.subTest(path=path.name):
                hits = [
                    f"{path.name}:{lineno}: {hit}"
                    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                    for hit in identity_hits_in_line(line)
                ]
                self.assertEqual(
                    hits,
                    [],
                    "Construct offenders at runtime instead of writing literals; do NOT "
                    "add this file to KNOWN_IDENTITY_HITS or a skip list.\n  - " + "\n  - ".join(hits),
                )


class IdentitySandboxTests(unittest.TestCase):
    """End-to-end negative test in a throwaway git repo."""

    def test_synthetic_offenders_fail_in_a_sandbox_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=sandbox, check=True)

            offender_address = _address("canary", "notallowlisted.example-fake")
            offender_path = _home_path("canaryuser", "Library", "Mail")
            offender_uuid = _uppercase_uuid()
            (sandbox / "notes.md").write_text(
                "\n".join(
                    (
                        "# synthetic live-test writeup",
                        f"sender: {offender_address}",
                        f"mail data: {offender_path}",
                        f"account dir: {offender_uuid}",
                        # Must NOT fire: allowlisted domain, placeholder segment,
                        # bare mention, lowercase id.
                        f"fixture sender: {_address('sender', 'example.com')}",
                        f"fixture path: {_home_path('example', 'Library')}",
                        "never commit an absolute /Users/ path",
                        f"plugin id: {offender_uuid.lower()}",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "notes.md"], cwd=sandbox, check=True)

            errors = validate_no_committed_identity(sandbox)

            self.assertEqual(len(errors), 1, errors)
            self.assertIn("notes.md", errors[0])
            self.assertIn("3 identity hit(s)", errors[0])
            # The report must locate the leak without republishing it.
            for secret in (offender_address, "canaryuser", offender_uuid):
                self.assertNotIn(secret, errors[0])

    def test_untracked_file_is_not_scanned(self) -> None:
        """Only committed/staged content is in scope; scratch files are not."""
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=sandbox, check=True)
            (sandbox / "scratch.md").write_text(
                _address("canary", "notallowlisted.example-fake") + "\n",
                encoding="utf-8",
            )
            self.assertEqual(validate_no_committed_identity(sandbox), [])


class IdentityCliTests(unittest.TestCase):
    def test_validate_script_cli_exit_zero(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
        self.assertIn("committed identity: OK", proc.stdout)

    def test_validate_script_cli_is_not_warn_only(self) -> None:
        """Unlike the module-line-budget report, a hit must exit non-zero.

        Run against a sandbox root via ``-c`` so the check exercises the real
        ``main``-equivalent path (``validate`` + non-empty errors) without
        dirtying this repo.
        """
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=sandbox, check=True)
            (sandbox / "leak.md").write_text(
                _address("canary", "notallowlisted.example-fake") + "\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "leak.md"], cwd=sandbox, check=True)

            program = (
                "import sys;"
                f"sys.path.insert(0, {str(ROOT)!r});"
                "from pathlib import Path;"
                "from tools.validators.validate_no_committed_identity import"
                " validate_no_committed_identity as v;"
                f"errs = v(Path({str(sandbox)!r}));"
                "print('\\n'.join(errs), file=sys.stderr);"
                "sys.exit(1 if errs else 0)"
            )
            proc = subprocess.run(
                [sys.executable, "-c", program],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 1, msg=proc.stdout or proc.stderr)
            self.assertIn("leak.md", proc.stderr)


if __name__ == "__main__":
    unittest.main()
