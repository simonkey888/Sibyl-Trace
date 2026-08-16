import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import AuditEvent, Wallet, WalletScoreProfile
from app.scanner import scan_wallets

WALLET = "0x" + "1" * 40


class Client:
    def leaderboard(self, _period: str, _limit: int) -> list[dict]:
        return [
            {
                "proxyWallet": WALLET,
                "pnl": 100,
                "vol": 10_000,
                "userName": "evidence-wallet",
            }
        ]

    def closed_positions(self, address: str, limit: int = 200) -> list[dict]:
        assert address == WALLET
        pnl = [2.0] * 15 + [-1.0] * 5 + [0.0] * 30
        return [
            {
                "realizedPnl": value,
                "timestamp": 2_000 - index,
                "transactionHash": f"closed-{index:03d}",
            }
            for index, value in enumerate(pnl)
        ]


def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_scanner_persists_score_bearing_decided_samples_and_audit_evidence() -> None:
    with session() as db:
        selected = scan_wallets(
            db,
            Client(),
            Settings(candidate_limit=3, tracked_wallet_limit=1),
        )
        wallet = db.get(Wallet, WALLET)
        assert wallet is not None
        assert wallet.rejection_reason is None, wallet.rejection_reason
        assert [row.address for row in selected] == [WALLET]
        profile = db.get(WalletScoreProfile, WALLET)
        assert profile is not None
        assert wallet.closed_count == 50
        assert wallet.win_rate == 0.75
        assert profile.short_sample_size == 20
        assert profile.long_sample_size == 20
        assert profile.global_score > 0

        event = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "wallet_quality_scored")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        payload = json.loads(event.payload_json)
        assert payload["score_kind"] == "HEURISTIC_QUALITY_RANKING"
        assert payload["score_history_basis"] == "DECIDED_OUTCOMES"
        assert payload["calibrated_probability"] is False
        assert payload["expected_return_claim"] is False
        assert payload["alpha_claim"] is False
        assert payload["short_closed_count"] == 50
        assert payload["short_decided_count"] == 20
        assert payload["long_closed_count"] == 50
        assert payload["long_decided_count"] == 20
        assert payload["global_score"] == profile.global_score
        assert payload["win_rate_denominator"] == "wins_plus_losses"
        assert payload["history_status"] == "COMPLETE"
        assert payload["history_scope"] == "LEGACY_TEST_DOUBLE"
