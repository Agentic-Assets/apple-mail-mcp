# Codex Goal - Apple Mail Scripting Audit

## Goal

Audit Apple Mail MCP for Mail.app AppleScript design quality using the repo-local `mail-scripting-dictionary` skill as the controlling lens. The end state is an evidence-backed audit plus high-confidence fixes that make the plugin simpler, safer, and aligned with Mail's local scripting dictionary. Focus on code paths that create, save, verify, read, search, move, archive, delete, reply to, and attach files to messages. Confirm that dictionary-backed commands and writable properties are used where available, and that UI scripting, clipboard use, focus assumptions, broad scans, or subject-only matching are removed, justified, or tested.

## Boundaries

Work in `/Users/caymanseagraves/Documents/GitHub/agentic-assets/apple-mail-mcp` on a feature branch. Read `AGENTS.md`, relevant `CLAUDE.md` files, `.agents/skills/mail-scripting-dictionary/SKILL.md`, and its source reference before changing code. Use `/System/Applications/Mail.app/Contents/Resources/Mail.sdef` as the local contract for Mail terms and read/write access. Keep this developer-only skill out of packaged plugin skills and manifests. Never auto-send email. Do not use AppleScript fallback outside the MCP implementation. Preserve unrelated dirty files. Do not push to `main` or open a PR by API.

## Iteration Policy

Start with a repo audit map, then work in priority order. First inspect `compose.py`, the native reply builder, attachment handling, signature handling, and Drafts verification. Next inspect other Mail AppleScript builders and shared helpers. Search for `System Events`, `NSPasteboard`, `keystroke`, `clipboard`, `opening window`, `reply to all`, `message signature`, `content of`, `save`, `close`, broad Drafts scans, and subject-only cleanup. Use focused subagents for search, tests, docs, and plugin validation when useful. Patch only when evidence shows a reliability, simplicity, or documentation issue. If a path is correct, record why.

## Verification

For each changed AppleScript path, verify syntax or behavior at the smallest safe level. Prefer unit tests that inspect generated AppleScript and mocked Mail outputs. Add or tighten tests for reply body above quote, exact Drafts id verification, attachment preserving reply body, `include_signature=false` body insertion, structured artifact ids, bounded scans, and no UI paste in safe paths. Run focused tests, then `bash tools/dev-check.sh release`. If available and safe, run `.venv/bin/apple-mail quick-check --json`; live draft smokes must never send and must clean up by exact Drafts id. Confirm manifest validation still blocks developer-only skills from packaged surfaces.

## Deliverables

Produce a dated audit file under `tasks/` or `docs/` listing findings, decisions, fixes, and deferred risks. Include Apple official source URLs and local `Mail.sdef` landmarks when they justify a decision. Commit changes only after verification passes. Keep the final summary evidence-first: changed files, fixed risks, tests run, live smoke result, gaps, and branch status. If no code changes are needed, still deliver the audit and verification evidence.

## Blocked Stop Condition

Stop and report if Mail's local dictionary conflicts with current assumptions, a live Drafts smoke cannot be made safe, exact-id cleanup cannot be verified, required plugin-dev validation is unavailable after reasonable attempts, or a fix requires broad behavior changes outside the AppleScript/MCP/tool contract. Summarize the blocker, evidence, safest next action, and any artifacts needing manual cleanup by exact id.
