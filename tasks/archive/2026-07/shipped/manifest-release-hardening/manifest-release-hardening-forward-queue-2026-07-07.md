# Forward queue after manifest release hardening (2026-07-07)

Candidate work surfaced during the version and artifact verification pass. This is a menu, not a roadmap.

## Hardening

- **Track local artifact parity in one command** (confidence: verified gap, priority: high)
  Add a lightweight command or make target that prints the release version, artifact names, and `zip` to `.plugin` byte parity in one place. The release gate already enforces this, but a quick diagnostic would make operator checks less error-prone.

- **Document ignored artifact expectations near `.gitignore`** (confidence: verified gap, priority: medium)
  `apple-mail.plugin` and `.mcpb` are intentionally ignored/local while `apple-mail-plugin.zip` is tracked. A short comment or docs pointer would reduce confusion when rebuilt ignored artifacts appear during release validation.

## Process / docs

- **Refresh stale active task pointer after PR merge** (confidence: verified gap, priority: medium)
  `tasks/todo.md` still carries historical shipped context from v3.9.1. After this PR merges, update it to either the next live workstream or a clean "no active release branch" pointer.

- **Separate marketplace metadata from plugin release docs** (confidence: hypothesis, priority: low)
  The Claude marketplace has `metadata.version` plus `plugins[0].version`. A one-sentence note in release docs could make clear that only `plugins[0].version` tracks the plugin release.

## Evaluation

- **Add a negative full-validator subprocess fixture later** (confidence: passing idea, priority: low)
  Current focused tests cover the version helper directly. A future integration fixture could run the full validator against a minimal temp repo with one drifted version surface, but it would need enough fake repo structure to avoid unrelated contract failures.
