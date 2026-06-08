# Context Guidance Audit — automation prompt

Run a full **context guidance audit** on this repository.

## Step 0 — Invoke the skill

**First:** Read and follow the skill at the path that matches where you are working:

- **In this Agent-Skills repo:** `skills/context-guidance-audit/SKILL.md`
- **In another codebase:** `.agents/skills/context-guidance-audit/SKILL.md` (or that repo’s equivalent path for `context-guidance-audit`)

Treat `SKILL.md` as the source of truth for phases, subagent rules, report format, and cleanup.

**If the skill is not in the codebase you are auditing:** copy it in before continuing — do not improvise the workflow from memory.

1. **Canonical source:** [Agent-Skills `skills/context-guidance-audit/`](https://github.com/Agentic-Assets/Agent-Skills/tree/main/skills/context-guidance-audit) on `main`.
2. **Copy the full folder** into the target repo at `.agents/skills/context-guidance-audit/` (create parent dirs if needed), including:
   - `SKILL.md`
   - `automation-prompt.md`
   - `references/example-scope-splits.md`
3. **Prefer a local checkout** when available (e.g. sibling `Agent-Skills/skills/context-guidance-audit/` under the Agentic Assets GitHub folder). Otherwise fetch from GitHub (`git clone`, `gh api` raw files, or equivalent).
4. After copy, **read `SKILL.md`** and proceed with this automation.

Discover guidance artifacts before scoping (`Glob **/CLAUDE.md`, root `AGENTS.md` / `CLAUDE.md`, `.cursor/`, `.agents/`, docs index). Map **recent codebase changes** to nearby guidance and ensure **at least one or two Phase 1 scopes** cover those areas. **Do not assume** this repo matches any example layout in the skill references.

## Step 1 — Phase 1 (read-only audit)

1. Create `docs/reviews/context-guidance-audit-YYYY-MM-DD/` (or the repo’s standard reviews path with today’s date).
2. Split the repo into **4–8 disjoint scopes**, including **≥1–2 lanes for recently changed code paths** and their guidance (see skill reference `references/example-scope-splits.md` for patterns).
3. Launch **one subagent per scope in parallel** — each on **Composer 2.5** (`composer-2.5`), or the latest Composer generation available in Cursor if newer. Each subagent must:
   - **Not edit** any guidance files
   - Verify findings against the live codebase
   - Write its report to `NN-<scope-slug>.md` using the skill’s findings table template
4. **Wait until every Phase 1 subagent finishes** before continuing.
5. Optionally write `SYNTHESIS.md` with top issues and fix batch order. **Do not remediate yet** unless the user already asked to audit and fix in one run.

## Step 2 — Gate

If the user has not already approved fixes, **stop after Phase 1**, summarize findings, and ask permission to proceed to Phase 2. If this automation is configured for end-to-end runs, continue to Step 3.

## Step 3 — Phase 2 (verify + remediate)

1. Launch **one subagent per Phase 1 report**, same scope boundaries, in parallel where scopes do not overlap — each on **Composer 2.5** (`composer-2.5`), or the latest Composer generation available in Cursor if newer.
2. Each subagent must:
   - Re-read its audit report
   - **Verify** each finding; skip false positives
   - Apply **minimal** edits to guidance files only (no application code unless explicitly in scope)
   - Optimize for **clear, concise** agent-facing documentation
   - Append `## Remediation log` to its report (fixed / skipped / deferred)
3. **Wait until every Phase 2 subagent finishes.**
4. Run any repo-documented verification for doc/link changes if paths were updated.

## Step 4 — Close out

1. Give the user a short summary: what was fixed, what was deferred, and any follow-ups.
2. **Delete** the entire `docs/reviews/context-guidance-audit-YYYY-MM-DD/` folder after the user accepts the remediation (or immediately if the automation is configured for unattended cleanup).

## Orchestration rules

- Use **many focused subagents**, not one full-repo pass.
- **Cursor subagent model:** **Composer 2.5** (`composer-2.5`) for every subagent unless the user names another model; if a newer Composer generation is available, use the latest Composer model instead.
- Phase 1 = reports only. Phase 2 = fixes only.
- Subagents that must write files need **write-capable** delegation; do not rely on read-only explorers unless the orchestrator saves their output.
- Do not commit unless the user or automation explicitly requests it.
