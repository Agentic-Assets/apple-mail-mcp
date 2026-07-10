"""Entry point for `python -m apple_mail_mcp` and `apple-mail-mcp` CLI."""

import argparse
import os
import threading
import time
from collections.abc import Callable
from contextlib import suppress

import apple_mail_mcp.server as server


# Workaround for modelcontextprotocol/python-sdk#526.
# When the MCP client (e.g. Claude) exits without closing stdin cleanly,
# the FastMCP server can keep running orphaned in the background. The
# orphaned server keeps polling Mail.app via Apple Events, which causes
# Mail to relaunch after the user quits it. Capture the initial PPID at
# startup and self-terminate when it changes (parent died, we have been
# reparented). Uses os._exit because sys.exit does not tear down FastMCP's
# background asyncio loop reliably. get_ppid and exit_fn are injectable
# for unit testing.
def _start_orphan_watcher(
    interval_sec: int = 10,
    get_ppid: Callable[[], int] = os.getppid,
    exit_fn: Callable[[int], object] = os._exit,
) -> None:
    initial_ppid = get_ppid()

    def _watch() -> None:
        while True:
            if get_ppid() != initial_ppid:
                exit_fn(0)
                return
            time.sleep(interval_sec)

    threading.Thread(target=_watch, daemon=True).start()


def main() -> None:
    _start_orphan_watcher()

    parser = argparse.ArgumentParser(description="Apple Mail MCP Server")
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Disable tools that send email (compose, reply, forward). Drafts can still be created and listed.",
    )
    parser.add_argument(
        "--draft-safe",
        action="store_true",
        help="Disable actual sends while keeping draft/open composition tools available.",
    )
    args = parser.parse_args()

    server.READ_ONLY = args.read_only
    server.DRAFT_SAFE = args.draft_safe or args.read_only

    from apple_mail_mcp import mcp  # noqa: E402
    from apple_mail_mcp.server import CALENDAR_DESTRUCTIVE_TOOLS, CALENDAR_WRITE_TOOLS, SEND_TOOLS

    if args.read_only:
        for name in SEND_TOOLS + CALENDAR_WRITE_TOOLS + CALENDAR_DESTRUCTIVE_TOOLS:
            with suppress(KeyError, ValueError):
                mcp.remove_tool(name)

    mcp.run()


if __name__ == "__main__":
    main()
