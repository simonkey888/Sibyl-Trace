from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .discovery import build_discovery_evidence, load_verified_pairs
from .economics import EconomicsLedger
from .feeds import public_feed_smoke
from .preflight import dry_run_preflight, emit_preflight, sanitized_upstream_env


def main() -> int:
    evidence_dir = Path(os.environ.get("SIBYL_V6_EVIDENCE_DIR", "/var/lib/sibyl-v6/evidence"))
    upstream_root = Path(os.environ.get("SIBYL_V6_UPSTREAM_ROOT", "/opt/agents-starter"))
    pair_file = Path(os.environ.get("SIBYL_V6_VERIFIED_PAIRS", "/app/v6/config/verified_pairs.json"))
    evidence_dir.mkdir(parents=True, exist_ok=True)

    preflight = dry_run_preflight(upstream_root)
    emit_preflight(evidence_dir / "preflight.json", preflight)
    if preflight.DRY_RUN_PREFLIGHT != "PASS":
        print(json.dumps({"event": "v6_preflight_failed", **preflight.to_dict()}), flush=True)
        return 2

    feeds = public_feed_smoke()
    (evidence_dir / "public-feeds.json").write_text(
        json.dumps(feeds, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if feeds["status"] != "PASS":
        print(json.dumps({"event": "v6_public_feed_failed", "feeds": feeds}), flush=True)
        return 3

    discovery = build_discovery_evidence(evidence_dir / "pair-discovery.json", pair_file)
    exact = load_verified_pairs(pair_file)
    economics = EconomicsLedger()
    (evidence_dir / "economics.json").write_text(
        json.dumps(economics.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "event": "v6_ready",
                "timestamp_ms": int(time.time() * 1000),
                "DRY_RUN_PREFLIGHT": preflight.DRY_RUN_PREFLIGHT,
                "LIVE_PREFLIGHT": preflight.LIVE_PREFLIGHT,
                "CANDIDATE_PAIR_COUNT": discovery["CANDIDATE_PAIR_COUNT"],
                "EXACT_EQUIVALENT_PAIR_COUNT": discovery["EXACT_EQUIVALENT_PAIR_COUNT"],
                "TARGET_80_STATUS": economics.target_80_status().value,
                "LIVE": "NO",
            },
            sort_keys=True,
        ),
        flush=True,
    )

    if not exact:
        print(
            json.dumps(
                {
                    "event": "v6_no_exact_pairs",
                    "action": "UPSTREAM_EXECUTION_REFUSED",
                    "reason": "NO_EXACT_EQUIVALENT_PAIR",
                }
            ),
            flush=True,
        )
        return 0

    if os.environ.get("SIBYL_V6_RUN_UPSTREAM", "0") != "1":
        print(json.dumps({"event": "v6_upstream_execution_disabled", "exact_pairs": len(exact)}), flush=True)
        return 0

    env = sanitized_upstream_env()
    # The wrapper delegates execution; it does not copy/reimplement cross-market-mm.
    proc = subprocess.run(
        ["npm", "run", "cross-market-mm"],
        cwd=upstream_root,
        env=env,
        check=False,
    )
    return int(proc.returncode)


if __name__ == "__main__":
    sys.exit(main())
