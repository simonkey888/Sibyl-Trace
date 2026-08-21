from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .checkpoint import persist_evidence_directory
from .discovery import build_discovery_evidence, load_verified_pairs
from .economics import EconomicsLedger
from .feeds import public_feed_smoke
from .preflight import dry_run_preflight, emit_preflight, sanitized_upstream_env
from .simulate import write_simulated_hedge
from .upstream_adapter import write_upstream_config


def _persist(evidence_dir: Path, source_sha: str) -> list[str]:
    uploaded = persist_evidence_directory(evidence_dir, source_sha=source_sha)
    if uploaded:
        print(
            json.dumps({"event": "v6_gcs_checkpoint", "objects": uploaded}, sort_keys=True),
            flush=True,
        )
    return uploaded


def main() -> int:
    evidence_dir = Path(os.environ.get("SIBYL_V6_EVIDENCE_DIR", "/var/lib/sibyl-v6/evidence"))
    upstream_root = Path(os.environ.get("SIBYL_V6_UPSTREAM_ROOT", "/opt/agents-starter"))
    pair_file = Path(
        os.environ.get("SIBYL_V6_VERIFIED_PAIRS", "/app/v6/config/verified_pairs.json")
    )
    source_sha = os.environ.get("SOURCE_SHA", "UNKNOWN")
    evidence_dir.mkdir(parents=True, exist_ok=True)

    preflight = dry_run_preflight(upstream_root)
    emit_preflight(evidence_dir / "preflight.json", preflight)
    if preflight.DRY_RUN_PREFLIGHT != "PASS":
        print(json.dumps({"event": "v6_preflight_failed", **preflight.to_dict()}), flush=True)
        _persist(evidence_dir, source_sha)
        return 2

    feeds = public_feed_smoke()
    feeds["source_sha"] = source_sha
    (evidence_dir / "public-feeds.json").write_text(
        json.dumps(feeds, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if feeds["status"] != "PASS":
        print(json.dumps({"event": "v6_public_feed_failed", "feeds": feeds}), flush=True)
        _persist(evidence_dir, source_sha)
        return 3

    discovery = build_discovery_evidence(evidence_dir / "pair-discovery.json", pair_file)
    discovery["source_sha"] = source_sha
    (evidence_dir / "pair-discovery.json").write_text(
        json.dumps(discovery, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    exact = load_verified_pairs(pair_file)

    synthetic = write_simulated_hedge(evidence_dir / "simulated-hedge.json")
    synthetic["source_sha"] = source_sha
    (evidence_dir / "simulated-hedge.json").write_text(
        json.dumps(synthetic, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    economics = EconomicsLedger()
    economics_payload = economics.to_dict()
    economics_payload["source_sha"] = source_sha
    (evidence_dir / "economics.json").write_text(
        json.dumps(economics_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    summary = {
        "schema_version": "SIBYL_V6_RUNTIME_SUMMARY_V1",
        "event": "v6_ready",
        "timestamp_ms": int(time.time() * 1000),
        "source_sha": source_sha,
        "DRY_RUN_PREFLIGHT": preflight.DRY_RUN_PREFLIGHT,
        "LIVE_PREFLIGHT": preflight.LIVE_PREFLIGHT,
        "CANDIDATE_PAIR_COUNT": discovery["CANDIDATE_PAIR_COUNT"],
        "EXACT_EQUIVALENT_PAIR_COUNT": discovery["EXACT_EQUIVALENT_PAIR_COUNT"],
        "SIMULATED_HEDGE_RESULT": synthetic["result"]["status"],
        "SIMULATED_HEDGE_EVIDENCE_HASH": synthetic["result"]["evidence_hash"],
        "TARGET_80_STATUS": economics.target_80_status().value,
        "REALIZED_NET_24H": "0",
        "LIVE": "NO",
        "REAL_ORDERS": 0,
        "CAPITAL_MOVED_USD": "0",
    }
    (evidence_dir / "runtime-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, sort_keys=True), flush=True)
    _persist(evidence_dir, source_sha)

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
        print(
            json.dumps({"event": "v6_upstream_execution_disabled", "exact_pairs": len(exact)}),
            flush=True,
        )
        return 0

    # No caller-controlled upstream config is accepted. The only config handed
    # to the official strategy is generated from the exact-equivalent set.
    config_path = Path("/tmp/sibyl-v6-cross-market-mm.config.json")
    upstream_config = write_upstream_config(exact, config_path)
    binding_path = evidence_dir / "upstream-binding.json"
    binding_path.write_text(
        json.dumps(upstream_config["binding"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _persist(evidence_dir, source_sha)

    env = sanitized_upstream_env()
    env["CROSS_MARKET_MM_CONFIG_PATH"] = str(config_path)
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
