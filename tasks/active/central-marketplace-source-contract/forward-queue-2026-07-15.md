# Central marketplace source contract forward queue

This queue records work that is intentionally outside the Apple Mail source
contract branch. It is not evidence that any external mutation has occurred.

## Apple Mail repository

1. Review and merge the source-contract PR only after Cayman gives fresh merge
   approval for that PR.
2. After merge, create the first future release through
   `tools/gates/create-release-tag.sh`; do not retrofit or move `v3.11.6`.
3. Before that release-sensitive push, run
   `bash tools/gates/source-release-gate.sh` from a clean, current `main`.
4. Preserve the direct `apple-mail-mcp` marketplace only as the documented
   standalone compatibility and development lane.

## Agentic Assets Marketplace repository

1. Make `provenance/plugin-policies.json` the authoritative multi-plugin
   registry and consume the Apple Mail source contract by plugin ID.
2. Promote only an allowlisted payload from a trusted signed source tag into
   `plugins/apple-mail/`; do not copy repository tests, CI files, generators,
   development environments, or source-only documentation.
3. Independently recompute and verify the payload, lock, and wheelhouse digests
   before admission. Source evidence alone must never grant admission.
4. Upsert Apple Mail in each platform catalog without replacing other plugin
   entries. Keep Claude, Codex, and Cursor manifests separate because their
   schemas differ.
5. Generalize promotion, evidence, validation, and attestation tooling around
   `--plugin <plugin-id>` before promoting Corbis.
6. Narrow the Apple Mail allowlist to the 235-file source contract, including
   the exclusion of `skills/**/README.md`, and add a drift regression.
7. Run every Marketplace check locally through checked-in commit/push blockers;
   do not add GitHub-hosted Actions.

## Client registration remediation

1. Verify the shared Marketplace repository and its Apple Mail catalog entry
   before changing local client state.
2. Inspect the existing Claude registration named `agentic-assets`, currently
   pointing at `Agentic-Assets/Corbis-Plugin`.
3. Back up the relevant Claude and Codex registration files or export their
   current state before any change.
4. Replace the same-name collision only with explicit Cayman authorization,
   then run `bash tools/gates/refresh-central-marketplace.sh`.
5. Confirm exactly one Claude installation scope, one Codex plugin installation,
   the Codex MCP launcher contract, and a live 41-tool runtime smoke.
6. Restart the desktop clients and separately record Marketplace UI acceptance;
   local CLI/runtime success is not proof of UI admission.
