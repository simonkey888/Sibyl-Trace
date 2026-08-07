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
    if lowered in {"wallet", "address", "proxywallet"}:
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


def build_cloudflare_snapshot(input_dir: Path) -> dict[str, Any]:
    trial = _load_json(input_dir / "trial-summary.json")
    research = _load_json(input_dir / "research-summary.json", required=False)
    latency = _load_json(input_dir / "latency-summary.json", required=False)
    manifest = _load_json(input_dir / "evidence-manifest.json", required=False)

    run = trial.get("run") or {}
    safety = trial.get("safety") or {}
    if run.get("status") != "PASS":
        raise ValueError("only PASS PAPER evidence may be published")
    if safety.get("trading_mode") != "PAPER" or safety.get("live_available") is not False:
        raise ValueError("snapshot is not PAPER-only / LIVE-absent")

    generation = str(trial.get("evidence_generation") or "")
    if generation != "SIBYL_PAPER_V2":
        raise ValueError(f"unsupported evidence generation: {generation or 'missing'}")

    if manifest:
        cost = manifest.get("cost_policy") or {}
        live = manifest.get("live_policy") or {}
        if float(cost.get("authorized_usd", -1)) != 0 or cost.get("paid_apis") is not False:
            raise ValueError("snapshot violates zero-cost policy")
        if live.get("available") is not False or live.get("real_money") is not False:
            raise ValueError("snapshot violates LIVE-absent policy")

    trial_public = dict(trial)
    trial_public.pop("research", None)
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

    snapshot = {
        "schema_version": 1,
        "snapshot_at": run.get("completed_at"),
        "source": {
            "github_run_id": run.get("github_run_id"),
            "github_sha": run.get("github_sha"),
            "evidence_generation": generation,
            "profile": run.get("profile"),
        },
        "trial": trial_public,
        "research": research,
        "latency": latency,
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
