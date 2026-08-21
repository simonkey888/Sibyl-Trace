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
    "limitless_ws": "wss://ws.limitless.exchange/",
}


@dataclass(frozen=True)
class Sample:
    target: str
    connect_ms: float | None
    tls_ms: float | None
    ttfb_ms: float | None
    status: int | None
    error: str | None



def _probe(url: str, timeout: float = 8.0) -> tuple[float, float, float, int]:
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
            "Sec-WebSocket-Key: c2lieWwtdjYtcHJvYmU=\r\n"
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
    return (
        round((connected - start) * 1000, 3),
        round((tls_done - connected) * 1000, 3),
        round((first_byte - tls_done) * 1000, 3),
        status,
    )


def run_probe(region: str, repetitions: int = 5) -> dict:
    samples: list[Sample] = []
    for name, url in TARGETS.items():
        for _ in range(repetitions):
            try:
                connect, tls, ttfb, status = _probe(url)
                samples.append(Sample(name, connect, tls, ttfb, status, None))
            except Exception as exc:
                samples.append(Sample(name, None, None, None, None, str(exc)))
            time.sleep(0.15)

    summary = {}
    for target in TARGETS:
        rows = [row for row in samples if row.target == target]
        good = [row for row in rows if row.ttfb_ms is not None]
        values = [row.ttfb_ms for row in good if row.ttfb_ms is not None]
        statuses = [row.status for row in good]
        summary[target] = {
            "samples": len(rows),
            "successful_samples": len(good),
            "median_ttfb_ms": round(statistics.median(values), 3) if values else None,
            "p95_ttfb_ms": round(_p95(values), 3) if values else None,
            "http_statuses": statuses,
            "geoblock_451_observed": 451 in statuses,
        }
    return {
        "schema_version": "SIBYL_V6_REGION_PROBE_V1",
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
