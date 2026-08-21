#!/usr/bin/env python3
"""Print a verified, compact handoff for an Apple Mail Marketplace update."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from release import source_release  # noqa: I001


ROOT = Path(__file__).resolve().parents[2]
IDENTITY_PATH = Path("tools/marketplace_identity.json")


def _required_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        source_release.fail(f"marketplace identity has invalid {field}")
    return value


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        source_release.fail(f"marketplace identity has invalid {field}")
    return value


def marketplace_identity(root: Path, commit: str, policy: source_release.Policy) -> dict[str, str]:
    """Read the release commit's Marketplace contract and reject identity drift."""
    raw = source_release.blob(root, commit, IDENTITY_PATH.as_posix())
    identity = json.loads(raw.decode("utf-8"))
    if not isinstance(identity, dict) or identity.get("schema_version") != 1:
        source_release.fail("unsupported marketplace identity schema")
    plugin = _required_object(identity.get("plugin"), "plugin")
    primary = _required_object(identity.get("primary_marketplace"), "primary_marketplace")
    promotion = _required_object(identity.get("promotion"), "promotion")

    plugin_id = _required_string(plugin.get("id"), "plugin.id")
    source_repository = _required_string(plugin.get("source_repository"), "plugin.source_repository")
    source_payload = _required_string(plugin.get("source_payload"), "plugin.source_payload")
    repository = _required_string(primary.get("repository"), "primary_marketplace.repository")
    selector = _required_string(primary.get("selector"), "primary_marketplace.selector")
    destination = _required_string(primary.get("payload_destination"), "primary_marketplace.payload_destination")

    if plugin_id != policy.plugin_id:
        source_release.fail("marketplace identity plugin does not match source-release policy")
    if source_payload != policy.payload_root:
        source_release.fail("marketplace identity payload root does not match source-release policy")
    if source_release.canonical_repository(root, source_repository) != source_release.canonical_repository(
        root, policy.repository
    ):
        source_release.fail("marketplace identity source repository does not match source-release policy")
    if promotion.get("source_ref") != "immutable-signed-tag":
        source_release.fail("marketplace identity must require immutable signed tags")
    if promotion.get("policy_owner") != "marketplace":
        source_release.fail("marketplace identity must keep promotion policy in the Marketplace")
    if promotion.get("evidence_owner") != "marketplace" or promotion.get("attestation_owner") != "marketplace":
        source_release.fail("marketplace identity must keep evidence and attestation in the Marketplace")

    return {
        "plugin_id": plugin_id,
        "repository": repository,
        "selector": selector,
        "payload_destination": destination,
    }


def handoff(root: Path, tag: str) -> dict[str, object]:
    """Return only facts bound to a remotely verified signed source tag."""
    bindings = source_release.verify_tag(root, tag, require_remote=True)
    policy = source_release.load_policy_at(root, bindings.commit)
    identity = marketplace_identity(root, bindings.commit, policy)
    tag_object = source_release.git(root, "rev-parse", f"refs/tags/{tag}^{{tag}}")
    return {
        "schema_version": 1,
        "source": {
            "tag": tag,
            "tag_object": tag_object,
            "commit": bindings.commit,
            "version": bindings.version,
            "payload_inventory_sha256": bindings.payload_inventory_sha256,
            "requirements_lock_sha256": bindings.requirements_lock_sha256,
            "wheelhouse_sha256": bindings.wheelhouse_inventory_sha256,
        },
        "marketplace": identity,
    }


def render_handoff(record: dict[str, object]) -> str:
    source = _required_object(record.get("source"), "source")
    marketplace = _required_object(record.get("marketplace"), "marketplace")
    return "\n".join(
        (
            "Marketplace handoff ready (source verification passed)",
            f"- Release: {_required_string(source.get('tag'), 'source.tag')} -> "
            f"{_required_string(source.get('commit'), 'source.commit')}",
            f"- Source inventory SHA-256: {_required_string(source.get('payload_inventory_sha256'), 'source.payload_inventory_sha256')}",
            f"- Target: {_required_string(marketplace.get('repository'), 'marketplace.repository')}",
            f"- Selector: {_required_string(marketplace.get('selector'), 'marketplace.selector')}",
            "",
            "Next steps in a clean Marketplace chore/* branch:",
            f"1. python3 tools/prepare_plugin_update.py --plugin "
            f"{_required_string(marketplace.get('plugin_id'), 'marketplace.plugin_id')} --prepare --next-steps",
            "2. Run the isolated client checks required by that candidate digest; commit and push the candidate plus redacted proof files.",
            "3. Add digest-bound evidence, admit it, sign its attestation, and run the Marketplace release gate.",
            "4. Open and merge a normal reviewed PR. After merge, return here and run:",
            "   bash tools/gates/refresh-central-marketplace.sh",
            "",
            "The source inventory hash binds the signed source tag. The Marketplace computes its candidate payload digest during preparation; admission, client evidence, and attestation remain separate release authority.",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("tag")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        record = handoff(args.root.resolve(), args.tag)
    except (source_release.ReleaseError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(record, sort_keys=True))
    else:
        print(render_handoff(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
