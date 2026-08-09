import time
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Wallet, WalletScoreProfile, WalletSnapshot
from app.polymarket import PolymarketClient
from app.repository import audit, set_state
from app.scoring import score_matrix


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def scan_wallets(
    db: Session,
    client: PolymarketClient,
    settings: Settings,
    *,
    prospective: bool = False,
) -> list[Wallet]:
    candidates: dict[str, dict] = {}
    for period in ("WEEK", "MONTH", "ALL"):
        for item in client.leaderboard(period, settings.candidate_limit):
            address = str(item.get("proxyWallet") or "").lower()
            if len(address) != 42:
                continue
            candidate = candidates.setdefault(
                address,
                {"pnl": 0.0, "vol": 0.0, "username": None},
            )
            candidate["pnl"] = max(
                float(candidate["pnl"]),
                float(item.get("pnl") or 0.0),
            )
            candidate["vol"] = max(
                float(candidate["vol"]),
                float(item.get("vol") or 0.0),
            )
            candidate["username"] = candidate["username"] or item.get("userName")

    db.execute(update(Wallet).values(selected=False))
    wallets: list[Wallet] = []
    for address, candidate in candidates.items():
        try:
            closed = client.closed_positions(address)
        except Exception as exc:
            audit(
                db,
                "wallet_score_failed",
                f"Could not score {address}: {exc}",
                severity="WARN",
                wallet=address,
            )
            continue

        matrix = score_matrix(
            db,
            address,
            closed,
            volume=float(candidate["vol"]),
        )
        metrics = matrix.long_metrics
        wallet = db.get(Wallet, address) or Wallet(address=address)
        wallet.username = candidate["username"]
        wallet.score = matrix.global_score
        wallet.win_rate = metrics.win_rate
        wallet.profit_factor = metrics.profit_factor
        wallet.realized_pnl = metrics.realized_pnl
        wallet.volume = metrics.volume
        wallet.closed_count = metrics.closed_count
        wallet.concentration = metrics.concentration
        wallet.rejection_reason = matrix.rejection_reason
        wallet.selected = False
        wallet.updated_at = datetime.now(UTC)
        db.add(wallet)

        profile = db.get(WalletScoreProfile, address) or WalletScoreProfile(
            wallet_address=address
        )
        profile.short_score = matrix.short_score
        profile.long_score = matrix.long_score
        profile.global_score = matrix.global_score
        profile.execution_edge_score = matrix.execution_edge_score
        profile.execution_edge_sample_size = matrix.execution_edge_sample_size
        profile.average_execution_edge = matrix.average_execution_edge
        profile.short_sample_size = matrix.short_metrics.closed_count
        profile.long_sample_size = matrix.long_metrics.closed_count
        profile.updated_at = datetime.now(UTC)
        db.add(profile)

        db.add(
            WalletSnapshot(
                wallet_address=address,
                score=matrix.global_score,
                win_rate=metrics.win_rate,
                profit_factor=metrics.profit_factor,
                realized_pnl=metrics.realized_pnl,
                concentration=metrics.concentration,
                closed_count=metrics.closed_count,
            )
        )
        wallets.append(wallet)

    eligible = sorted(
        (wallet for wallet in wallets if wallet.rejection_reason is None),
        key=lambda wallet: wallet.score,
        reverse=True,
    )[: settings.tracked_wallet_limit]
    selection_effective_at = int(time.time()) if prospective else None
    for wallet in eligible:
        wallet.selected = True
        if selection_effective_at is not None:
            # A freshly scored selection may only authorize source activity that
            # occurs after the score exists. Advancing the cursor deliberately
            # sacrifices late-indexed pre-selection trades rather than backfilling
            # them with future information.
            wallet.last_activity_at = max(
                int(wallet.last_activity_at or 0), selection_effective_at
            )

    set_state(db, "last_scan_at", now_iso())
    if selection_effective_at is not None:
        set_state(db, "paper_v5_selection_effective_at", str(selection_effective_at))
    audit(
        db,
        "wallet_scan_completed",
        f"Scored {len(wallets)} wallets; selected {len(eligible)}",
        candidates=len(wallets),
        selected=[wallet.address for wallet in eligible],
        score_contract="GLOBAL=60% SHORT + 40% LONG; EDGE=execution copyability only",
        selection_mode="PROSPECTIVE_ONLY" if prospective else "LEGACY_SCAN_THEN_INGEST",
        selection_effective_at=selection_effective_at,
    )
    db.commit()
    return eligible
