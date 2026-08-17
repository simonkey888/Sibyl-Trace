from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import serialize_forensics, serialize_wallet
from app.config import Settings
from app.db import Base
from app.models import AuditEvent, Wallet, WalletForensicsProfile
from app.scanner import scan_wallets
from app.source_strategy import (
    DIRECTIONAL_CANDIDATE,
    NON_DIRECTIONAL_TWO_SIDED,
    ActivityHistoryEvidence,
    SourceActivityHistory,
    SourceStrategyPolicy,
    canonical_hash,
    classify_source_strategy,
    fetch_public_activity_events,
    profile_hash_valid,
)
from app.wallet_forensics import (
    DIRECTIONAL_COPY_RESEARCH,
    STRUCTURAL_TWO_SIDED_RESEARCH,
    compute_execution_mix,
    compute_wallet_forensics,
    fetch_public_execution_mix,
)

MAKER = "0x" + "a" * 40
DIRECTIONAL = "0x" + "b" * 40


def event(
    event_type: str,
    timestamp: int,
    *,
    asset: str = "asset-yes",
    condition: str = "condition-1",
    side: str = "BUY",
    outcome_index: int = 0,
    usdc: float = 10.0,
) -> dict:
    return {
        "type": event_type,
        "timestamp": timestamp,
        "transactionHash": f"0x{event_type.lower()}-{timestamp}-{asset}",
        "conditionId": condition,
        "asset": asset,
        "side": side,
        "outcomeIndex": outcome_index,
        "outcome": "Yes" if outcome_index == 0 else "No",
        "price": 0.5,
        "size": usdc / 0.5,
        "usdcSize": usdc,
    }


def closed_positions(*, wins: int = 15, losses: int = 5) -> list[dict]:
    return (
        [{"realizedPnl": 2.0} for _ in range(wins)]
        + [{"realizedPnl": -1.0} for _ in range(losses)]
        + [{"realizedPnl": 0.0} for _ in range(5)]
    )


def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def authoritative(rows: list[dict]) -> SourceActivityHistory:
    evidence = ActivityHistoryEvidence(
        status="COMPLETE",
        scope="FULL_AVAILABLE_FILTERED_HISTORY",
        requested_limit=len(rows) + 1,
        returned_rows=len(rows),
        pages_fetched=1,
        page_size=len(rows) + 1,
        exhausted=True,
        has_more=False,
        malformed_rows=0,
        invalid_timestamp_rows=0,
        source_hash=canonical_hash("wallet-forensics-test-fixture"),
    )
    return SourceActivityHistory(rows, evidence)


def test_activity_fetch_uses_corrected_csv_activity_enum_filter() -> None:
    calls: list[dict] = []

    class Client:
        settings = SimpleNamespace(data_api_base="https://data-api.polymarket.com")

        def _get(self, _url: str, params: dict) -> list[dict]:
            calls.append(params)
            return []

    fetch_public_activity_events(Client(), DIRECTIONAL, cutoff_at=1_000, limit=30)
    assert len(calls) == 1
    requested = set(calls[0]["type"].split(","))
    assert requested == {
        "TRADE",
        "SPLIT",
        "MERGE",
        "REDEEM",
        "REWARD",
        "CONVERSION",
        "MAKER_REBATE",
        "REFERRAL_REWARD",
    }
    assert "TAKER_REBATE" not in requested
    assert calls[0]["sortBy"] == "TIMESTAMP"
    assert calls[0]["sortDirection"] == "DESC"


def test_execution_mix_uses_only_comparable_window_and_reconciles_taker_subset() -> None:
    all_rows = [event("TRADE", 100 + index, asset=f"asset-{index}") for index in range(5)]
    taker_rows = [all_rows[1], all_rows[2], event("TRADE", 50, asset="older-taker")]
    mix = compute_execution_mix(all_rows, taker_rows, row_limit=500)
    assert mix["proven"] is True
    assert mix["window_from"] == 100
    assert mix["window_to"] == 104
    assert mix["sample_total"] == 5
    assert mix["maker"] == 3
    assert mix["taker"] == 2
    assert mix["maker_ratio"] == pytest.approx(0.6)
    assert mix["taker_ratio"] == pytest.approx(0.4)
    assert len(mix["all_sample_hash"]) == 64
    assert len(mix["taker_sample_hash"]) == 64


def test_execution_mix_fails_closed_when_taker_page_is_not_subset() -> None:
    all_rows = [event("TRADE", 100, asset="all-only")]
    taker_rows = [event("TRADE", 100, asset="orphan-taker")]
    mix = compute_execution_mix(all_rows, taker_rows, row_limit=500)
    assert mix["proven"] is False
    assert mix["maker_ratio"] is None
    assert mix["orphan_taker_fills"] == 1


def test_execution_mix_fails_closed_if_full_raw_page_contains_invalid_rows() -> None:
    all_rows = [event("TRADE", 100 + index, asset=f"all-{index}") for index in range(5)]
    taker_rows = [all_rows[0], all_rows[1], {"timestamp": 0}]
    mix = compute_execution_mix(all_rows, taker_rows, row_limit=3)
    assert mix["proven"] is False
    assert mix["reason"] == "INVALID_PUBLIC_TRADE_ROWS"
    assert mix["maker_ratio"] is None
    assert mix["invalid_taker_rows"] == 1
    assert mix["truncated"] is True


def test_public_execution_mix_rejects_non_list_api_shapes() -> None:
    class Client:
        settings = SimpleNamespace(data_api_base="https://data-api.polymarket.com")

        def _get(self, _url: str, params: dict):
            return {} if params.get("takerOnly") is False else []

    with pytest.raises(ValueError, match="public_execution_mix_all_response_not_list"):
        fetch_public_execution_mix(Client(), DIRECTIONAL)


def test_forensics_routes_maker_to_research_without_inventing_maker_ratio() -> None:
    cutoff = 1_000
    events = [
        event("TRADE", 900, asset="asset-yes", side="BUY", outcome_index=0),
        event("TRADE", 901, asset="asset-yes", side="SELL", outcome_index=0),
        event("TRADE", 902, asset="asset-no", side="BUY", outcome_index=1),
        event("MAKER_REBATE", 903, usdc=7.25),
        event("REWARD", 904, usdc=3.5),
        event("REDEEM", 905, usdc=20),
    ]
    policy = SourceStrategyPolicy(min_trade_count=1, min_paired_conditions=1)
    source = classify_source_strategy(
        MAKER, authoritative(events), cutoff_at=cutoff, policy=policy
    ).to_dict()
    assert source["classification"] == NON_DIRECTIONAL_TWO_SIDED
    assert source["taker_rebate_count"] == 0
    assert profile_hash_valid(source)

    forensic = compute_wallet_forensics(
        MAKER,
        events,
        closed_positions(),
        cutoff_at=cutoff,
        source_strategy_profile=source,
    ).to_dict()
    assert forensic["lane"] == STRUCTURAL_TWO_SIDED_RESEARCH
    assert forensic["copyable_directional"] is False
    assert forensic["research_only"] is True
    assert forensic["execution_gate"] is False
    assert forensic["maker_rebate_count"] == 1
    assert forensic["maker_rebate_usdc_observed"] == pytest.approx(7.25)
    assert forensic["reward_usdc_observed"] == pytest.approx(3.5)
    assert forensic["redeem_usdc_observed"] == pytest.approx(20)
    assert forensic["round_trip_asset_count"] == 1
    assert forensic["maker_ratio"] is None
    assert forensic["taker_ratio"] is None
    assert forensic["maker_taker_ratio_proven"] is False
    assert forensic["cashflow_pnl_reconstructed"] is False
    assert forensic["claims"]["profitability_proven_by_forensics_v1"] is False
    assert len(forensic["evidence_hash"]) == 64


def test_forensics_keeps_directional_lane_separate_from_structural_research() -> None:
    cutoff = 2_000
    events = [
        event(
            "TRADE",
            1_900 + index,
            asset=f"asset-{index}",
            condition=f"condition-{index}",
            side="BUY",
            outcome_index=0,
        )
        for index in range(5)
    ]
    policy = SourceStrategyPolicy(min_trade_count=5, min_paired_conditions=2)
    source = classify_source_strategy(
        DIRECTIONAL, authoritative(events), cutoff_at=cutoff, policy=policy
    ).to_dict()
    assert source["classification"] == DIRECTIONAL_CANDIDATE

    forensic = compute_wallet_forensics(
        DIRECTIONAL,
        events,
        closed_positions(),
        cutoff_at=cutoff,
        source_strategy_profile=source,
    ).to_dict()
    assert forensic["lane"] == DIRECTIONAL_COPY_RESEARCH
    assert forensic["copyable_directional"] is True
    assert forensic["research_only"] is False
    assert forensic["execution_gate"] is False
    assert forensic["paired_trade_fraction"] == 0
    assert forensic["reported_closed_position_count"] == 25
    assert forensic["reported_decided_closed_position_count"] == 20
    assert forensic["reported_realized_pnl"] == pytest.approx(25.0)


def test_forensics_rejects_tampered_source_strategy_evidence() -> None:
    cutoff = 2_000
    events = [
        event(
            "TRADE",
            1_900 + index,
            asset=f"asset-{index}",
            condition=f"condition-{index}",
        )
        for index in range(5)
    ]
    source = classify_source_strategy(
        DIRECTIONAL,
        authoritative(events),
        cutoff_at=cutoff,
        policy=SourceStrategyPolicy(min_trade_count=5),
    ).to_dict()
    source["paired_trade_fraction"] = 0.99
    with pytest.raises(ValueError, match="source_strategy_profile_hash_invalid"):
        compute_wallet_forensics(
            DIRECTIONAL,
            events,
            closed_positions(),
            cutoff_at=cutoff,
            source_strategy_profile=source,
        )


def test_maker_heavy_execution_style_does_not_rewrite_directionality() -> None:
    cutoff = 2_000
    events = [
        event(
            "TRADE",
            1_900 + index,
            asset=f"asset-{index}",
            condition=f"condition-{index}",
        )
        for index in range(5)
    ]
    source = classify_source_strategy(
        DIRECTIONAL,
        authoritative(events),
        cutoff_at=cutoff,
        policy=SourceStrategyPolicy(min_trade_count=5),
    ).to_dict()
    forensic = compute_wallet_forensics(
        DIRECTIONAL,
        events,
        closed_positions(),
        cutoff_at=cutoff,
        source_strategy_profile=source,
        execution_mix={
            "proven": True,
            "scope": "RECENT_OVERLAP_SAMPLE",
            "sample_total": 100,
            "maker": 90,
            "taker": 10,
            "maker_ratio": 0.90,
            "taker_ratio": 0.10,
            "orphan_taker_fills": 0,
            "truncated": False,
            "window_from": 1_000,
            "window_to": 2_000,
            "reason": None,
        },
    ).to_dict()
    assert forensic["lane"] == DIRECTIONAL_COPY_RESEARCH
    assert forensic["copyable_directional"] is True
    assert forensic["execution_style"] == "MAKER_HEAVY"
    assert forensic["selection_veto"] is False
    assert forensic["execution_gate"] is False


class ScannerClient:
    def leaderboard(self, _period: str, _limit: int) -> list[dict]:
        return [
            {"proxyWallet": MAKER, "pnl": 1_000, "vol": 100_000, "userName": "maker"},
            {
                "proxyWallet": DIRECTIONAL,
                "pnl": 500,
                "vol": 50_000,
                "userName": "directional",
            },
        ]

    def closed_positions(self, address: str) -> list[dict]:
        assert address in {MAKER, DIRECTIONAL}
        return closed_positions()

    settings = SimpleNamespace(data_api_base="https://data-api.polymarket.com")

    def _get(self, _url: str, params: dict) -> list[dict]:
        wallet = params["user"]
        if _url.endswith("/closed-positions"):
            offset = int(params.get("offset") or 0)
            limit = int(params.get("limit") or 50)
            rows = closed_positions()
            return rows[offset : offset + limit]
        if _url.endswith("/trades"):
            all_rows = [
                event(
                    "TRADE",
                    700 + index,
                    asset=f"mix-{wallet[-1]}-{index}",
                    condition=f"mix-condition-{wallet[-1]}-{index}",
                )
                for index in range(4)
            ]
            return all_rows[:1] if params.get("takerOnly") is True else all_rows
        if wallet == MAKER:
            rows = [
                event(
                    "TRADE",
                    100 + index,
                    asset=f"maker-{index}",
                    condition=f"maker-condition-{index}",
                )
                for index in range(5)
            ]
            for index in (0, 1):
                rows.append(
                    event(
                        "TRADE",
                        118 + index,
                        asset=f"maker-opposite-{index}",
                        condition=f"maker-condition-{index}",
                        outcome_index=1,
                    )
                )
            rows.append(event("MAKER_REBATE", 120, usdc=2.5))
            return rows
        return [
            event(
                "TRADE",
                200 + index,
                asset=f"dir-{index}",
                condition=f"dir-condition-{index}",
            )
            for index in range(5)
        ]


def test_scanner_persists_forensics_but_does_not_let_it_bypass_directional_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.scanner.time.time", lambda: 1_000.0)
    with session() as db:
        selected = scan_wallets(
            db,
            ScannerClient(),
            Settings(
                candidate_limit=3,
                tracked_wallet_limit=1,
                source_strategy_min_trade_count=5,
                source_strategy_activity_limit=30,
            ),
            prospective=True,
            source_strategy_gate=True,
        )
        assert [row.address for row in selected] == [DIRECTIONAL]

        maker_profile = db.get(WalletForensicsProfile, MAKER)
        directional_profile = db.get(WalletForensicsProfile, DIRECTIONAL)
        assert maker_profile is not None
        assert directional_profile is not None
        assert maker_profile.lane == STRUCTURAL_TWO_SIDED_RESEARCH
        assert maker_profile.copyable_directional is False
        assert maker_profile.research_only is True
        assert directional_profile.lane == DIRECTIONAL_COPY_RESEARCH
        assert directional_profile.copyable_directional is True
        assert directional_profile.research_only is False

        maker_wallet = db.get(Wallet, MAKER)
        directional_wallet = db.get(Wallet, DIRECTIONAL)
        assert maker_wallet is not None and maker_wallet.selected is False
        assert maker_wallet.rejection_reason == "source_strategy_two_sided"
        assert directional_wallet is not None and directional_wallet.selected is True

        audit_event = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "wallet_forensics_profiled")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert audit_event is not None
        payload = json.loads(audit_event.payload_json)
        assert payload["execution_gate"] is False
        assert payload["cashflow_pnl_reconstructed"] is False

        serialized = serialize_wallet(directional_wallet, forensic_profile=directional_profile)
        assert serialized["forensics"]["lane"] == DIRECTIONAL_COPY_RESEARCH
        assert serialized["forensics"]["execution_gate"] is False
        assert serialized["forensics"]["scan_candidate_count"] == 2
        assert serialized["forensics"]["scan_realized_pnl_percentile"] is not None
        assert serialized["forensics"]["control_group_percentile_proven"] is False
        assert serialized["forensics"]["maker_taker_ratio_proven"] is True
        assert serialized["forensics"]["maker_ratio"] == pytest.approx(0.75)
        assert serialize_forensics(maker_profile)["maker_ratio"] == pytest.approx(0.75)


def test_forensics_failure_does_not_discard_valid_directional_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.scanner.time.time", lambda: 1_000.0)

    class DirectionalOnlyClient(ScannerClient):
        def leaderboard(self, _period: str, _limit: int) -> list[dict]:
            return [
                {
                    "proxyWallet": DIRECTIONAL,
                    "pnl": 500,
                    "vol": 50_000,
                    "userName": "directional",
                }
            ]

    def broken_forensics(*_args, **_kwargs):
        raise RuntimeError("forensics exploded")

    monkeypatch.setattr("app.scanner.compute_wallet_forensics", broken_forensics)
    with session() as db:
        selected = scan_wallets(
            db,
            DirectionalOnlyClient(),
            Settings(
                candidate_limit=3,
                tracked_wallet_limit=1,
                source_strategy_min_trade_count=5,
                source_strategy_activity_limit=30,
            ),
            prospective=True,
            source_strategy_gate=True,
        )
        assert [row.address for row in selected] == [DIRECTIONAL]
        wallet = db.get(Wallet, DIRECTIONAL)
        profile = db.get(WalletForensicsProfile, DIRECTIONAL)
        assert wallet is not None and wallet.selected is True
        assert wallet.rejection_reason is None
        assert profile is not None
        assert profile.lane == "UNAVAILABLE_RESEARCH"
        assert profile.copyable_directional is False
        assert profile.research_only is True


class BrokenActivityClient(ScannerClient):
    def _get(self, _url: str, params: dict) -> list[dict]:
        if _url.endswith("/activity"):
            raise RuntimeError("activity unavailable")
        return super()._get(_url, params)


def test_scanner_activity_failure_fails_closed_without_selecting_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.scanner.time.time", lambda: 1_000.0)
    with session() as db:
        selected = scan_wallets(
            db,
            BrokenActivityClient(),
            Settings(
                candidate_limit=3,
                tracked_wallet_limit=1,
                source_strategy_min_trade_count=5,
                source_strategy_activity_limit=30,
            ),
            prospective=True,
            source_strategy_gate=True,
        )
        assert selected == []
        rows = list(db.scalars(select(WalletForensicsProfile)).all())
        assert len(rows) == 2
        assert all(row.lane == "UNAVAILABLE_RESEARCH" for row in rows)
        assert all(row.copyable_directional is False for row in rows)
        assert all(row.research_only is True for row in rows)
        assert all(json.loads(row.payload_json)["execution_gate"] is False for row in rows)
