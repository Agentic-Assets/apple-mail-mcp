# Cleanup Docs and Simplify - 2026-06-08

## Goal

After the Codex launcher and Mail-native reply draft fixes shipped on `main`, simplify the plugin surface without changing core behavior:

- Retire stale duplicate entry points that confuse host skill discovery.
- Keep future agents pointed at Mail-native reply drafts, bounded Drafts verification, and the current signature contract.
- Preserve release validation so the simplification is hard to regress.

## Plan

1. Remove the legacy `plugin/commands/` surface and update validator coverage so it cannot reappear silently.
2. Refresh docs and task navigation that still described legacy slash commands, synthetic reply quoting, or normal full scans.
3. Make only low-risk code simplifications that preserve the tested native reply flow.
4. Run focused tests first, then full release validation and artifact rebuild.

