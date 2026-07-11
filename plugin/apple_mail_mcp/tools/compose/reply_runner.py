"""Native-reply abort dispatch, typing-timeout, and stray-artifact-delete helpers.

Leaf module split out of ``reply.py`` (AGENTIC-1214) so the retry loop there
does not have to carry these self-contained pieces and push the module over
the 600 LOC budget. The BODY-verification failure mapping (``body_missing`` /
``body_after_quote`` / ``not_found`` / timeouts) still lives in
``verification.py`` and is dispatched from ``reply.py``; only the pre-typing
and mid-typing ABORT sentinels (``GUARD_ABORT*``, ``TYPING_INTERRUPTED``) are
mapped here, since ``reply.py`` calls the same dispatcher for both the first
compose attempt and a retype attempt.
"""

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.core import escape_applescript, normalize_message_ids
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.constants import (
    TYPING_CHUNK_SIZE,
    TYPING_INTER_CHUNK_DELAY,
    TYPING_PER_CHUNK_OVERHEAD_SECONDS,
)
from apple_mail_mcp.tools.compose.saved_draft_checks import _verify_saved_reply_draft
from apple_mail_mcp.tools.compose.verification import _extract_output_field

# Fixed overhead the native compose script spends outside chunk-typing delays:
# the up-to-4 focus-guard attempts (~0.3-0.5s settle delays each), the initial
# `reply ... with opening window` render (~1.6s), and the post-type save/close
# settle. Slack is extra cushion for host variance. Both exist so a timeout
# scaled only from chunk-typing time cannot come in under budget and let
# AppleScriptTimeout fire mid-typing, stranding a partially typed compose
# window that a retry could then type into on top of.
_NATIVE_TYPING_FIXED_OVERHEAD_SECONDS = 20
_NATIVE_TYPING_SLACK_SECONDS = 30
# Bodies whose projected chunk-typing time would exceed this are refused with
# a structured error instead of silently under- or wildly over-provisioning
# the AppleScript timeout.
_NATIVE_TYPING_MAX_PROJECTED_SECONDS = 480


def _native_reply_effective_timeout(reply_body: str, timeout: int | None) -> tuple[int | None, str | None]:
    """Return ``(effective_timeout, error_json)`` for the native typed-reply script.

    An explicit ``timeout`` from the caller is used as-is (returned unchanged,
    with no error). When ``timeout`` is ``None``, the effective timeout scales
    with the projected chunk-typing duration (chunk count x (inter-chunk delay
    + per-chunk focus/keystroke overhead)) plus fixed overhead and slack,
    floored at the standard 120s. Bodies whose projected typing time exceeds
    the documented cap are refused with a structured error naming the
    ``timeout`` parameter rather than handed a timeout that
    ``AppleScriptTimeout`` could fire mid-typing.
    """
    if timeout is not None:
        return timeout, None
    body_length = len(reply_body)
    chunk_count = -(-body_length // TYPING_CHUNK_SIZE) if body_length else 0
    projected_seconds = chunk_count * (TYPING_INTER_CHUNK_DELAY + TYPING_PER_CHUNK_OVERHEAD_SECONDS)
    if projected_seconds > _NATIVE_TYPING_MAX_PROJECTED_SECONDS:
        return None, serialize_tool_error(
            ToolError(
                code="REPLY_BODY_TYPING_BUDGET_EXCEEDED",
                message=(
                    f"reply_body is {body_length} characters, which projects to "
                    f"~{int(projected_seconds)}s of focus-guarded chunked typing and exceeds "
                    f"the {_NATIVE_TYPING_MAX_PROJECTED_SECONDS}s documented cap for the native "
                    "reply path. No draft was created."
                ),
                remediation={
                    "preferred": (
                        "Shorten reply_body, or pass an explicit timeout large enough for the "
                        "full typing pass plus overhead if a long body is genuinely required."
                    ),
                    "projected_typing_seconds": int(projected_seconds),
                    "cap_seconds": _NATIVE_TYPING_MAX_PROJECTED_SECONDS,
                },
            )
        )
    effective = max(
        120,
        int(projected_seconds + _NATIVE_TYPING_FIXED_OVERHEAD_SECONDS + _NATIVE_TYPING_SLACK_SECONDS),
    )
    return effective, None


def _delete_reply_artifact(account: str, draft_id: str, *, timeout: int | None) -> bool:
    """Best-effort delete of a stray reply-draft artifact by exact Drafts id.

    Returns True only when Mail confirmed the delete (``DELETED|id``). Returns
    False when the id was non-numeric, not found, the AppleScript errored, or
    the call timed out; the caller treats False as "unconfirmed" and surfaces
    a stale-artifact warning instead of assuming the draft is gone. Exchange
    Drafts ids drift across sync (AGENTIC-1214 observation), so a caller that
    assumed success here could otherwise leave a truncated duplicate behind.
    """
    normalized = normalize_message_ids([draft_id])
    if not normalized:
        return False
    numeric_id = normalized[0]
    safe_account = escape_applescript(account)
    script = f'''
tell application "Mail"
    try
        set targetAccount to account "{safe_account}"
        set draftsMailbox to mailbox "Drafts" of targetAccount
        set targetDrafts to every message of draftsMailbox whose id is {numeric_id}
        if (count of targetDrafts) > 0 then
            delete (item 1 of targetDrafts)
            return "DELETED|{numeric_id}"
        end if
        return "NOT_FOUND|{numeric_id}"
    on error
        return "NOT_FOUND|{numeric_id}"
    end try
end tell
'''
    delete_timeout = 30 if timeout is None else max(15, min(timeout, 60))
    try:
        result = compose.run_applescript(script, timeout=delete_timeout)
    except Exception:  # noqa: BLE001 - best-effort cleanup; caller surfaces a stale-artifact warning
        return False
    return result.strip().startswith("DELETED|")


def _probe_abort_artifact(
    result: str,
    *,
    account: str,
    reply_body: str,
    timeout: int | None,
) -> tuple[str, str | None, str, str]:
    """Return ``(artifact_status, suspected_draft_id, guard_subject, derived_subject)``.

    Runs the same signature-agnostic saved-draft probe used by every native-reply
    abort path (mid-typing interruption or pre-typing guard failure), so all
    three abort error responses below report from one consistent verification
    pass instead of three separately duplicated ones.
    """
    guard_reply_subject = _extract_output_field(result, "Subject") or ""
    derived_reply_subject = _extract_output_field(result, "DerivedSubject") or ""
    probe = _verify_saved_reply_draft(
        account,
        guard_reply_subject or derived_reply_subject,
        reply_body,
        draft_id=None,
        quoted_needle="wrote:",
        signature_requested=None,
        timeout=timeout,
    )
    suspected = probe.matched_artifact_id or probe.body_missing_artifact_id or probe.error_artifact_id
    return probe.status, suspected, guard_reply_subject, derived_reply_subject


def _native_reply_abort_response(
    result: str,
    *,
    account: str,
    reply_body: str,
    timeout: int | None,
) -> str | None:
    """Return a structured error for a native-reply abort sentinel, or None.

    Handles ``TYPING_INTERRUPTED`` (focus lost mid-chunk-typing; the partial
    compose window was already discarded by the AppleScript) and
    ``GUARD_ABORT`` / ``GUARD_ABORT_SUBJECT`` (pre-typing focus failures).
    Returns None when ``result`` is not one of these sentinels so the caller
    proceeds to the normal success/verification handling. Callable for both
    the first compose attempt and a retype attempt so a second-run abort is
    routed through the same branches instead of looping again.
    """
    if result.startswith("TYPING_INTERRUPTED"):
        artifact_status, suspected, _guard_subject, _derived_subject = _probe_abort_artifact(
            result, account=account, reply_body=reply_body, timeout=timeout
        )
        return serialize_tool_error(
            ToolError(
                code="REPLY_BODY_TYPING_INTERRUPTED",
                message=(
                    "Native reply lost window focus partway through typing the body, so typing was "
                    "aborted and the partial compose window was discarded (closed without saving). "
                    "No draft with a partial body was left and no email was sent."
                ),
                remediation={
                    "preferred": (
                        "Retry with native_format=True (the default) and Mail visible and not being "
                        "clicked; native replies type into the reply window and need it to hold focus."
                    ),
                    "draft_artifact_status": artifact_status,
                    "suspected_draft_id": suspected,
                    "cleanup": (
                        "If suspected_draft_id is present, a stray artifact may still exist; inspect or "
                        "delete it with verify_draft or manage_drafts(action='delete', draft_id=...)."
                    ),
                    "detail": result,
                },
            )
        )
    if not result.startswith("GUARD_ABORT"):
        return None

    artifact_status, suspected_artifact_id, guard_reply_subject, derived_reply_subject = _probe_abort_artifact(
        result, account=account, reply_body=reply_body, timeout=timeout
    )
    if result.startswith("GUARD_ABORT_SUBJECT"):
        return serialize_tool_error(
            ToolError(
                code="REPLY_SUBJECT_GUARD_MISMATCH",
                message=(
                    "Native reply opened a compose window, but the window title did not match the "
                    "expected reply subject after Mail subject normalization, so the body was not "
                    "typed and no email was sent."
                ),
                remediation={
                    "preferred": (
                        "Retry once with Mail visible. If this persists, report the Subject / "
                        "DerivedSubject / mailFront values from detail; Mail may have normalized the "
                        "subject differently than expected."
                    ),
                    "alternative": (
                        "Do not switch off native formatting. Inspect or delete any empty compose "
                        "window left open, then retry native_format=True."
                    ),
                    "expected_subject": guard_reply_subject or derived_reply_subject,
                    "derived_subject": derived_reply_subject or None,
                    "draft_artifact_status": artifact_status,
                    "suspected_draft_id": suspected_artifact_id,
                    "cleanup": (
                        "If suspected_draft_id is present, inspect or delete that exact Drafts "
                        "artifact with verify_draft or manage_drafts(action='delete', draft_id=...)."
                    ),
                    "detail": result,
                },
            )
        )
    return serialize_tool_error(
        ToolError(
            code="REPLY_WINDOW_FOCUS_FAILED",
            message=(
                "Native reply could not bring the reply window into focus to type the body, so the "
                "intended reply body was not safely saved and no email was sent."
            ),
            remediation={
                "preferred": (
                    "Retry with Mail visible and not being clicked; native replies type into the "
                    "reply window and need it to hold focus for a moment."
                ),
                "alternative": (
                    "Do not switch off native formatting. Retry with native_format=True (the "
                    "default) once Mail can take focus. If focus still cannot be acquired, stop and "
                    "report the blocker."
                ),
                "draft_artifact_status": artifact_status,
                "suspected_draft_id": suspected_artifact_id,
                "cleanup": (
                    "If suspected_draft_id is present, inspect or delete that exact Drafts artifact "
                    "with verify_draft or manage_drafts(action='delete', draft_id=...)."
                ),
                "detail": result,
            },
        )
    )
