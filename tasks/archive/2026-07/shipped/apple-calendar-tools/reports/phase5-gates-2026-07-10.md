# Phase 5: verification gate results (feat/apple-calendar-tools)

Branch `feat/apple-calendar-tools`, uncommitted working tree, verified 2026-07-10 against the
Phase 4 implementation report at
[`phase4-implementation-2026-07-10.md`](phase4-implementation-2026-07-10.md). This run followed
the git rules of the task: no commit, no checkout, no push, no branch switch. All seven
requested steps were executed from a clean start, in order, with full output captured.

## PASS/FAIL table

| # | Step | Command | Result | Notes |
|---|------|---------|--------|-------|
| 1a | ruff check (task-literal scope) | `.venv/bin/ruff check plugin/apple_mail_mcp tools` | **FAIL** | 3 errors, all in `tools/`, all pre-existing and untouched by this branch (stale expectation, see below) |
| 1b | ruff check (dev-check exact scope) | `.venv/bin/ruff check plugin/apple_mail_mcp/` | **PASS** | `All checks passed!` |
| 2a | ruff format --check (task-literal scope) | `.venv/bin/ruff format --check plugin/apple_mail_mcp tools` | **FAIL** | 10 files in `tools/` would reformat, all pre-existing and untouched by this branch |
| 2b | ruff format --check (dev-check exact scope) | `.venv/bin/ruff format --check plugin/apple_mail_mcp/` | **PASS** | `99 files already formatted` |
| 3 | mypy --strict | `.venv/bin/mypy --strict plugin/apple_mail_mcp` | **PASS** | `Success: no issues found in 99 source files` |
| 4 | Full pytest suite | `PYTEST_ADDOPTS='' .venv/bin/pytest tests/` | **PASS** | `1365 passed, 45 subtests passed in 66.22s`; matches `tools/expected_test_count.txt` (1365) and `--collect-only` (1365 tests collected) |
| 5 | Module line budget | `python3 tools/validators/check_module_line_budget.py` | **PASS** | `OK: no modules exceed 600 LOC budget.` |
| 6 | Skill reference sync | `python3 tools/validators/sync_skill_references.py --check` | **PASS** | `skill reference sync: OK` |
| 7 | Full release gate | `bash tools/gates/dev-check.sh release` | **PASS** | lint, artifact rebuild, manifest validation, tasks layout, repo root, pytest, test-count, wrapper surface all green (exit 0) |

**Bottom line: the release gate that actually governs this repo (`dev-check.sh release`) is
fully green.** The only failures observed anywhere in this run are ruff findings in `tools/`
that appear solely because the task instructions asked for a broader scope
(`plugin/apple_mail_mcp tools`) than the gate itself checks. `dev-check.sh` scopes both
`ruff check` and `ruff format --check` to `plugin/apple_mail_mcp/` only (see
`tools/gates/dev-check.sh` lines 92-111); CI (`tools/CLAUDE.md` § CI) also never lints
`tools/`. These findings are pre-existing repo debt outside the calendar workstream, not a
regression introduced by this branch.

## Step 1: `ruff check`

### 1a. Task-literal scope: `plugin/apple_mail_mcp tools`

Command: `.venv/bin/ruff check plugin/apple_mail_mcp tools`
Exit code: 1

Full output:

```
I001 [*] Import block is un-sorted or un-formatted
  --> tools/probes/mcp_tool_smoke.py:4:1
   |
 2 |   """Launch an MCP stdio server and assert that required tools are registered."""
 3 |
 4 | / from __future__ import annotations
 5 | |
 6 | | import argparse
 7 | | import json
 8 | | import sys
 9 | | from pathlib import Path
10 | | from typing import Any
11 | |
12 | | import anyio
13 | | from mcp import ClientSession, StdioServerParameters
14 | | from mcp.client.stdio import stdio_client
   | |_________________________________________^
   |
help: Organize imports

I001 [*] Import block is un-sorted or un-formatted
  --> tools/probes/patch_mcporter_wrapper.py:11:1
   |
 9 |   """
10 |
11 | / from __future__ import annotations
12 | |
13 | | import argparse
14 | | import shutil
15 | | from pathlib import Path
   | |________________________^
   |
help: Organize imports

SIM102 Use a single `if` statement instead of nested `if` statements
  --> tools/validators/validate_repo_root.py:94:9
   |
92 |                       f"unexpected file at repo root: {name} ({SUGGESTED_HOME})"
93 |                   )
94 | /         elif child.is_dir():
95 | |             if name not in ALLOWED_ROOT_DIRS:
   | |_____________________________________________^
96 |                   errors.append(
97 |                       f"unexpected directory at repo root: {name}/ ({SUGGESTED_HOME})"
   |
help: Combine `if` statements using `and`

Found 3 errors.
[*] 2 fixable with the `--fix` option (1 hidden fix can be enabled with the `--unsafe-fixes` option).
```

**Classification: stale expectation, not an implementation bug.** All three flagged files
(`tools/probes/mcp_tool_smoke.py`, `tools/probes/patch_mcporter_wrapper.py`,
`tools/validators/validate_repo_root.py`) have zero diff against `main`
(`git diff main -- <file> | wc -l` returned `0` for each) and do not appear anywhere in the
Phase 4 implementation report's file lists. They are pre-existing lint debt in `tools/` that
predates this branch and is outside the scope of `dev-check.sh` (which never lints `tools/`)
and CI (same). Confirmed with `git log --oneline -1 -- <file>`, which points to prior commits
(`5ba28fa`, `a15d9aa`), not this branch's working-tree diff.

### 1b. dev-check exact scope: `plugin/apple_mail_mcp/`

Command: `.venv/bin/ruff check plugin/apple_mail_mcp/`
Exit code: 0

```
All checks passed!
```

This is the scope `dev-check.sh` lint tier actually runs (`tools/gates/dev-check.sh` line
104: `"$RUFF" check plugin/apple_mail_mcp/`). Clean.

## Step 2: `ruff format --check`

### 2a. Task-literal scope: `plugin/apple_mail_mcp tools`

Command: `.venv/bin/ruff format --check plugin/apple_mail_mcp tools`
Exit code: 1

```
Would reformat: tools/manifest_checks/common.py
Would reformat: tools/manifest_checks/module_budget.py
Would reformat: tools/manifest_checks/tool_count.py
Would reformat: tools/manifest_checks/version.py
Would reformat: tools/probes/mcp_tool_smoke.py
Would reformat: tools/probes/patch_mcporter_wrapper.py
Would reformat: tools/validators/check_module_line_budget.py
Would reformat: tools/validators/check_wrapper_surface.py
Would reformat: tools/validators/validate_repo_root.py
Would reformat: tools/validators/validate_tasks_layout.py
10 files would be reformatted, 110 files already formatted
```

**Classification: stale expectation, not an implementation bug.** All ten flagged files have
zero diff against `main` (checked the same way as step 1a). None of the files this branch
actually touched under `tools/` (`tools/manifest_checks/artifacts.py`,
`tools/validators/sync_skill_references.py`, `tools/expected_test_count.txt`) appear in this
list. Pre-existing formatting drift in `tools/`, outside the gate's checked scope.

### 2b. dev-check exact scope: `plugin/apple_mail_mcp/`

Command: `.venv/bin/ruff format --check plugin/apple_mail_mcp/`
Exit code: 0

```
99 files already formatted
```

## Step 3: `mypy --strict plugin/apple_mail_mcp`

Command: `.venv/bin/mypy --strict plugin/apple_mail_mcp`
Exit code: 0

```
Success: no issues found in 99 source files
```

Covers all new calendar modules (`calendar_core/`, `tools/calendar/`) plus the rest of the
package. Clean, matches the Phase 4 report's claim.

## Step 4: full pytest suite

Command: `PYTEST_ADDOPTS='' .venv/bin/pytest tests/`
Exit code: 0

```
........................................................................ [  5%]
........................................................................ [ 10%]
........................................................................ [ 15%]
........................................................................ [ 21%]
........................................................................ [ 26%]
........................................................................ [ 31%]
...................................................................... [ 36%]
...................................................................... [ 41%]
........................................................................ [ 47%]
....................................................................................................... [ 54%]
........................................................................ [ 60%]
........................................................................ [ 65%]
........................................................................ [ 70%]
........................................................................ [ 75%]
........................................................................ [ 81%]
........................................................................ [ 86%]
........................................................................ [ 91%]
........................................................................ [ 96%]
..........................................                               [100%]
1365 passed, 45 subtests passed in 66.22s (0:01:06)
```

`tools/expected_test_count.txt` says `1365`. `PYTEST_ADDOPTS='' .venv/bin/pytest --collect-only
tests` independently reports `1365 tests collected`. All three numbers agree: no drift.

## Step 5: module line budget

Command: `python3 tools/validators/check_module_line_budget.py`
Exit code: 0

```
OK: no modules exceed 600 LOC budget.
```

Matches the Phase 4 report's claim that the largest new module (`calendar_core/engine.py`,
366 lines) stays well under the 600 LOC warn threshold.

## Step 6: skill reference sync

Command: `python3 tools/validators/sync_skill_references.py --check`
Exit code: 0

```
skill reference sync: OK
```

Confirms `plugin/skills/references/calendar-safety-limits.md` is correctly synced into both
new skills' `references/` folders via `SYNC_MAP`.

## Step 7: full release gate

Command: `bash tools/gates/dev-check.sh release`
Exit code: 0

Full output:

```
→ ruff check plugin/apple_mail_mcp/
All checks passed!
→ ruff format --check plugin/apple_mail_mcp/
99 files already formatted
→ mypy --strict plugin/apple_mail_mcp/
Success: no issues found in 99 source files
lint: OK
→ Pruning stale apple-mail-mcp-v*.mcpb (keeping apple-mail-mcp-v3.10.0.mcpb)
→ Building apple-mail-plugin.zip (Claude Code plugin)
→ Building apple-mail-mcp-v3.10.0.mcpb (Claude Desktop bundle)
→ Verifying artifacts
validate_manifests.sh: OK (version=3.10.0, tools=41)
→ mcpb unpack + validate OK
→ claude plugin validate --strict OK (manifest at zip root, no warnings)

Artifacts ready:
-rw-r--r--@ 1 cayman-mac-mini  staff   445K Jul 10 03:30 apple-mail-mcp-v3.10.0.mcpb
-rw-r--r--@ 1 cayman-mac-mini  staff   423K Jul 10 03:30 apple-mail-plugin.zip
-rw-r--r--@ 1 cayman-mac-mini  staff   423K Jul 10 03:30 apple-mail.plugin
→ tasks/ layout (active/reference/archive buckets; enforced for agent handoffs)
tasks layout: OK
→ repo root hygiene (allowlisted navigation + release artifacts only)
repo root: OK
........................................................................ [  5%]
........................................................................ [ 10%]
........................................................................ [ 15%]
........................................................................ [ 21%]
........................................................................ [ 26%]
........................................................................ [ 31%]
...................................................................... [ 36%]
...................................................................... [ 41%]
........................................................................ [ 47%]
....................................................................................................... [ 54%]
........................................................................ [ 60%]
........................................................................ [ 65%]
........................................................................ [ 70%]
........................................................................ [ 75%]
........................................................................ [ 81%]
........................................................................ [ 86%]
........................................................................ [ 91%]
........................................................................ [ 96%]
..........................................                               [100%]
test count: OK (1365 collected, matches tools/expected_test_count.txt)
wrapper: /Users/cayman-mac-mini/.local/bin/apple-mail
  ok   get-email-by-id
  ok   search-emails
  ok   get-email-thread
  ok   list-inbox-emails
  ok   get-inbox-overview
wrapper surface: OK
```

This confirms every claim in the Phase 4 report's "Gate results" section: lint clean over 99
package files, artifacts rebuilt and verified (`apple-mail-plugin.zip`,
byte-identical `apple-mail.plugin`, `apple-mail-mcp-v3.10.0.mcpb`), `validate_manifests.sh`
reporting `version=3.10.0, tools=41`, 1365 tests passing, test-count gate clean, tasks layout
and repo root clean, and the generated CLI wrapper surface intact.

Rebuilding the artifacts touched two files on disk: `apple-mail-plugin.zip` (tracked, now
shows `M` in `git status`, expected since the gate regenerates it byte-for-byte) and
`apple-mail-mcp-v3.10.0.mcpb` (gitignored build output, not tracked). No source files were
modified by this verification run.

## Failure classification summary

| Failure | Classification | Rationale |
|---------|----------------|-----------|
| `ruff check plugin/apple_mail_mcp tools` (3 errors) | Stale expectation | All 3 files have zero diff vs `main`; outside `dev-check.sh` lint scope and outside CI lint scope; not part of the calendar workstream |
| `ruff format --check plugin/apple_mail_mcp tools` (10 files) | Stale expectation | All 10 files have zero diff vs `main`; same scope gap as above |

No implementation bugs and no environment failures were found in any of the seven steps. Every
check that the repo's actual release gate (`dev-check.sh release`) enforces passed cleanly on
a from-scratch run, and the full pytest suite, mypy strict pass, module budget, and skill
reference sync all corroborate the Phase 4 implementation report's claims exactly (1365 tests,
41 tools, version 3.10.0, no oversized modules).

## Verification commands (for reproduction)

```bash
cd /Users/cayman-mac-mini/Documents/GitHub/apple-mail-mcp
.venv/bin/ruff check plugin/apple_mail_mcp tools
.venv/bin/ruff check plugin/apple_mail_mcp/            # dev-check exact scope
.venv/bin/ruff format --check plugin/apple_mail_mcp tools
.venv/bin/ruff format --check plugin/apple_mail_mcp/   # dev-check exact scope
.venv/bin/mypy --strict plugin/apple_mail_mcp
PYTEST_ADDOPTS='' .venv/bin/pytest tests/
python3 tools/validators/check_module_line_budget.py
python3 tools/validators/sync_skill_references.py --check
bash tools/gates/dev-check.sh release
```

## Recommendation

No action required against the calendar branch itself: it did not introduce any lint,
format, type, test, budget, or sync regressions, and the release gate that actually governs
merge readiness is fully green. The `tools/` lint/format debt (13 distinct files, all
pre-existing) is a separate, unrelated cleanup item; if the team wants `tools/` covered going
forward, that is a `dev-check.sh` scope change to propose separately, not something this
branch needs to carry.
