"""Small AppleScript snippet builders shared by Apple Mail tools."""

from typing import Literal


def sanitize_field_handler(*, include_attachment_row_delimiter: bool = False, name: str = "sanitize_field") -> str:
    """Return an AppleScript handler that normalizes fields for delimited output."""
    attachment_delimiter_block = ""
    if include_attachment_row_delimiter:
        attachment_delimiter_block = """
        set AppleScript's text item delimiters to ";;"
        set valueParts to text items of valueText
        set AppleScript's text item delimiters to "; "
        set valueText to valueParts as string"""

    return f"""
    on {name}(value)
        try
            set valueText to value as string
        on error
            set valueText to ""
        end try
        set AppleScript's text item delimiters to {{return, linefeed, tab}}
        set valueParts to text items of valueText
        set AppleScript's text item delimiters to " "
        set valueText to valueParts as string
        set AppleScript's text item delimiters to "|||"
        set valueParts to text items of valueText
        set AppleScript's text item delimiters to " | "
        set valueText to valueParts as string{attachment_delimiter_block}
        set AppleScript's text item delimiters to ""
        return valueText
    end {name}
    """


def text_offset_handler(*, name: str = "textOffset") -> str:
    """Return an AppleScript handler that finds a substring offset safely."""
    return f"""
    on {name}(haystackText, needleText)
        if needleText is "" then return 0
        set previousDelimiters to AppleScript's text item delimiters
        try
            set AppleScript's text item delimiters to needleText
            set splitItems to text items of haystackText
            if (count of splitItems) is 1 then
                set AppleScript's text item delimiters to previousDelimiters
                return 0
            end if
            set beforeNeedle to item 1 of splitItems
            set AppleScript's text item delimiters to previousDelimiters
            return ((count of characters of beforeNeedle) + 1)
        on error
            set AppleScript's text item delimiters to previousDelimiters
            return 0
        end try
    end {name}
    """


def thread_headers_block(
    *,
    message_var: str,
    in_reply_to_var: str,
    references_var: str,
    sanitize_fn: str | None = "sanitize_field",
    include_on_error: bool = False,
) -> str:
    """Return an AppleScript block that reads In-Reply-To and References headers."""

    def _value_expr(offset: int) -> str:
        raw = f"text {offset} thru -1 of headerLineText"
        if sanitize_fn is None:
            return raw
        return f"my {sanitize_fn}({raw})"

    on_error_block = ""
    if include_on_error:
        on_error_block = f'''
                on error
                    set {in_reply_to_var} to ""
                    set {references_var} to ""'''

    return f"""
                set {in_reply_to_var} to ""
                set {references_var} to ""
                try
                    set msgHeaders to all headers of {message_var}
                    set AppleScript's text item delimiters to {{return, linefeed}}
                    set headerLines to text items of msgHeaders
                    set AppleScript's text item delimiters to ""
                    repeat with headerLine in headerLines
                        set headerLineText to headerLine as string
                        ignoring case
                            if headerLineText starts with "In-Reply-To:" and length of headerLineText > 12 then
                                set {in_reply_to_var} to {_value_expr(13)}
                            else if headerLineText starts with "References:" and length of headerLineText > 11 then
                                set {references_var} to {_value_expr(12)}
                            end if
                        end ignoring
                    end repeat{on_error_block}
                end try
    """


def recipient_addresses_block(
    *,
    message_var: str,
    recipient_kind: Literal["to", "cc", "bcc"],
    output_var: str,
    sanitize_fn: str | None = "sanitize_field",
    include_on_error: bool = False,
) -> str:
    """Return an AppleScript block that collects one recipient kind from one message."""
    list_var = f"{recipient_kind}Addrs"
    value_expr = f"{list_var} as string"
    if sanitize_fn is not None:
        value_expr = f"my {sanitize_fn}({value_expr})"
    on_error_block = ""
    if include_on_error:
        on_error_block = f'''
                on error
                    set {output_var} to ""'''

    return f'''
                set {output_var} to ""
                try
                    set {list_var} to {{}}
                    repeat with aRecip in ({recipient_kind} recipients of {message_var})
                        try
                            set end of {list_var} to address of aRecip
                        end try
                    end repeat
                    set AppleScript's text item delimiters to ", "
                    set {output_var} to {value_expr}
                    set AppleScript's text item delimiters to ""{on_error_block}
                end try
    '''
