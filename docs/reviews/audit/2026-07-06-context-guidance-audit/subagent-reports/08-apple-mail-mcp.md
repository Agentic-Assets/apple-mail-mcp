# apple-mail-mcp Guidance Audit

**Scope:** Read-only Phase 1 context-guidance audit  
**Repo:** `/agent/repos/apple-mail-mcp`  
**Date:** 2026-07-06  
**Auditor:** Subagent 08 (guidance hierarchy, tool counts, manifests, task routing, cross-checks)

## Summary

The apple-mail-mcp repo has a **strong, mostly accurate guidance hierarchy** centered on root `CLAUDE.md` / `AGENTS.md` with ten child `CLAUDE.md` routers under `plugin/`, `docs/`, `tools/`, `tests/`, `tasks/`, `apple-mail-mcpb/`, and `.claude-plugin/`. Automated gates (`tools/gates/dev-check.sh`, `tools/validators/validate_manifests.py`, `tools/manifest_checks/`) enforce the **31-tool** claim, **test-count SSOT** (`tools/expected_test_count.txt` = **1021**), and **600 LOC module line budget** at v3.9.1.

Verified cross-checks:

| Claim | Result |
|-------|--------|
| `@mcp.tool` count | **31** (`find plugin/apple_mail_mcp/tools -name '*.py' \| xargs grep -h '^@mcp.tool' \| wc -l`) |
| MCPB `tools[]` length | **31** (`apple-mail-mcpb/manifest.json`) |
| Bundled workflow skills | **9** (`plugin/skills/*/SKILL.md`) |
| Version 3.9.1 sync | **Aligned** across all six bump targets |
| Test count SSOT | **1021** in `tools/expected_test_count.txt` |
| dev-check path | Correct in hubs/README: `bash tools/gates/dev-check.sh` |
| Codex tool-count gate | Dynamic from decorators in `tools/gates/validate-codex-plugin.sh` |

**Guidance hierarchy (AGENTS.md / CLAUDE.md):**

```
CLAUDE.md (root hub + Distribution channels)
AGENTS.md (root hub, near-duplicate; no Distribution channels section)
├── plugin/docs/CLAUDE.md
├── plugin/apple_mail_mcp/CLAUDE.md
│   └── plugin/apple_mail_mcp/tools/CLAUDE.md
├── plugin/skills/CLAUDE.md
├── docs/CLAUDE.md (+ docs/CLAUDE-conventions.md deep rules)
├── tools/CLAUDE.md
├── tests/CLAUDE.md
├── tasks/CLAUDE.md
├── apple-mail-mcpb/CLAUDE.md
└── .claude-plugin/CLAUDE.md
```

No child `AGENTS.md` files exist (root only). `README.md` serves as human-facing doc map and correctly references `tools/gates/dev-check.sh` without hardcoding test totals.

**Main gaps:** (1) `finalize-apple-mail-mcp` skill still says **five** version files while root hubs and `docs/CLAUDE-conventions.md` say **six**; (2) active tool guidance references a **missing** `tasks/phase-3-annotation-matrix.md`; (3) `tasks/todo.md` carries **stale** test count and dev-check path; (4) **venv self-healing** is implemented in `plugin/start_mcp.sh` but not documented in agent-facing CLAUDE hubs.

## Findings

| Severity | Location | Issue | Recommended change |
|----------|----------|-------|-------------------|
| High | `.agents/skills/finalize-apple-mail-mcp/SKILL.md` (lines 38, 137, 148–150, 222); symlinked `.claude/skills/finalize-apple-mail-mcp/SKILL.md` | Skill says bump **five** version files; manifest table omits `plugin/.codex-plugin/plugin.json`. Root `CLAUDE.md` / `AGENTS.md` and `docs/CLAUDE-conventions.md` § Versioning list **six** files. Release agents following finalize may skip Codex manifest version. | Update finalize skill to **six files**, add `plugin/.codex-plugin/plugin.json` to the manifest table, align wording at lines 38 and 222. |
| High | `plugin/apple_mail_mcp/tools/CLAUDE.md` (lines 39, 112); `plugin/apple_mail_mcp/server.py` (line 42) | References `tasks/phase-3-annotation-matrix.md` for `@mcp.tool(annotations=…)` presets. **File does not exist** anywhere in the repo (glob returned 0 matches). | Restore the matrix under `tasks/reference/` or `tasks/active/`, or retarget links to an existing doc (e.g. inline presets in `server.py` + `docs/CLAUDE-conventions.md`). |
| Medium | `AGENTS.md` vs `CLAUDE.md` | `CLAUDE.md` has **Distribution channels (four install surfaces)** section (lines 5+); `AGENTS.md` lacks it. Codex/cloud agents that read `AGENTS.md` only miss install-surface routing present in `docs/CLAUDE-conventions.md` § Distribution channels. | Add Distribution channels section to `AGENTS.md` or add explicit cross-link at top of `AGENTS.md`. |
| Medium | `tasks/todo.md` (line 7) | Shipped note cites **1016 tests**; SSOT `tools/expected_test_count.txt` is **1021** (+5 drift). | Update shipped note to 1021 or remove hardcoded count and point at SSOT file. |
| Medium | `tasks/todo.md` (line 7) | Uses `dev-check.sh release` without `tools/gates/` prefix. Active hubs use correct path. | Change to `bash tools/gates/dev-check.sh release`. |
| Medium | `.agents/skills/finalize-apple-mail-mcp/SKILL.md` (line 135) | Test-count sync table says update **Root CLAUDE.md, README.md** from pytest result. Contradicts SSOT policy in root hubs and `tools/CLAUDE.md` (only `tools/expected_test_count.txt`). | Point finalize at `tools/expected_test_count.txt` + gate refresh; do not scatter counts in prose docs. |
| Medium | `tools/CLAUDE.md` (lines 48–74) | **`## validate_repo_root` section duplicated** verbatim (two headings, slightly different allowlist wording in second copy). | Merge into one section; keep the stricter/current allowlist from `validators/validate_repo_root.py`. |
| Medium | `plugin/docs/CLAUDE.md` (line 16); root CLAUDE hubs | `start_mcp.sh` implements **venv self-healing** (`--ensure-only`, `--check`, `--doctor`, rebuild on broken interpreter) per script header comments; plugin docs only say "First-run venv bootstrap". Agents miss repair/health-check flags. | Document self-heal behavior and flags in `plugin/docs/CLAUDE.md` and optionally root hub Dev setup. |
| Medium | `.agents/skills/` vs `.claude/skills/` | Symlink policy documented in root hubs. **13** skill dirs under `.agents/skills/`; **11** symlinks under `.claude/skills/`. Missing symlinks: `context-guidance-audit`, `find-skills`. | Add symlinks per documented pattern or document intentional Codex-only skills. |
| Low | `tasks/active/v4-performance-consolidation-2026-05-27/*.md` | Multiple references to pre-reorg paths `tools/dev-check.sh` and stale tool count **28** / version **3.4.0** in progress logs (historical lane context). | Add archive banner or update copy-on-edit when touching lane; low priority for closed consolidation work. |
| Low | `docs/superpowers/specs/2026-06-30-tools-probes-reorg-design.md` (line 101) | Cites **1016** tests in gate description. | Update to SSOT or say "see expected_test_count.txt". |
| Low | `tasks/reference/phase-plan-3.1.7.md`, `tasks/reference/robustness-backlog-2026-05-22.md` | Historical "Version bump (five files)" checklist items. | Accept as historical or add footnote pointing to six-file policy. |
| Low | `plugin/.claude-plugin/plugin.json` (description); `tasks/todo.md` (line 15) | Em dash (`—`) in shipped plugin description; todo already tracks **deferred brand-voice sweep**. Informational, not broken guidance. | Complete deferred brand-voice pass when convenient. |

## Positive observations

- **Hierarchical routing works:** All ten child `CLAUDE.md` paths referenced from root hubs exist and cross-link consistently.
- **31 tools / 9 skills claims match code and manifests** at v3.9.1; `tools/manifest_checks/common.py` `ACTIVE_DOC_TOOL_COUNT_REQUIRED` scans key docs automatically.
- **Six-file version bump is correct** in root `CLAUDE.md`, `AGENTS.md`, and `docs/CLAUDE-conventions.md`; all six locations verified at **3.9.1**.
- **Test count SSOT is enforced:** `tools/expected_test_count.txt` + dev-check test-count gate; root hubs document the pattern clearly.
- **Module line budget is real:** 600 LOC warn, baseline regression in `tests/fixtures/module_line_budget/baseline.json`, wired through dev-check, CI, and validate_manifests.
- **Codex plugin validation is dynamic:** `validate-codex-plugin.sh` derives expected tool count from `@mcp.tool` decorators rather than hardcoding.
- **Task routing is well-specified:** `tasks/CLAUDE.md` enforces `active/` · `reference/` · `archive/` layout with CI validator; `todo.md` points at active handoffs.
- **README.md stays clean:** Uses correct gate paths; does not hardcode obsolete test totals.
- **Symlink pattern mostly followed** for repo agent skills (11/13); policy is explicit in root hubs.

## Suggested fix priority order

1. **Fix finalize-apple-mail-mcp version bump checklist** (six files, include Codex manifest) — prevents release omissions.
2. **Resolve or restore `tasks/phase-3-annotation-matrix.md`** — fixes broken links in active tool authoring guidance.
3. **Refresh `tasks/todo.md`** (1021 tests, `tools/gates/dev-check.sh` path).
4. **Align finalize skill test-count policy** with `tools/expected_test_count.txt` SSOT.
5. **Dedupe `tools/CLAUDE.md` validate_repo_root section.**
6. **Document venv self-healing** in `plugin/docs/CLAUDE.md`.
7. **Sync AGENTS.md** with CLAUDE.md Distribution channels (or cross-link).
8. **Add missing `.claude/skills` symlinks** for `context-guidance-audit` and `find-skills`.
9. **Sweep historical stale paths/counts** in active/archive task lanes and design specs (low urgency).

---

**Finding counts by severity**

| Severity | Count |
|----------|------:|
| High | 2 |
| Medium | 7 |
| Low | 4 |
| **Total** | **13** |

## Remediation log

**Date:** 2026-07-06 (Phase 2, scope 08)  
**Mode:** Guidance-only (no version bumps, no artifact rebuilds, no test-count gate run)

### Re-verification (pre-fix)

| Claim | Result |
|-------|--------|
| `@mcp.tool` count | **31** (confirmed) |
| `tools/expected_test_count.txt` | **1021** (confirmed) |
| Six-file version sync at 3.9.1 | **Aligned** (confirmed) |
| Missing `tasks/phase-3-annotation-matrix.md` | **Confirmed** — only referenced from `server.py` comment and `tools/CLAUDE.md` |
| `.claude/skills/` symlinks | **11/13** — missing `context-guidance-audit`, `find-skills` |
| `tools/CLAUDE.md` duplicate `validate_repo_root` | **Confirmed** (lines 48–74) |

### Changes applied

| Priority | Finding | Remediation |
|----------|---------|-------------|
| 1 | finalize skill: five vs six version files | Updated `.agents/skills/finalize-apple-mail-mcp/SKILL.md` (symlink unchanged): six files incl. `plugin/.codex-plugin/plugin.json`; out-of-scope, manifest table, and release note aligned |
| 1 | finalize skill: test-count scatter policy | Test-count row now points at `tools/expected_test_count.txt` only |
| 2 | Missing annotation matrix | Created `tasks/reference/phase-3-annotation-matrix.md`; retargeted `plugin/apple_mail_mcp/tools/CLAUDE.md` and `server.py` comment |
| 3 | Stale `tasks/todo.md` | 1021 tests + SSOT pointer; `bash tools/gates/dev-check.sh release` path |
| 5 | Duplicate `validate_repo_root` in `tools/CLAUDE.md` | Removed duplicate section; kept first (stricter allowlist) |
| 6 | Undocumented venv self-healing | Added § Venv self-healing to `plugin/docs/CLAUDE.md`; updated `start_mcp.sh` row in key files table |
| 7 | AGENTS.md missing Distribution channels | Added condensed section + cross-links to `CLAUDE.md` and `docs/CLAUDE-conventions.md` |
| 8 | Missing skill symlinks | Added `.claude/skills/context-guidance-audit` and `.claude/skills/find-skills` → `.agents/skills/` |

### Deferred (low priority, unchanged)

- Historical stale paths/counts in `tasks/active/v4-performance-consolidation-2026-05-27/` and design specs
- Brand-voice em-dash sweep in manifest descriptions (`tasks/todo.md` deferred item)
- `tasks/reference/phase-plan-3.1.7.md` / `robustness-backlog-2026-05-22.md` historical "five files" footnotes

### Post-remediation status

| Severity | Open |
|----------|-----:|
| High | **0** |
| Medium | **0** (guidance items addressed) |
| Low | **4** (historical/deferred only) |
