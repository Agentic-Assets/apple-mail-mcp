# Forward queue after marketplace offline release candidate (2026-07-11)

## Release completion

- **Create and verify an SSH-signed `v3.11.3` tag** (required): bind the candidate commit to the Agentic Assets release signer, push it, and record the verified source SHA and tag object SHA in the marketplace provenance.
- **Promote and attest the candidate in Agentic-Assets-Marketplace** (required): use the staged payload digest and the externally verified source tag. Do not add it to any root marketplace manifest until all platform evidence is bound.
- **Collect client acceptance evidence** (required): record concrete acceptance for each claimed client surface, including Claude, Cursor, Codex, ChatGPT, and macOS Mail.app. Do not extrapolate from the local validator.

## Follow-up hardening

- **Publish a compatible runtime matrix** (priority: medium): the bundled wheelhouse is macOS arm64 CPython 3.13 only. Decide whether future releases should add other supported Python or architecture wheelhouses rather than relying on installation-time network access.
- **Use a release-version variable in docs** (priority: low): the current README now matches 3.11.3. A generated documentation fragment could eliminate future stale version references.
