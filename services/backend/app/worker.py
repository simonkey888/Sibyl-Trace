import logging
import signal
import time
from datetime import UTC, datetime

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.paper import PaperEngine, ingest_wallet_activity
from app.polymarket import PolymarketClient
from app.repository import audit, initialize_state, set_state
from app.scanner import scan_wallets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sibyl.worker")
settings = get_settings()
running = True


def stop(*_: object) -> None:
    global running
    running = False


def main() -> None:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    init_db()
    client = PolymarketClient(settings)
    engine = PaperEngine(settings, client)
    next_scan = 0.0
    next_geoblock = 0.0
    try:
        with SessionLocal() as db:
            initialize_state(db, settings)
        while running:
            now = time.time()
            try:
                with SessionLocal() as db:
                    if now >= next_geoblock:
                        geoblock = client.geoblock()
                        geoblock_state = "blocked" if geoblock.get("blocked") else "clear"
                        set_state(db, "geoblock", geoblock_state)
                        next_geoblock = now + 300
                    if now >= next_scan:
                        selected = scan_wallets(db, client, settings)
                        log.info(
                            "selected wallets=%s",
                            [wallet.address for wallet in selected],
                        )
                        next_scan = now + settings.scan_interval_seconds
                    processed = ingest_wallet_activity(db, client, settings, engine)
                    if processed:
                        log.info("processed signals=%s", processed)
                    db.commit()
            except Exception as exc:
                log.exception("worker iteration failed")
                with SessionLocal() as db:
                    audit(
                        db,
                        "worker_iteration_failed",
                        str(exc),
                        severity="ERROR",
                        at=datetime.now(UTC).isoformat(),
                    )
                    db.commit()
            time.sleep(settings.watch_interval_seconds)
    finally:
        client.close()


if __name__ == "__main__":
    main()
