"""Exercise bounded mailbox page ordering without reading Mail.app."""

import shutil
import subprocess

import pytest
from apple_mail_mcp.tools.analytics.export_formatting import deterministic_newest_first_block


@pytest.mark.skipif(shutil.which("osascript") is None, reason="requires the AppleScript interpreter")
def test_page_sort_preserves_every_message_in_date_and_id_order():
    # Substitute synthetic date/id pairs for Mail message references and run
    # the real sort. Inserting before an existing item must preserve that item.
    block = deterministic_newest_first_block()
    for variable in ("candidateMessage", "existingMessage"):
        block = block.replace(f"date received of {variable}", f"item 1 of {variable}")
        block = block.replace(f"id of {variable}", f"item 2 of {variable}")
    script = f"""set pageMessages to {{{{1, 10}}, {{2, 20}}, {{2, 30}}, {{1, 40}}}}
    {block}
    set exportedIds to {{}}
    repeat with aMessage in pageMessages
        set end of exportedIds to item 2 of aMessage
    end repeat
    return exportedIds
    """
    result = subprocess.run(["osascript", "-"], input=script, text=True, capture_output=True, timeout=10, check=True)

    assert result.stdout.strip() == "30, 20, 40, 10"
