"""Identity tweaks applied to the already-open native reply window.

``reply_to_email``'s native path opens Mail's own reply window and then applies
two optional tweaks to it: the caller's ``from_address`` and a named signature.
Both used to sit in one bare ``try ... end try`` block commented "best-effort
identity tweaks", which is right for the signature and wrong for the sender.

The signature stays swallowed on purpose. When the caller did not name a
signature, ``_reply_signature_script`` emits nothing at all and Mail's own
default reply signature (with its logo) is the intended result; when a name was
given, a failed application is still caught downstream by the saved-draft
verifier's ``expected_signature_name`` check.

The sender is different on both counts. A sender statement is emitted only when
``_validate_from_address`` already matched an explicit ``from_address`` against
the account's configured aliases, so its presence *is* the caller's explicit
request. Mail can still refuse the assignment (an alias the account may not send
as, Exchange send-as or delegated-mailbox policy), and swallowing that refusal
drafts or sends the reply from whatever identity Mail chose instead - exactly
the outcome the parameter exists to prevent - while the tool reports plain
success. Nothing downstream catches it either: no verifier in the reply path
reads the saved draft's ``From``.

So the override is emitted under an ``on error`` handler that fails closed. It
runs before ``save replyMessage``, so no Drafts artifact exists yet: the handler
discards the open reply window (``close ... saving no`` via
``closeNativeReplyWindowSafely``), removes the body temp file, and returns the
whole-script ``SENDER_OVERRIDE_FAILED`` sentinel. ``reply_runner`` maps that to
a structured ``REPLY_SENDER_OVERRIDE_FAILED`` error - the same producer/consumer
sentinel shape already used for ``GUARD_ABORT`` and ``TYPING_INTERRUPTED``.

Residual gap, deliberately not covered here: this guard catches a *refusal*
(an AppleScript error). Mail accepting ``set sender`` without applying it would
still pass silently, because proving otherwise needs a ``From`` readback whose
live string form is unverified.
"""

# Whole-script sentinel returned when an explicitly requested from_address could
# not be applied. Consumed by ``reply_runner._native_reply_abort_response``.
NATIVE_REPLY_SENDER_OVERRIDE_ABORT = "SENDER_OVERRIDE_FAILED"


def native_reply_identity_tweak_script(sender_script: str, signature_script: str, cleanup_script: str) -> str:
    """Return the sender + signature tweak block for the open native reply window.

    The returned block is spliced at an 8-space indent inside the native reply
    script's ``tell application "Mail"`` block, after the reply window has been
    adopted and before ``save replyMessage``, so ``replyWindowId``,
    ``replySubject``, ``derivedReplySubject`` and the window handlers are all in
    scope. The first line carries no indent (the splice site supplies it).

    ``sender_script`` is wrapped in a fail-closed ``on error`` handler, and is
    omitted entirely when empty (no ``from_address`` was requested), so the
    default native path emits no sender statement and no abort branch.
    ``signature_script`` keeps its intentionally swallowed ``try`` wrapper.
    """
    signature_block = f"""try
            {signature_script}
        end try"""
    if not sender_script:
        return signature_block
    abort_return = (
        f'return "{NATIVE_REPLY_SENDER_OVERRIDE_ABORT}" & return & "Subject: " & replySubject'
        ' & return & "DerivedSubject: " & derivedReplySubject & return & "Detail: " & senderErrMsg'
    )
    return f"""-- An explicitly requested from_address is a requirement, not a tweak: Mail can
        -- refuse the alias, and drafting or sending from an unrequested identity is
        -- what the parameter exists to prevent. Nothing is saved yet at this point,
        -- so discard the open reply window and refuse instead of swallowing.
        try
            {sender_script}
        on error senderErrMsg
            my closeNativeReplyWindowSafely(replyWindowId, replySubject, derivedReplySubject)
            {cleanup_script}
            {abort_return}
        end try
        {signature_block}"""
