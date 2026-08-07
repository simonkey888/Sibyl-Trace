import json

from app.evidence import (
    PHASES,
    build_manifest,
    canonical_json,
    hash_payload,
    protected_hashes,
    sha256_file,
    verify_file,
)


def test_canonical_json_and_hash_are_order_independent() -> None:
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})
    assert hash_payload({"b": 2, "a": 1}) == hash_payload({"a": 1, "b": 2})


def test_file_integrity_round_trip(tmp_path) -> None:
    path = tmp_path / "state.bin"
    path.write_bytes(b"sibyl-state")
    expected = sha256_file(path)
    assert verify_file(path, expected).ok
    path.write_bytes(b"tampered")
    assert not verify_file(path, expected).ok


def test_protected_hashes_reject_path_escape(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "risk.txt").write_text("fixed", encoding="utf-8")
    assert protected_hashes(root, ["risk.txt"])["risk.txt"]
    try:
        protected_hashes(root, ["../outside.txt"])
    except ValueError:
        pass
    else:
        raise AssertionError("path escape was not rejected")


def test_manifest_hard_codes_zero_cost_and_absent_live() -> None:
    manifest = build_manifest(
        baseline_sha="a" * 40,
        tree_sha="b" * 40,
        python_version="3.12",
        node_version="22",
        scoring_version="2",
        risk_version="1",
        simulator_version="2",
        contract_version="2026-08-07",
        evidence_generation="SIBYL_PAPER_V2",
        protected_files={"risk": "c" * 64},
    )
    assert manifest["cost_policy"] == {"authorized_usd": 0, "paid_apis": False}
    assert manifest["live_policy"] == {"available": False, "real_money": False}
    assert len(manifest["manifest_hash"]) == 64
    json.dumps(manifest)


def test_checkpoint_phase_contract_includes_research_labs() -> None:
    assert PHASES[0] == "RESTORE"
    assert "LATENCY_CAPTURE" in PHASES
    assert "WEATHER_CAPTURE" in PHASES
    assert "SPORTS_FAIR_PRICE" in PHASES
    assert PHASES[-1] == "PERSIST"
