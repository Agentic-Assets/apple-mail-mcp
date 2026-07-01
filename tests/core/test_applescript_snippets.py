from apple_mail_mcp.applescript_snippets import (
    recipient_addresses_block,
    sanitize_field_handler,
    text_offset_handler,
    thread_headers_block,
)


def test_sanitize_field_handler_can_include_attachment_row_delimiter():
    script = sanitize_field_handler(include_attachment_row_delimiter=True)

    assert "on sanitize_field(value)" in script
    assert 'set AppleScript\'s text item delimiters to "|||"' in script
    assert 'set AppleScript\'s text item delimiters to ";;"' in script


def test_sanitize_field_handler_omits_attachment_row_delimiter_by_default():
    script = sanitize_field_handler()

    assert "on sanitize_field(value)" in script
    assert 'set AppleScript\'s text item delimiters to "|||"' in script
    assert 'set AppleScript\'s text item delimiters to ";;"' not in script


def test_text_offset_handler_uses_restored_delimiters_and_zero_missing():
    script = text_offset_handler()

    assert "on textOffset(haystackText, needleText)" in script
    assert 'if needleText is "" then return 0' in script
    assert "set AppleScript's text item delimiters to previousDelimiters" in script


def test_thread_headers_block_reads_in_reply_to_and_references():
    script = thread_headers_block(
        message_var="aDraft",
        in_reply_to_var="inReplyTo",
        references_var="refsValue",
    )

    assert "set msgHeaders to all headers of aDraft" in script
    assert 'starts with "In-Reply-To:"' in script
    assert 'starts with "References:"' in script
    assert "set inReplyTo to my sanitize_field(text 13 thru -1 of headerLineText)" in script
    assert "set refsValue to my sanitize_field(text 12 thru -1 of headerLineText)" in script


def test_recipient_addresses_block_collects_single_message_recipients():
    script = recipient_addresses_block(message_var="aMessage", recipient_kind="cc", output_var="ccRecips")

    assert 'set ccRecips to ""' in script
    assert "repeat with aRecip in (cc recipients of aMessage)" in script
    assert "set end of ccAddrs to address of aRecip" in script
    assert "set ccRecips to my sanitize_field(ccAddrs as string)" in script


def test_recipient_addresses_block_supports_bcc_without_sanitizer():
    script = recipient_addresses_block(
        message_var="aDraft",
        recipient_kind="bcc",
        output_var="draftBcc",
        sanitize_fn=None,
    )

    assert "repeat with aRecip in (bcc recipients of aDraft)" in script
    assert "set draftBcc to bccAddrs as string" in script
