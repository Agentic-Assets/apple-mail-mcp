# Mail Scripting Dictionary Audit - 2026-06-19

## Scope

Audit Apple Mail MCP AppleScript paths against the repo-local
`mail-scripting-dictionary` skill and the local Mail scripting dictionary:

- `/System/Applications/Mail.app/Contents/Resources/Mail.sdef`
- `plugin/apple_mail_mcp/tools/compose.py`
- `plugin/apple_mail_mcp/tools/analytics.py`
- non-compose tool surfaces under `plugin/apple_mail_mcp/tools/`
- bundled workflow skills and user-facing docs that teach draft operations

This audit is developer-facing only. It does not add the developer skill to
packaged plugin skills.

## Source References

Official Apple references used:

- Apple Support, View an app's scripting dictionary in Script Editor on Mac:
  https://support.apple.com/guide/script-editor/view-an-apps-scripting-dictionary-scpedt1126/mac
- Apple Developer, Mac Automation Scripting Guide: About Scripting Terminology:
  https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/AboutScriptingTerminology.html
- Apple Developer, Mac Automation Scripting Guide: Opening a Scripting Dictionary:
  https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/OpenaScriptingDictionary.html
- Apple Developer, Mac Automation Scripting Guide: Navigating a Scripting Dictionary:
  https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/NavigateaScriptingDictionary.html
- Apple Developer, AppleScript Language Guide: AppleScript Fundamentals:
  https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleScriptLangGuide/conceptual/ASLR_fundamentals.html
- Apple Developer, AppleScript Language Guide: Reference Forms:
  https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleScriptLangGuide/reference/ASLR_reference_forms.html
- Apple Developer, Mac Automation Scripting Guide: Automating the User Interface:
  https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/AutomatetheUserInterface.html

Local `Mail.sdef` landmarks checked on this machine:

- `forward` returns `outgoing message`: `Mail.sdef:193-199`
- `reply` returns `outgoing message`; optional `opening window` and `reply to all` default false: `Mail.sdef:247-255`
- `outgoing message` has writable `content`, `message signature`, read-only `id`, and responds to `save`, `close`, `send`: `Mail.sdef:269-306`
- saved/source `message.content` is read-only rich text: `Mail.sdef:554-575`
- `mail attachment` exposes `file size`, not generic `size`: verified against local dictionary and existing `save_email_attachment` implementation

## Fixed In This Phase

1. Rich `.eml` draft save now uses Mail's object model.
   - Replaced `System Events`, `frontmost`, Cmd-S, and positional `close window 1` in `_save_front_compose_window_as_draft`.
   - New path saves `item 1 of outgoing messages` with `save targetMessage` and closes `(window of targetMessage) saving no` when requested.
   - Regression tests assert no `System Events`, no save keystroke, and no positional `window 1` close in that helper.

2. HTML compose post-actions now use Mail object commands where the dictionary supports them.
   - `mode="send"` uses `send newMsg` instead of Cmd-Shift-D.
   - `mode="open"` uses `save newMsg` instead of Cmd-S.
   - The remaining `NSPasteboard` and `System Events` path is limited to the HTML paste itself, because Mail's dictionary does not provide a reliable writable rich HTML body property and `html content` is deprecated/no-op in the local dictionary.

3. Attachment listing now uses the correct Mail attachment property.
   - Changed `list_email_attachments` from `size of anAttachment` to `file size of anAttachment`.
   - Added regression coverage so the generated AppleScript must use `file size of anAttachment`.

4. Standalone draft lifecycle now records exact artifacts.
   - `manage_drafts(action="create")` now calls `save newDraft`, then captures `id of newDraft` when Mail exposes it and prints `Draft ID`.
   - `manage_drafts(action="send"|"open"|"delete")` now accepts optional exact `draft_id`, prefers it over `draft_subject`, and reports the targeted `Draft ID`.
   - Tests cover the new parameter, exact-id lookup for send/open/delete, invalid-id rejection before AppleScript, explicit `save newDraft`, and emitted `Draft ID`.

5. Guidance was updated.
   - README, `docs/CLAUDE-conventions.md`, MCPB tool description, and bundled workflow skills now instruct agents to use exact `draft_id` from `manage_drafts(action="list")` for send/open/delete operations.

## Already Aligned

- Native reply drafts are dictionary-backed through `reply foundMessage`.
- Draft-mode replies omit `with opening window`; open-mode replies use the affirmative `with opening window` form.
- Reply-all uses the affirmative `with reply to all` form.
- `include_signature=False` sets `message signature of replyMessage to missing value` and does not skip body insertion.
- Reply body verification checks the exact Drafts id first, then bounded newest Drafts fallback, and treats body after quote as failure.
- Non-compose tools had no `System Events`, clipboard, or keystroke paths.
- Message scans use bounded slices or exact-id `whose` lookups guarded by existing no-unbounded-`whose` tests.

## Deferred Risks

1. HTML body paste remains UI-dependent.
   - Reason: Mail's dictionary supports plain/rich text `content` but not reliable HTML body insertion; `.eml` remains the preferred rich-draft path.
   - Current mitigation: UI paste is isolated in `_send_html_email`; object model handles recipients, attachments, save, and send after paste.

2. Broad mailbox enumeration remains in some mailbox-summary paths.
   - These are dictionary-backed and lower risk than broad message scans, but they can still be slow on accounts with many folders.
   - Future improvement: where AppleScript allows it, bind capped mailbox ranges instead of `every mailbox` followed by slicing.

3. Filter-based destructive message operations remain workflow-risky.
   - `move_email`, `manage_trash`, and `update_email_status` prefer exact `message_ids` and require explicit filter-scan opt-in.
   - Future improvement: for `dry_run=False` filter paths, require a second confirmation tied to previewed ids.

## Verification

Focused checks run during this phase:

```bash
.venv/bin/pytest tests/test_compose_tools.py -q
.venv/bin/pytest tests/test_applescript_builders_compile.py tests/test_applescript_script_idioms.py -q
.venv/bin/pytest tests/test_analytics_resource_safety.py tests/test_phase_2_scan_hardening.py -q
.venv/bin/pytest tests/test_no_unbounded_whose.py tests/test_bounded_scan_contract.py tests/test_applescript_builders_compile.py -q
.venv/bin/pytest tests/test_compose_tools.py tests/test_compose_none_handling.py -q
.venv/bin/ruff check plugin/apple_mail_mcp/tools/compose.py plugin/apple_mail_mcp/tools/analytics.py tests/test_compose_tools.py tests/test_analytics_resource_safety.py
.venv/bin/mypy --strict plugin/apple_mail_mcp/tools/compose.py plugin/apple_mail_mcp/tools/analytics.py
.venv/bin/pytest tests/test_compose_tools.py tests/test_compose_none_handling.py tests/test_analytics_resource_safety.py tests/test_phase_2_scan_hardening.py tests/test_applescript_builders_compile.py tests/test_applescript_script_idioms.py -q
```

Release gate and live smoke are still required before final completion.

Final verification completed:

```bash
bash tools/dev-check.sh release
.venv/bin/apple-mail quick-check --json
cmp apple-mail-plugin.zip apple-mail.plugin
git diff --check
```

Release gate result: passed. It rebuilt `apple-mail-plugin.zip`,
`apple-mail.plugin`, and `apple-mail-mcp-v3.7.1.mcpb`, then passed manifest
validation, MCPB unpack plus validate, Claude plugin strict validation, full
pytest, and wrapper-surface optional skip.

Live quick-check result: passed on account `iCloud` with metadata,
no-hit-search, and inbox cases all under thresholds.
