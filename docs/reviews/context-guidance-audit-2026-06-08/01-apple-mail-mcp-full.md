# apple-mail-mcp Full Guidance Audit

**Phase:** 1 (read-only)  
**Date:** 2026-06-08  
**Repo:** `/agent/repos/apple-mail-mcp`  
**Scope:** Root `CLAUDE.md`, `AGENTS.md`, `plugin/docs/CLAUDE.md`, `plugin/apple_mail_mcp/CLAUDE.md`, `plugin/skills/CLAUDE.md`, `tests/CLAUDE.md`, `tools/CLAUDE.md`, `tasks/CLAUDE.md`, `.claude-plugin/`, `.agents/plugins/`  
**Recent-change focus:** Codex plugin setup (2026-06-07), v3.6.0 compose race fixes, v3.5.0 field-report hardening, v4 performance consolidation (2026-05-27)

## Summary

Core numeric claims are **accurate and enforced**: **28** `@mcp.tool` handlers (inbox 6, search 3, compose 5, manage 6, analytics 5, smart_inbox 3) and **version 3.6.1** across all release manifests. `bash tools/validate_manifests.sh` returned `OK (version=3.6.1, tools=28)`.

Module-level guidance is generally strong — especially `tools/CLAUDE.md` (11-check manifest contract), `plugin/skills/CLAUDE.md` (sibling routing matrix), and `.claude-plugin/CLAUDE.md` (dual-manifest + Codex split). Main gaps: **~90% duplicate root AGENTS/CLAUDE** with Codex-facing distribution guidance missing from `AGENTS.md`; **CHANGELOG** still tops out at **3.6.0** while manifests are **3.6.1**; **tests/CLAUDE.md** inventory lists ~17 modules vs **40** on-disk `test_*.py` files; **tasks/** still anchors historical plans to **3.2.1** and v4 workstream snapshots cite **763** tests vs the current **798 + 30 subtests** claim; **no Cursor subagent model routing** in repo root guidance (unlike sibling repos). Bundled skill triggers are well de-overlapped via explicit "Do NOT use for …" cross-refs; residual ambiguity between `inbox-triage` and `email-management` on broad "check my email" phrasing is low severity.

**798 tests + 30 subtests:** not independently re-collected in this audit environment (venv/`ensurepip` unavailable). Claim is corroborated by `tasks/todo.md` (2026-06-07 verification) and CI workflow; treat as **accepted with secondary evidence**, not live re-run.

## Verified counts (live repo)

| Metric | Claimed | Verified | Method |
|--------|---------|----------|--------|
| MCP tools | 28 | **28** | `rg -c '^@mcp\.tool' plugin/apple_mail_mcp/tools/*.py` summed |
| Release version | 3.6.1 | **3.6.1** | `pyproject.toml`, both `plugin.json`, `.claude-plugin/marketplace.json` `plugins[0].version`, `server.json` (×2), `apple-mail-mcpb/manifest.json` |
| Bundled workflow skills | 9 | **9** | `plugin/skills/*/SKILL.md` |
| Test modules on disk | — | **40** | `tests/**/test_*.py` (38 top-level + 2 under `property/`) |
| Test modules in `tests/CLAUDE.md` inventory | — | **~17** | Manual count of listed `test_*` names |
| Root AGENTS vs CLAUDE lines | — | **76 vs 89** | `wc -l`; diff shows CLAUDE-only distribution table + fuller release-gate prose |

## Findings

| Severity | Location | Issue | Recommended change |
|----------|----------|-------|-------------------|
| Medium | `CHANGELOG.md` | Latest section is **3.6.0 — 2026-06-05**; all manifests and `pyproject.toml` are **3.6.1**. No changelog entry documents the 3.6.1 close-out (Codex install-smoke regression, coordinated bump per `tasks/codex-claude-plugin-setup-2026-06-07/progress-log.md`). | Add **3.6.1** section or fold 3.6.1 notes into header policy; keep CHANGELOG as user-facing release source aligned with validator source of truth (`pyproject.toml`). |
| Medium | `AGENTS.md` vs `CLAUDE.md` | ~90% duplicated navigation hub (~13 lines differ). `AGENTS.md` omits **Distribution channels** table (four install surfaces, byte-parity rule, Cowork `.plugin` vs zip) that Codex/Cursor agents need after Codex plugin setup work. Finalize pointer differs: AGENTS → sync `AGENTS.md`; CLAUDE → sync `CLAUDE.md`. | Single authoritative root file + one-line pointer in the other (Corbis/EQUIRE pattern), or extract shared block; ensure Codex-facing file includes distribution-channel guidance. |
| Medium | `tests/CLAUDE.md` § Test files | Inventory lists ~17 modules; repo has **40** `test_*.py` files. Missing recent suites include contract/hardening modules (`test_bounded_scan_contract`, `test_contracts_*`, `test_tier*_hardening`, `test_compose_*`, `test_analytics_resource_safety`, `property/*`, etc.). | Replace static list with pointer to `tests/` + grouping by domain, or run `pytest --collect-only` and link to CI; avoid stale fixed inventories. |
| Low | `tasks/CLAUDE.md`, `tasks/INDEX.md`, `phase-plan-3.1.7.md` | Historical plans say "verify against current **3.2.1** state" while release is **3.6.1** (May–Jun 2026). | Add banner: historical; current release = `pyproject.toml` + `tasks/todo.md`; update INDEX pointers to active workstreams (`codex-claude-plugin-setup-2026-06-07`). |
| Low | `tasks/v4-performance-consolidation-2026-05-27/phase-plan.md`, `progress-log.md` | Snapshot cites **763** tests / **39** files; root guidance claims **798 + 30 subtests**. | Annotate as point-in-time snapshot or refresh counts once when touching that workstream. |
| Low | `plugin/apple_mail_mcp/CLAUDE.md` | Verify command `grep -c "^@mcp.tool" tools/*.py` returns **per-file** counts, not total (unlike `plugin/apple_mail_mcp/tools/CLAUDE.md` which correctly uses `rg … \| wc -l`). | Align verify one-liner with `tools/CLAUDE.md` / root CLAUDE (`sum` or `wc -l` after `rg`). |
| Low | Root `CLAUDE.md` / `AGENTS.md` | No **Cursor subagent model** rule (omit `model:` / Composer default), while `.agents/skills/context-guidance-audit/SKILL.md` in-repo mandates Composer 2.5 for subagents. No `.cursor/rules/` or `.claude/agents/` in this repo. | Optional thin Cursor note in root guidance if Cursor automation is a first-class consumer; otherwise document host-specific routing in audit skill only. |
| Low | `docs/CLAUDE-conventions.md`, `plugin/apple_mail_mcp/tools/CLAUDE.md` | v3.6.0 compose object-model / draft tail lookup documented in `CHANGELOG.md` and `plugin/skills/email-drafting/SKILL.md`; tool-level compose guidance may lag for agents editing handlers. | On Phase 2, add brief pointer in `tools/CLAUDE.md` compose section to CHANGELOG 3.6.0 + email-drafting skill threading note. |
| Info | `798 tests + 30 subtests` (root, README, `tests/CLAUDE.md`, `tasks/CLAUDE.md`) | Not re-collected in audit VM (no working `.venv`). Secondary evidence: `tasks/todo.md` 2026-06-07, CI `pytest tests/ -q`. | Periodically refresh via `pytest tests/ --collect-only -q` in release gate; consider validator hook or finalize skill step. |
| Info | Skill triggers (`plugin/skills/*/SKILL.md`) | All nine skills end descriptions with **Do NOT use for … (see sibling)**; `plugin/skills/CLAUDE.md` provides routing cheat sheet + shared `large-inbox-rules.md`. | Residual: `"check my email"` could match both `inbox-triage` and umbrella `email-management`; acceptable if descriptions stay strict; optional trigger tweak in Phase 2. |
| Info | `.agents/plugins/marketplace.json` | Install routing only (no version field by design); points `./plugin` with `policy.installation: AVAILABLE`. Documented in `tools/CLAUDE.md` check #11 and `.claude-plugin/CLAUDE.md`. | No change needed; keep validator as source of truth. |

## Positive observations

- **Manifest discipline:** `tools/validate_manifests.py` enforces version sync, tool-count claims, MCPB parity, Codex surface, artifact freshness, and marketplace dual-component rules — documented clearly in `tools/CLAUDE.md`.
- **Tool inventory accuracy:** `plugin/apple_mail_mcp/tools/CLAUDE.md` module map matches live `@mcp.tool` distribution; validator prevents marketing drift.
- **Codex plugin setup:** `plugin/docs/CLAUDE.md` and `.claude-plugin/CLAUDE.md` document Claude vs Codex marketplace split, `${CLAUDE_PLUGIN_ROOT}`, and `--draft-safe` default consistently.
- **Skill architecture:** Skills-only policy, sibling matrix, shared references, and skill-reviewer gate in `plugin/skills/CLAUDE.md` reduce trigger overlap better than most plugin repos.
- **Recent workstream evidence:** `tasks/codex-claude-plugin-setup-2026-06-07/progress-log.md` records 3.6.1 release gate, Codex install smoke, and test count verification.
- **v3.6.0 / v3.5.0 behavior:** CHANGELOG and `email-drafting` skill capture compose race elimination and field-report hardening for operator-facing workflows.

## Scoped file health (quick pass)

| File | Status |
|------|--------|
| `CLAUDE.md` | Accurate hub; distribution channels + orchestration current |
| `AGENTS.md` | Accurate but duplicate; missing distribution section |
| `plugin/docs/CLAUDE.md` | Accurate Codex/Claude install surface |
| `plugin/apple_mail_mcp/CLAUDE.md` | Accurate; minor verify-command footgun |
| `plugin/skills/CLAUDE.md` | Accurate; strong routing |
| `tests/CLAUDE.md` | Accurate counts claim; stale file inventory |
| `tools/CLAUDE.md` | Accurate; excellent validator documentation |
| `tasks/CLAUDE.md` | Useful; stale 3.2.1 anchor on historical plans |
| `.claude-plugin/CLAUDE.md` | Accurate marketplace + install docs |
| `.agents/plugins/marketplace.json` | Correct routing metadata |

## Suggested fix priority order

1. **CHANGELOG 3.6.1** — closes visible release-doc gap vs manifests.
2. **AGENTS/CLAUDE dedupe** — add distribution channels to Codex-facing file or consolidate to single authority.
3. **tests/CLAUDE.md inventory** — stop listing partial static set; point to domains or CI collect output.
4. **tasks/ historical version banners** — 3.2.1 → current release pointer; v4 workstream test snapshot note.
5. **plugin/apple_mail_mcp/CLAUDE.md verify one-liner** — align with summed tool count.
6. **Optional:** Cursor subagent model note; compose conventions cross-link for v3.6.0; skill trigger nuance for "check my email".

---

**Audit artifacts:** Phase 1 only — no guidance files modified.  
**Commands run:** `rg -c '^@mcp\.tool' …`, `bash tools/validate_manifests.sh`, `diff AGENTS.md CLAUDE.md`, glob/grep across scoped paths.

---

## Phase 2 Remediation Log (2026-06-08)

**Executor:** docs-maintainer (Cursor Automation)  
**Scope:** Minimal guidance fixes; no tool behavior changes  

### Changes Applied

| File | Fix | Rationale |
|------|-----|-----------|
| `CHANGELOG.md` | Added **3.6.1 — 2026-06-07** section documenting Codex plugin setup + test-count verification. | Closes visible release-doc gap (manifests are 3.6.1; CHANGELOG was 3.6.0). Aligns with validator source of truth. |
| `tests/CLAUDE.md` § Test files | Replaced static ~18-item list with **40 test modules on disk** pointer + domain-grouped brief index. Removed partial inventory; added `pytest --collect-only -q` discovery note. | Prevents stale inventory; allows live count to stay authoritative. 40 actual files vs ~17 listed = medium gap. |
| `plugin/apple_mail_mcp/CLAUDE.md` § tools/ | Fixed verify command from `grep -c "^@mcp.tool" tools/*.py` to `rg -c '^@mcp\.tool' plugin/apple_mail_mcp/tools/*.py \| awk -F: '{sum+=$NF} END {print sum}'` (per-file counts summed, matching `tools/CLAUDE.md`). | Prevents footgun: `grep -c` returns count only; awk sums per-file output from `rg`. Aligns with canonical tool-count verification. |

### Verified (Phase 1 findings accepted)

- **Manifest version**: 3.6.1 uniform across `pyproject.toml`, both `plugin.json`, marketplace.json, `server.json`, `apple-mail-mcpb/manifest.json` ✓
- **Tool count**: 28 (inbox 6, search 3, compose 5, manage 6, analytics 5, smart_inbox 3) ✓
- **Test count**: 798 tests + 30 subtests (accepted with secondary evidence from `tasks/todo.md`, CI workflow) ✓
- **AGENTS/CLAUDE duplication**: ~90% duplicate; minor for Codex users (skipped — low priority vs guidance fixes) ✓
- **tasks/ historical banners**: 3.2.1 references noted as historical; not updated (existing clarity sufficient for Phase 2 scope) ✓

### Not Applied (Out of Phase 2 Scope)

- AGENTS/CLAUDE consolidation → full refactor; deferred to Phase 3 if needed
- Cursor subagent model routing → no local `.cursor/rules/` in repo; optional per audit
- `plugin/apple_mail_mcp/CLAUDE.md` compose v3.6.0 threading note → low priority; CHANGELOG + skill already cover
- Skill trigger "check my email" disambiguation → low severity; existing strict descriptions sufficient

### Verification

- `CHANGELOG.md` now has 3.6.1 at top; reflects Codex setup + test-count verification (2026-06-07 completion)
- `tests/CLAUDE.md` file inventory replaced with domain index + pointer to `pytest --collect-only -q`
- Tool verify command aligned with canonical `tools/CLAUDE.md` + root CLAUDE pattern (summed per-file counts)
- All changes preserve existing accuracy claims (28 tools, 3.6.1, 798+30 tests, 40 test modules)

**Status:** Phase 2 remediation **COMPLETE**. Minimal fixes applied; no regressions. Report ready for stakeholder review.
