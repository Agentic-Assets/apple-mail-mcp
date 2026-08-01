# Forward queue after the v3.11.6 Cursor adapter (2026-07-15)

Candidate follow-up work. This is a menu, not a roadmap.

## Marketplace and client evidence

**Resolution update (2026-08-01):** the v3.11.6 source branch merged on
`origin/main` and its immutable tag exists. The remaining work is external
Marketplace admission and client evidence, not another source-release merge.

- **Regenerate central marketplace admission from v3.11.6** (priority: high; confidence: verified prerequisite)
  Promote only a trusted signed source tag and rerun Codex, Claude, Cursor,
  and macOS Mail evidence against the resulting digest.
- **Prove Cursor marketplace/UI distribution** (priority: high; confidence: verified evidence gap)
  Import the corrected official-schema catalog through Cursor's current team
  marketplace surface and capture install, update, rollback, and 41-tool
  runtime evidence. Do not substitute `--plugin-dir` proof for this lane.

## Hardening

- **Keep local-only gates authoritative** (priority: high; confidence: standing policy)
  Preserve pre-commit/pre-push and session-local release verification. Do not
  introduce GitHub Actions or hosted CI as the development blocker.
- **Retest host path variables on client upgrades** (priority: medium; confidence: compatibility risk)
  Re-run isolated Claude, Codex, and Cursor launcher smokes when any client
  changes its plugin loader contract.
