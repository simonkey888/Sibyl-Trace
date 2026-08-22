from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .feeds import _freshness, _source_timestamp_ms

DEFAULT_MAX_AGE_MS = 15_000
DEFAULT_TIMEOUT_MS = 5_000
MAX_FRAME_BYTES = 2_000_000
WS_HOST = "ws-subscriptions-clob.polymarket.com"
WS_PATH = "/ws/market"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def desired_token_ids(tokens: dict[str, str]) -> list[str]:
    values = sorted({str(v) for v in tokens.values() if str(v)})
    return values if len(values) == 2 and all(v.isdigit() for v in values) else []


def _helper_path() -> Path:
    configured = os.environ.get("SIBYL_V6_POLYMARKET_WS_HELPER")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "polymarket_ws_snapshot.mjs"


def _failure(token_ids: list[str], error: str, **extra: Any) -> dict[str, Any]:
    return {
        "connected": False,
        "event_received": False,
        "books": {},
        "timestamps": {},
        "received_at_ms": {},
        "reconnects": int(extra.get("reconnects") or 0),
        "resubscribe_count": int(extra.get("resubscribe_count") or 0),
        "pong_count": int(extra.get("pong_count") or 0),
        "desired_token_ids": token_ids,
        "error": error,
    }


def _recv_exact(sock: Any, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("WS_EOF")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _masked_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    if len(payload) > MAX_FRAME_BYTES:
        raise ValueError("WS_CLIENT_FRAME_TOO_LARGE")
    mask = os.urandom(4)
    first = 0x80 | (opcode & 0x0F)
    length = len(payload)
    if length < 126:
        head = bytes([first, 0x80 | length])
    elif length <= 0xFFFF:
        head = bytes([first, 0x80 | 126]) + struct.pack("!H", length)
    else:
        head = bytes([first, 0x80 | 127]) + struct.pack("!Q", length)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return head + mask + masked


def _read_frame(sock: Any) -> tuple[int, bool, bytes]:
    first, second = _recv_exact(sock, 2)
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    if length > MAX_FRAME_BYTES:
        raise ValueError("WS_SERVER_FRAME_TOO_LARGE")
    mask = _recv_exact(sock, 4) if masked else None
    payload = _recv_exact(sock, length) if length else b""
    if mask:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, fin, payload


def _connect(timeout_seconds: float) -> Any:
    raw = socket.create_connection((WS_HOST, 443), timeout=timeout_seconds)
    context = ssl.create_default_context()
    sock = context.wrap_socket(raw, server_hostname=WS_HOST)
    sock.settimeout(timeout_seconds)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {WS_PATH} HTTP/1.1\r\n"
        f"Host: {WS_HOST}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "User-Agent: sibyl-v6-polymarket-ws/1.0\r\n\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = bytearray()
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("WS_HANDSHAKE_EOF")
        response.extend(chunk)
        if len(response) > 65536:
            raise ValueError("WS_HANDSHAKE_TOO_LARGE")
    header = bytes(response).split(b"\r\n\r\n", 1)[0].decode("latin1")
    lines = header.split("\r\n")
    if not lines or " 101 " not in f" {lines[0]} ":
        raise ConnectionError("WS_HANDSHAKE_NOT_101")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().casefold()] = value.strip()
    expected = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")
    if headers.get("sec-websocket-accept") != expected:
        raise ConnectionError("WS_HANDSHAKE_ACCEPT_MISMATCH")
    return sock


def _events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _fetch_direct(token_ids: list[str], timeout_ms: int, max_reconnects: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_ms / 1000.0
    books: dict[str, dict[str, Any]] = {}
    timestamps: dict[str, int] = {}
    received_at: dict[str, int] = {}
    reconnects = 0
    resubscribe_count = 0
    pong_count = 0
    last_error = "NO_EVENT"

    for attempt in range(max_reconnects + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock = None
        try:
            sock = _connect(min(remaining, max(1.0, timeout_ms / 1000.0)))
            subscribe = json.dumps(
                {"assets_ids": token_ids, "type": "market", "custom_feature_enabled": True},
                separators=(",", ":"),
            ).encode("utf-8")
            sock.sendall(_masked_frame(subscribe))
            resubscribe_count += 1
            last_app_ping = time.monotonic()
            fragments = bytearray()
            fragment_opcode: int | None = None

            while time.monotonic() < deadline:
                if time.monotonic() - last_app_ping >= 10.0:
                    sock.sendall(_masked_frame(b"PING"))
                    last_app_ping = time.monotonic()
                opcode, fin, data = _read_frame(sock)
                if opcode == 0x8:
                    last_error = "WS_CLOSE"
                    break
                if opcode == 0x9:
                    sock.sendall(_masked_frame(data, opcode=0xA))
                    continue
                if opcode == 0xA:
                    pong_count += 1
                    continue
                if opcode in (0x1, 0x2):
                    fragments = bytearray(data)
                    fragment_opcode = opcode
                elif opcode == 0x0 and fragment_opcode is not None:
                    fragments.extend(data)
                else:
                    continue
                if not fin:
                    continue
                if fragment_opcode != 0x1:
                    fragments.clear()
                    fragment_opcode = None
                    continue
                text = bytes(fragments).decode("utf-8", errors="strict")
                fragments.clear()
                fragment_opcode = None
                if text == "PONG":
                    pong_count += 1
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                now_ms = int(time.time() * 1000)
                for msg in _events(parsed):
                    if msg.get("event_type") != "book":
                        continue
                    token = str(msg.get("asset_id") or "")
                    if token not in token_ids:
                        continue
                    if not isinstance(msg.get("bids"), list) or not isinstance(msg.get("asks"), list):
                        continue
                    source_ts = _source_timestamp_ms(msg)
                    if source_ts is None:
                        continue
                    books[token] = {
                        "bids": msg["bids"],
                        "asks": msg["asks"],
                        "timestamp": str(source_ts),
                        "hash": msg.get("hash"),
                        "market": msg.get("market"),
                        "asset_id": token,
                    }
                    timestamps[token] = source_ts
                    received_at[token] = now_ms
                if all(token in books for token in token_ids):
                    return {
                        "connected": True,
                        "event_received": True,
                        "books": books,
                        "timestamps": timestamps,
                        "received_at_ms": received_at,
                        "reconnects": reconnects,
                        "resubscribe_count": resubscribe_count,
                        "pong_count": pong_count,
                        "desired_token_ids": token_ids,
                        "error": None,
                    }
        except (OSError, ssl.SSLError, ConnectionError, ValueError, UnicodeError) as exc:
            last_error = f"{type(exc).__name__}:{str(exc)[:120]}"
        finally:
            try:
                if sock is not None:
                    sock.close()
            except OSError:
                pass
        if attempt < max_reconnects:
            reconnects += 1

    return _failure(
        token_ids,
        last_error,
        reconnects=reconnects,
        resubscribe_count=resubscribe_count,
        pong_count=pong_count,
    )


def fetch_polymarket_ws_snapshot(
    *,
    token_ids: list[str],
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_reconnects: int = 1,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Fetch both public books from the fixed Polymarket market WS.

    Runtime uses a validated stdlib WebSocket transport. Tests may inject a
    subprocess-like runner to verify argument isolation without network access.
    """
    token_ids = sorted({str(v) for v in token_ids})
    if len(token_ids) != 2 or any(not value.isdigit() for value in token_ids):
        return _failure(token_ids, "INVALID_EXACT_TOKEN_SET")
    timeout_ms = min(30_000, max(1_000, int(timeout_ms)))
    max_reconnects = min(3, max(0, int(max_reconnects)))
    if runner is None:
        return _fetch_direct(token_ids, timeout_ms, max_reconnects)

    try:
        proc = runner(
            [
                "node",
                str(_helper_path()),
                json.dumps(token_ids, separators=(",", ":")),
                str(timeout_ms),
                str(max_reconnects),
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
    received = snapshot.get("received_at_ms") if isinstance(snapshot.get("received_at_ms"), dict) else {}
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
