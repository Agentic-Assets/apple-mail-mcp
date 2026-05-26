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
- [ ] 4. Docs, CLAUDE.md, skills, manifests synced (remaining drift)
- [ ] 5. skill-reviewer (if plugin/skills touched)
- [ ] 6. Rebuild release artifacts — `bash tools/dev-check.sh release` (rebuilds apple-mail-plugin.zip + .mcpb, runs full validators, runs mcpb unpack smoke). NEVER skip this step.
- [ ] 7. Final review checklist
- [ ] 8. Commit (default: yes, after release tier is green)
- [ ] 9. Push (default: yes, to current branch — open PR if branch is protected)
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

### 4. Sync documentation (delegate to `generalPurpose` subagent)

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

### 5. skill-reviewer (if plugin skills touched)

If step 4 edited any `plugin/skills/*/SKILL.md`, delegate to `plugin-dev:skill-reviewer` and apply wording fixes.

### 6. Rebuild release artifacts (required — never skip)

`apple-mail-plugin.zip` (Claude Code) and `apple-mail-mcp-v{VERSION}.mcpb` (Claude Desktop) must be regenerated from current sources before commit. Both ship with the repo and stale artifacts have caused real installer failures (e.g. Claude Desktop rejecting an MCPB built without `mcpb pack`).

```bash
bash tools/dev-check.sh release
```

That tier runs `validate_manifests` + `pytest` + the wrapper-surface check, then invokes `tools/build-artifacts.sh` to:

1. Rebuild `apple-mail-plugin.zip` with the README exclusion list (`venv`, `__pycache__`, `*.pyc`, `.DS_Store`, `CLAUDE.md`, `.env*`, logs, temp/backup files).
2. Rebuild `apple-mail-mcp-v{VERSION}.mcpb` via `apple-mail-mcpb/build-mcpb.sh` (which prefers official `mcpb pack`).
3. Re-run `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh`.
4. Run `mcpb unpack` + `mcpb validate` as a final structural smoke (if `mcpb` CLI present).

If any step fails, fix the underlying issue — do not commit stale artifacts.

### 7. Final review checklist

- [ ] plugin-validator PASS after fixes
- [ ] `tools/dev-check.sh release` finished green (artifacts rebuilt, `mcpb unpack` smoke OK, `claude plugin validate --strict` OK when CLI is available)
- [ ] `apple-mail-plugin.zip` and `apple-mail-mcp-v{VERSION}.mcpb` modified time newer than every changed plugin source
- [ ] Behavior described in docs matches `compose.py` / other tool defaults
- [ ] No stale "open by default" or subject-matching guidance where `message_id` is preferred
- [ ] No skill suggests `compose_email` / `create_rich_email_draft` / `manage_drafts(action="create")` for replies; `standalone_confirmed=True` is documented where standalone-with-Re: is legitimate
- [ ] `email-drafting` and `apple-mail-operator` skills agree with README draft-safe section
- [ ] No secrets or local paths committed
- [ ] Unrelated dirty files left unstaged

### 8. Commit and push (default: yes — close the loop yourself)

Once steps 1-7 are green, **commit and push without waiting to be asked**. The user's standing preference is that finalize closes its own loop. Pause and ask only when there is genuine ambiguity (unrelated WIP in the tree, secrets in staged paths, partial implementation, or a force-push would be required).

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

If shipping a version bump, bump all five version files together (root `CLAUDE.md` § Version bump), re-run plugin-validator, then `bash tools/dev-check.sh release` (which rebuilds both artifacts and runs the structural mcpb-unpack smoke).

## Additional resources

- Deep conventions: [docs/CLAUDE-conventions.md](../../docs/CLAUDE-conventions.md)
- Live verification: [docs/AGENT_LIVE_TESTING.md](../../docs/AGENT_LIVE_TESTING.md)
