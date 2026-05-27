---
name: "finalize-apple-mail-mcp"
description: "Final codebase review and doc/manifest sync for apple-mail-mcp after feature work. Starts with plugin-validator to fix manifest and doc drift, then pytest, CLAUDE.md/README/skills/MCPB sync, and commit/push when the user asks. Use when finishing a change, before release, when the user says finalize, sync docs, update manifests, or ship the branch."
---

# Finalize apple-mail-mcp

Run this **after implementation is done** and before calling the branch finished. Orchestrate with subagents; do not solo large doc/manifest sweeps.

## When to use

- User finished a feature/fix and wants docs, guides, and manifests aligned
- User says: finalize, ship, sync docs, update CLAUDE.md, validate manifests, pre-release check
- Before opening a PR or tagging a release

## Recommended skills for the change being finalized

Pick by what the diff actually touched — don't run all of them. Each is
either a Skill (run inline) or an Agent (delegate via Task). The dev-mode
hook in `.claude/hooks/dev_mode_reminder.sh` reflects the same map.

| If the diff touched… | Use |
|----------------------|-----|
| AppleScript inside Python f-strings (`tools/*.py`, `core.py`) | The `.claude/hooks/check_applescript_compiles.py` parse check fires automatically on edit. Live-verify on TU Exchange (`apple-mail awaiting-reply --account "TU - Cayman" --days 7 --limit 5`) before ship. |
| Perf-sensitive paths (`smart_inbox.py`, `analytics.py`, large-inbox loops) | `python-performance-optimization` skill |
| Timeout subdivision, retry/backoff, `AppleScriptTimeout` handling | `python-resilience` skill |
| Silent `except` / `on error` skips, `errors[]` surfacing, partial-failure JSON | `python-error-handling` skill |
| New tests, missing test coverage, parser-vs-script gaps | `testing-python` or `python-testing-patterns` skill |
| `asyncio` fan-out, `asyncio.run()`-in-loop bugs | `async-python-patterns` skill |
| Pre-ship review pass | `reviewing-code` + `code-review` skills; `python-anti-patterns` as checklist |
| Confirming a change actually works in the running app | `verify` + `run` skills |
| Plugin manifest / marketplace / MCPB drift | `plugin-dev:plugin-validator` agent (REQUIRED — step 1 below) |
| `plugin/skills/*/SKILL.md` wording or triggers | `plugin-dev:skill-reviewer` agent |

## Out of scope

- New feature implementation
- Version bump across five files unless user explicitly requests a release
- Force push or amending pushed commits

## Workflow

Copy and track:

```
Finalize progress:
- [ ] 1. plugin-validator — run and fix all reported issues
- [ ] 2. Scope the diff (what changed, why)
- [ ] 3. Code + tests verified
- [ ] 4. code-simplifier — pass over the diff (REQUIRED for any non-trivial change)
- [ ] 5. Docs, CLAUDE.md, skills, manifests synced (remaining drift)
- [ ] 6. skill-reviewer (if plugin/skills touched)
- [ ] 7. Rebuild release artifacts — `bash tools/dev-check.sh release` (rebuilds **all three** artifacts: `apple-mail-plugin.zip` + `apple-mail.plugin` + `apple-mail-mcp-v{VERSION}.mcpb`, runs full validators including byte-parity check, runs mcpb unpack smoke). NEVER skip this step.
- [ ] 8. Final review checklist
- [ ] 9. Commit (default: yes, after release tier is green)
- [ ] 10. Push (default: yes, to current branch — open PR if branch is protected)
```

### 1. plugin-validator first (required)

**Delegate immediately** to `plugin-dev:plugin-validator` (Task `subagent_type="plugin-validator"`). Do not run pytest or doc sweeps before this step completes.

Prompt must include:

- Full validation pass (manifests, tool counts, versions, MCPB parity, plugin structure)
- **Fix every blocker and every fixable warning** in-repo (doc test counts, stale MCPB descriptions, manifest args drift, etc.)
- Re-run `bash tools/validate_manifests.sh` and report PASS/FAIL after fixes

If the validator reports **FAIL** or cannot fix something, stop finalize and surface blockers to the user. Do not proceed to step 2 until plugin-validator ends at **PASS** or the user accepts known exceptions.

### 2. Scope the change

```bash
git status
git log --oneline -5
git diff main...HEAD --stat
```

Identify touched areas: `plugin/apple_mail_mcp/tools/`, `plugin/skills/`, `tests/`, manifests, `README.md`, `docs/`.

### 3. Verify code (delegate to `shell` subagent)

From repo root with `.venv/`:

```bash
.venv/bin/pytest tests/ -q
bash tools/validate_manifests.sh
.venv/bin/pytest tests/test_validate_manifests.py tests/test_wrapper_surface.py -q
```

Optional when tools or CLI changed:

```bash
bash tools/pre-commit-validate.sh
.venv/bin/apple-mail quick-check --json   # live Mail smoke (~30s)
```

All must pass before updating any remaining doc claims.

### 4. code-simplifier (REQUIRED for any non-trivial change)

Delegate to the **`code-simplifier:code-simplifier`** agent (Task
`subagent_type="code-simplifier:code-simplifier"`). This is non-optional
for any change beyond a one-line bugfix — root `CLAUDE.md` § Agent
orchestration mandates it as part of every "ready to ship" pass.

Scope the agent to the **recently-modified files** in the diff (it
defaults to recent changes; pass explicit paths when the diff is large):

- Behavior must be preserved — pytest after the simplifier pass must
  match the pytest results from step 3.
- The simplifier collapses duplication, drops dead branches, tightens
  names; it does NOT redesign abstractions.
- Especially important after refactors touching many call sites
  (capability-token, structured-error, bounded-scan-style work), any
  file that grew past ~600 LOC, or any helper with >3 near-copies.
- If the simplifier returns edits, re-run pytest before continuing.

Skip only when: the diff is a one-line bugfix, a manifest version bump,
or docs-only edits with zero Python changed.

### 5. Sync documentation (delegate to `generalPurpose` subagent)

Update **only** what the code change still affects after step 1. Do not rewrite unrelated files.

| If you changed… | Update |
|-----------------|--------|
| MCP tools (`@mcp.tool`, params, defaults) | `plugin/apple_mail_mcp/tools/CLAUDE.md`, tool docstrings, `README.md` tool table, `docs/CLAUDE-conventions.md`, `apple-mail-mcpb/manifest.json` `tools[].description` |
| Plugin wiring / flags | `plugin/docs/CLAUDE.md`, `plugin/apple_mail_mcp/CLAUDE.md`, `README.md` Configuration |
| Agent workflows | `plugin/skills/*/SKILL.md`, `plugin/skills/CLAUDE.md`, `docs/CLAUDE.md` skill map |
| Test count | Root `CLAUDE.md`, `README.md`, any doc citing test totals — use `pytest tests/ -q` result from step 3 |
| Tool count | Five version files only on release; always sync **claims**: `grep -c '^@mcp.tool' plugin/apple_mail_mcp/tools/*.py` vs `plugin.json`, marketplace, MCPB `tools[]` |

**CLAUDE.md hubs to spot-check** (stale cross-links or wrong counts):

- `CLAUDE.md` (root)
- `plugin/docs/CLAUDE.md`, `plugin/apple_mail_mcp/CLAUDE.md`, `plugin/apple_mail_mcp/tools/CLAUDE.md`
- `plugin/skills/CLAUDE.md`, `tests/CLAUDE.md`, `tools/CLAUDE.md`, `docs/CLAUDE.md`
- `.claude-plugin/CLAUDE.md`, `apple-mail-mcpb/CLAUDE.md`, `tasks/CLAUDE.md`

**Manifest rules** (see `tools/CLAUDE.md`):

- Versions: `pyproject.toml`, `plugin/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` `plugins[0].version`, `server.json`, `apple-mail-mcpb/manifest.json`
- Do **not** bump `metadata.version` in marketplace.json
- MCPB `tools[]` names must match registered tool function names

### 6. skill-reviewer (if plugin skills touched)

If step 5 edited any `plugin/skills/*/SKILL.md`, delegate to `plugin-dev:skill-reviewer` and apply wording fixes.

### 7. Rebuild release artifacts (required — never skip)

**Three artifacts must regenerate together** from current sources before commit. All three ship with the repo, and drift between any of them has caused real installer failures.

| Artifact | Install path | Why drift breaks users |
|----------|--------------|------------------------|
| `apple-mail-plugin.zip` | Claude Code plugin marketplace | Stale bytes → users get an older tool surface than the manifest claims |
| `apple-mail.plugin` | Cowork → Customize → Add plugin → Upload plugin | Missing or diverged from the `.zip` → Cowork upload silently fails or installs stale code |
| `apple-mail-mcp-v{VERSION}.mcpb` | Claude Desktop chat "Add Custom Plugin" | Wrong version filename or directory entries → Desktop installer aborts |

```bash
bash tools/dev-check.sh release
```

That tier runs `validate_manifests` + `pytest` + the wrapper-surface check, then invokes `tools/build-artifacts.sh` to:

1. Rebuild `apple-mail-plugin.zip` with the README exclusion list (`venv`, `__pycache__`, `*.pyc`, `.DS_Store`, `CLAUDE.md`, `.env*`, logs, temp/backup files).
2. Copy the zip bytes to `apple-mail.plugin` so the Cowork artifact stays byte-identical to the marketplace zip.
3. Rebuild `apple-mail-mcp-v{VERSION}.mcpb` via `apple-mail-mcpb/build-mcpb.sh` (which prefers official `mcpb pack`).
4. Re-run `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh` — fails if any of the three artifacts is missing or the `.plugin` bytes diverge from the `.zip`.
5. Run `mcpb unpack` + `mcpb validate` as a final structural smoke (if `mcpb` CLI present).

If any step fails, fix the underlying issue — do not commit stale artifacts. **Never delete `apple-mail.plugin` or build it manually** — it must come from the build script's byte-copy, not a hand-zip, or the parity check rejects it.

### 8. Final review checklist

- [ ] plugin-validator PASS after fixes
- [ ] code-simplifier pass complete (or explicitly skipped per step 4 exceptions); pytest still green afterward
- [ ] `tools/dev-check.sh release` finished green (artifacts rebuilt, `mcpb unpack` smoke OK, `claude plugin validate --strict` OK when CLI is available)
- [ ] `apple-mail-plugin.zip`, `apple-mail.plugin`, and `apple-mail-mcp-v{VERSION}.mcpb` modified time newer than every changed plugin source
- [ ] `apple-mail.plugin` bytes == `apple-mail-plugin.zip` bytes (validator enforces; manual check: `cmp apple-mail-plugin.zip apple-mail.plugin`)
- [ ] Behavior described in docs matches `compose.py` / other tool defaults
- [ ] No stale "open by default" or subject-matching guidance where `message_id` is preferred
- [ ] No skill suggests `compose_email` / `create_rich_email_draft` / `manage_drafts(action="create")` for replies; `standalone_confirmed=True` is documented where standalone-with-Re: is legitimate
- [ ] `email-drafting` and `apple-mail-operator` skills agree with README draft-safe section
- [ ] No secrets or local paths committed
- [ ] Unrelated dirty files left unstaged

### 9. Commit and push (default: yes — close the loop yourself)

Once steps 1-8 are green, **commit and push without waiting to be asked**. The user's standing preference is that finalize closes its own loop. Pause and ask only when there is genuine ambiguity (unrelated WIP in the tree, secrets in staged paths, partial implementation, or a force-push would be required).

Stage focused paths; never `git add -A`.

```bash
git add <relevant paths>
git commit -m "$(cat <<'EOF'
<1-2 sentences: why, not what>

EOF
)"
```

**Push** as the closing action of finalize:

```bash
git push -u origin HEAD
```

If `HEAD` is on a protected branch (e.g. `main` with branch-protection rules), switch to a feature branch and open a PR with `gh pr create` instead — same default-to-action principle.

## Release note

If shipping a version bump, bump all five version files together (root `CLAUDE.md` § Version bump), re-run plugin-validator, then `bash tools/dev-check.sh release` (which rebuilds all three artifacts — `apple-mail-plugin.zip`, `apple-mail.plugin`, and the `.mcpb` — and runs the structural mcpb-unpack smoke plus the byte-parity check between the zip and `.plugin`).

## Additional resources

- Deep conventions: [docs/CLAUDE-conventions.md](../../docs/CLAUDE-conventions.md)
- Live verification: [docs/AGENT_LIVE_TESTING.md](../../docs/AGENT_LIVE_TESTING.md)
