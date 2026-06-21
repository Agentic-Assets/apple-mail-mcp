# Official Sources And Local Dictionary Map

This reference supports plugin maintenance only. It is not part of the packaged Apple Mail workflow skills exposed to end users.

Use these sources before changing Apple Mail AppleScript. Prefer Apple documentation and the local Mail dictionary over examples from blogs or forums.

## Local Source Of Truth

- Local dictionary: `/System/Applications/Mail.app/Contents/Resources/Mail.sdef`
- Useful commands:
  - `rg -n '<command name="reply"|<class name="outgoing message"|<class name="message"|<property name="content"|<property name="message signature"' /System/Applications/Mail.app/Contents/Resources/Mail.sdef -C 5`
  - `nl -ba /System/Applications/Mail.app/Contents/Resources/Mail.sdef | sed -n '247,306p'`
  - `nl -ba /System/Applications/Mail.app/Contents/Resources/Mail.sdef | sed -n '554,579p'`

Key Mail dictionary facts confirmed on macOS in this repo:

- `reply` creates a reply message and returns `outgoing message`.
- `reply` optional parameters include `opening window` and `reply to all`; both default to false.
- `outgoing message.content` is writable `rich text`.
- `outgoing message.message signature` accepts `signature` or `missing value`.
- `outgoing message` responds to `save`, `close`, and `send`.
- Saved/source `message.content` is `rich text` with read-only access.

## Official Apple References

- Apple Support, View an app's scripting dictionary in Script Editor on Mac:
  https://support.apple.com/guide/script-editor/view-an-apps-scripting-dictionary-scpedt1126/mac
  - Use for the user-facing path to inspect an app dictionary in Script Editor.

- Apple Developer, Mac Automation Scripting Guide: About Scripting Terminology:
  https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/AboutScriptingTerminology.html
  - Use for the principle that app terminology lives in the app's `.sdef` and may change between app or OS versions.

- Apple Developer, Mac Automation Scripting Guide: Opening a Scripting Dictionary:
  https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/OpenaScriptingDictionary.html
  - Use for Script Editor dictionary access procedures.

- Apple Developer, Mac Automation Scripting Guide: Navigating a Scripting Dictionary:
  https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/NavigateaScriptingDictionary.html
  - Use for interpreting command, class, property, inheritance, containment, and read/write access.

- Apple Developer, AppleScript Language Guide: AppleScript Fundamentals:
  https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleScriptLangGuide/conceptual/ASLR_fundamentals.html
  - Use for core AppleScript concepts and how dictionaries define application terminology.

- Apple Developer, AppleScript Language Guide: Reference Forms:
  https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleScriptLangGuide/reference/ASLR_reference_forms.html
  - Use for object specifiers, filters, and `whose` clause behavior.

- Apple Developer, Mac Automation Scripting Guide: Automating the User Interface:
  https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/AutomatetheUserInterface.html
  - Use only when dictionary-backed automation is insufficient. Treat UI scripting as more fragile because it depends on processes, windows, focus, and accessibility state.

## Practical Review Checklist

- Confirm the target object class and containment path.
- Confirm every property access is read/write compatible with the dictionary.
- Prefer dictionary commands over `System Events`.
- Compile the generated AppleScript if possible.
- Use exact Drafts ids first for live cleanup and saved-reply verification when Mail exposes `id of replyMessage`.
- Add regression tests for the observed lifecycle risk, not just static string shape.
- For reply-body assignment, test that the unique body sentinel appears above the quoted-original block, not merely somewhere in the saved draft.
