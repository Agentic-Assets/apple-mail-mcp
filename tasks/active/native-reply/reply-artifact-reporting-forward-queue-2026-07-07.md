# Forward queue after reply artifact reporting (2026-07-07)

Candidate work surfaced during the #54 and AGENTIC-946 pass. This is a menu, not a roadmap. Verify each item before acting.

## Hardening

- **Live focus-failure smoke with cleanup** (confidence: verified gap)
  Add or document a safe live smoke that can intentionally trigger `REPLY_WINDOW_FOCUS_FAILED`, then verify whether `suspected_draft_id` is present and clean it up by exact Drafts id. This closes the remaining gap between deterministic tests and real Mail focus behavior.

- **End-of-body verifier sentinel** (confidence: issue-backed hypothesis)
  GitHub #53 reports clipped native reply bodies that still pass early-body verification. Add a final-line or end-of-body sentinel check in `_verify_saved_reply_draft` so success requires the tail of `reply_body` above the quote, not only the first non-empty line.

## Robustness

- **Draft artifact status docs** (confidence: verified follow-up)
  Update `plugin/skills/email-drafting/SKILL.md` and `docs/CLAUDE-conventions.md` to explain `draft_artifact_status`, `suspected_draft_id`, `draft_id_source`, and `captured_draft_id` after this branch lands.

- **Account-list timeout fixture** (confidence: verified gap)
  Add a unit test that patches `list_mail_account_names` to raise `AppleScriptTimeout` in the missing-account JSON branch. The live gate exposed the bug, but a deterministic fixture would keep it from returning.

## Simplification

- **Unify reply id payload helpers** (confidence: passing idea)
  The reply success payload and verification-line formatter both reason about exact ids versus fallback ids. After this branch lands, consider a small pure helper for id provenance if another caller needs the same contract.

## Evaluation

- **Reconcile GitHub #32, #35, #48, #53, and #54** (confidence: issue-backed)
  Several open issues overlap native reply identity, signature, body placement, and artifact ids. After this branch ships, audit which are fixed, partially fixed, or still active, then comment or split them so future work is not chasing stale symptoms.
