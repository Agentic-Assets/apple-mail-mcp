# Marketplace release handoff

Use this page when a source release must reach the Agentic Assets Marketplace.
It is the compact operational path; the Marketplace repository owns its
admission policy, evidence, and attestation details.

## One source command

After the source PR is merged, the release gate is green, and the signed tag is
pushed, run:

```bash
bash tools/gates/marketplace-handoff.sh vX.Y.Z
```

The command verifies the tag signature and remote ref, then prints the exact
source commit, signed source inventory hash, Marketplace target, selector, and
next command. The Marketplace computes its own candidate payload digest during
preparation, so do not substitute the source inventory hash for Marketplace
evidence.
Use `--json` when passing the handoff to another tool or agent. It is read-only
and fails rather than using a local tag, an untrusted signer, or a mismatched
source identity.

## Short release sequence

1. On a clean source `main` checkout, run `bash tools/gates/source-release-gate.sh`.
2. Create and push the signed tag with `bash tools/gates/create-release-tag.sh --confirm-create`.
3. Run the handoff command above. Do not copy a commit or source inventory hash
   by hand, and let the Marketplace tool compute its candidate payload digest.
4. In a clean `chore/*` branch of `Agentic-Assets-Marketplace`, run the command
   printed by the handoff, currently `prepare_plugin_update.py --plugin apple-mail --prepare --next-steps`.
5. Complete only the Marketplace-owned stages: isolated evidence, admission,
   signed attestation, the Marketplace release gate, and a normal reviewed PR.
   Commit and push the candidate plus redacted proof files before admission so
   their immutable evidence URLs are reachable from `origin`.
6. After that PR merges, run `bash tools/gates/refresh-central-marketplace.sh`
   from this source checkout to update Claude Code and Codex on the release
   machine. Restart both clients.

The tag authenticates an immutable source payload. It does not by itself prove
Marketplace admission, client acceptance, or Cursor Team Marketplace UI support.
