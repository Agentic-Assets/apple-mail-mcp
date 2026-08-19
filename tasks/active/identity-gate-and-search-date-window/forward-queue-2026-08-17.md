# Forward queue after identity-gate-and-search-date-window (2026-08-17)

Candidate work surfaced while shipping `055a968` / PR #90. This is a menu, not a roadmap. Verify each before acting; confidence levels are stated.

## Correctness / bugs

- **AGENTIC-2359: `manage_trash` deletes what the caller did not ask for** (confidence: verified by source trace, not live-observed). `empty_trash` never consults `dry_run`, so `confirm_empty=True` deletes under the default. `delete_permanent` is *gated* on `older_than_days` / `recent_days` but the emitted script applies neither, and deletes the **newest** messages, so a request to purge mail older than a year permanently deletes the newest instead. Highest-priority item in this queue: irreversible, and `--read-only` does not remove either path. Needs a disposable fixture account.
- **`analytics/dashboard.py` renders every failure as an authoritative empty inbox** (confidence: verified by source trace). Its script-level `try` has no `on error` arm, so any throw returns `""` → `[]` → `"recent_emails": [], "errors": []`. Same silent-zero class as AGENTIC-2344 and 2355, with no `ERROR_MAILBOX` equivalent to fall back on. Tracked in AGENTIC-2359.
- **Unclamped scan caps are the real AGENTIC-2355 exposure** (confidence: verified). Four of the five remaining sites take their bound from a caller-supplied parameter with no `SCAN_BOUNDS` clamp, which is what makes the enumeration arm reachable at all. Fixing the spelling without clamping the cap fixes nothing that matters. Note `export_helpers`' effective clamp is a bare `250` literal at `export.py:356`, not a `SCAN_BOUNDS` key.
- **`messages 1 thru 0` behavior is assumed, not confirmed** (confidence: hypothesis). Every three-arm guard added this branch assumes it raises. If it does not, the zero-arm is dead code; if it does and a site omits the arm, an empty mailbox becomes a spurious error. One disposable-fixture probe settles it for all sites. Blocks confident work on the two destructive sites.
- **Deleting while iterating a bound specifier list is assumed stable** (confidence: hypothesis). Mail returns `message id N of mailbox …` specifiers so it should be, but a delete loop is not the place to assume. Same probe.

## Hardening

- **The identity gate reads the working tree but enumerates the git index** (confidence: verified, documented limitation). A leak staged with `git add -p` and then removed from the working tree is invisible to it. Matches existing `tools/manifest_checks/artifacts.py` behavior, so fixing it is a convention change across both, not a local patch.
- **Nothing gates commit messages, PR bodies, or issue comments** (confidence: verified gap). The identity scan covers tracked files only, and those three channels never pass through `git diff --cached`. A session ID already reached this repo's published history that way. A `commit-msg` hook would cover one of the three cheaply.
- **The remaining `KNOWN_IDENTITY_HITS` addresses are all in `tasks/archive/**`** (confidence: verified). Once the archive is redacted or accepted as frozen, the address ratchet could go to zero and the rule could become absolute for non-archive paths, which is a much stronger invariant than a 64-occurrence baseline.
- **`ruff` cannot pass on `tools/`** (confidence: verified). 3 `I001` errors and 9 format-dirty files, pre-existing; the enforced `lint` tier covers `plugin/apple_mail_mcp/` only. Bringing `tools/` in is mechanical but touches files other lanes edit, so it wants a quiet window.

## Simplification

- **The three `message_collection` branches in `search/script.py` now nearly rhyme** (confidence: verified, deliberately not done). After AGENTIC-2356, branch 1 is close to branch 2 minus the expensive reads, and branches 1 and 2 already share the cap check, `ignoring case`, and both scan-failure fragments. A flag-driven merged builder is tempting and risky: the 40-space indentation inside those f-strings is exactly where a whitespace regression hides. The emission-snapshot harness (6,277 variants) makes it provable if attempted.
- **The raw-enumeration regex now exists in three test modules** (confidence: verified). Two of them scan generated scripts and could share one helper; the third scans source files and correctly does not. Needs a `tests/` helper module, so it is a structural call rather than a simplification.
- **`search/script.py` is 584 lines against a hard 600** (confidence: verified). It survived this branch only because the simplifier recovered 15 lines. The next feature touching it will hit the wall. Splitting the per-message condition assembly into a sibling module in the `search/` package is the obvious seam.
- **Two identity tests now assert the same fact at two layers** (confidence: verified). `test_no_new_identity_hits` (ratchet seam) and `test_validator_reports_clean_tree` (composed entry point). Defensible as unit + integration; merging drops the collected count to 1814 and so needs the count file updated in the same commit.

## Robustness

- **`ERROR_MAILBOX` is a `|||`-delimited string contract parsed in Python** (confidence: passing idea). It is sanitized at every emission site added this branch, but the delimiter is structural and a new emitter that forgets `sanitize_field` shifts every downstream field. A lint asserting every `ERROR_MAILBOX` emission passes through `my sanitize_field` would make that impossible rather than merely unlikely.

## Process / docs

- **The AGENTIC-2355 issue description still says the sites are "mostly `on error` fallbacks"** (confidence: verified wrong). Only one of seven was. The comment on the issue corrects it and the lint's in-file comment is fixed, but the description itself would still mislead someone starting from the issue.
- **`tasks/todo.md` cites branch `fix/github-issues-mcp-hardening-20260617`, which exists neither locally nor on origin** (confidence: verified), holding a `get_email_source` tool that appears in no source file or CHANGELOG entry. Either the work was lost or the pointer is stale; both are worth resolving because the roadmap's "next three builds" leads with porting that tool forward.
- **No `v3.11.7` tag exists** (confidence: verified) though 3.11.7 is merged and is the repo version. The rolling-current-version CHANGELOG convention makes this easy to lose track of.
- **Subagent briefs need the public-repo constraint inlined** (confidence: verified this session). Every agent that wrote files needed it restated; the one that redacted docs had to choose a redaction style with no established convention to follow (`~/` was chosen). A one-paragraph canonical snippet in `tasks/CLAUDE.md` would stop each lane from re-deriving it.

## Evaluation

- **Nothing measures whether the identity gate would have caught the leak that motivated it** (confidence: verified gap). The canary proves it catches a synthetic address today. A stronger check runs the validator against the pre-redaction blob from the branch that introduced AGENTIC-2358 and asserts it fails, which turns the gate from "plausible" into "demonstrated against the real incident."
- **No perf baseline was captured for the extra `date received` read** (confidence: verified gap). The early `exit repeat` should make the fast path net faster on a quiet mailbox and slightly slower on a busy one, but the claim is reasoned, not measured. `cli/test_cli_perf.py` and `perf-test` exist and could hold a real number.
