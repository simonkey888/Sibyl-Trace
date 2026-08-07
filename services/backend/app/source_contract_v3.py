from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def normalize_v2_source(source_dir: Path, normalized_dir: Path) -> Path:
    """Adapt the frozen V2 evidence contract for V3 without mutating source evidence.

    V2 records PAPER/LIVE state in trial-summary.json and the $0/paid-API policy
    in evidence-manifest.json. V3 consumes a normalized copy only after both
    independent documents pass their canonical checks.
    """
    trial = _read_object(source_dir / "trial-summary.json")
    manifest = _read_object(source_dir / "evidence-manifest.json")

    run = trial.get("run")
    safety = trial.get("safety")
    cost = manifest.get("cost_policy")
    live = manifest.get("live_policy")
    if not isinstance(run, dict) or run.get("status") != "PASS":
        raise ValueError("V2 source is not PASS")
    if not isinstance(safety, dict):
        raise ValueError("V2 safety payload missing")
    if safety.get("trading_mode") != "PAPER" or safety.get("live_available") is not False:
        raise ValueError("V2 source is not PAPER-only / LIVE-absent")
    if not isinstance(cost, dict) or float(cost.get("authorized_usd", -1)) != 0:
        raise ValueError("V2 source does not prove zero authorized cost")
    if cost.get("paid_apis") is not False:
        raise ValueError("V2 source does not prove paid APIs absent")
    if not isinstance(live, dict) or live.get("available") is not False:
        raise ValueError("V2 manifest does not prove LIVE absent")
    if live.get("real_money") is not False:
        raise ValueError("V2 manifest does not prove real-money execution absent")

    normalized = deepcopy(trial)
    normalized_safety = dict(safety)
    normalized_safety["cost_authorized_usd"] = 0
    normalized["safety"] = normalized_safety
    normalized_dir.mkdir(parents=True, exist_ok=True)
    destination = normalized_dir / "trial-summary.json"
    destination.write_text(
        json.dumps(normalized, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return destination
