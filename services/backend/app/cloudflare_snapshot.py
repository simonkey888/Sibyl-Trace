from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DENIED_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "private_key",
    "seed_phrase",
    "mnemonic",
    "password",
    "cookie",
    "secret",
    "bearer_token",
}


def _mask_wallet(value: str) -> str:
    if value.startswith("0x") and len(value) >= 42:
        return f"{value[:6]}…{value[38:42]}"
    return value


def _sanitize_string(key: str, value: str) -> str:
    lowered = key.lower()
    if lowered in {"wallet", "address", "proxywallet", "wallet_address"}:
        return _mask_wallet(value)
    if lowered == "username" and value.startswith("0x") and len(value) >= 42:
        return f"{_mask_wallet(value[:42])}{value[42:]}"
    return value


def sanitize_public(value: Any, path: str = "root") -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in DENIED_KEYS:
                raise ValueError(f"refusing to publish sensitive-looking key at {path}.{key}")
            if isinstance(item, str):
                clean[str(key)] = _sanitize_string(str(key), item)
            else:
                clean[str(key)] = sanitize_public(item, f"{path}.{key}")
        return clean
    if isinstance(value, list):
        return [sanitize_public(item, f"{path}[]") for item in value]
    return value


def _load_json(path: Path, *, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _validate_v3(v3: dict[str, Any], trial: dict[str, Any]) -> None:
    if not v3:
        return
    if v3.get("status") != "PASS" or v3.get("evidence_generation") != "SIBYL_RESEARCH_V3":
        raise ValueError("Research V3 snapshot is not PASS evidence")
    safety = v3.get("safety") or {}
    if (
        safety.get("trading_mode") != "PAPER"
        or safety.get("live_available") is not False
        or safety.get("real_money") is not False
        or float(safety.get("cost_authorized_usd", -1)) != 0
        or safety.get("paid_apis") is not False
    ):
        raise ValueError("Research V3 snapshot violates PAPER/LIVE/$0 policy")
    source_v2 = v3.get("source_v2") or {}
    trial_run = trial.get("run") or {}
    if str(source_v2.get("github_run_id") or "") != str(trial_run.get("github_run_id") or ""):
        raise ValueError("Research V3 does not match the embedded PAPER V2 source run")
    if str(source_v2.get("github_sha") or "") != str(trial_run.get("github_sha") or ""):
        raise ValueError("Research V3 does not match the embedded PAPER V2 source SHA")


def _validate_v4(v4: dict[str, Any], v3: dict[str, Any]) -> None:
    if not v4:
        return
    if not v3:
        raise ValueError("Research V4 cannot be published without Research V3")
    if (
        v4.get("status") != "PASS"
        or v4.get("evidence_generation") != "SIBYL_RESEARCH_V4_OPERATIONAL"
    ):
        raise ValueError("Research V4 snapshot is not PASS operational evidence")
    safety = v4.get("safety") or {}
    if (
        safety.get("mode") != "PAPER_SHADOW_ONLY"
        or safety.get("trading_mode") != "PAPER"
        or safety.get("live_available") is not False
        or safety.get("real_money") is not False
        or float(safety.get("cost_authorized_usd", -1)) != 0
        or safety.get("paid_apis") is not False
        or safety.get("order_placement") is not False
        or safety.get("private_keys") is not False
        or safety.get("historical_fill_rewrite") is not False
    ):
        raise ValueError("Research V4 snapshot violates shadow PAPER safety policy")


def _validate_v5(v5: dict[str, Any]) -> None:
    if not v5:
        return
    if (
        v5.get("status") != "PASS"
        or v5.get("evidence_generation") != "SIBYL_PAPER_V5_EXECUTION_REALISTIC"
    ):
        raise ValueError("PAPER V5 snapshot is not PASS truthful-execution evidence")
    safety = v5.get("safety") or {}
    if (
        safety.get("trading_mode") != "PAPER"
        or safety.get("live_available") is not False
        or safety.get("real_money") is not False
        or safety.get("order_placement") is not False
        or safety.get("private_keys") is not False
        or safety.get("paid_apis") is not False
        or float(safety.get("cost_authorized_usd", -1)) != 0
    ):
        raise ValueError("PAPER V5 snapshot violates PAPER/LIVE/$0 policy")
    method = v5.get("methodology") or {}
    if (
        method.get("execution_model") != "L2_TAKER_FAK_ARRIVAL_BOOK_V1"
        or method.get("midpoint_fills") is not False
        or method.get("arrival_book_refetch") is not True
        or method.get("l2_depth_consumed") is not True
        or method.get("partial_fills") is not True
        or method.get("legacy_history_rewritten") is not False
    ):
        raise ValueError("PAPER V5 snapshot violates truthful-execution methodology")


def build_cloudflare_snapshot(input_dir: Path) -> dict[str, Any]:
    trial = _load_json(input_dir / "trial-summary.json")
    research = _load_json(input_dir / "research-summary.json", required=False)
    latency = _load_json(input_dir / "latency-summary.json", required=False)
    manifest = _load_json(input_dir / "evidence-manifest.json", required=False)
    research_v3 = _load_json(input_dir / "research-v3-summary.json", required=False)
    research_v4 = _load_json(input_dir / "research-v4-summary.json", required=False)
    paper_v5 = _load_json(input_dir / "paper-v5-summary.json", required=False)

    run = trial.get("run") or {}
    safety = trial.get("safety") or {}
    if run.get("status") != "PASS":
        raise ValueError("only PASS legacy PAPER evidence may anchor the public snapshot")
    if safety.get("trading_mode") != "PAPER" or safety.get("live_available") is not False:
        raise ValueError("legacy anchor is not PAPER-only / LIVE-absent")

    generation = str(trial.get("evidence_generation") or "")
    if generation != "SIBYL_PAPER_V2":
        raise ValueError(f"unsupported legacy evidence generation: {generation or 'missing'}")

    if manifest:
        cost = manifest.get("cost_policy") or {}
        live = manifest.get("live_policy") or {}
        if float(cost.get("authorized_usd", -1)) != 0 or cost.get("paid_apis") is not False:
            raise ValueError("snapshot violates zero-cost policy")
        if live.get("available") is not False or live.get("real_money") is not False:
            raise ValueError("snapshot violates LIVE-absent policy")

    _validate_v3(research_v3, trial)
    _validate_v4(research_v4, research_v3)
    _validate_v5(paper_v5)

    trial_public = dict(trial)
    trial_public.pop("research", None)
    trial_public["methodology_label"] = "LEGACY_SIMULATION_MIDPOINT_V2"
    trial_public["canonical_performance"] = not paper_v5
    if paper_v5:
        paper_v5 = dict(paper_v5)
        paper_v5["canonical_performance"] = True

    manifest_public = {
        "baseline_sha": manifest.get("baseline_sha"),
        "tree_sha": manifest.get("tree_sha"),
        "manifest_hash": manifest.get("manifest_hash"),
        "evidence_generation": manifest.get("evidence_generation"),
        "risk_version": manifest.get("risk_version"),
        "scoring_version": manifest.get("scoring_version"),
        "simulator_version": manifest.get("simulator_version"),
        "polymarket_contract_version": manifest.get("polymarket_contract_version"),
        "cost_policy": manifest.get("cost_policy"),
        "live_policy": manifest.get("live_policy"),
    }

    schema_version = 4 if paper_v5 else 3 if research_v4 else 2 if research_v3 else 1
    v5_run = paper_v5.get("run") if paper_v5 else {}
    snapshot = {
        "schema_version": schema_version,
        "snapshot_at": (v5_run or {}).get("completed_at") or run.get("completed_at"),
        "source": {
            "github_run_id": (v5_run or {}).get("github_run_id") or run.get("github_run_id"),
            "github_sha": (v5_run or {}).get("github_sha") or run.get("github_sha"),
            "evidence_generation": (
                paper_v5.get("evidence_generation") if paper_v5 else generation
            ),
            "profile": "PAPER_V5_TRUTHFUL_EXECUTION" if paper_v5 else run.get("profile"),
        },
        "paper_v5": paper_v5,
        "trial": trial_public,
        "research": research,
        "latency": latency,
        "research_v3": research_v3,
        "research_v4": research_v4,
        "manifest": manifest_public,
    }
    return sanitize_public(snapshot)


def write_cloudflare_snapshot(input_dir: Path, output_dir: Path) -> Path:
    snapshot = build_cloudflare_snapshot(input_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "snapshot.json"
    destination.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build sanitized Cloudflare PAPER dashboard snapshot"
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    destination = write_cloudflare_snapshot(args.input_dir, args.output_dir)
    print(destination)


if __name__ == "__main__":
    main()
