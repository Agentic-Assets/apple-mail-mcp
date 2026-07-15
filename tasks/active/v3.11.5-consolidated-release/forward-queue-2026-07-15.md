# Forward queue after the v3.11.5 consolidated release (2026-07-15)

Candidate work surfaced during consolidation. This is a menu, not a roadmap.

## Release and marketplace

- **Admit Apple Mail to the central marketplace** (priority: high; confidence: verified prerequisite)
  After `v3.11.5` is merged and signed as an annotated immutable tag, run the central repository's promotion, evidence, admission, attestation, and final local verification workflow. Its current Claude and Codex plugin lists are empty by design.
- **Capture live client evidence** (priority: high; confidence: verified requirement)
  Install `apple-mail@agentic-assets` from the admitted central snapshot in isolated Claude and Codex homes, then record the exact version, source digest, 41-tool handshake, and offline runtime proof required by the central admission policy.
- **Exercise the Cursor client** (priority: medium; confidence: unverified surface)
  The Cursor adapter matches the current official manifest shape, but this session did not claim a live Cursor installation. Add client evidence before advertising Cursor marketplace support broadly.

## Compatibility

- **Evaluate an additional offline runtime matrix** (priority: medium; confidence: deliberate limitation)
  The bundled payload is Apple Silicon plus CPython 3.13. Measure demand for Intel Macs or another CPython minor before adding platform-specific payloads; do not silently reintroduce network installation.

## Cleanup

- **Close superseded branch PRs after the combined merge** (priority: medium; confidence: verified workflow)
  PRs #76 and #77, plus the two marketplace feature branches, remain useful provenance until the combined PR lands. Close or delete their remote branches only after `main` contains the consolidated merge.
- **Add shellcheck when approved** (priority: low; confidence: tooling gap)
  Shell syntax and behavioral tests passed, but `shellcheck` was unavailable locally. Treat it as an optional local tool addition that requires maintainer approval under the repository lint policy.
