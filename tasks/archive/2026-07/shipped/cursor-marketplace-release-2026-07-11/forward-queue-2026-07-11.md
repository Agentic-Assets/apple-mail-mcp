# Forward queue after Cursor marketplace source candidate (2026-07-11)

## Required release controls

- **Protect source release tags**: configure an immutable tag rule for `v*` before signing and pushing `v3.11.4`. The marketplace records both tag object and peeled commit identity, but source governance must prevent silent retagging.
- **Merge the source candidate before release**: review the feature branch under normal source-repository controls, then create the release tag from the reviewed commit.
- **Promote from the protected tag**: use the marketplace promoter on a fresh `chore/*` marketplace branch and record the generated payload digest.

## Client evidence

- **Run Cursor Team Marketplace acceptance**: use the digest-verified preview catalog, confirm the distinct Cursor adapter is installed, then run the draft-safe `list_accounts` smoke. Do not treat direct CLI MCP configuration as marketplace-selector proof.
- **Record all required surface evidence**: Codex CLI, Claude Code, Cursor local marketplace, and macOS Mail evidence must be saved at the canonical digest-bound path before admission.
