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


def git_value(root: Path, value: str) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", value], cwd=root, text=True
    ).strip()


def protected_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in PROTECTED_PATHS:
        path = root / relative
        if not path.is_file():
            raise SystemExit(f"freeze protected file missing: {relative}")
        hashes[relative] = sha256(path)
    return hashes


def policy_checks(root: Path) -> dict[str, bool]:
    config = (root / "services/backend/app/config.py").read_text(encoding="utf-8")
    workflow = (root / ".github/workflows/github-paper-trial.yml").read_text(
        encoding="utf-8"
    )
    secret_scan = (root / "scripts/secret-scan.sh").read_text(encoding="utf-8")
    checks = {
        "cost_authorization_locked_zero": bool(
            re.search(r"cost_authorized_usd: float = Field\(default=0\.0, ge=0\.0, le=0\.0\)", config)
        ),
        "live_validator_rejects_true": "if value:" in config
        and "LIVE trading is not available" in config,
        "scheduled_live_false": 'LIVE_TRADING_ENABLED: "false"' in workflow,
        "scheduled_cost_zero": 'COST_AUTHORIZED_USD: "0"' in workflow,
        "scheduled_ai_disabled": 'AI_ANALYSIS_ENABLED: "false"' in workflow,
        "secret_scan_checks_live": "LIVE trading enablement detected" in secret_scan,
        "secret_scan_checks_cost": "Non-zero cost authorization detected" in secret_scan,
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
    checks = policy_checks(root)
    if not all(checks.values()):
        raise SystemExit("runtime safety policy no longer matches freeze contract")
    expected = manifest.get("protected_files_sha256")
    actual = protected_hashes(root)
    if expected != actual:
        changed = sorted(
            path for path in set(expected or {}) | set(actual) if (expected or {}).get(path) != actual.get(path)
        )
        raise SystemExit(
            "protected files changed without a new preregistered freeze: " + ", ".join(changed)
        )
    print("SIBYL_PAPER_V2 freeze guard PASS")


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
