# Central marketplace source contract

**Branch:** `chore/central-marketplace-source-contract`
**Source repository:** `Agentic-Assets/apple-mail-mcp`
**Central marketplace:** `Agentic-Assets/Agentic-Assets-Marketplace`

## Objective

Make the Apple Mail source repository a fail-closed producer for the shared
multi-plugin marketplace without duplicating the marketplace control plane.
The source repository owns editable plugin development and signed releases.
The central marketplace owns plugin admission, catalog upserts, provenance,
client evidence, attestations, and rollback.

## To-do

- [x] Publish a machine-readable source payload contract.
- [x] Generate a deterministic payload inventory and digest.
- [x] Reject unclassified payload files, symlinks, secrets, authoring guides,
      dependency inputs, virtual environments, and generated provenance.
- [x] Centralize the direct-source and central marketplace identities.
- [x] Make `apple-mail@agentic-assets` the primary Agentic Assets install path.
- [x] Keep `apple-mail@apple-mail-mcp` as an explicit development and
      public-standalone compatibility path.
- [x] Add a signed annotated source-tag verifier and safe tag creator.
- [x] Add local commit and push blockers for release-sensitive changes.
- [x] Install the checked-in hooks in every local or cloud checkout with
      `bash tools/gates/install-git-hooks.sh`, then require readback of
      `core.hooksPath=.githooks` before commit or push.
- [x] Add behavioral and regression tests for the new contracts.
- [x] Run manifest, payload, task-layout, unit, lint, artifact, and offline
      release gates.
- [x] Run an independent adversarial review and resolve all P0-P2 findings.
- [x] Write a branch closeout and separate forward queue.
- [ ] Commit and push the feature branch. Do not merge without a new literal
      `Cayman approved this merge` authorization.

## Verification path

1. Focused payload, identity, release-tag, and hook tests.
2. `bash tools/gates/validate_manifests.sh`.
3. `python3 tools/validators/validate_tasks_layout.py`.
4. `bash tools/gates/dev-check.sh release`.
5. Verify `apple-mail-plugin.zip` and `apple-mail.plugin` are byte-identical.
6. Verify the source payload inventory is deterministic and excludes every
   source-only path.
7. Verify the existing signed `v3.11.6` tag only as a legacy expected-negative
   fixture for the new payload-binding contract. It predates the payload and
   binding trailers, so its historical signature alone is not a positive
   fixture. The first future release tag after this contract merges must pass
   the complete verifier. Do not create or move a release tag in this workstream.
8. Install the hooks and verify `git config --get core.hooksPath` returns
   `.githooks` before the first commit; run the source release gate immediately
   before push.

## Explicit boundaries

- Do not edit the promoted `plugins/apple-mail/` snapshot in the Marketplace.
- Do not copy source-repository tests, CI, generators, development
  environments, or administration scripts into the Marketplace payload.
- Do not make this repository claim the shared `agentic-assets` marketplace
  identifier in its root compatibility manifests.
- Do not add Git submodules.
- Do not run GitHub-hosted Actions. All CI-equivalent checks and release
  blockers run in the coding checkout before commit and push.
