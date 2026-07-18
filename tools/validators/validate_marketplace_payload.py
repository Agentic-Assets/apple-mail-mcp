#!/usr/bin/env python3
"""Validate and print the Apple Mail marketplace payload evidence."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from marketplace_payload import (  # noqa: E402
    PayloadContractError,
    build_inventory,
    inventory_json,
    load_contract,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the deterministic path/content digest inventory",
    )
    args = parser.parse_args()
    try:
        inventory = build_inventory(ROOT, load_contract(ROOT))
    except (PayloadContractError, OSError, ValueError) as exc:
        print("validate_marketplace_payload: FAILED", file=sys.stderr)
        for line in str(exc).splitlines():
            print(f"  ERROR: {line}", file=sys.stderr)
        return 1
    if args.json:
        print(inventory_json(inventory), end="")
    else:
        print(
            "validate_marketplace_payload: OK "
            f"(plugin={inventory['plugin_id']}, files={inventory['file_count']}, "
            f"sha256={inventory['payload_sha256']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
