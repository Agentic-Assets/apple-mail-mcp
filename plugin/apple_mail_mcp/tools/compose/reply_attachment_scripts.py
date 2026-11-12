"""Add attachments through Mail's native file picker without replacing the editor."""

from apple_mail_mcp.core import escape_applescript


def native_reply_attachment_scripts(paths: list[str], cleanup_script: str) -> tuple[str, str]:
    """Build native attachment handlers and the guarded pre-save step."""
    if not paths:
        return "", ""
    path_list = ", ".join(f'"{escape_applescript(path)}"' for path in paths)
    return (
        _ATTACHMENT_HANDLERS,
        f"""
    set attachmentResult to my attachFilesInNativeReply({{{path_list}}}, replySubject, derivedReplySubject, replyWindowId)
    if attachmentResult is not "attached" then
        my closeNativeReplyWindowSafely(replyWindowId, replySubject, derivedReplySubject)
        {cleanup_script}
        return "ATTACHMENT_INSERT_FAILED" & return & "Subject: " & replySubject & return & "DerivedSubject: " & derivedReplySubject & return & "Detail: " & attachmentResult
    end if
""",
    )


_ATTACHMENT_HANDLERS = """
use framework "Foundation"
use scripting additions

on replyAttachmentSheet(expectedTitle, derivedTitle, expectedWindowId)
    tell application "Mail"
        if (id of front window as string) is not expectedWindowId then error "reply window changed during attachment selection"
        set frontTitle to name of front window as string
        if frontTitle is not expectedTitle and frontTitle is not derivedTitle then error "reply title changed during attachment selection"
    end tell
    -- The Accessibility title can be the modal sheet. Anchor it to the exact
    -- compose window by Mail's front-window id and sheet containment instead.
    tell application "System Events" to tell process "Mail"
        if not frontmost then error "Mail lost focus during attachment selection"
        if exists sheet 1 of front window then
            set fileSheet to sheet 1 of front window
            if (value of attribute "AXIdentifier" of fileSheet) is not "open-panel" then error "another sheet is open on the reply"
            return fileSheet
        end if
    end tell
    return missing value
end replyAttachmentSheet

on waitForReplyAttachmentSheet(expectedTitle, derivedTitle, expectedWindowId, shouldBeOpen)
    repeat 20 times
        set fileSheet to my replyAttachmentSheet(expectedTitle, derivedTitle, expectedWindowId)
        if (fileSheet is not missing value) is shouldBeOpen then return fileSheet
        delay 0.1
    end repeat
    error "Mail did not open or close its attachment picker"
end waitForReplyAttachmentSheet

on normalizedReplyAttachmentPath(fileURL)
    set selectedURL to current application's NSURL's URLWithString:(fileURL as text)
    if selectedURL is missing value then return ""
    if ((selectedURL's isFileURL()) as boolean) is false then return ""
    return (selectedURL's URLByResolvingSymlinksInPath()'s |path|()) as text
end normalizedReplyAttachmentPath

on confirmNativeReplyAttachment(fileSheet, filePath, expectedTitle, derivedTitle, expectedWindowId)
    set requestedPath to current application's NSString's stringWithString:filePath
    set selectedFile to missing value
    set chooseButton to missing value
    tell application "System Events" to tell process "Mail"
        repeat with pickerElement in entire contents of fileSheet
            set selectedFileURL to missing value
            if (value of attribute "AXRole" of pickerElement) is "AXButton" then
                if exists attribute "AXIdentifier" of pickerElement then
                    if (value of attribute "AXIdentifier" of pickerElement) is "OKButton" then set chooseButton to contents of pickerElement
                end if
            else if exists attribute "AXSelected" of pickerElement then
                if (value of attribute "AXSelected" of pickerElement) is true and (exists attribute "AXURL" of pickerElement) then
                    set selectedFileURL to value of attribute "AXURL" of pickerElement
                end if
            end if
            if selectedFileURL is not missing value then
                set selectedPath to my normalizedReplyAttachmentPath(selectedFileURL)
                if selectedPath is not "" then
                    if (requestedPath's isEqualToString:selectedPath) as boolean then
                        set selectedFile to contents of pickerElement
                    else if not ((requestedPath's hasPrefix:(selectedPath & "/")) as boolean) then
                        -- In column view, selected ancestor directories may also be exposed.
                        error "attachment picker selected a different file"
                    end if
                end if
            end if
        end repeat
    end tell
    if selectedFile is missing value then error "Mail did not select the requested file"
    if chooseButton is missing value then error "attachment picker confirmation is unavailable"
    set activeSheet to my replyAttachmentSheet(expectedTitle, derivedTitle, expectedWindowId)
    if activeSheet is missing value then error "attachment picker was dismissed before confirmation"
    tell application "System Events" to tell process "Mail"
        if not frontmost or (exists sheet 1 of activeSheet) then error "attachment picker lost focus before confirmation"
        if (value of attribute "AXSelected" of selectedFile) is not true then error "attachment picker selection changed"
        set selectedPath to my normalizedReplyAttachmentPath(value of attribute "AXURL" of selectedFile)
        if not ((requestedPath's isEqualToString:selectedPath) as boolean) then error "attachment picker selection changed"
        if not enabled of chooseButton then error "attachment picker confirmation is disabled"
        perform action "AXPress" of chooseButton
    end tell
end confirmNativeReplyAttachment

on chooseNativeReplyAttachment(filePath, expectedTitle, derivedTitle, expectedWindowId)
    if (my replyAttachmentSheet(expectedTitle, derivedTitle, expectedWindowId)) is missing value then error "attachment picker was dismissed"
    tell application "System Events" to keystroke "g" using {shift down, command down}
    set pathSubmitted to false
    set pathSheetDismissed to false
    repeat 20 times
        set fileSheet to my replyAttachmentSheet(expectedTitle, derivedTitle, expectedWindowId)
        if fileSheet is missing value then error "attachment picker was dismissed"
        tell application "System Events" to tell process "Mail"
            if not frontmost then error "Mail lost focus during attachment selection"
            if exists sheet 1 of fileSheet then
                set pathSheet to sheet 1 of fileSheet
                set pathField to value of attribute "AXFocusedUIElement"
                if (value of attribute "AXIdentifier" of pathSheet) is "GoToWindow" and (value of attribute "AXIdentifier" of pathField) is "PathTextField" then
                    set value of attribute "AXValue" of pathField to filePath
                    if not frontmost then error "Mail lost focus before accepting the attachment path"
                    if (value of attribute "AXFocusedUIElement") is not pathField then error "attachment path field lost focus"
                    if ((value of attribute "AXValue" of pathField) as string) is not filePath then error "Mail did not accept the requested path"
                    key code 36
                    set pathSubmitted to true
                    exit repeat
                end if
            end if
        end tell
        delay 0.1
    end repeat
    if not pathSubmitted then error "Mail did not open the attachment path field"
    repeat 20 times
        set fileSheet to my replyAttachmentSheet(expectedTitle, derivedTitle, expectedWindowId)
        if fileSheet is missing value then error "attachment picker was dismissed before the file was chosen"
        tell application "System Events" to tell process "Mail"
            if not frontmost then error "Mail lost focus during attachment selection"
            if not (exists sheet 1 of fileSheet) then
                set pathSheetDismissed to true
                exit repeat
            end if
        end tell
        delay 0.1
    end repeat
    if not pathSheetDismissed then error "Mail did not accept the attachment path"
    my confirmNativeReplyAttachment(fileSheet, filePath, expectedTitle, derivedTitle, expectedWindowId)
end chooseNativeReplyAttachment

on attachFilesInNativeReply(attachmentPaths, expectedTitle, derivedTitle, expectedWindowId)
    set pickerOpened to false
    try
        repeat with attachmentPath in attachmentPaths
            if (my replyAttachmentSheet(expectedTitle, derivedTitle, expectedWindowId)) is not missing value then error "another sheet is open on the reply"
            tell application "System Events" to keystroke "a" using {shift down, command down}
            my waitForReplyAttachmentSheet(expectedTitle, derivedTitle, expectedWindowId, true)
            set pickerOpened to true
            my chooseNativeReplyAttachment(contents of attachmentPath, expectedTitle, derivedTitle, expectedWindowId)
            my waitForReplyAttachmentSheet(expectedTitle, derivedTitle, expectedWindowId, false)
            set pickerOpened to false
        end repeat
        return "attached"
    on error attachmentError
        if pickerOpened then
            try
                repeat 2 times
                    if (my replyAttachmentSheet(expectedTitle, derivedTitle, expectedWindowId)) is missing value then exit repeat
                    tell application "System Events" to key code 53
                    delay 0.1
                end repeat
            on error cleanupError
                set attachmentError to attachmentError & " [attachment picker cleanup: " & cleanupError & "]"
            end try
        end if
        return attachmentError
    end try
end attachFilesInNativeReply
"""
