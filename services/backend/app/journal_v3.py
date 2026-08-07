from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.evidence import hash_payload
from app.market_data_v3 import V3Event
from app.research_models import ResearchObservation


def _payload(event: V3Event, run_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "source": event.source,
        "event_type": event.event_type,
        "source_timestamp_ms": event.source_timestamp_ms,
        "receive_timestamp_ms": event.receive_timestamp_ms,
        "price": event.price,
        "bid": event.bid,
        "ask": event.ask,
        "bid_size": event.bid_size,
        "ask_size": event.ask_size,
        "size": event.size,
        "aggressor_side": event.aggressor_side,
        "asset_id": event.asset_id,
        "sequence": event.sequence,
    }


def persist_downsampled_capture(
    db: Session,
    *,
    experiment_id: str,
    market_id: str,
    run_id: str,
    events: tuple[V3Event, ...],
    bucket_ms: int = 1_000,
) -> int:
    if bucket_ms <= 0:
        raise ValueError("bucket_ms must be positive")
    buckets: dict[tuple[str, str, int], V3Event] = {}
    for event in events:
        asset = event.asset_id or ""
        bucket = event.receive_timestamp_ms // bucket_ms
        buckets[(event.source, asset, bucket)] = event

    inserted = 0
    for (source, asset, bucket), event in sorted(buckets.items()):
        payload = _payload(event, run_id)
        key = hash_payload(
            {
                "schema_version": 1,
                "source": source,
                "asset_id": asset,
                "bucket": bucket,
                "event_type": event.event_type,
            }
        )
        exists = db.scalar(
            select(ResearchObservation.id).where(
                ResearchObservation.observation_key == key
            )
        )
        if exists is not None:
            continue
        db.add(
            ResearchObservation(
                observation_key=key,
                experiment_id=experiment_id,
                source=f"V3_{source}",
                market_id=market_id,
                asset_id=event.asset_id,
                category="CRYPTO",
                source_timestamp_ms=event.source_timestamp_ms,
                receive_timestamp_ms=event.receive_timestamp_ms,
                market_price=event.price,
                payload_hash=hash_payload(payload),
                payload_json=json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
            )
        )
        inserted += 1
    db.commit()
    return inserted


def journal_row_count(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(ResearchObservation)
            .where(ResearchObservation.source.like("V3_%"))
        )
        or 0
    )


def export_journal(db: Session, path: Path, *, limit: int = 5_000) -> int:
    if limit <= 0:
        raise ValueError("limit must be positive")
    rows = list(
        db.scalars(
            select(ResearchObservation)
            .where(ResearchObservation.source.like("V3_%"))
            .order_by(ResearchObservation.id.desc())
            .limit(limit)
        )
    )
    rows.reverse()
    lines = [
        json.dumps(
            {
                "id": row.id,
                "observation_key": row.observation_key,
                "experiment_id": row.experiment_id,
                "source": row.source,
                "market_id": row.market_id,
                "asset_id": row.asset_id,
                "source_timestamp_ms": row.source_timestamp_ms,
                "receive_timestamp_ms": row.receive_timestamp_ms,
                "payload_hash": row.payload_hash,
                "payload": json.loads(row.payload_json),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        for row in rows
    ]
    raw = ("\n".join(lines) + ("\n" if lines else "")).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    return len(rows)


def read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = gzip.decompress(path.read_bytes()).decode()
    return [json.loads(line) for line in raw.splitlines() if line.strip()]
