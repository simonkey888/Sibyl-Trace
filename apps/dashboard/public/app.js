const $ = (id) => document.getElementById(id);
const money = (value) => new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
}).format(Number(value || 0));
const number = (value, digits = 0) => Number(value || 0).toLocaleString("en-US", {
  minimumFractionDigits: digits,
  maximumFractionDigits: digits,
});
const pct = (value, digits = 2) => `${(Number(value || 0) * 100).toFixed(digits)}%`;
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
})[character]);
const dateTime = (value) => value ? new Date(value).toLocaleString() : "—";
const score = (value) => value == null ? "—" : Number(value).toFixed(1);

function relativeAge(value) {
  if (!value) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

function statusClass(value) {
  const normalized = String(value || "").toUpperCase();
  if (["GREEN", "PASS", "CAPTURED", "COMPLETE", "FILLED", "PAPER"].includes(normalized)) return "good";
  if (["RED", "FAIL", "FAILED", "ERROR", "REJECTED"].includes(normalized)) return "bad";
  return "warn";
}

function setText(id, value) {
  const node = $(id);
  if (node) node.textContent = value;
}

function setStatus(id, value) {
  const node = $(id);
  if (!node) return;
  node.textContent = value || "—";
  node.className = statusClass(value);
}

function renderWallets(wallets) {
  const list = Array.isArray(wallets) ? wallets : [];
  $("walletCards").innerHTML = list.length
    ? list.slice(0, 3).map((wallet) => `
      <div class="source-card">
        <div class="source-main">
          <strong>${escapeHtml(wallet.username || wallet.wallet || "Anonymous")}</strong>
          <small>${escapeHtml(wallet.wallet || "—")} · n=${number(wallet.closed_count)}</small>
        </div>
        <div class="score-cluster">
          <span><i>S</i><b>${score(wallet.short_score)}</b></span>
          <span><i>L</i><b>${score(wallet.long_score)}</b></span>
          <span><i>G</i><b>${score(wallet.global_score)}</b></span>
          <span><i>E</i><b>${score(wallet.execution_edge_score)}</b></span>
        </div>
      </div>`).join("")
    : '<div class="empty">No tracked wallets in this snapshot.</div>';

  $("walletTable").innerHTML = list.length
    ? list.map((wallet) => `
      <tr>
        <td><strong>${escapeHtml(wallet.username || "Anonymous")}</strong><br><small class="mono">${escapeHtml(wallet.wallet || "—")}</small></td>
        <td class="mono">${score(wallet.short_score)}</td>
        <td class="mono">${score(wallet.long_score)}</td>
        <td class="mono emphasis">${score(wallet.global_score)}</td>
        <td class="mono ${Number(wallet.execution_edge_score || 0) > 50 ? "good" : "muted"}">${score(wallet.execution_edge_score)}</td>
        <td>${number(wallet.execution_edge_sample_size)}</td>
        <td>${pct(wallet.win_rate)}</td>
        <td>${Number(wallet.profit_factor || 0).toFixed(2)}</td>
        <td class="${Number(wallet.realized_pnl || 0) >= 0 ? "good" : "bad"}">${money(wallet.realized_pnl)}</td>
      </tr>`).join("")
    : '<tr><td colspan="9" class="empty-cell">No wallet scores available.</td></tr>';
}

function renderOrders(orders) {
  const list = Array.isArray(orders) ? orders : [];
  $("orderCards").innerHTML = list.length
    ? list.slice(0, 5).map((order) => `
      <div class="order-card">
        <span class="side ${String(order.side).toUpperCase() === "BUY" ? "buy" : "sell"}">${escapeHtml(order.side || "—")}</span>
        <div><strong>${escapeHtml(order.market || "Unknown market")}</strong><small>${escapeHtml(order.outcome || "—")} · ${escapeHtml(dateTime(order.created_at))}</small></div>
        <div class="order-result"><b class="${statusClass(order.status)}">${escapeHtml(order.status || "—")}</b><small>${money(order.filled_usd)}</small></div>
      </div>`).join("")
    : '<div class="empty">No paper orders in this snapshot.</div>';

  $("orderTable").innerHTML = list.length
    ? list.map((order) => `
      <tr>
        <td>${escapeHtml(dateTime(order.created_at))}</td>
        <td>${escapeHtml(order.market || "—")}<br><small>${escapeHtml(order.outcome || "—")}</small></td>
        <td><span class="side compact ${String(order.side).toUpperCase() === "BUY" ? "buy" : "sell"}">${escapeHtml(order.side || "—")}</span></td>
        <td class="mono">${order.source_price == null ? "—" : Number(order.source_price).toFixed(4)}</td>
        <td class="mono">${order.observed_price == null ? "—" : Number(order.observed_price).toFixed(4)}</td>
        <td class="mono">${order.slippage == null ? "—" : Number(order.slippage).toFixed(4)}</td>
        <td>${money(order.filled_usd)}</td>
        <td><span class="state-tag ${statusClass(order.status)}">${escapeHtml(order.status || "—")}${order.reason ? ` · ${escapeHtml(order.reason)}` : ""}</span></td>
      </tr>`).join("")
    : '<tr><td colspan="8" class="empty-cell">No order evidence available.</td></tr>';
}

function renderPositions(positions) {
  const list = Array.isArray(positions) ? positions : [];
  $("positionTable").innerHTML = list.length
    ? list.map((position) => `
      <tr>
        <td>${escapeHtml(position.market || "—")}</td>
        <td>${escapeHtml(position.outcome || "—")}</td>
        <td class="mono">${number(position.shares, 4)}</td>
        <td class="mono">${Number(position.average_price || 0).toFixed(4)}</td>
        <td class="mono">${Number(position.current_price || 0).toFixed(4)}</td>
        <td class="${Number(position.realized_pnl || 0) >= 0 ? "good" : "bad"}">${money(position.realized_pnl)}</td>
      </tr>`).join("")
    : '<tr><td colspan="6" class="empty-cell">No open positions.</td></tr>';
}

function renderLatency(latency) {
  const data = latency || {};
  const counts = data.source_counts || {};
  const values = [Number(counts.POLYMARKET || 0), Number(counts.BINANCE || 0), Number(counts.COINBASE || 0)];
  const max = Math.max(...values, 1);
  setText("feedEventValue", number(data.events));
  setText("execEdgeValue", number(data.executable_divergences));
  setText("polyCount", number(counts.POLYMARKET));
  setText("binanceCount", number(counts.BINANCE));
  setText("coinbaseCount", number(counts.COINBASE));
  $("polyBar").style.width = `${values[0] / max * 100}%`;
  $("binanceBar").style.width = `${values[1] / max * 100}%`;
  $("coinbaseBar").style.width = `${values[2] / max * 100}%`;
  setText("latencyEvents", number(data.events));
  setText("latencyDivergences", number(data.divergences));
  setText("latencyExecutable", number(data.executable_divergences));
  setText("latencyLag", data.average_lag_ms == null ? "—" : `${number(data.average_lag_ms, 1)} ms`);

  const target = data.target || {};
  setText("marketQuestion", target.question || "No active latency target in this snapshot");
  setText("tickValue", target.tick_size == null ? "—" : Number(target.tick_size).toFixed(3));
  setText("feeValue", target.fee_rate == null ? "—" : `${Number(target.fee_rate).toFixed(3)}`);

  $("feedMatrix").innerHTML = [
    ["Polymarket", counts.POLYMARKET],
    ["Binance", counts.BINANCE],
    ["Coinbase", counts.COINBASE],
  ].map(([name, count]) => `
    <div><span>${name}</span><b>${number(count)}</b><small>${Number(count || 0) > 0 ? "OBSERVED" : "NO DATA"}</small></div>`).join("");

  const opportunities = Array.isArray(data.opportunities) ? data.opportunities : [];
  $("opportunityList").innerHTML = opportunities.length
    ? opportunities.slice(0, 8).map((item) => `
      <div class="opportunity">
        <div><strong>${escapeHtml(item.outcome || item.side || "Opportunity")}</strong><small>${escapeHtml(item.status || "OBSERVED")}</small></div>
        <b>${item.executable_edge == null ? "—" : Number(item.executable_edge).toFixed(5)}</b>
      </div>`).join("")
    : '<div class="empty">No executable divergences. This is valid negative evidence.</div>';
}

function renderReferences(research) {
  const references = research?.reference_research?.traders || {};
  const entries = Object.entries(references);
  $("referenceGrid").innerHTML = entries.length
    ? entries.map(([name, payload]) => {
      const overall = payload?.overall || {};
      const leaderboard = payload?.leaderboard || {};
      return `
        <article class="panel reference-card">
          <div class="panel-head"><div><span class="kicker">PUBLIC RECONSTRUCTION</span><h2>${escapeHtml(name)}</h2></div><span class="state-tag ${statusClass(payload?.status)}">${escapeHtml(payload?.status || "UNKNOWN")}</span></div>
          <div class="reference-metrics">
            <div><span>Sample</span><b>${number(payload?.sample_size)}</b></div>
            <div><span>Win rate</span><b>${overall.win_rate == null ? "—" : pct(overall.win_rate)}</b></div>
            <div><span>Payoff</span><b>${overall.payoff_ratio == null ? "—" : `${Number(overall.payoff_ratio).toFixed(2)}R`}</b></div>
            <div><span>Expectancy</span><b class="${Number(overall.expectancy_r || 0) > 0 ? "good" : "muted"}">${overall.expectancy_r == null ? "—" : `${Number(overall.expectancy_r).toFixed(3)}R`}</b></div>
            <div><span>Realized PnL</span><b>${overall.realized_pnl == null ? "—" : money(overall.realized_pnl)}</b></div>
            <div><span>Leaderboard rank</span><b>${leaderboard.rank || "—"}</b></div>
          </div>
        </article>`;
    }).join("")
    : '<article class="panel"><div class="empty">No reference reconstructions available.</div></article>';

  const hypotheses = Array.isArray(research?.preregistered_hypotheses)
    ? research.preregistered_hypotheses
    : [];
  $("hypothesisList").innerHTML = hypotheses.length
    ? hypotheses.map((id, index) => `<div><span>${String(index + 1).padStart(2, "0")}</span><code>${escapeHtml(id)}</code><b>FROZEN</b></div>`).join("")
    : '<div class="empty">No preregistered hypotheses.</div>';
}

function renderSnapshot(snapshot) {
  const trial = snapshot.trial || {};
  const portfolio = trial.portfolio || {};
  const totals = trial.totals || {};
  const system = trial.system || {};
  const accounting = trial.accounting_watchdog || {};
  const research = snapshot.research || {};
  const latency = snapshot.latency || research.latency || {};
  const source = snapshot.source || {};
  const manifest = snapshot.manifest || {};

  const initial = Number(portfolio.initial_bankroll || 0);
  const equity = Number(portfolio.equity || 0);
  const equityReturn = initial ? (equity - initial) / initial : 0;
  const fillRate = Number(totals.signals || 0) ? Number(totals.filled_orders || 0) / Number(totals.signals) : 0;

  setText("equityValue", money(equity));
  setText("equityReturn", `${equityReturn >= 0 ? "+" : ""}${pct(equityReturn)} from initial`);
  setText("realizedValue", money(portfolio.realized_pnl));
  setText("unrealizedValue", money(portfolio.unrealized_pnl));
  setText("drawdownValue", pct(portfolio.drawdown));
  setText("fillSignalValue", `${number(totals.filled_orders)} / ${number(totals.signals)}`);
  setText("fillRateValue", `${pct(fillRate)} fill rate`);
  setText("settledValue", number(totals.settled_positions));
  setText("openPositionValue", `${number(totals.open_positions)} open positions`);

  setText("modeValue", trial.safety?.trading_mode || "PAPER");
  setText("shaValue", String(source.github_sha || "—").slice(0, 8));
  setText("evidenceValue", source.evidence_generation || "—");
  setText("runValue", source.github_run_id || "—");
  setText("ageValue", relativeAge(snapshot.snapshot_at));
  setText("geoValue", String(system.geoblock || "UNKNOWN").toUpperCase());
  setStatus("watchdogValue", research.watchdog_state || "YELLOW");
  setStatus("accountingValue", accounting.state || "UNKNOWN");
  setStatus("healthAccounting", accounting.state || "UNKNOWN");
  setStatus("healthLatency", latency.watchdog?.state || (latency.feed_errors?.length ? "RED" : "GREEN"));
  setStatus("healthResearch", research.watchdog_state || "YELLOW");
  setText("footerVersion", `${manifest.scoring_version || "—"} · ${manifest.risk_version || "—"}`);

  renderWallets(trial.selected_wallets);
  renderOrders(trial.recent_orders);
  renderPositions(trial.open_positions);
  renderLatency(latency);
  renderReferences(research);

  const pill = $("snapshotStatus");
  pill.className = "status-pill good";
  pill.querySelector("span").textContent = "SNAPSHOT ONLINE";
}

function renderFailure(message) {
  const pill = $("snapshotStatus");
  pill.className = "status-pill bad";
  pill.querySelector("span").textContent = "NO SNAPSHOT";
  setText("marketQuestion", message);
  setText("ageValue", "—");
}

async function loadSnapshot() {
  try {
    const response = await fetch(`/data/snapshot.json?t=${Date.now()}`, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`snapshot HTTP ${response.status}`);
    const snapshot = await response.json();
    if (snapshot?.trial?.run?.status !== "PASS") throw new Error("latest snapshot is not PASS evidence");
    renderSnapshot(snapshot);
  } catch (error) {
    console.error(error);
    renderFailure("Waiting for the first successful Cloudflare PAPER snapshot.");
  }
}

function navigate(target) {
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === target));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === target));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => navigate(button.dataset.view));
});
document.querySelectorAll("[data-jump]").forEach((button) => {
  button.addEventListener("click", () => navigate(button.dataset.jump));
});

function updateClock() {
  setText("utcClock", `${new Date().toISOString().slice(11, 19)} UTC`);
}

updateClock();
setInterval(updateClock, 1000);
loadSnapshot();
setInterval(loadSnapshot, 60000);
