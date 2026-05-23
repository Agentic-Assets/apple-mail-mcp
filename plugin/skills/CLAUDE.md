# plugin/skills/ — Agent skills directory

Skills are the **primary entry point** for email workflows in Claude Code. They teach the model when and how to call MCP tools — they do not implement tool logic.

New or edited skills: delegate drafting to subagents; run **`plugin-dev:plugin-validator`** and **`plugin-dev:skill-reviewer`** before merge. See root [`CLAUDE.md`](../../CLAUDE.md) § Agent orchestration.

## Skills-only policy

**Ship new entry points as skills only.** Do not add new files under `commands/`. The existing `commands/email-management.md` stays for backward compatibility; Claude Code may auto-convert commands to skills at install time, so authoring both is duplicative.

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

**Shared references:** Single-sourced large-inbox safety rules live in [`references/large-inbox-rules.md`](references/large-inbox-rules.md), included by `apple-mail-operator`, `inbox-triage`, `email-archive-cleanup`, and `email-management`.

Already-replied safeguard — honored by:

- `email-drafting/` — honors already-replied safeguard (pre-draft thread verification required before `reply_to_email`; never compose-as-reply).
- `inbox-triage/` — honors already-replied safeguard (default `include_already_replied=False`; pass `exclude_replied=True` on list/search).
- `email-management/` — honors already-replied safeguard (cross-references email-drafting for verification).
- `apple-mail-operator/` — honors already-replied safeguard (operator guidance for the new params and overrides).

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

## SKILL.md conventions (summary)

Follow the shipped `email-management/SKILL.md` as the canonical template for umbrella depth. Narrow skills can stay shorter if they still include: purpose, triggers, performance notes, sibling matrix, destructive red lines. Full rules: [`docs/CLAUDE-conventions.md`](../../docs/CLAUDE-conventions.md) (Skill authoring section).

- **Directory name == frontmatter `name`**
- **`description`**: third-person, 4–6 quoted triggers, name 3–5 central MCP tools, end with "Do NOT use for … (see sibling)"
- **Body**: imperative voice; no persona openers ("You are an expert…")
- **Length**: Larger skills target ~1,500–2,000 words; spill to `references/` as needed

## Before merging skill changes

Run **`plugin-dev:skill-reviewer`** on the description and body. Description quality determines whether the skill triggers at all.

## Related folders

- **`../commands/`** — Legacy slash command that delegates to `email-management/SKILL.md` via `${CLAUDE_PLUGIN_ROOT}`
- **`../../docs/CLAUDE-conventions.md`** — Deep skill-authoring and tool rules
- **`../apple_mail_mcp/tools/`** — MCP tools referenced by skills
- **`../.claude-plugin/plugin.json`** — Plugin manifest; skills auto-discovered from this tree
