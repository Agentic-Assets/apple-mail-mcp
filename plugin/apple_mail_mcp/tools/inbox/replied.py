"""Replied-detection helpers: Sent-mailbox Message-ID probe plus text/JSON filter and flag.

``fetch_replied_ids`` routes ``run_applescript`` through the ``inbox`` facade so
the ``patch('...tools.inbox.run_applescript')`` seam still covers the Sent probe."""

from typing import Any

from apple_mail_mcp.core import (
    fetch_replied_ids as _core_fetch_replied_ids,
)
from apple_mail_mcp.tools import inbox


def fetch_replied_ids(account: str, sent_cap: int = 200, timeout: int | None = 60) -> set[str]:
    """Fetch replied Message-ID set using this module's ``run_applescript``.

    Wraps the core helper so tests that patch
    ``apple_mail_mcp.tools.inbox.run_applescript`` also cover the
    Sent-mailbox probe.
    """
    return _core_fetch_replied_ids(account, sent_cap=sent_cap, timeout=timeout, runner=inbox.run_applescript)


def _normalize_message_id_token(raw: str) -> str:
    """Return a Message-ID wrapped in angle brackets for set lookups."""
    token = (raw or "").strip()
    if not token:
        return ""
    if not token.startswith("<"):
        token = "<" + token
    if not token.endswith(">"):
        token = token + ">"
    return token


def _filter_text_body_by_replied(
    body: str,
    replied_ids: set[str],
    *,
    exclude_replied: bool,
    flag_replied: bool,
) -> tuple[str, int]:
    """Apply replied detection to a text-format inbox body.

    Each email block starts with a ``__MSG_ID__|||<id>`` marker line emitted
    by the text script when message-id capture is enabled. The marker line
    is stripped on the way out; when *exclude_replied* is True replied
    emails (subject + From/Date lines through the next blank line) are
    dropped; when *flag_replied* is True the ✓/✉ line is prefixed with
    ``[REPLIED] ``.
    """
    lines = body.splitlines()
    out: list[str] = []
    skipped = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("__MSG_ID__|||"):
            raw_id = line.split("|||", 1)[1].strip()
            token = _normalize_message_id_token(raw_id)
            is_replied = bool(token) and token in replied_ids
            # Look ahead for the next non-empty line — the rendered email block.
            j = i + 1
            # First non-empty content line is the indicator + subject.
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines):
                i = j
                continue
            indicator_line = lines[j]
            # Find the trailing blank that ends this email block, so we can
            # cleanly skip it when exclude_replied is True.
            k = j + 1
            while k < len(lines) and lines[k].strip() != "":
                k += 1
            block_end = k  # 'k' points at the first blank or end-of-list.

            if is_replied and exclude_replied:
                skipped += 1
                # Advance past block; also drop the trailing blank if present.
                i = block_end + 1 if block_end < len(lines) else block_end
                continue

            if is_replied and flag_replied:
                # Inject [REPLIED] after the indicator and single space.
                # Indicator line shape: "<sym> <subject>"
                if " " in indicator_line:
                    sym, rest = indicator_line.split(" ", 1)
                    indicator_line = f"{sym} [REPLIED] {rest}"
                else:
                    indicator_line = f"{indicator_line} [REPLIED]"

            # Emit any blank lines between marker and indicator, then block.
            out.extend(lines[i + 1 : j])
            out.append(indicator_line)
            out.extend(lines[j + 1 : block_end])
            if block_end < len(lines):
                out.append(lines[block_end])
            i = block_end + 1 if block_end < len(lines) else block_end
            continue
        out.append(line)
        i += 1
    return "\n".join(out), skipped


def _apply_replied_to_emails(
    emails: list[dict[str, Any]],
    replied_set: set[str],
    *,
    exclude_replied: bool,
    flag_replied: bool,
) -> list[dict[str, Any]]:
    """Filter or flag email dicts based on a replied Message-ID set."""
    if not (exclude_replied or flag_replied):
        return emails
    out: list[dict[str, Any]] = []
    for em in emails:
        token = _normalize_message_id_token(em.get("internet_message_id", ""))
        is_replied = bool(token) and token in replied_set
        if is_replied and exclude_replied:
            continue
        if is_replied and flag_replied and not exclude_replied:
            em = dict(em)
            em["already_replied"] = True
        out.append(em)
    return out
