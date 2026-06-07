# Active Pointer — apple-mail-mcp

**Branch:** `fix/v3.6.0-compose-race-and-draft-lookup` (stacked on the 3.5.0 field-report work; not yet merged).

**Active workstream:** v3.6.0 compose-path race elimination + reliable draft lookup, from a 2nd live draft-QA session. The 3.5.0 `saving no` change was insufficient — reply/forward were driving Mail's GUI (window + clipboard + positional `close window 1`), causing duplicate drafts, an empty-body draft, a cross-thread body leak (one reply's body landed in an unrelated thread), and dropped reply-all recipients. Drafts search was also slow/missing new drafts.

**What shipped:** reply_to_email + forward_email rebuilt on the object model (`make new outgoing message`, no window/clipboard/System Events, single save); deterministic reply-all recipients; `compose_email` HTML path hardened to `window of newMsg`; `manage_drafts(action="list")` + draft lookup read newest-first with a new `subject_contains` filter and no date dependency. Trade-off: replies are now plain-text "Re:" drafts without native In-Reply-To headers.

**Next action:** push branch + open PR; await "Cayman approved this merge" before merging. Earlier 3.5.0 PR is #17.

**Latest verification (2026-06-05):** `bash tools/dev-check.sh release` green; `validate_manifests OK (version=3.6.0, tools=28)`; 795 passed + 30 subtests; all 11 rendered compose scripts compile via `osacompile`; zip/.plugin byte-identical. **Live verification still pending on the user's TU Exchange Mail** (the GUI races only reproduce live) — user is testing.

**Blockers / caveats:** replies lose native In-Reply-To/References threading (visual "Re:" threading only) — a deliberate reliability trade-off; revisit a native-threaded path later if desired. `manage_drafts(action="cleanup_empty")` still scans oldest-first (minor; the empty-draft generator is now fixed). See project memory `exchange-applescript-footguns.md` and `compose-must-use-object-model.md`.
