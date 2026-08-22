#!/usr/bin/env node

const slugs = JSON.parse(process.argv[2] || '[]');
const targetSlug = process.argv[3] || '';
const timeoutMs = Number(process.argv[4] || '5000');
const maxReconnects = Number(process.argv[5] || '1');

if (!Array.isArray(slugs) || slugs.length === 0 || !targetSlug || !slugs.includes(targetSlug)) {
  console.error('INVALID_WS_SUBSCRIPTION_SET');
  process.exit(2);
}
if (!slugs.every((s) => typeof s === 'string' && s.length > 0 && s.length <= 256)) {
  console.error('INVALID_WS_MARKET_SLUG');
  process.exit(2);
}

const endpoint = 'wss://ws.limitless.exchange/socket.io/?EIO=4&transport=websocket';
const startedAt = Date.now();
let reconnects = 0;
let resubscribeCount = 0;
let attempts = 0;
let settled = false;
let lastError = null;
let everConnected = false;
let namespaceReadyAny = false;

function emit(payload) {
  if (settled) return;
  settled = true;
  process.stdout.write(JSON.stringify(payload) + '\n');
  process.exit(0);
}

function parseSocketIoEvent(text) {
  const prefixes = ['42/markets,', '42'];
  for (const prefix of prefixes) {
    if (!text.startsWith(prefix)) continue;
    try {
      const payload = JSON.parse(text.slice(prefix.length));
      if (Array.isArray(payload) && payload.length >= 2) return payload;
    } catch {}
  }
  return null;
}

function connect() {
  attempts += 1;
  const ws = new WebSocket(endpoint);

  ws.addEventListener('open', () => { everConnected = true; });
  ws.addEventListener('message', (event) => {
    const text = String(event.data);
    if (text === '2') {
      ws.send('3');
      return;
    }
    if (text.startsWith('0')) {
      ws.send('40/markets,');
      return;
    }
    if (text.startsWith('40/markets')) {
      namespaceReadyAny = true;
      resubscribeCount += 1;
      ws.send(`42/markets,["subscribe_market_prices",${JSON.stringify({ marketSlugs: slugs })}]`);
      return;
    }
    const parsed = parseSocketIoEvent(text);
    if (!parsed || parsed[0] !== 'orderbookUpdate') return;
    const data = parsed[1];
    if (!data || data.marketSlug !== targetSlug) return;
    const book = data.orderbook && typeof data.orderbook === 'object'
      ? data.orderbook
      : { bids: data.bids || [], asks: data.asks || [] };
    emit({
      endpoint,
      connected: true,
      event_received: true,
      target_slug: targetSlug,
      desired_market_slugs: slugs,
      namespace_ready: namespaceReadyAny,
      timestamp: data.timestamp ?? null,
      received_at_ms: Date.now(),
      orderbook: book,
      reconnects,
      resubscribe_count: resubscribeCount,
      attempts,
      error: null,
    });
    try { ws.close(); } catch {}
  });
  ws.addEventListener('error', (event) => {
    lastError = event?.message || 'WEBSOCKET_ERROR';
  });
  ws.addEventListener('close', () => {
    if (settled || Date.now() - startedAt >= timeoutMs) return;
    if (reconnects < maxReconnects) {
      reconnects += 1;
      setTimeout(connect, 150);
    }
  });
}

setTimeout(() => {
  emit({
    endpoint,
    connected: everConnected,
    event_received: false,
    target_slug: targetSlug,
    desired_market_slugs: slugs,
    namespace_ready: namespaceReadyAny,
    timestamp: null,
    received_at_ms: Date.now(),
    orderbook: null,
    reconnects,
    resubscribe_count: resubscribeCount,
    attempts,
    error: lastError || 'NO_ORDERBOOK_EVENT_WITHIN_TIMEOUT',
  });
}, Math.max(500, timeoutMs));

connect();
