const URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market";

function emit(payload, code = 0) {
  process.stdout.write(JSON.stringify(payload) + "\n");
  process.exit(code);
}

let tokenIds;
try {
  tokenIds = JSON.parse(process.argv[2] || "[]");
} catch {
  emit({ connected: false, event_received: false, error: "INVALID_TOKEN_JSON" }, 2);
}
const timeoutMs = Math.min(30000, Math.max(1000, Number(process.argv[3] || 5000)));
const maxReconnects = Math.min(3, Math.max(0, Number(process.argv[4] || 1)));
if (!Array.isArray(tokenIds) || tokenIds.length === 0 || tokenIds.length > 32 ||
    tokenIds.some((v) => typeof v !== "string" || !/^\d{1,100}$/.test(v))) {
  emit({ connected: false, event_received: false, error: "INVALID_TOKEN_SET" }, 2);
}
tokenIds = [...new Set(tokenIds)].sort();

const deadline = Date.now() + timeoutMs;
const books = {};
const timestamps = {};
const receivedAt = {};
let reconnects = 0;
let resubscribeCount = 0;
let pongCount = 0;
let connected = false;
let lastError = null;
let finished = false;
let ws = null;
let pingTimer = null;
let timeoutTimer = null;

function cleanSocket() {
  if (pingTimer) clearInterval(pingTimer);
  pingTimer = null;
  try { ws?.close(); } catch {}
}

function finish(error = null, code = 0) {
  if (finished) return;
  finished = true;
  cleanSocket();
  if (timeoutTimer) clearTimeout(timeoutTimer);
  emit({
    connected,
    event_received: tokenIds.every((id) => books[id] && timestamps[id] != null),
    books,
    timestamps,
    received_at_ms: receivedAt,
    reconnects,
    resubscribe_count: resubscribeCount,
    pong_count: pongCount,
    desired_token_ids: tokenIds,
    error: error || lastError,
  }, code);
}

function normalizeEvents(raw) {
  if (Array.isArray(raw)) return raw;
  return raw && typeof raw === "object" ? [raw] : [];
}

function maybeDone() {
  if (tokenIds.every((id) => books[id] && timestamps[id] != null)) finish(null, 0);
}

function connect() {
  if (finished) return;
  if (Date.now() >= deadline) return finish("WS_TIMEOUT", 0);
  ws = new WebSocket(URL);

  ws.addEventListener("open", () => {
    connected = true;
    const frame = {
      assets_ids: tokenIds,
      type: "market",
      custom_feature_enabled: true,
    };
    ws.send(JSON.stringify(frame));
    resubscribeCount += 1;
    pingTimer = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN) {
        try { ws.send("PING"); } catch {}
      }
    }, 10000);
  });

  ws.addEventListener("message", (event) => {
    const now = Date.now();
    const text = typeof event.data === "string" ? event.data : String(event.data);
    if (text === "PONG") {
      pongCount += 1;
      return;
    }
    let parsed;
    try { parsed = JSON.parse(text); } catch { return; }
    for (const msg of normalizeEvents(parsed)) {
      if (msg?.event_type !== "book") continue;
      const token = String(msg.asset_id || "");
      if (!tokenIds.includes(token)) continue;
      if (!Array.isArray(msg.bids) || !Array.isArray(msg.asks)) continue;
      const ts = Number(msg.timestamp);
      if (!Number.isFinite(ts) || ts <= 0) continue;
      books[token] = {
        bids: msg.bids,
        asks: msg.asks,
        timestamp: String(msg.timestamp),
        hash: msg.hash ?? null,
        market: msg.market ?? null,
        asset_id: token,
      };
      timestamps[token] = ts;
      receivedAt[token] = now;
    }
    maybeDone();
  });

  ws.addEventListener("error", () => {
    lastError = "WS_ERROR";
  });

  ws.addEventListener("close", () => {
    connected = false;
    if (finished) return;
    if (pingTimer) clearInterval(pingTimer);
    pingTimer = null;
    if (tokenIds.every((id) => books[id] && timestamps[id] != null)) return finish(null, 0);
    if (reconnects < maxReconnects && Date.now() < deadline) {
      reconnects += 1;
      setTimeout(connect, 100);
    } else {
      finish(lastError || "WS_CLOSED_BEFORE_COMPLETE_BOOK_SET", 0);
    }
  });
}

timeoutTimer = setTimeout(() => finish("WS_TIMEOUT", 0), timeoutMs + 250);
connect();
