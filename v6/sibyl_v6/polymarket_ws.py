from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .feeds import _freshness, _source_timestamp_ms

DEFAULT_MAX_AGE_MS = 15_000
DEFAULT_TIMEOUT_MS = 5_000


def desired_token_ids(tokens: dict[str, str]) -> list[str]:
    values = sorted({str(v) for v in tokens.values() if str(v)})
    return values if len(values) == 2 and all(v.isdigit() for v in values) else []


def _helper_path() -> Path:
    configured = os.environ.get("SIBYL_V6_POLYMARKET_WS_HELPER")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "polymarket_ws_snapshot.mjs"


def _failure(token_ids: list[str], error: str) -> dict[str, Any]:
    return {
        "connected": False,
        "event_received": False,
        "books": {},
        "timestamps": {},
        "received_at_ms": {},
        "reconnects": 0,
        "resubscribe_count": 0,
        "pong_count": 0,
        "desired_token_ids": token_ids,
        "error": error,
    }


def fetch_polymarket_ws_snapshot(
    *,
    token_ids: list[str],
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_reconnects: int = 1,
    runner=subprocess.run,
) -> dict[str, Any]:
    """Fetch one full public book snapshot for every requested Polymarket token.

    The helper is invoked without a shell. The complete token set is passed on every
    connection and reconnect so a reconnect cannot silently restore only one outcome.
    """
    token_ids = sorted({str(v) for v in token_ids})
    if len(token_ids) != 2 or any(not value.isdigit() for value in token_ids):
        return _failure(token_ids, "INVALID_EXACT_TOKEN_SET")
    try:
        proc = runner(
            [
                "node",
                str(_helper_path()),
                json.dumps(token_ids, separators=(",", ":")),
                str(int(timeout_ms)),
                str(int(max_reconnects)),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=max(2.0, timeout_ms / 1000.0 + 2.0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _failure(token_ids, f"WS_HELPER_FAILURE:{type(exc).__name__}")
    if proc.returncode != 0:
        return _failure(token_ids, f"WS_HELPER_EXIT_{proc.returncode}")
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return _failure(token_ids, "WS_HELPER_INVALID_JSON")
    return payload if isinstance(payload, dict) else _failure(token_ids, "WS_HELPER_INVALID_PAYLOAD")


def classify_ws_books(
    snapshot: dict[str, Any] | None,
    *,
    token_ids: list[str],
    observed_at_ms: int,
    max_age_ms: int = DEFAULT_MAX_AGE_MS,
) -> dict[str, Any]:
    token_ids = sorted({str(v) for v in token_ids})
    if not isinstance(snapshot, dict):
        snapshot = _failure(token_ids, "MISSING_SNAPSHOT")
    books = snapshot.get("books") if isinstance(snapshot.get("books"), dict) else {}
    received = (
        snapshot.get("received_at_ms")
        if isinstance(snapshot.get("received_at_ms"), dict)
        else {}
    )
    per_token: dict[str, dict[str, Any]] = {}
    for token in token_ids:
        book = books.get(token)
        source_ts = _source_timestamp_ms(book) if isinstance(book, dict) else None
        age_ms, freshness = _freshness(source_ts, observed_at_ms, max_age_ms)
        if not snapshot.get("connected"):
            status = "DISCONNECTED"
        elif not isinstance(book, dict):
            status = "NO_EVENT"
        elif not isinstance(book.get("bids"), list) or not isinstance(book.get("asks"), list):
            status = "INVALID"
        else:
            status = freshness
        per_token[token] = {
            "status": status,
            "age_ms": age_ms,
            "source_timestamp_ms": source_ts,
            "received_at_ms": received.get(token),
        }
    statuses = [row["status"] for row in per_token.values()]
    overall = "FRESH" if statuses and all(value == "FRESH" for value in statuses) else (
        statuses[0] if statuses and len(set(statuses)) == 1 else "MIXED"
    )
    return {
        "status": overall,
        "per_token": per_token,
        "reconnects": int(snapshot.get("reconnects") or 0),
        "resubscribe_count": int(snapshot.get("resubscribe_count") or 0),
        "pong_count": int(snapshot.get("pong_count") or 0),
        "desired_token_ids": snapshot.get("desired_token_ids") or token_ids,
        "error": snapshot.get("error"),
    }
