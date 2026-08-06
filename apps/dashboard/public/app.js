const state = { data: null, connected: false };
const $ = (id) => document.getElementById(id);
const money = (value) => new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
}).format(Number(value || 0));
const pct = (value) => `${(Number(value || 0) * 100).toFixed(2)}%`;
const shortWallet = (value = "") => value.length > 12
  ? `${value.slice(0, 6)}…${value.slice(-4)}`
  : value || "—";
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
})[character]);
const ago = (unix) => {
  if (!unix) return "—";
  const seconds = Math.max(Math.floor(Date.now() / 1000 - Number(unix)), 0);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
};
const dateTime = (value) => value ? new Date(value).toLocaleString() : "—";
const emptyRow = (cols, label) => `<tr class="empty-row"><td colspan="${Number(cols)}">${escapeHtml(label)}</td></tr>`;

function setConnection(connected) {
  state.connected = connected;
  const pill = $("connectionPill");
  pill.className = `pill ${connected ? "connected" : "disconnected"}`;
  pill.innerHTML = `<span></span> ${connected ? "CONNECTED" : "DISCONNECTED"}`;
  $("originState").textContent = connected ? "ONLINE" : "OFFLINE";
}

function render(data) {
  state.data = data;
  const p = data.portfolio || {};
  const system = data.system || {};
  const initial = Number(p.initial_bankroll || 0);
  const delta = initial ? (Number(p.equity || 0) - initial) / initial : 0;
  $("equityValue").textContent = money(p.equity);
  $("equityDelta").textContent = `${delta >= 0 ? "+" : ""}${pct(delta)}`;
  $("equityDelta").className = delta >= 0 ? "positive" : "negative";
  $("cashValue").textContent = money(p.cash);
  $("exposureValue").textContent = money(p.exposure);
  $("realizedValue").textContent = money(p.realized_pnl);
  $("unrealizedValue").textContent = money(p.unrealized_pnl);
  $("drawdownValue").textContent = pct(p.drawdown);
  $("realizedValue").className = `metric-value small ${Number(p.realized_pnl) >= 0 ? "positive" : "negative"}`;
  $("unrealizedValue").className = `metric-value small ${Number(p.unrealized_pnl) >= 0 ? "positive" : "negative"}`;
  $("realizedBar").style.width = `${Math.min(Math.abs(Number(p.realized_pnl || 0)) / 30 * 100, 100)}%`;
  $("unrealizedBar").style.width = `${Math.min(Math.abs(Number(p.unrealized_pnl || 0)) / 30 * 100, 100)}%`;
  $("drawdownBar").style.width = `${Math.min(Number(p.drawdown || 0) / 0.1 * 100, 100)}%`;

  $("runtimeMode").textContent = system.mode || "—";
  $("runtimeVersion").textContent = system.version || "—";
  $("modeState").textContent = system.mode || "—";
  $("pauseState").textContent = system.paused ? "PAUSED" : "ACTIVE";
  $("pauseState").className = system.paused ? "negative" : "positive";
  $("killState").textContent = system.kill_switch ? "ACTIVE" : "CLEAR";
  $("killState").className = system.kill_switch ? "negative" : "positive";
  $("geoState").textContent = String(system.geoblock || "UNKNOWN").toUpperCase();
  $("geoState").className = system.geoblock === "clear"
    ? "positive"
    : system.geoblock === "blocked" ? "negative" : "";

  renderChart(data.equity || []);
  renderWallets(data.wallets || []);
  renderSignals(data.signals || []);
  renderOrders(data.orders || []);
  renderAudit(data.events || []);
}

function renderChart(points) {
  const svg = $("equityChart");
  const empty = $("chartEmpty");
  svg.replaceChildren();
  if (!points.length) {
    empty.style.display = "grid";
    return;
  }
  empty.style.display = "none";
  const width = 900;
  const height = 300;
  const pad = 22;
  const values = points.map((point) => Number(point.equity));
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const x = (index) => pad + (index / Math.max(points.length - 1, 1)) * (width - pad * 2);
  const y = (value) => height - pad - ((value - min) / (max - min)) * (height - pad * 2);
  for (let index = 0; index < 5; index += 1) {
    const lineY = pad + index * ((height - pad * 2) / 4);
    svg.insertAdjacentHTML(
      "beforeend",
      `<line x1="${pad}" y1="${lineY}" x2="${width - pad}" y2="${lineY}" stroke="rgba(143,189,205,.09)" stroke-width="1"/>`,
    );
  }
  const line = points
    .map((point, index) => `${index ? "L" : "M"}${x(index)},${y(Number(point.equity))}`)
    .join(" ");
  const area = `${line} L${x(points.length - 1)},${height - pad} L${x(0)},${height - pad} Z`;
  svg.insertAdjacentHTML(
    "beforeend",
    `<defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#45f1d2" stop-opacity=".22"/><stop offset="1" stop-color="#45f1d2" stop-opacity="0"/></linearGradient></defs>`,
  );
  svg.insertAdjacentHTML(
    "beforeend",
    `<path d="${area}" fill="url(#area)"/><path d="${line}" fill="none" stroke="#45f1d2" stroke-width="2" vector-effect="non-scaling-stroke"/>`,
  );
  const last = points.length - 1;
  svg.insertAdjacentHTML(
    "beforeend",
    `<circle cx="${x(last)}" cy="${y(values[last])}" r="4" fill="#071017" stroke="#45f1d2" stroke-width="2" vector-effect="non-scaling-stroke"/>`,
  );
}

function renderWallets(wallets) {
  const selected = wallets.filter((wallet) => wallet.selected);
  $("walletStack").innerHTML = selected.length
    ? selected.map((wallet) => `
      <div class="wallet-card">
        <div><strong>${escapeHtml(wallet.username || shortWallet(wallet.address))}</strong><small>${escapeHtml(shortWallet(wallet.address))} · ${Number(wallet.closed_count || 0)} closed</small></div>
        <div class="score-ring">${Math.round(Number(wallet.score || 0))}</div>
        <span class="tag good">TRACKED</span>
      </div>`).join("")
    : `<div class="empty-row"><div>Awaiting first wallet scan</div></div>`;
  $("walletTable").innerHTML = wallets.length
    ? wallets.map((wallet) => {
      const status = wallet.selected ? "TRACKED" : wallet.rejection_reason || "ELIGIBLE";
      const statusClass = wallet.selected ? "good" : wallet.rejection_reason ? "bad" : "warn";
      return `
        <tr>
          <td><strong>${escapeHtml(wallet.username || "Anonymous")}</strong><br><span class="mono muted">${escapeHtml(shortWallet(wallet.address))}</span></td>
          <td class="mono ${Number(wallet.score) >= 65 ? "positive" : "muted"}">${Number(wallet.score || 0).toFixed(1)}</td>
          <td>${pct(wallet.win_rate)}</td>
          <td class="mono">${Number(wallet.profit_factor || 0).toFixed(2)}</td>
          <td class="${Number(wallet.realized_pnl) >= 0 ? "positive" : "negative"}">${money(wallet.realized_pnl)}</td>
          <td>${Number(wallet.closed_count || 0)}</td>
          <td>${pct(wallet.concentration)}</td>
          <td><span class="tag ${statusClass}">${escapeHtml(status)}</span></td>
        </tr>`;
    }).join("")
    : emptyRow(8, "No wallets scored yet");
}

function renderSignals(signals) {
  $("signalStack").innerHTML = signals.length
    ? signals.slice(0, 5).map((signal) => {
      const side = String(signal.side || "UNKNOWN").toUpperCase();
      const decisionClass = signal.decision === "APPROVED"
        ? "good"
        : signal.decision === "REJECTED" ? "bad" : "warn";
      return `
        <div class="signal-card">
          <div class="signal-side ${side === "BUY" ? "buy" : "sell"}">${escapeHtml(side)}</div>
          <div><strong>${escapeHtml(signal.market)}</strong><small>${escapeHtml(signal.outcome)} · ${escapeHtml(ago(signal.timestamp))} · ${escapeHtml(shortWallet(signal.wallet))}</small></div>
          <span class="tag ${decisionClass}">${escapeHtml(signal.decision)}</span>
        </div>`;
    }).join("")
    : `<div class="empty-row"><div>No wallet activity observed</div></div>`;
  $("signalTable").innerHTML = signals.length
    ? signals.map((signal) => {
      const side = String(signal.side || "UNKNOWN").toUpperCase();
      const decision = `${signal.decision || "UNKNOWN"}${signal.reason ? ` · ${signal.reason}` : ""}`;
      return `
        <tr>
          <td>${escapeHtml(ago(signal.timestamp))}</td>
          <td class="mono">${escapeHtml(shortWallet(signal.wallet))}</td>
          <td>${escapeHtml(signal.market)}</td>
          <td><span class="tag ${side === "BUY" ? "good" : "warn"}">${escapeHtml(side)}</span></td>
          <td>${escapeHtml(signal.outcome)}</td>
          <td class="mono">${Number(signal.price || 0).toFixed(3)}</td>
          <td>${money(signal.usdc)}</td>
          <td><span class="tag ${signal.decision === "APPROVED" ? "good" : "bad"}">${escapeHtml(decision)}</span></td>
        </tr>`;
    }).join("")
    : emptyRow(8, "No signals observed");
}

function renderOrders(orders) {
  $("orderTable").innerHTML = orders.length
    ? orders.map((order) => {
      const side = String(order.side || "UNKNOWN").toUpperCase();
      const status = `${order.status || "UNKNOWN"}${order.reason ? ` · ${order.reason}` : ""}`;
      return `
        <tr>
          <td>${escapeHtml(dateTime(order.created_at))}</td>
          <td>${escapeHtml(order.market)}<br><span class="muted">${escapeHtml(order.outcome)}</span></td>
          <td><span class="tag ${side === "BUY" ? "good" : "warn"}">${escapeHtml(side)}</span></td>
          <td>${money(order.requested_usd)}</td>
          <td>${money(order.filled_usd)}</td>
          <td class="mono">${Number(order.source_price || 0).toFixed(3)}</td>
          <td class="mono">${order.observed_price == null ? "—" : Number(order.observed_price).toFixed(3)}</td>
          <td class="mono">${order.slippage == null ? "—" : Number(order.slippage).toFixed(4)}</td>
          <td><span class="tag ${order.status === "FILLED" ? "good" : "bad"}">${escapeHtml(status)}</span></td>
        </tr>`;
    }).join("")
    : emptyRow(9, "No paper orders yet");
}

function renderAudit(events) {
  $("auditList").innerHTML = events.length
    ? events.map((event) => {
      const severity = String(event.severity || "INFO").toUpperCase();
      const severityClass = severity === "ERROR" || severity === "CRITICAL"
        ? "negative"
        : severity === "WARN" ? "muted" : "positive";
      return `
        <div class="audit-event">
          <time>${escapeHtml(dateTime(event.time))}</time>
          <b class="${severityClass}">${escapeHtml(severity)}</b>
          <p><strong>${escapeHtml(event.type)}</strong><br>${escapeHtml(event.message)}</p>
        </div>`;
    }).join("")
    : `<div class="empty-row"><div>No audit events yet</div></div>`;
}

async function load() {
  try {
    const response = await fetch("/api/v1/dashboard", {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`API ${response.status}`);
    render(await response.json());
    setConnection(true);
  } catch (error) {
    console.error(error);
    setConnection(false);
  }
}

async function control(action, confirmation) {
  if (confirmation && !window.confirm(confirmation)) return;
  try {
    const response = await fetch(`/api/v1/control/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `Control failed: ${response.status}`);
    toast(`Control accepted: ${action}`);
    await load();
  } catch (error) {
    toast(error.message || String(error), true);
  }
}

function toast(message, error = false) {
  const node = $("toast");
  node.textContent = message;
  node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => {
    node.className = "toast";
  }, 3200);
}

function navigate(target) {
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === target);
  });
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.target === target);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => navigate(button.dataset.target));
});
document.querySelectorAll("[data-jump]").forEach((button) => {
  button.addEventListener("click", () => navigate(button.dataset.jump));
});
$("pauseButton").addEventListener("click", () => control("pause"));
$("resumeButton").addEventListener("click", () => control("resume"));
$("killButton").addEventListener("click", () => control(
  "kill",
  "Activate the emergency stop? All new paper orders will be blocked.",
));
$("clearKillButton").addEventListener("click", () => control(
  "clear-kill",
  "Clear the emergency stop? The system will remain paused.",
));
setInterval(() => {
  $("clock").textContent = `${new Date().toISOString().slice(11, 19)} UTC`;
}, 1000);
setInterval(load, 12000);
load();
