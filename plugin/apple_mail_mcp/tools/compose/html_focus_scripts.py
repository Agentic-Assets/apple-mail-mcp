"""Focus the HTML compose body editor without indenting the first line.

Mail's WebKit compose field treats Tab as first-paragraph indent once the
caret is already in the body. Unguarded Tab loops caused the four-tab
leading indent on HTML paste.

During compose, System Events names the window after the temporary
subject marker. Bind to that window, AXFocus/click the editor, and Tab
only while Accessibility reports a header field. Never Tab when the
focused role is the body, and never Tab when the role cannot be read.
"""


def html_compose_focus_handler() -> str:
    """Return the AppleScript handler that focuses the HTML compose body."""
    return """
on focusComposeBody(theMarker)
    tell application "System Events"
        tell process "Mail"
            try
                set composeWindow to first window whose name contains theMarker
                perform action "AXRaise" of composeWindow
                if name of composeWindow does not contain theMarker then return false
                try
                    set focusedElement to value of attribute "AXFocusedUIElement"
                    set focusedRole to value of attribute "AXRole" of focusedElement as string
                    if focusedRole is "AXWebArea" or focusedRole is "AXTextArea" then return true
                end try
                set composeEditor to missing value
                set webAreaFallback to missing value
                set allElements to entire contents of composeWindow
                repeat with candidateElement in allElements
                    try
                        set candidateRole to value of attribute "AXRole" of candidateElement as string
                        if candidateRole is "AXTextArea" then
                            set composeEditor to contents of candidateElement
                            exit repeat
                        else if candidateRole is "AXWebArea" and webAreaFallback is missing value then
                            set webAreaFallback to contents of candidateElement
                        end if
                    end try
                end repeat
                if composeEditor is missing value then set composeEditor to webAreaFallback
                if composeEditor is not missing value then
                    set editorIsFocused to false
                    try
                        perform action "AXFocus" of composeEditor
                    end try
                    try
                        set editorIsFocused to value of attribute "AXFocused" of composeEditor
                    end try
                    if editorIsFocused is not true then
                        click composeEditor
                    end if
                    delay 0.1
                end if
                try
                    set focusedElement to value of attribute "AXFocusedUIElement"
                    set focusedRole to value of attribute "AXRole" of focusedElement as string
                    if focusedRole is "AXWebArea" or focusedRole is "AXTextArea" then return true
                end try
                set headerRoles to {"AXTextField", "AXComboBox", "AXPopUpButton", "AXMenuButton", "AXCheckBox"}
                repeat with focusAttempt from 1 to 6
                    try
                        set focusedElement to value of attribute "AXFocusedUIElement"
                        set focusedRole to value of attribute "AXRole" of focusedElement as string
                        if focusedRole is "AXWebArea" or focusedRole is "AXTextArea" then return true
                        if headerRoles contains focusedRole then
                            if focusAttempt is less than 6 then
                                key code 48
                                delay 0.2
                            end if
                        else
                            exit repeat
                        end if
                    on error
                        exit repeat
                    end try
                end repeat
                try
                    set focusedElement to value of attribute "AXFocusedUIElement"
                    set focusedRole to value of attribute "AXRole" of focusedElement as string
                    if focusedRole is "AXWebArea" or focusedRole is "AXTextArea" then return true
                end try
                return false
            on error
                return false
            end try
        end tell
    end tell
end focusComposeBody
"""
