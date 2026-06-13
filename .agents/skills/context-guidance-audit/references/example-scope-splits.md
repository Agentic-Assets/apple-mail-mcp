# Example scope splits

Use these as **templates**, not requirements. Discover your repo’s actual guidance paths before assigning lanes.

## Generic 8-lane pattern

| # | Slug | Typical scope (adapt paths) | Common findings |
|---|------|---------------------------|-----------------|
| 01 | `root-and-docs` | Root agent instructions + docs index/policy | Broken links, duplicate indexes, strategy hierarchy unclear |
| 02 | `app-and-api` | Application route/page layer context files | Handler rules applied to pages; stale routes |
| 03 | `lib-and-ui` | Domain libraries + UI component context files | Stale migration notes; wrong canonical APIs |
| 04 | `tasks-agents-tooling` | Task trackers, agent dirs, IDE rules, commands | Symlinks; script drift; conflicting discovery docs |
| 05 | `architecture-guides` | Architecture docs, maintainer guides, workflow docs | Docs describe unimplemented design; wrong commands |
| 06 | `docs-site` | Static/docs-site plan + content inventory | Inventory vs on-disk pages; outdated generator paths |
| 07 | `skills-inventory` | Skill names referenced across all guidance | Nonexistent skills; plugin vs local mismatch |
| 08 | `product-and-ops` | User guides, runbooks, platform overviews | UI naming vs app; wrong URLs; API tables |

**Phase 2:** Lane `N` reads `0N-*.md` and edits **only** that lane’s scope.

**Smaller pass (4 lanes):** Merge (01+08), (02), (03+07), (04+05+06) — or audit one subtree only.

---

## Example: large Next.js app with module CLAUDE files

*One real layout (EQUIRE-style). Your repo may use different folder names, fewer nested `CLAUDE.md` files, or no `.cursor/` symlink pattern.*

| # | Slug | Example paths from that project | Example findings seen there |
|---|------|--------------------------------|-----------------------------|
| 01 | `root-and-docs` | `CLAUDE.md`, `AGENTS.md`, `README.md`, `docs/**` | Codex link moved to subfolder; platform overview vs strategy category language |
| 02 | `src-app-and-api` | `src/app/**/CLAUDE.md`, `src/app/api/README.md` | `NextResponse.json` in page-level rules; missing `unstable_retry` note |
| 03 | `src-lib-and-components` | `src/lib/**/CLAUDE.md`, `src/components/**/CLAUDE.md`, `src/hooks/CLAUDE.md` | Phase table said “not shipped” but routes persisted; wrong DCF helper name |
| 04 | `tasks-agents-and-crossrefs` | `tasks/**`, `.cursor/rules/`, `.claude/commands/`, root § subagent discovery | `.cursor/agents` symlink pointed at another machine; INDEX vs `todo.md` drift |
| 05 | `coding-guides-and-architecture` | `docs/coding-agent-guides/`, `docs/workflows/`, `docs/architecture/` | Architecture doc still “planned” while `src/lib/prompts/` existed |
| 06 | `fumadocs-and-docs-site` | `docs/documentation-page/`, Fumadocs skill, `content/docs/` | Content inventory listed stub pages that were already written |
| 07 | `skills-inventory-and-references` | All `CLAUDE.md` + `.mdc` skill strings vs `.agents/skills/` | References to `valuation-engine` skill that did not exist on disk |
| 08 | `product-operations-and-platform-docs` | `docs/product/`, `docs/operations/`, root `platform-overview.md`, etc. | User guide said “Extracted Data” tab; UI showed “Deal Data” |

That project’s strategy north star lived under `docs/goals-northstar-strategies/` — use **your** canonical strategy path when checking positioning drift.

### Example verification (that project)

```bash
pnpm lint:docs-links          # if the repo defines it
readlink .cursor/agents       # if using Cursor symlink pattern
rg --files .claude/agents .agents/agents .cursor/agents
```

Replace commands with whatever the target repo documents.
