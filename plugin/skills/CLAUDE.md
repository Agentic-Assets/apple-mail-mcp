# plugin/skills/ — Agent skills directory

Skills are the **primary entry point** for email workflows in Claude Code. They teach the model when and how to call MCP tools — they do not implement tool logic.

**Agent contract (read first):** [`references/agent-id-first-workflow.md`](references/agent-id-first-workflow.md)

New or edited skills: delegate drafting to subagents when available and permitted; run **`plugin-dev:plugin-validator`** and **`plugin-dev:skill-reviewer`** before merge when available. If not, document the gap and run local validation. See root [`CLAUDE.md`](../../CLAUDE.md), Agent orchestration section.

## Skills-only policy

**Ship entry points as skills only.** Do not add files under `commands/`. The old `/email-management` slash command was retired because Claude-style hosts can surface commands beside skills, creating duplicate/confusing entry points.

## Shipped skills

| Directory | Purpose |
|-----------|---------|
| `apple-mail-operator/` | MCP + Mail bootstrap, account/mailbox introspection, safe navigation, performance |
| `inbox-triage/` | 5–10 min daily read-first scan (needs-response / awaiting-reply) |
| `email-management/` | Umbrella sustained Inbox Zero habits and cross-cutting workflows |
| `mailbox-taxonomy/` | Folder strategy, noise diagnosis, `create_mailbox` after approval |
| `email-archive-cleanup/` | Staged archive / bulk move / trash with dry runs and exports |
| `mail-rules-advisor/` | Mail filter / rule **proposals only** (no rule-creation MCP tool) |
| `email-drafting/` | Compose, reply, forward, rich drafts; respects `--draft-safe` |
| `email-style-profile/` | Learn writing voice from Sent mail + `USER_EMAIL_PREFERENCES` |
| `email-attachments/` | List + save attachments safely |

**Shared references:** Canonical sources live in [`references/`](references/) at this directory level (maintainer edit + `python3 tools/sync_skill_references.py` to copy into each skill's `references/`). Packaged skills must only link in-skill paths like `references/large-inbox-rules.md`, never `../references/...` — enforced by `tests/test_packaged_skill_paths.py`.

| Canonical file | Synced into |
|----------------|-------------|
| `references/large-inbox-rules.md` | operator, triage, management, archive-cleanup, style-profile, attachments, mail-rules-advisor, mailbox-taxonomy |
| `references/pre-draft-verification.md` | operator, triage, management, email-drafting |
| `references/agent-id-first-workflow.md` | maintainer index only (not copied; link from this CLAUDE.md) |

Already-replied safeguard — canonical rules in [`references/pre-draft-verification.md`](references/pre-draft-verification.md); honored by:

- `email-drafting/` — full compose workflow and native reply defaults.
- `inbox-triage/` — default `include_already_replied=False`; pass `exclude_replied=True` on list/search.
- `email-management/` — cross-references pre-draft verification before replies in program workflows.
- `apple-mail-operator/` — hands off to `email-drafting` when navigation leads to a reply.

## Sibling routing cheat sheet

| User intent | Prefer |
|-------------|--------|
| MCP errors, which tool, timeouts | `apple-mail-operator` |
| Quick what's-new scan | `inbox-triage` |
| Ongoing zero-inbox program | `email-management` |
| Folder ontology / naming | `mailbox-taxonomy` |
| Bulk moves / trash / export | `email-archive-cleanup` |
| Filter text for Mail UI | `mail-rules-advisor` |
| Draft mail | `email-drafting` |
| Match my tone | `email-style-profile` → `email-drafting` |
| Save PDFs / zips | `email-attachments` |

**Reply drafting after triage or operator navigation:** `inbox-triage` and `apple-mail-operator` stay read-first. When the user wants a reply, hand off to **`email-drafting`**: `reply_to_email(message_id=..., reply_body=..., mode="draft")` with default `native_format=True` (Mail focus + Accessibility). On `REPLY_WINDOW_FOCUS_FAILED`, retry with Mail visible; do not switch to `native_format=False` (gated: `WINDOWLESS_FALLBACK_DISABLED` unless `allow_windowless_fallback=True`, which agents must never set). If focus still cannot be acquired, stop and report the blocker. Never pass `subject_keyword` to action tools; discover via `search_emails` / `list_inbox_emails` first.

## SKILL.md conventions (summary)

Follow the shipped `email-management/SKILL.md` as the canonical template for umbrella depth. Narrow skills can stay shorter if they still include: purpose, triggers, performance notes, sibling matrix, destructive red lines. Full rules: [`docs/CLAUDE-conventions.md`](../../docs/CLAUDE-conventions.md) (Skill authoring section).

- **Directory name == frontmatter `name`**
- **`description`**: third-person, 4–6 quoted triggers, name 3–5 central MCP tools, end with "Do NOT use for … (see sibling)"
- **Body**: imperative voice; no persona openers ("You are an expert…")
- **Length**: Larger skills target ~1,500–2,000 words; spill to `references/` as needed

## Before merging skill changes

Run **`plugin-dev:skill-reviewer`** on the description and body when available. Description quality determines whether the skill triggers at all.

## Related folders

- **`../../docs/CLAUDE-conventions.md`** — Deep skill-authoring and tool rules
- **`../apple_mail_mcp/tools/`** — MCP tools referenced by skills
- **`../.claude-plugin/plugin.json`** — Plugin manifest; skills auto-discovered from this tree
