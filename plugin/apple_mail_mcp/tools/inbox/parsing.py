"""Pure parsing and read-filter predicate helpers for inbox listing (no Mail IO)."""

from typing import Any

from apple_mail_mcp.core.reply_state import DraftsSnapshot, reply_state_tags, resolve_has_draft

_VALID_READ_FILTERS = ("all", "read", "unread")


def _resolve_read_filter(
    read_status: str | None,
    include_read: bool,
) -> str:
    """Map the public ``read_status``/``include_read`` pair to an internal filter.

    Returns one of ``"all"``, ``"read"``, ``"unread"``. ``read_status``
    wins when provided; otherwise ``include_read`` is interpreted as
    ``True → "all"``, ``False → "unread"`` (the legacy bool semantics).
    Raises ``ValueError`` for an unknown ``read_status``.
    """
    if read_status is not None:
        if read_status not in _VALID_READ_FILTERS:
            raise ValueError(f"read_status must be one of {_VALID_READ_FILTERS}; got {read_status!r}")
        return read_status
    return "all" if include_read else "unread"


def _read_filter_condition(read_filter: str) -> str | None:
    """Return the per-message AppleScript predicate for *read_filter*.

    Returns ``None`` for the no-filter case (``"all"``). The predicate
    references ``aMessage`` and is intended for use inside an in-loop
    ``if`` — never as the body of a ``whose`` clause over a bound slice
    (which crashes on Gmail; see ``bounded_scan.build_bounded_filtered_scan``).
    """
    if read_filter == "unread":
        return "read status of aMessage is false"
    if read_filter == "read":
        return "read status of aMessage is true"
    return None


def _parse_pipe_delimited_emails(raw: str, *, has_message_id: bool = False) -> list[dict[str, Any]]:
    """Parse '|||'-delimited AppleScript output into a list of email dicts.

    Current schema (7, 8, or 9 fields):
        subject|||sender|||date|||read|||account|||mail_app_id|||was_replied_to
        [|||internet_message_id][|||content_preview]

    ``mail_app_id`` (the integer Mail.app ``id`` property) is always present
    and emitted as ``"message_id"`` to match the ``search_emails`` record
    shape. ``was_replied_to`` (Mail's native ``was replied to`` boolean) is
    always present too, and no parameter gates it (see
    ``tasks/active/reply-state-annotation/plan-2026-07-10.md``).

    Extended schema when *has_message_id* is True (8 or 9 fields):
        subject|||sender|||date|||read|||account|||mail_app_id|||was_replied_to
        |||internet_message_id[|||content_preview]

    **Field-count validation:** the AppleScript side runs
    ``sanitize_pipe_delimited_field`` on ``messageSubject`` and
    ``messageSender`` to strip the ``|||`` sequence before emission. This
    parser ALSO defensively checks the field count — if a row produces
    too few fields (e.g. a hand-crafted test fixture or a Mail return
    we didn't anticipate), the ``mail_app_id`` would land on the wrong
    column and a subsequent ``manage_trash(action="delete_permanent")``
    could delete the wrong message. Rows that don't validate are dropped.
    """
    emails: list[dict[str, Any]] = []
    if not raw:
        return emails
    # Fields: subject(0) sender(1) date(2) read(3) account(4) mail_app_id(5)
    #         was_replied_to(6) [internet_message_id(7)] [content_preview(7 or 8)]
    # maxsplit = field_count - 1 so the LAST field (content_preview, or
    # internet_message_id when content is absent) keeps any literal ||| inside
    # it intact (content previews legitimately contain pipe sequences). The
    # *earlier* user-controlled fields (subject, sender) are sanitized on the
    # AppleScript side via `sanitize_pipe_delimited_field` before emission;
    # any sanitizer escape that slipped through would land a non-numeric
    # value in the mail_app_id slot (parts[5]) — the isdigit() check below
    # rejects those rows rather than risk mapping the wrong id onto a
    # downstream destructive op.
    maxsplit = 8 if has_message_id else 7
    for line in raw.split("\n"):
        if "|||" not in line:
            continue
        parts = line.split("|||", maxsplit)
        if len(parts) < 6:
            continue
        mail_app_id = parts[5].strip()
        # mail_app_id must be a Mail integer id. If it's empty (transient sync
        # failure) or not all digits (parser corruption from leaked ||| in a
        # subject — sanitizer escape hatch), drop the row rather than risk
        # mapping the wrong id onto a downstream destructive op.
        if not mail_app_id or not mail_app_id.isdigit():
            continue
        item: dict[str, Any] = {
            "subject": parts[0].strip(),
            "sender": parts[1].strip(),
            "date": parts[2].strip(),
            "is_read": parts[3].strip().lower() == "true",
            "account": parts[4].strip(),
            "message_id": mail_app_id,
            "was_replied_to": len(parts) > 6 and parts[6].strip().lower() == "true",
        }
        if has_message_id:
            if len(parts) >= 8 and parts[7].strip():
                item["internet_message_id"] = parts[7].strip()
            if len(parts) >= 9 and parts[8].strip():
                item["content_preview"] = parts[8].strip()
        else:
            if len(parts) >= 8 and parts[7].strip():
                item["content_preview"] = parts[7].strip()
        emails.append(item)
    return emails


def _strip_count_marker(raw: str) -> tuple[str, int]:
    """Split out the `__COUNT__|||N` marker line if present.

    Returns (clean_text_without_marker, count). Count defaults to 0 when
    no marker is present (e.g. an empty-inbox account).
    """
    if not raw:
        return "", 0
    lines = raw.splitlines()
    count = 0
    kept: list[str] = []
    for line in lines:
        if line.startswith("__COUNT__|||"):
            try:
                count = int(line.split("|||", 1)[1])
            except (IndexError, ValueError):
                count = 0
        else:
            kept.append(line)
    return "\n".join(kept), count


_ROW_MARKER_PREFIX = "__ROW__|||"


def _annotate_text_rows_with_reply_state(
    body: str,
    *,
    exclude_replied: bool = False,
    exclude_drafted: bool = False,
    draft_snapshot: DraftsSnapshot | None = None,
) -> str:
    """Walk ``__ROW__`` marker lines, tag/drop blocks, strip the markers.

    Each ``__ROW__|||subject|||sender|||date|||internetMessageId|||wasRepliedToken``
    marker line (emitted by ``list_scripts._build_list_inbox_text_script``)
    precedes exactly one rendered email block, up to (not including) the
    next blank line. The marker itself is always removed from the output.

    ``was_replied_to`` comes straight from the marker's native token (no
    Sent-mailbox scan). ``has_draft`` is computed via *draft_snapshot*
    (``None`` when the scan was skipped or unavailable: no tag and no
    exclusion, matching the JSON ``has_draft=null`` contract). Matching
    blocks get a ``[REPLIED]``/``[HAS DRAFT]`` tag prefixed onto their
    indicator line; when *exclude_replied*/*exclude_drafted* is True the
    whole block is dropped instead.
    """
    lines = body.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith(_ROW_MARKER_PREFIX):
            out.append(line)
            i += 1
            continue

        fields = line[len(_ROW_MARKER_PREFIX) :].split("|||")
        subject = fields[0] if len(fields) > 0 else ""
        sender = fields[1] if len(fields) > 1 else ""
        date_text = fields[2] if len(fields) > 2 else ""
        internet_message_id = fields[3] if len(fields) > 3 else ""
        was_replied = len(fields) > 4 and fields[4].strip().lower() == "true"

        has_draft = resolve_has_draft(
            draft_snapshot,
            subject=subject,
            sender_email=sender,
            internet_message_id=internet_message_id or None,
            email_date=date_text or None,
        )

        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            i = j
            continue
        indicator_line = lines[j]
        k = j + 1
        while k < len(lines) and lines[k].strip() != "":
            k += 1
        block_end = k

        if (was_replied and exclude_replied) or (has_draft and exclude_drafted):
            i = block_end + 1 if block_end < len(lines) else block_end
            continue

        tags = reply_state_tags(was_replied, has_draft)
        if tags:
            tag_text = " ".join(tags)
            if " " in indicator_line:
                sym, rest = indicator_line.split(" ", 1)
                indicator_line = f"{sym} {tag_text} {rest}"
            else:
                indicator_line = f"{indicator_line} {tag_text}"

        out.extend(lines[i + 1 : j])
        out.append(indicator_line)
        out.extend(lines[j + 1 : block_end])
        if block_end < len(lines):
            out.append(lines[block_end])
        i = block_end + 1 if block_end < len(lines) else block_end
    return "\n".join(out)
