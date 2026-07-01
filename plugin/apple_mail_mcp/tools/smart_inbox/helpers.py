"""Pure leaf shared by ``awaiting_reply`` and ``needs_response``: Message-ID normalization."""


def _normalize_message_id(raw_id: str) -> str:
    """Ensure a Message-ID has angle brackets."""
    raw_id = raw_id.strip()
    if not raw_id.startswith("<"):
        raw_id = "<" + raw_id
    if not raw_id.endswith(">"):
        raw_id = raw_id + ">"
    return raw_id
