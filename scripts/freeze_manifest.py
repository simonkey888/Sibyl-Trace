#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

AUDITED_BASE_SHA = "139f704375e0f71c4eeb1d4bebcd262c6d3599aa"
V2_MANIFEST_PATH = Path("evidence/freeze/SIBYL_PAPER_V2_MANIFEST.json")
V2_MANIFEST_SHA256 = "4324a67b881ad28871afe469d18e279dd9268e156b45aeaa5715ac25b0a77a65"
V5_MANIFEST_PATH = Path("evidence/freeze/SIBYL_PAPER_V5_SAFETY_MANIFEST.json")
V5_CONTRACT_ID = "SIBYL_PAPER_V5_CURRENT_SAFETY_V1"

V5_PROTECTED_PATHS = (
    ".github/workflows/ci.yml",
    ".github/workflows/github-paper-v5.yml",
    ".github/workflows/paper-v5-candidate.yml",
    ".github/workflows/paper-v5-oos-preregister.yml",
    ".github/workflows/paper-v5-oos-registration-finalize.yml",
    ".github/workflows/publish-cloudflare-terminal-v5.yml",
    "services/backend/app/config.py",
    "services/backend/app/ai.py",
    "services/backend/app/scanner.py",
    "services/backend/app/source_strategy.py",
    "services/backend/app/evidence_v1.py",
    "services/backend/app/evidence_lineage.py",
    "services/backend/app/research_history_v1.py",
    "services/backend/app/ci_gate.py",
    "services/backend/app/scoring.py",
    "services/backend/app/paper_v5_r43.py",
    "services/backend/app/paper_v5_r44.py",
    "services/backend/app/paper_v5_r45.py",
    "services/backend/app/cloudflare_snapshot_r45.py",
    "scripts/secret-scan.sh",
    "scripts/test-secret-scan.sh",
    "scripts/freeze_manifest.py",
    "services/backend/pyproject.toml",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def git_bytes(root: Path, revision: str, relative: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{revision}:{relative}"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"historical frozen object unavailable: {revision}:{relative}")
    return result.stdout


def check_historical_v2(root: Path) -> None:
    path = root / V2_MANIFEST_PATH
    if not path.is_file():
        raise SystemExit(f"historical V2 manifest missing: {V2_MANIFEST_PATH}")
    if sha256(path) != V2_MANIFEST_SHA256:
        raise SystemExit("historical V2 manifest bytes changed")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    frozen = str(manifest.get("frozen_source_sha") or "")
    frozen_tree = str(manifest.get("frozen_source_tree_sha") or "")
    actual_tree = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", f"{frozen}^{{tree}}"], text=True
    ).strip()
    if actual_tree != frozen_tree:
        raise SystemExit("historical V2 frozen source tree mismatch")
    for relative, expected in manifest.get("protected_files_sha256", {}).items():
        actual = sha256_bytes(git_bytes(root, frozen, relative))
        if actual != expected:
            raise SystemExit(f"historical V2 frozen object hash mismatch: {relative}")
    print("SIBYL_PAPER_V2_HISTORICAL_FREEZE=PASS")


def current_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in V5_PROTECTED_PATHS:
        path = root / relative
        if not path.is_file():
            raise SystemExit(f"V5 safety protected file missing: {relative}")
        hashes[relative] = sha256(path)
    return hashes


def policy_checks(root: Path) -> dict[str, bool]:
    def text(relative: str) -> str:
        return (root / relative).read_text(encoding="utf-8")

    config = text("services/backend/app/config.py")
    ai = text("services/backend/app/ai.py")
    scoring = text("services/backend/app/scoring.py")
    source = text("services/backend/app/source_strategy.py")
    evidence = text("services/backend/app/evidence_v1.py")
    lineage = text("services/backend/app/evidence_lineage.py")
    ci = text(".github/workflows/ci.yml")
    v5 = text(".github/workflows/github-paper-v5.yml")
    candidate = text(".github/workflows/paper-v5-candidate.yml")
    publisher = text(".github/workflows/publish-cloudflare-terminal-v5.yml")
    secret_scan = text("scripts/secret-scan.sh")

    required_ci = (
        "backend",
        "dashboard",
        "safety",
        "container",
        "research-evidence",
        "scoring-integrity",
    )
    checks = {
        "audited_base_registered": len(AUDITED_BASE_SHA) == 40,
        "cost_authorization_locked_zero": bool(
            re.search(
                r"cost_authorized_usd: float = Field\(default=0\.0, ge=0\.0, le=0\.0\)",
                config,
            )
        ),
        "live_unavailable": "LIVE trading is not available in Sibyl Trace" in config,
        "github_trial_billable_ai_rejected": "github-trial forbids billable AI analysis at USD0" in config,
        "billable_ai_runtime_blocked": "billable_ai_blocked_by_zero_cost_authorization" in ai,
        "score_windows_50_200": "canonical_score_windows(closed_positions)" in scoring,
        "source_history_metadata_required": "source_strategy_history_evidence_missing" in source,
        "source_history_incomplete_fails_closed": "source_strategy_history_incomplete" in source,
        "maker_rebate_not_directionality_shortcut": 'counts["MAKER_REBATE"] > 0' not in source,
        "trusted_oos_github_registration_required": (
            "TrustedOOSRegistrationProof" in evidence
            and "oos_trusted_registration_required" in evidence
            and 'registration_provider": "GITHUB"' in evidence
            and "paper-v5-oos-preregister.yml"
            in str(V5_PROTECTED_PATHS)
        ),
        "compatibility_computed_not_declared": (
            "compute_compatibility" in lineage
            and "compatibility_status:" not in lineage
            and "semantic_contract_hash" in lineage
        ),
        "canonical_v5_paper_only": (
            'LIVE_TRADING_ENABLED: "false"' in v5
            and 'COST_AUTHORIZED_USD: "0"' in v5
            and 'AI_ANALYSIS_ENABLED: "false"' in v5
        ),
        "canonical_v5_exact_ci_gate_before_state": (
            "Require exact-head CI before any rolling-state transition" in v5
            and "python -m app.ci_gate" in v5
        ),
        "canonical_v5_transition_idempotency": (
            "TRANSITIONS.json" in v5 and "transition_key" in v5
        ),
        "canonical_v5_no_push_reset": "\n  push:" not in v5 and "reset_state" not in v5,
        "ci_requires_all_required_jobs": (
            "paper-v5-dispatch:" in ci
            and all(f"      - {name}" in ci for name in required_ci)
        ),
        "candidate_read_only": (
            "permissions:\n  actions: read\n  contents: read" in candidate
            and "CLOUDFLARE_API_TOKEN" not in candidate
            and "wrangler deploy" not in candidate
            and "gh release" not in candidate
        ),
        "publisher_exact_ci_gate": (
            "Require exact-source full CI green before publication" in publisher
            and "python -m app.ci_gate" in publisher
        ),
        "publisher_no_manual_bypass": "workflow_dispatch:" not in publisher,
        "publisher_computed_compatibility": (
            "compatibility_contract" in publisher
            and "compatibility_status=" not in publisher
        ),
        "secret_scan_fail_closed": (
            "git grep -n -E -e" in secret_scan
            and "Secret/live/cost scanner execution failure" in secret_scan
        ),
    }
    return checks


def build_v5_manifest(root: Path) -> dict[str, Any]:
    checks = policy_checks(root)
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise SystemExit("cannot freeze unsafe V5 state: " + ", ".join(failed))
    return {
        "schema_version": 1,
        "contract_id": V5_CONTRACT_ID,
        "audited_base_sha": AUDITED_BASE_SHA,
        "scope": "current Sibyl PAPER V5 safety and evidence contract",
        "cost_policy": {"authorized_usd": 0, "paid_apis": False},
        "live_policy": {
            "available": False,
            "real_money": False,
            "order_placement": False,
            "private_keys": False,
        },
        "external_sources_policy": {
            "boros_execution": False,
            "kalshi_execution": False,
            "external_research_can_change_score_or_execution": False,
        },
        "policy_checks": checks,
        "protected_files_sha256": current_hashes(root),
    }


def write_v5(root: Path) -> None:
    manifest = build_v5_manifest(root)
    path = root / V5_MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {V5_MANIFEST_PATH}")


def check_v5(root: Path) -> None:
    path = root / V5_MANIFEST_PATH
    if not path.is_file():
        raise SystemExit(f"current V5 safety manifest missing: {V5_MANIFEST_PATH}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("contract_id") != V5_CONTRACT_ID:
        raise SystemExit("current V5 safety contract ID drift")
    if manifest.get("audited_base_sha") != AUDITED_BASE_SHA:
        raise SystemExit("current V5 audited base drift")
    expected_policy = {
        "authorized_usd": 0,
        "paid_apis": False,
    }
    if manifest.get("cost_policy") != expected_policy:
        raise SystemExit("current V5 cost policy drift")
    checks = policy_checks(root)
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise SystemExit("current V5 policy check failed: " + ", ".join(failed))
    if manifest.get("policy_checks") != checks:
        raise SystemExit("current V5 frozen policy result drift")
    expected = manifest.get("protected_files_sha256") or {}
    actual = current_hashes(root)
    if expected != actual:
        changed = sorted(name for name in set(expected) | set(actual) if expected.get(name) != actual.get(name))
        raise SystemExit("current V5 protected file drift: " + ", ".join(changed))
    print("SIBYL_PAPER_V5_CURRENT_SAFETY=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-historical-v2", action="store_true")
    parser.add_argument("--write-v5", action="store_true")
    parser.add_argument("--check-v5", action="store_true")
    parser.add_argument("--check", action="store_true", help="check both historical V2 and current V5")
    args = parser.parse_args()
    selected = sum(bool(x) for x in (args.check_historical_v2, args.write_v5, args.check_v5, args.check))
    if selected != 1:
        parser.error("choose exactly one verification/write mode")
    root = Path(__file__).resolve().parents[1]
    if args.check_historical_v2:
        check_historical_v2(root)
    elif args.write_v5:
        check_historical_v2(root)
        write_v5(root)
    elif args.check_v5:
        check_v5(root)
    else:
        check_historical_v2(root)
        check_v5(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
