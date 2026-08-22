from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

UPSTREAM_SOURCE_SHA = "e35ad881f88c7b5d60388461095ee11b7aa161c5"
BAD_URL = "https://pkg.pr.new/ox@386a3439fe1ce76d237930f8c6e6bb493746069a"
BAD_VERSION = "0.14.26-386a343.0"
BAD_INTEGRITY = "sha512-OHHm9re1yVjiMN66GZ2JSGuqmvJPrk40zh3PIS/3I6prZLbt6U/zKlgW18eIIkO0Y/ZyySKr6D/4mUXjBmky1g=="
FIXED_URL = "https://registry.npmjs.org/ox/-/ox-0.14.26.tgz"
FIXED_VERSION = "0.14.26"
FIXED_INTEGRITY = "sha512-t68x49i+heyvHFp1iSq+JjgGyNGJ0AujNMTGXKt96sPGxX4T74c5QUE3LL57hDxGXYG/PM4sq2xCJ4/wCGOQ/w=="


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def count_value(value, needle: str) -> int:
    if isinstance(value, dict):
        return sum(count_value(v, needle) for v in value.values())
    if isinstance(value, list):
        return sum(count_value(v, needle) for v in value)
    return int(value == needle)


def repair(lock_path: Path, provenance_path: Path) -> dict:
    original = lock_path.read_bytes()
    data = json.loads(original)
    if count_value(data, BAD_URL) != 2:
        raise RuntimeError("UPSTREAM_LOCK_BAD_URL_CARDINALITY_CHANGED")
    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise RuntimeError("UPSTREAM_LOCK_PACKAGES_MISSING")
    ox = packages.get("node_modules/ox")
    viem = packages.get("node_modules/viem")
    if not isinstance(ox, dict) or not isinstance(viem, dict):
        raise RuntimeError("UPSTREAM_LOCK_EXPECTED_NODES_MISSING")
    expected = {
        "version": BAD_VERSION,
        "resolved": BAD_URL,
        "integrity": BAD_INTEGRITY,
    }
    for key, value in expected.items():
        if ox.get(key) != value:
            raise RuntimeError(f"UPSTREAM_OX_{key.upper()}_CHANGED")
    dependencies = viem.get("dependencies")
    if not isinstance(dependencies, dict) or dependencies.get("ox") != BAD_URL:
        raise RuntimeError("UPSTREAM_VIEM_OX_EDGE_CHANGED")

    ox["version"] = FIXED_VERSION
    ox["resolved"] = FIXED_URL
    ox["integrity"] = FIXED_INTEGRITY
    dependencies["ox"] = FIXED_VERSION

    if count_value(data, BAD_URL) != 0:
        raise RuntimeError("UPSTREAM_EPHEMERAL_URL_REMAINS")
    patched = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    lock_path.write_bytes(patched)
    patch_bytes = Path(__file__).read_bytes()
    provenance = {
        "schema_version": "SIBYL_V6_DEPENDENCY_PROVENANCE_V1",
        "SOURCE_PROVENANCE_SHA": UPSTREAM_SOURCE_SHA,
        "DEPENDENCY_RESOLUTION_PROVENANCE": "SIBYL_MINIMAL_LOCK_REPAIR:pkg.pr.new/ox@386a3439->registry.npmjs.org/ox@0.14.26",
        "ORIGINAL_LOCK_SHA256": sha256(original),
        "LOCK_PATCH_HASH": sha256(patch_bytes),
        "PATCHED_LOCK_SHA256": sha256(patched),
        "PACKAGE_INTEGRITY_HASHES": {
            "ox@0.14.26": FIXED_INTEGRITY,
            "upstream_preview_ox_removed": BAD_INTEGRITY,
        },
        "changed_nodes": [
            "packages.node_modules/ox.{version,resolved,integrity}",
            "packages.node_modules/viem.dependencies.ox",
        ],
        "floating_dependency_versions_added": False,
        "lock_deleted": False,
    }
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return provenance


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: repair_lock.py PACKAGE_LOCK PROVENANCE_JSON")
    print(json.dumps(repair(Path(sys.argv[1]), Path(sys.argv[2])), sort_keys=True))
