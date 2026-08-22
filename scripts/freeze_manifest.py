#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

BASELINE_SHA = "e4676c8d494a9d83f42749a0b85eac2288de5a54"
EVIDENCE_GENERATION = "SIBYL_PAPER_V2"
MANIFEST_PATH = Path("evidence/freeze/SIBYL_PAPER_V2_MANIFEST.json")
PROTECTED_PATHS = (
    ".github/workflows/ci.yml",
    ".github/workflows/github-paper-trial.yml",
    "apps/gateway/package-lock.json",
    "scripts/secret-scan.sh",
    "services/backend/app/config.py",
    "services/backend/app/domain.py",
    "services/backend/app/evidence.py",
    "services/backend/app/hypothesis.py",
    "services/backend/app/paper.py",
    "services/backend/app/polymarket.py",
    "services/backend/app/latency.py",
    "services/backend/app/research.py",
    "services/backend/app/research_cycle.py",
    "services/backend/app/scoring.py",
    "services/backend/app/settlement.py",
    "services/backend/pyproject.toml",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_value(root: Path, value: str) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", value], cwd=root, text=True
    ).strip()


def git_blob(root: Path, commit: str, relative: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "show", f"{commit}:{relative}"], cwd=root
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"freeze source object unavailable for {relative} at {commit}; "
            "CI must checkout full history"
        ) from exc


def protected_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in PROTECTED_PATHS:
        path = root / relative
        if not path.is_file():
            raise SystemExit(f"freeze protected file missing: {relative}")
        hashes[relative] = sha256(path)
    return hashes


def frozen_protected_hashes(root: Path, frozen_source_sha: str) -> dict[str, str]:
    return {
        relative: sha256_bytes(git_blob(root, frozen_source_sha, relative))
        for relative in PROTECTED_PATHS
    }


def policy_checks(root: Path) -> dict[str, bool]:
    config = (root / "services/backend/app/config.py").read_text(encoding="utf-8")
    canonical_v5 = (root / ".github/workflows/github-paper-v5.yml").read_text(
        encoding="utf-8"
    )
    retired_v2 = (root / ".github/workflows/github-paper-trial.yml").read_text(
        encoding="utf-8"
    )
    secret_scan = (root / "scripts/secret-scan.sh").read_text(encoding="utf-8")
    checks = {
        "cost_authorization_locked_zero": bool(
            re.search(r"cost_authorized_usd: float = Field\(default=0\.0, ge=0\.0, le=0\.0\)", config)
        ),
        "live_validator_rejects_true": "if value:" in config
        and "LIVE trading is not available" in config,
        "canonical_v5_live_false": 'LIVE_TRADING_ENABLED: "false"' in canonical_v5,
        "canonical_v5_cost_zero": 'COST_AUTHORIZED_USD: "0"' in canonical_v5,
        "canonical_v5_ai_disabled": 'AI_ANALYSIS_ENABLED: "false"' in canonical_v5,
        "retired_v2_dispatch_only": "workflow_dispatch:" in retired_v2
        and "\n  push:" not in retired_v2
        and "\n  schedule:" not in retired_v2,
        "retired_v2_read_only": "contents: read" in retired_v2
        and "contents: write" not in retired_v2,
        "retired_v2_declares_no_execution": "does not create state, execute PAPER, publish Cloudflare, or use write permissions" in retired_v2,
        "secret_scan_checks_live": "LIVE trading enablement detected" in secret_scan,
        "secret_scan_checks_cost": "Non-zero cost authorization detected" in secret_scan,
        "secret_scan_uses_explicit_pattern_operand": 'git grep -n -E -e "$pattern"' in secret_scan,
    }
    return checks


def build_manifest(root: Path) -> dict[str, Any]:
    checks = policy_checks(root)
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise SystemExit(f"cannot freeze unsafe policy state: {failed}")
    return {
        "schema_version": 1,
        "baseline_main_sha": BASELINE_SHA,
        "frozen_source_sha": git_value(root, "HEAD"),
        "frozen_source_tree_sha": git_value(root, "HEAD^{tree}"),
        "evidence_generation": EVIDENCE_GENERATION,
        "scoring_version": "SCORE_V2",
        "risk_version": "RISK_V1_FROZEN",
        "simulator_version": "PAPER_SIM_V2",
        "polymarket_contract_version": "POLYMARKET_PREDICTIONS_2026-08-07",
        "cost_policy": {"authorized_usd": 0, "paid_apis": False},
        "live_policy": {"available": False, "real_money": False},
        "policy_checks": checks,
        "protected_files_sha256": protected_hashes(root),
    }


def write_manifest(root: Path) -> None:
    manifest = build_manifest(root)
    path = root / MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {MANIFEST_PATH}")


def check_manifest(root: Path) -> None:
    path = root / MANIFEST_PATH
    if not path.is_file():
        raise SystemExit(f"freeze manifest missing: {MANIFEST_PATH}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("baseline_main_sha") != BASELINE_SHA:
        raise SystemExit("freeze baseline SHA drift")
    if manifest.get("evidence_generation") != EVIDENCE_GENERATION:
        raise SystemExit("evidence generation drift")
    if manifest.get("cost_policy") != {"authorized_usd": 0, "paid_apis": False}:
        raise SystemExit("cost policy drift")
    if manifest.get("live_policy") != {"available": False, "real_money": False}:
        raise SystemExit("LIVE policy drift")

    frozen_source_sha = str(manifest.get("frozen_source_sha") or "")
    expected_tree = str(manifest.get("frozen_source_tree_sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", frozen_source_sha):
        raise SystemExit("freeze source SHA invalid")
    actual_tree = git_value(root, f"{frozen_source_sha}^{{tree}}")
    if actual_tree != expected_tree:
        raise SystemExit("freeze source tree drift")

    # The V2 manifest is historical evidence. Verify its hashes against the
    # immutable commit it froze, not against today's R4.5/V5 runtime files.
    expected = manifest.get("protected_files_sha256")
    actual = frozen_protected_hashes(root, frozen_source_sha)
    if expected != actual:
        changed = sorted(
            item
            for item in set(expected or {}) | set(actual)
            if (expected or {}).get(item) != actual.get(item)
        )
        raise SystemExit(
            "historical freeze object no longer matches manifest: " + ", ".join(changed)
        )

    # Current runtime safety is a separate contract. V2 is retired; R4.5/V5
    # must remain zero-cost/no-live and the retired workflow must remain inert.
    checks = policy_checks(root)
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise SystemExit("current runtime safety policy mismatch: " + ", ".join(failed))

    print("SIBYL_PAPER_V2 historical freeze + current R4.5 safety guard PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.write == args.check:
        parser.error("choose exactly one of --write or --check")
    root = Path(__file__).resolve().parents[1]
    if args.write:
        write_manifest(root)
    else:
        check_manifest(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
