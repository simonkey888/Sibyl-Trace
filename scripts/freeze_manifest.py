#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

BASE_SHA = "139f704375e0f71c4eeb1d4bebcd262c6d3599aa"
ORDER_ID = "ORDER-001"
MANIFEST_PATH = Path("evidence/freeze/ORDER-001-SAFETY-MANIFEST.json")
PROTECTED_PATHS = (
    ".github/workflows/ci.yml",
    ".github/workflows/github-paper-v5.yml",
    ".github/workflows/publish-cloudflare-terminal-v5.yml",
    ".github/workflows/order-001-candidate.yml",
    "services/backend/app/config.py",
    "services/backend/app/ai.py",
    "services/backend/app/scanner.py",
    "services/backend/app/research_history_v1.py",
    "services/backend/app/wallet_forensics.py",
    "apps/dashboard/public/app.js",
    "apps/dashboard/public/index.html",
    "services/backend/app/domain.py",
    "services/backend/app/scoring.py",
    "services/backend/app/source_strategy.py",
    "services/backend/app/evidence_v1.py",
    "services/backend/app/evidence_lineage.py",
    "services/backend/app/ci_gate.py",
    "services/backend/app/paper_v5.py",
    "services/backend/app/paper_v5_r3.py",
    "services/backend/app/paper_v5_r4.py",
    "services/backend/app/paper_v5_r42.py",
    "services/backend/app/paper_v5_r43.py",
    "services/backend/app/paper_v5_r44.py",
    "services/backend/app/paper_v5_r45.py",
    "services/backend/app/cloudflare_snapshot_r44.py",
    "services/backend/app/cloudflare_snapshot_r45.py",
    "scripts/secret-scan.sh",
    "scripts/test-secret-scan.sh",
    "scripts/freeze_manifest.py",
    "services/backend/pyproject.toml",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def protected_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in PROTECTED_PATHS:
        path = root / relative
        if not path.is_file():
            raise SystemExit(f"freeze protected file missing: {relative}")
        hashes[relative] = sha256(path)
    return hashes


def policy_checks(root: Path) -> dict[str, bool]:
    def text(relative: str) -> str:
        return (root / relative).read_text(encoding="utf-8")

    config = text("services/backend/app/config.py")
    scoring = text("services/backend/app/scoring.py")
    source = text("services/backend/app/source_strategy.py")
    evidence = text("services/backend/app/evidence_v1.py")
    ci = text(".github/workflows/ci.yml")
    v5 = text(".github/workflows/github-paper-v5.yml")
    publisher = text(".github/workflows/publish-cloudflare-terminal-v5.yml")
    candidate = text(".github/workflows/order-001-candidate.yml")
    secret_scan = text("scripts/secret-scan.sh")

    ci_required = (
        "backend",
        "dashboard",
        "safety",
        "container",
        "research-evidence",
        "scoring-integrity",
    )
    publisher_ci_index = publisher.find(
        "Require exact-source full CI green before publication"
    )
    publisher_secret_index = publisher.find(
        "Require Cloudflare configuration only after every truth gate"
    )
    publisher_deploy_index = publisher.find(
        "Deploy truthful public read-only terminal"
    )

    checks = {
        "base_sha_registered": len(BASE_SHA) == 40,
        "cost_authorization_locked_zero": bool(
            re.search(
                r"cost_authorized_usd: float = Field\(default=0\.0, ge=0\.0, le=0\.0\)",
                config,
            )
        ),
        "live_validator_rejects_true": (
            "LIVE trading is not available in Sibyl Trace" in config
        ),
        "billable_ai_rejected_globally_at_zero": (
            "cost_authorized_usd > 0"
            in text("services/backend/app/ai.py")
            and "billable_ai_blocked_by_zero_cost_authorization"
            in text("services/backend/app/ai.py")
        ),
        "score_windows_canonical_50_200": (
            "canonical_score_windows(closed_positions)" in scoring
            and "0.60 * short_score + 0.40 * long_score" in scoring
        ),
        "source_activity_completeness_gate": (
            "ActivityHistoryEvidence" in source
            and "activity_history_has_more" in source
            and "source_strategy_history_incomplete" in source
            and "offset=target" in source
        ),
        "maker_rebate_not_directionality_shortcut": (
            'counts["MAKER_REBATE"] > 0' not in source
        ),
        "prospective_oos_registry": (
            "ProspectiveOOSCohort" in evidence
            and "persist_prospective_oos_cohort" in evidence
            and "os.O_EXCL" in evidence
        ),
        "external_t0_never_future": (
            'timestamps["t0"] > source_timestamp_ms' in evidence
            and "external_t0_must_not_use_future_data" in evidence
        ),
        "canonical_v5_not_push_triggered": "\n  push:" not in v5,
        "canonical_v5_no_reset_input": "reset_state" not in v5,
        "canonical_v5_live_false": 'LIVE_TRADING_ENABLED: "false"' in v5,
        "canonical_v5_cost_zero": 'COST_AUTHORIZED_USD: "0"' in v5,
        "canonical_v5_ai_disabled": 'AI_ANALYSIS_ENABLED: "false"' in v5,
        "canonical_v5_serialized": (
            "group: sibyl-github-paper-v5" in v5
            and "cancel-in-progress: false" in v5
        ),
        "ci_requires_all_six_gates_before_paper": (
            "paper-v5-dispatch:" in ci
            and all(f"      - {name}" in ci for name in ci_required)
            and "gh workflow run github-paper-v5.yml --ref main" in ci
        ),
        "publisher_checks_exact_ci_before_secrets_and_deploy": (
            0 <= publisher_ci_index < publisher_secret_index < publisher_deploy_index
            and "python -m app.ci_gate" in publisher
        ),
        "publisher_requires_evidence_lineage": (
            "evidence-lineage.json" in publisher
            and "validate_evidence_lineage" in publisher
        ),
        "candidate_read_only_permissions": (
            "permissions:\n  contents: read" in candidate
            and "ORDER_001_NO_PRODUCTION_MUTATION=PASS" in candidate
        ),
        "secret_scan_uses_pattern_separator": "git grep -n -E -e" in secret_scan,
        "secret_scan_distinguishes_execution_error": (
            "if [[ $rc -eq 1 ]]" in secret_scan
            and "Secret/live/cost scanner execution failure" in secret_scan
        ),
    }
    return checks


def build_manifest(root: Path) -> dict[str, Any]:
    checks = policy_checks(root)
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise SystemExit(f"cannot freeze unsafe ORDER-001 policy state: {failed}")
    return {
        "schema_version": 1,
        "order_id": ORDER_ID,
        "audited_base_sha": BASE_SHA,
        "scope": "ORDER-001 P0-P7 F001-F011 corrective safety contract",
        "cost_policy": {"authorized_usd": 0, "paid_apis": False},
        "live_policy": {
            "available": False,
            "real_money": False,
            "order_placement": False,
        },
        "external_sources_policy": {"boros": False, "kalshi": False},
        "policy_checks": checks,
        "protected_files_sha256": protected_hashes(root),
    }


def write_manifest(root: Path) -> None:
    manifest = build_manifest(root)
    path = root / MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {MANIFEST_PATH}")


def check_manifest(root: Path) -> None:
    path = root / MANIFEST_PATH
    if not path.is_file():
        raise SystemExit(f"freeze manifest missing: {MANIFEST_PATH}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("order_id") != ORDER_ID:
        raise SystemExit("freeze order ID drift")
    if manifest.get("audited_base_sha") != BASE_SHA:
        raise SystemExit("freeze audited base SHA drift")
    if manifest.get("cost_policy") != {
        "authorized_usd": 0,
        "paid_apis": False,
    }:
        raise SystemExit("cost policy drift")
    if manifest.get("live_policy") != {
        "available": False,
        "real_money": False,
        "order_placement": False,
    }:
        raise SystemExit("LIVE policy drift")
    if manifest.get("external_sources_policy") != {
        "boros": False,
        "kalshi": False,
    }:
        raise SystemExit("external source scope drift")

    checks = policy_checks(root)
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise SystemExit(f"runtime safety policy no longer matches freeze: {failed}")
    if manifest.get("policy_checks") != checks:
        raise SystemExit("frozen policy-check result drift")

    expected = manifest.get("protected_files_sha256")
    actual = protected_hashes(root)
    if expected != actual:
        changed = sorted(
            relative
            for relative in set(expected or {}) | set(actual)
            if (expected or {}).get(relative) != actual.get(relative)
        )
        raise SystemExit(
            "protected files changed without a new ORDER-001 freeze: "
            + ", ".join(changed)
        )
    print("ORDER-001 safety freeze guard PASS")


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
