from __future__ import annotations

import argparse
import json
import socket
import ssl
import statistics
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path


TARGETS = {
    "polymarket_rest": "https://clob.polymarket.com/time",
    "limitless_rest": "https://api.limitless.exchange/markets/active?limit=1&page=1",
    "polymarket_ws": "wss://ws-subscriptions-clob.polymarket.com/ws/market",
    # Official pinned upstream uses socket.io-client with path=/socket.io and
    # websocket-only transport. Probe the same Engine.IO transport endpoint.
    "limitless_ws": "wss://ws.limitless.exchange/socket.io/?EIO=4&transport=websocket",
}


@dataclass(frozen=True)
class Sample:
    target: str
    connect_ms: float | None
    tls_ms: float | None
    ttfb_ms: float | None
    ws_connect_ms: float | None
    status: int | None
    error: str | None


def _probe(url: str, timeout: float = 8.0) -> tuple[float, float, float, float | None, int]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError("target host missing")
    port = parsed.port or 443
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    start = time.perf_counter()
    raw = socket.create_connection((host, port), timeout=timeout)
    connected = time.perf_counter()
    context = ssl.create_default_context()
    tls = context.wrap_socket(raw, server_hostname=host)
    tls_done = time.perf_counter()
    if parsed.scheme == "wss":
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: sibyl-v6-region-probe/1.0\r\n\r\n"
        )
    else:
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n"
            "User-Agent: sibyl-v6-region-probe/1.0\r\n\r\n"
        )
    tls.sendall(request.encode("ascii"))
    first = tls.recv(1)
    first_byte = time.perf_counter()
    if not first:
        raise RuntimeError("empty response")
    rest = tls.recv(4095)
    line = (first + rest).split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    try:
        status = int(line.split()[1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(f"invalid status line: {line}") from exc
    tls.close()

    connect_ms = round((connected - start) * 1000, 3)
    tls_ms = round((tls_done - connected) * 1000, 3)
    ttfb_ms = round((first_byte - tls_done) * 1000, 3)
    ws_connect_ms = (
        round((first_byte - start) * 1000, 3) if parsed.scheme == "wss" else None
    )
    return connect_ms, tls_ms, ttfb_ms, ws_connect_ms, status


def _metric(values: list[float]) -> dict[str, float | None]:
    return {
        "median_ms": round(statistics.median(values), 3) if values else None,
        "p95_ms": round(_p95(values), 3) if values else None,
    }


def summarize_samples(samples: list[Sample]) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for target, url in TARGETS.items():
        rows = [row for row in samples if row.target == target]
        statuses = [row.status for row in rows if row.status is not None]
        is_ws = urllib.parse.urlparse(url).scheme == "wss"
        protocol_ok = [row for row in rows if row.status == (101 if is_ws else 200)]
        metric_rows = [row for row in rows if row.connect_ms is not None]
        connect_values = [float(row.connect_ms) for row in metric_rows if row.connect_ms is not None]
        tls_values = [float(row.tls_ms) for row in metric_rows if row.tls_ms is not None]
        ttfb_values = [float(row.ttfb_ms) for row in metric_rows if row.ttfb_ms is not None]
        ws_values = [
            float(row.ws_connect_ms)
            for row in metric_rows
            if row.ws_connect_ms is not None
        ]
        summary[target] = {
            "samples": len(rows),
            "transport_samples": len(metric_rows),
            "protocol_successful_samples": len(protocol_ok),
            "connect": _metric(connect_values),
            "tls": _metric(tls_values),
            "ttfb": _metric(ttfb_values),
            "ws_connect": _metric(ws_values) if is_ws else None,
            "http_statuses": statuses,
            "expected_status": 101 if is_ws else 200,
            "geoblock_451_observed": 451 in statuses,
            "errors": [row.error for row in rows if row.error],
        }
    return summary


def run_probe(region: str, repetitions: int = 5) -> dict:
    samples: list[Sample] = []
    for name, url in TARGETS.items():
        for _ in range(repetitions):
            try:
                connect, tls, ttfb, ws_connect, status = _probe(url)
                samples.append(Sample(name, connect, tls, ttfb, ws_connect, status, None))
            except Exception as exc:
                samples.append(Sample(name, None, None, None, None, None, str(exc)))
            time.sleep(0.15)

    summary = summarize_samples(samples)
    return {
        "schema_version": "SIBYL_V6_REGION_PROBE_V2",
        "region": region,
        "measured_at_unix_ms": int(time.time() * 1000),
        "repetitions": repetitions,
        "summary": summary,
        "samples": [asdict(row) for row in samples],
        "jurisdiction_bypass_attempted": False,
    }


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty")
    index = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = run_probe(args.region, args.repetitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
