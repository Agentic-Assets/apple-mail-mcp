"""Native reply Drafts identity capsules.

The native AppleScript emits an RFC-backed capsule when Mail has persisted
both message identifiers. iCloud can defer the outgoing Message-ID, so a
second capsule type represents only one bounded, count-plus-one Drafts
transaction. That temporary proof may verify this call's exact numeric row;
it is never sufficient for a later mutation such as delete-and-retype.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NativeReplyDraftIdentity:
    """Exact Drafts evidence returned by the native reply operation."""

    draft_id: str
    draft_rfc_message_id: str
    source_rfc_message_id: str
    evidence: str = "rfc"

    @property
    def is_rfc_backed(self) -> bool:
        """Return whether this identity can safely authorize a later mutation."""
        return self.evidence == "rfc"


def native_reply_draft_identity_from_output(output: str) -> NativeReplyDraftIdentity | None:
    """Parse a valid native Drafts identity capsule, otherwise return None."""
    prefix = "Draft Identity: "
    for line in output.splitlines():
        if not line.startswith(prefix):
            continue
        parts = line[len(prefix) :].split("|||")
        if len(parts) not in {3, 4}:
            return None
        draft_id, draft_rfc_message_id, source_rfc_message_id = (part.strip() for part in parts[:3])
        if not draft_id.isdigit():
            return None
        if len(parts) == 3:
            if not _is_rfc_message_id(draft_rfc_message_id) or not _is_rfc_message_id(source_rfc_message_id):
                return None
            return NativeReplyDraftIdentity(draft_id, draft_rfc_message_id, source_rfc_message_id)
        if parts[3].strip() != "transaction" or draft_rfc_message_id or source_rfc_message_id:
            return None
        return NativeReplyDraftIdentity(draft_id, "", "", evidence="transaction")
    return None


def _is_rfc_message_id(value: str) -> bool:
    """Return whether ``value`` has the unambiguous angle-bracket RFC-ID form."""
    return len(value) > 2 and value.startswith("<") and value.endswith(">") and " " not in value
