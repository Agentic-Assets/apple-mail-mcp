"""Native reply Drafts identity capsules.

The native AppleScript emits a capsule only after proving a complete bounded
Drafts snapshot gained one RFC-linked message. Python treats malformed or
absent capsules as unavailable, never as an exact Drafts handle.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NativeReplyDraftIdentity:
    """Persisted Drafts identity proven by the native AppleScript resolver."""

    draft_id: str
    draft_rfc_message_id: str
    source_rfc_message_id: str


def native_reply_draft_identity_from_output(output: str) -> NativeReplyDraftIdentity | None:
    """Parse a valid native Drafts identity capsule, otherwise return None."""
    prefix = "Draft Identity: "
    for line in output.splitlines():
        if not line.startswith(prefix):
            continue
        parts = line[len(prefix) :].split("|||")
        if len(parts) != 3:
            return None
        draft_id, draft_rfc_message_id, source_rfc_message_id = (part.strip() for part in parts)
        if not draft_id.isdigit():
            return None
        if not _is_rfc_message_id(draft_rfc_message_id) or not _is_rfc_message_id(source_rfc_message_id):
            return None
        return NativeReplyDraftIdentity(draft_id, draft_rfc_message_id, source_rfc_message_id)
    return None


def _is_rfc_message_id(value: str) -> bool:
    """Return whether ``value`` has the unambiguous angle-bracket RFC-ID form."""
    return len(value) > 2 and value.startswith("<") and value.endswith(">") and " " not in value
