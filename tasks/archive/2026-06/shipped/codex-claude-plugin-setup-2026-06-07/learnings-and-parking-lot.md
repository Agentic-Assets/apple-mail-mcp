# Codex and Claude Plugin Setup Learnings and Parking Lot

## Durable Learnings

- Codex and Claude plugin manifests should stay host-specific. Corbis works because `.agents/plugins/marketplace.json` plus `.codex-plugin/plugin.json` are separate from `.claude-plugin/marketplace.json` plus `.claude-plugin/plugin.json`.
- For Apple Mail, the shared runtime should remain `plugin/start_mcp.sh --draft-safe`; Codex packaging should wrap that same launcher rather than creating a second runtime path.
- Codex install failure mode is concrete: marketplace discovery can work while plugin install fails with `missing or invalid plugin.json` if `.codex-plugin/plugin.json` is absent.
- Validator coverage is the real product here. Adding manifests without `tools/validate_manifests.py` checks would recreate the drift problems this repo already solved for Claude/MCPB.

## Parking Lot

- CI release-gate hardening: add a GitHub Actions packaging job that runs `bash tools/dev-check.sh release` or at least `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh` plus strict plugin validation when CLIs are available.
- Skill frontmatter tightening: several `plugin/skills/*/SKILL.md` descriptions are longer than the repo's own 4-6 trigger guidance. Defer unless skill files are otherwise touched in this workstream.
- Potential future generator: Corbis has a source-to-generated plugin builder. Apple Mail likely only needs small static manifests plus validator checks now; a generator can wait until a second Codex/Claude drift vector appears.
