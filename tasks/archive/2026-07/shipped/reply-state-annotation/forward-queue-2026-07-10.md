# Reply-State Annotation forward queue (2026-07-10)

Deferred follow-ups from the v3.11.0 lane. None block the release.

1. **`quick-check` threshold scaling.** Static per-case thresholds (metadata 2s,
   inbox 5s) miss on a 100-mailbox account (3.4s / 6.9s measured live). The
   inbox case now includes the default draft snapshot (~2s/account). Either
   scale thresholds by mailbox/account count, or add a
   `include_draft_state=False` baseline case so the smoke separates base list
   cost from snapshot cost.
2. **Per-copy answered-flag caveat.** Mail stores `was replied to` per mailbox
   copy; `get_email_thread` can resolve a different physical copy ("All Mail")
   than `list_inbox_emails` / `get_email_by_id` (INBOX) and report a different
   `was_replied_to` for the same `message_id`. Add one sentence to
   `docs/CLAUDE-conventions.md` and the canonical `pre-draft-verification.md`
   (requires re-sync + artifact rebuild, so batch with the next shipped change).
   The safety precedence is unaffected: any true signal on any surface aborts
   drafting.
3. **`draft_scan.accounts` schema alignment.** `get_needs_response` returns a
   plain string list; the other annotated tools return
   `{"account","status","scanned"}` dicts. Public JSON contract change; align
   in the next minor with a CHANGELOG note and test updates
   (`tests/smart_inbox/test_smart_inbox_json.py` pins the current shape).
4. **Open caveat from the plan:** unconfirmed whether Mail flips the native flag
   when a reply draft is opened versus only on send. The precedence rule keeps
   guidance correct either way; confirm with a disposable message when
   convenient.
5. **`manage_drafts` / `verify_draft` localized Drafts resolver.** The new
   name-fallback resolver in `core/reply_state.py` ("Drafts", "Brouillons",
   "Entwürfe", "Borradores") should replace their single-name lookup (existing
   roadmap Hardening item).
6. **`tasks/todo.md` / `tasks/INDEX.md` rows for this lane.** Deferred because
   the unmerged `chore/tasks-roadmap-refresh` branch rewrites both files; add
   the lane row when that branch resolves.
