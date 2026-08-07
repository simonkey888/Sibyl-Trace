from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.source_contract_v3 import normalize_v2_source


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture(tmp_path: Path) -> None:
    _write(
        tmp_path / "trial-summary.json",
        {
            "run": {"status": "PASS", "github_run_id": "1", "github_sha": "abc"},
            "safety": {"trading_mode": "PAPER", "live_available": False},
        },
    )
    _write(
        tmp_path / "evidence-manifest.json",
        {
            "cost_policy": {"authorized_usd": 0, "paid_apis": False},
            "live_policy": {"available": False, "real_money": False},
        },
    )


def test_normalizes_real_v2_split_contract_without_mutating_source(tmp_path: Path) -> None:
    _fixture(tmp_path)
    original = (tmp_path / "trial-summary.json").read_text(encoding="utf-8")
    out = tmp_path / "normalized"
    destination = normalize_v2_source(tmp_path, out)
    normalized = json.loads(destination.read_text(encoding="utf-8"))
    assert normalized["safety"]["cost_authorized_usd"] == 0
    assert normalized["safety"]["trading_mode"] == "PAPER"
    assert (tmp_path / "trial-summary.json").read_text(encoding="utf-8") == original


def test_rejects_nonzero_cost_before_normalization(tmp_path: Path) -> None:
    _fixture(tmp_path)
    manifest = json.loads((tmp_path / "evidence-manifest.json").read_text(encoding="utf-8"))
    manifest["cost_policy"]["authorized_usd"] = 1
    _write(tmp_path / "evidence-manifest.json", manifest)
    with pytest.raises(ValueError, match="zero authorized cost"):
        normalize_v2_source(tmp_path, tmp_path / "normalized")


def test_rejects_live_or_real_money_drift(tmp_path: Path) -> None:
    _fixture(tmp_path)
    manifest = json.loads((tmp_path / "evidence-manifest.json").read_text(encoding="utf-8"))
    manifest["live_policy"]["available"] = True
    _write(tmp_path / "evidence-manifest.json", manifest)
    with pytest.raises(ValueError, match="LIVE absent"):
        normalize_v2_source(tmp_path, tmp_path / "normalized")
