"""``search.dispatch`` must validate page bounds on BOTH dispatch paths.

``_search_mail_records`` (async) guards its bounds up front. Its sibling
``_search_mail_records_sync(account=...)`` used to call ``_search_one_account``
directly, skipping that guard entirely, so a non-positive ``limit`` or a
negative ``offset`` reached ``script._build_search_script`` unchecked. That
builder derives its scan bound as ``base_cap = limit + 1 + offset``, and a
read-only live probe across four Mail backends (On My Mac, Exchange, Gmail
IMAP, iCloud; mailbox counts 0/1/2/3/11/48, byte-identical results) established
what the resulting slices do:

* ``messages 1 thru 1`` (from ``limit=0``) binds the newest message and hands
  it back — one real message returned as an authoritative result for a page the
  caller sized at zero.
* ``messages 1 thru 0`` (from ``limit=-1``) does **not** raise on a non-empty
  mailbox. AppleScript clamps index 0 up to 1 and returns exactly ONE message,
  verified by id to be the first. There is no error to catch.
* ``messages 1 thru -1`` (from ``limit=-2``, or from an ``offset`` negative
  enough to cancel the limit) is end-relative and spans the ENTIRE mailbox —
  the unbounded materialization this package bans everywhere else.

The sync bridge's callers are ``move_email``, ``manage_trash`` and
``export_emails`` (via the ``manage`` facade alias ``_search_mail_records`` and
the direct ``analytics`` import), two of which are mutations, so a bound this
bridge accepts can select a message the caller never asked for. The guard
therefore has to live on a seam both paths cross, and it has to be loud: the
async path's old ``limit <= 0 -> return [], [], [], False`` is an authoritative
empty indistinguishable from "no matches", which is the AGENTIC-2344 failure
shape. Nonsense bounds raise instead.
"""

import asyncio
import re

import pytest
from apple_mail_mcp.tools import manage
from apple_mail_mcp.tools.search import dispatch

# One synthetic record row in the pipe-delimited wire format the scan emits.
SEARCH_ROW = (
    "101|||<msg-101@example.com>|||Quarterly review|||sender@example.com|||INBOX|||"
    "Work|||false|||2026-08-17T09:00:00|||preview text|||to@example.com|||||||||||||false"
)

# Bound shapes that must never reach Mail.app, keyed by the kwargs that produce
# them through `base_cap = limit + 1 + offset`.
NON_POSITIVE_BOUND_KWARGS = [
    pytest.param({"limit": 0}, id="limit-0-binds-newest-message"),
    pytest.param({"limit": -1}, id="limit-neg1-binds-messages-1-thru-0"),
    pytest.param({"limit": -2}, id="limit-neg2-binds-whole-mailbox"),
    pytest.param({"offset": -1}, id="offset-neg1"),
    pytest.param({"offset": -102, "limit": 100}, id="offset-cancels-limit-binds-whole-mailbox"),
]


@pytest.fixture
def mail(monkeypatch):
    """Record every script the search package sends to Mail.app.

    Patches the ``search`` package facade, which is the seam `dispatch` routes
    its ``run_applescript`` calls through.
    """
    calls: list[str] = []

    def _run(script, timeout=None):
        calls.append(script)
        return SEARCH_ROW

    monkeypatch.setattr("apple_mail_mcp.tools.search.run_applescript", _run)
    return calls


def _scan_bounds(scripts: list[str]) -> list[int]:
    """Every literal ``scanUpperBound`` baked into the emitted scripts."""
    return [int(match) for script in scripts for match in re.findall(r"set scanUpperBound to (-?\d+)", script)]


@pytest.mark.parametrize("kwargs", NON_POSITIVE_BOUND_KWARGS)
def test_sync_bridge_refuses_non_positive_bounds(mail, kwargs):
    """The account path must raise before building any AppleScript."""
    with pytest.raises(ValueError):
        dispatch._search_mail_records_sync(account="Work", **kwargs)
    assert mail == [], "a refused page size still reached Mail.app"


@pytest.mark.parametrize("kwargs", NON_POSITIVE_BOUND_KWARGS)
def test_sync_bridge_never_emits_a_zero_or_negative_scan_bound(mail, kwargs):
    """Belt-and-braces on the emitted text, not just the call count.

    `messages 1 thru 0` returns a real message and `messages 1 thru -1` spans
    the whole mailbox, so neither bound may ever be written into a script.
    """
    with pytest.raises(ValueError):
        dispatch._search_mail_records_sync(account="Work", **kwargs)
    assert all(bound > 0 for bound in _scan_bounds(mail))


@pytest.mark.parametrize("kwargs", NON_POSITIVE_BOUND_KWARGS)
def test_manage_facade_alias_shares_the_guard(mail, kwargs):
    """``manage._search_mail_records`` IS the sync bridge.

    `move_email` and `manage_trash` reach the bridge exclusively through this
    alias, so the guard has to hold when it is called by its aliased name.
    """
    with pytest.raises(ValueError):
        manage._search_mail_records(account="Work", **kwargs)
    assert mail == []


@pytest.mark.parametrize("kwargs", NON_POSITIVE_BOUND_KWARGS)
def test_sync_bridge_multi_account_path_refuses_too(mail, kwargs):
    """No ``account`` routes through ``asyncio.run(_search_mail_records(...))``.

    That path must refuse for the same reason and with the same exception type,
    rather than returning the old silent empty, and must not spend the account
    listing round trip first.
    """
    with pytest.raises(ValueError):
        dispatch._search_mail_records_sync(**kwargs)
    assert mail == []


@pytest.mark.parametrize("kwargs", NON_POSITIVE_BOUND_KWARGS)
def test_async_path_raises_instead_of_returning_a_silent_empty(mail, kwargs):
    """The async guard must report a bad page, not absorb it.

    ``limit <= 0`` previously returned ``([], [], [], False)`` — a confident
    zero a caller cannot tell apart from an empty mailbox.
    """
    with pytest.raises(ValueError):
        asyncio.run(dispatch._search_mail_records(account="Work", **kwargs))
    assert mail == []


@pytest.mark.parametrize(
    "read_status",
    ["", "seen", "unseen", "UNREAD"],
)
def test_sync_bridge_rejects_invalid_read_status(mail, read_status):
    """The async path validated ``read_status``; the sync bridge did not.

    Proves the two paths now cross one shared validator rather than each
    carrying its own partial copy.
    """
    with pytest.raises(ValueError, match="read_status"):
        dispatch._search_mail_records_sync(account="Work", read_status=read_status, limit=5)
    assert mail == []


def test_sync_bridge_rejects_invalid_sort(mail):
    """Same seam, same reasoning, for the other bypassed validator."""
    with pytest.raises(ValueError, match="sort"):
        dispatch._search_mail_records_sync(account="Work", sort="relevance", limit=5)
    assert mail == []


def test_sync_bridge_valid_bounds_still_work_unchanged(mail):
    """Regression guard: the refusal must not narrow the working range."""
    records = dispatch._search_mail_records_sync(account="Work", limit=5)
    assert len(mail) == 1
    assert _scan_bounds(mail) == [6]  # base_cap = limit + 1 + offset
    assert [record["message_id"] for record in records] == ["101"]
    assert records[0]["sender"] == "sender@example.com"


def test_sync_bridge_valid_bounds_with_a_recent_window_still_work(mail):
    """A ``recent_days`` window widens the slice; the guard must not clamp it.

    The exact widened value is ``script``'s business (window cap, body cap and
    ``SEARCH_HARD_CEILING`` all feed it), so this only pins that the call
    happened with a positive bound at least as large as the requested page.
    """
    records = dispatch._search_mail_records_sync(account="Work", limit=5, recent_days=2.0)
    bounds = _scan_bounds(mail)
    assert len(bounds) == 1
    assert bounds[0] >= 6
    assert len(records) == 1


def test_sync_bridge_valid_paging_with_offset_still_works(mail):
    """A positive offset is legal and must keep widening the slice."""
    records = dispatch._search_mail_records_sync(account="Work", limit=10, offset=20)
    assert _scan_bounds(mail) == [31]
    assert len(records) == 1


@pytest.mark.parametrize("limit", [1, 2, 25])
def test_sync_bridge_accepts_the_smallest_legal_page(mail, limit):
    """``limit=1`` is the boundary the guard must not swallow."""
    records = dispatch._search_mail_records_sync(account="Work", limit=limit)
    assert _scan_bounds(mail) == [limit + 1]
    assert len(records) == 1


@pytest.mark.parametrize("read_status", ["all", "read", "unread"])
def test_sync_bridge_accepts_every_valid_read_status(mail, read_status):
    """The hoisted validator must keep the whole documented enum working."""
    assert dispatch._search_mail_records_sync(account="Work", read_status=read_status, limit=5)
    assert len(mail) == 1


def test_async_path_valid_bounds_still_work_unchanged(mail):
    """The shared validator must not disturb the async happy path."""
    records, errors, error_details, body_capped = asyncio.run(dispatch._search_mail_records(account="Work", limit=5))
    assert _scan_bounds(mail) == [6]
    assert [record["message_id"] for record in records] == ["101"]
    assert (errors, error_details, body_capped) == ([], [], False)
