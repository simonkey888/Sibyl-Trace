from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PHASES = (
    "RESTORE",
    "SETTLEMENT",
    "SCAN",
    "SCORE",
    "INGEST",
    "COPY_PAPER",
    "LATENCY_CAPTURE",
    "WEATHER_CAPTURE",
    "SPORTS_FAIR_PRICE",
    "RECONCILE",
    "WATCHDOG",
    "REPORT",
    "PERSIST",
)


@dataclass(frozen=True)
class IntegrityResult:
    ok: bool
    expected: str
    actual: str


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_payload(payload: Any) -> str:
    return sha256_text(canonical_json(payload))


def verify_file(path: Path, expected_sha256: str) -> IntegrityResult:
    normalized = expected_sha256.strip().lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        return IntegrityResult(False, normalized, "INVALID_EXPECTED_SHA256")
    actual = sha256_file(path)
    return IntegrityResult(actual == normalized, normalized, actual)


def protected_hashes(repo_root: Path, relative_paths: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in sorted(relative_paths):
        path = (repo_root / relative).resolve()
        if repo_root.resolve() not in path.parents:
            raise ValueError(f"protected path escaped repository root: {relative}")
        if not path.is_file():
            raise FileNotFoundError(relative)
        result[relative] = sha256_file(path)
    return result


def build_manifest(
    *,
    baseline_sha: str,
    tree_sha: str,
    python_version: str,
    node_version: str,
    scoring_version: str,
    risk_version: str,
    simulator_version: str,
    contract_version: str,
    evidence_generation: str,
    protected_files: dict[str, str],
) -> dict[str, Any]:
    manifest = {
        "schema_version": 1,
        "baseline_sha": baseline_sha,
        "tree_sha": tree_sha,
        "python_version": python_version,
        "node_version": node_version,
        "scoring_version": scoring_version,
        "risk_version": risk_version,
        "simulator_version": simulator_version,
        "polymarket_contract_version": contract_version,
        "evidence_generation": evidence_generation,
        "cost_policy": {"authorized_usd": 0, "paid_apis": False},
        "live_policy": {"available": False, "real_money": False},
        "protected_files": protected_files,
    }
    return {**manifest, "manifest_hash": hash_payload(manifest)}
