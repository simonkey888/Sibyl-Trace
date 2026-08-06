from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import compute_wallet_metrics, wallet_score
from app.models import Wallet, WalletSnapshot
from app.polymarket import PolymarketClient
from app.repository import audit, set_state


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def scan_wallets(db: Session, client: PolymarketClient, settings: Settings) -> list[Wallet]:
    candidates: dict[str, dict] = {}
    for period in ("WEEK", "MONTH", "ALL"):
        for item in client.leaderboard(period, settings.candidate_limit):
            address = str(item.get("proxyWallet") or "").lower()
            if len(address) != 42:
                continue
            candidate = candidates.setdefault(address, {"pnl": 0.0, "vol": 0.0, "username": None})
            candidate["pnl"] = max(float(candidate["pnl"]), float(item.get("pnl") or 0.0))
            candidate["vol"] = max(float(candidate["vol"]), float(item.get("vol") or 0.0))
            candidate["username"] = candidate["username"] or item.get("userName")

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
        metrics = compute_wallet_metrics(closed, volume=float(candidate["vol"]))
        score, rejection = wallet_score(metrics)
        wallet = db.get(Wallet, address) or Wallet(address=address)
        wallet.username = candidate["username"]
        wallet.score = score
        wallet.win_rate = metrics.win_rate
        wallet.profit_factor = metrics.profit_factor
        wallet.realized_pnl = metrics.realized_pnl
        wallet.volume = metrics.volume
        wallet.closed_count = metrics.closed_count
        wallet.concentration = metrics.concentration
        wallet.rejection_reason = rejection
        wallet.selected = False
        wallet.updated_at = datetime.now(timezone.utc)
        db.add(wallet)
        db.add(
            WalletSnapshot(
                wallet_address=address,
                score=score,
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
    for wallet in eligible:
        wallet.selected = True

    set_state(db, "last_scan_at", now_iso())
    audit(
        db,
        "wallet_scan_completed",
        f"Scored {len(wallets)} wallets; selected {len(eligible)}",
        candidates=len(wallets),
        selected=[wallet.address for wallet in eligible],
    )
    db.commit()
    return eligible
