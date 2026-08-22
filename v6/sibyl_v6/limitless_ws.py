from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .feeds import _freshness, _source_timestamp_ms

DEFAULT_MAX_AGE_MS = 15_000
DEFAULT_TIMEOUT_MS = 5_000


def desired_subscription_slugs(audit: dict[str, Any]) -> list[str]:
    """Return the complete current exact-pair slug set for one replacement subscription."""
    exact = audit.get("exact_pairs") or []
    if not isinstance(exact, list):
        return []
    return sorted(
        {
            str(row.get("limitless_slug"))
            for row in exact
            if isinstance(row, dict) and row.get("limitless_slug")
        }
    )


def _helper_path() -> Path:
    configured = os.environ.get("SIBYL_V6_LIMITLESS_WS_HELPER")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "limitless_ws_snapshot.mjs"


def fetch_limitless_ws_snapshot(
    *,
    target_slug: str,
    desired_slugs: list[str],
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_reconnects: int = 1,
    runner=subprocess.run,
) -> dict[str, Any]:
    """Read one public timestamped Limitless orderbookUpdate without shell interpolation.

    Subscription replacement is handled by always passing the complete desired slug set.
    On reconnect the helper re-emits that same set. Failure never falls back to fabricated
    freshness; callers may still retain REST as an untimestamped reconciliation snapshot.
    """
    if target_slug not in desired_slugs or not desired_slugs:
        return {
            "connected": False,
            "event_received": False,
            "orderbook": None,
            "timestamp": None,
            "received_at_ms": None,
            "reconnects": 0,
            "resubscribe_count": 0,
            "desired_market_slugs": desired_slugs,
            "error": "TARGET_NOT_IN_COMPLETE_SUBSCRIPTION_SET",
        }
    helper = _helper_path()
    try:
        proc = runner(
            [
                "node",
                str(helper),
                json.dumps(desired_slugs, separators=(",", ":")),
                target_slug,
                str(int(timeout_ms)),
                str(int(max_reconnects)),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=max(2.0, timeout_ms / 1000.0 + 2.0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "connected": False,
            "event_received": False,
            "orderbook": None,
            "timestamp": None,
            "received_at_ms": None,
            "reconnects": 0,
            "resubscribe_count": 0,
            "desired_market_slugs": desired_slugs,
            "error": f"WS_HELPER_FAILURE:{type(exc).__name__}",
        }
    if proc.returncode != 0:
        return {
            "connected": False,
            "event_received": False,
            "orderbook": None,
            "timestamp": None,
            "received_at_ms": None,
            "reconnects": 0,
            "resubscribe_count": 0,
            "desired_market_slugs": desired_slugs,
            "error": f"WS_HELPER_EXIT_{proc.returncode}",
        }
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {
            "connected": False,
            "event_received": False,
            "orderbook": None,
            "timestamp": None,
            "received_at_ms": None,
            "reconnects": 0,
            "resubscribe_count": 0,
            "desired_market_slugs": desired_slugs,
            "error": "WS_HELPER_INVALID_JSON",
        }
    return payload if isinstance(payload, dict) else {"error": "WS_HELPER_INVALID_PAYLOAD"}


def classify_ws_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    observed_at_ms: int,
    max_age_ms: int = DEFAULT_MAX_AGE_MS,
) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {"status": "DISCONNECTED", "age_ms": None, "source_timestamp_ms": None}
    source_ts = _source_timestamp_ms({"timestamp": snapshot.get("timestamp")})
    age_ms, freshness = _freshness(source_ts, observed_at_ms, max_age_ms)
    if not snapshot.get("connected") or not snapshot.get("event_received"):
        status = "DISCONNECTED"
    elif not isinstance(snapshot.get("orderbook"), dict):
        status = "INVALID"
    else:
        status = freshness
    return {
        "status": status,
        "age_ms": age_ms,
        "source_timestamp_ms": source_ts,
        "received_at_ms": snapshot.get("received_at_ms"),
        "reconnects": int(snapshot.get("reconnects") or 0),
        "resubscribe_count": int(snapshot.get("resubscribe_count") or 0),
        "desired_market_slugs": snapshot.get("desired_market_slugs") or [],
        "error": snapshot.get("error"),
    }
