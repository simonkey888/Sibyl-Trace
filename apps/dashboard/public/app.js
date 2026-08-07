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
  if (["GREEN", "PASS", "CAPTURED", "COMPLETE", "FILLED", "PAPER", "WIN", "FALSE"].includes(normalized)) return "good";
  if (["RED", "FAIL", "FAILED", "ERROR", "REJECTED", "LOSS"].includes(normalized)) return "bad";
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
          <small>${escapeHtml(wallet.wallet || "—")} · source n=${number(wallet.closed_count)}</small>
        </div>
        <div class="score-cluster">
          <span><i>SCORE</i><b>${score(wallet.score ?? wallet.global_score)}</b></span>
          <span><i>WR</i><b>${wallet.win_rate == null ? "—" : pct(wallet.win_rate, 1)}</b></span>
        </div>
      </div>`).join("")
    : '<div class="empty">No tracked wallets in this snapshot.</div>';

  $("walletTable").innerHTML = list.length
    ? list.map((wallet) => `
      <tr>
        <td><strong>${escapeHtml(wallet.username || "Anonymous")}</strong><br><small class="mono">${escapeHtml(wallet.wallet || "—")}</small></td>
        <td class="mono emphasis">${score(wallet.score ?? wallet.global_score)}</td>
        <td>${number(wallet.closed_count)}</td>
        <td>${wallet.win_rate == null ? "—" : pct(wallet.win_rate)}</td>
        <td>${wallet.profit_factor == null ? "—" : Number(wallet.profit_factor).toFixed(2)}</td>
        <td class="${Number(wallet.realized_pnl || 0) >= 0 ? "good" : "bad"}">${money(wallet.realized_pnl)}</td>
      </tr>`).join("")
    : '<tr><td colspan="6" class="empty-cell">No wallet scores available.</td></tr>';
}

function resultText(order) {
  const result = String(order.result || "").toUpperCase();
  if (result && result !== "UNRESOLVED") return result;
  if (String(order.resolution_status || "").toUpperCase() === "OPEN" && Number(order.filled_shares || 0) > 0) return "OPEN";
  return "—";
}

function renderOrders(orders) {
  const list = Array.isArray(orders) ? orders : [];
  $("orderCards").innerHTML = list.length
    ? list.slice(0, 5).map((order) => `
      <div class="order-card">
        <span class="side ${String(order.side).toUpperCase() === "BUY" ? "buy" : "sell"}">${escapeHtml(order.side || "—")}</span>
        <div><strong>${escapeHtml(order.market || "Unknown market")}</strong><small>${escapeHtml(order.outcome || "—")} · ${escapeHtml(dateTime(order.created_at))}</small></div>
        <div class="order-result"><b class="${statusClass(resultText(order) !== "—" ? resultText(order) : order.status)}">${escapeHtml(resultText(order) !== "—" ? resultText(order) : order.status || "—")}</b><small>${money(order.filled_usd)}</small></div>
      </div>`).join("")
    : '<div class="empty">No V5 decisions in this snapshot.</div>';

  $("orderTable").innerHTML = list.length
    ? list.map((order) => {
      const fillPrice = order.effective_price ?? order.average_fill_price;
      const result = resultText(order);
      return `
      <tr>
        <td>${escapeHtml(dateTime(order.created_at))}</td>
        <td>${escapeHtml(order.market || "—")}<br><small>${escapeHtml(order.outcome || "—")}</small></td>
        <td><span class="side compact ${String(order.side).toUpperCase() === "BUY" ? "buy" : "sell"}">${escapeHtml(order.side || "—")}</span></td>
        <td class="mono">${order.source_price == null ? "—" : Number(order.source_price).toFixed(4)}</td>
        <td class="mono">${fillPrice == null ? "—" : Number(fillPrice).toFixed(4)}</td>
        <td>${order.fee_usd == null ? "—" : money(order.fee_usd)}</td>
        <td>${money(order.filled_usd)}${order.fill_fraction != null ? `<br><small>${pct(order.fill_fraction, 1)} FAK</small>` : ""}</td>
        <td><span class="state-tag ${statusClass(order.status)}">${escapeHtml(order.status || "—")}${order.reason ? ` · ${escapeHtml(order.reason)}` : ""}</span></td>
        <td><span class="state-tag ${statusClass(result)}">${escapeHtml(result)}</span></td>
      </tr>`;
    }).join("")
    : '<tr><td colspan="9" class="empty-cell">No V5 execution evidence available.</td></tr>';
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
        <td>${money(position.mark_value_usd)}</td>
        <td class="${Number(position.realized_pnl || 0) >= 0 ? "good" : "bad"}">${money(position.realized_pnl)}</td>
      </tr>`).join("")
    : '<tr><td colspan="7" class="empty-cell">No open V5 positions.</td></tr>';
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

function renderTruth(v5) {
  if (!v5) {
    setText("truthTitle", "LEGACY V2 only — not canonical performance");
    setText("truthState", "LEGACY");
    $("truthState").className = "badge warn";
    setText("truthCopy", "The available historical cohort used midpoint fills. It remains visible for provenance but is not promoted as execution-realistic performance.");
    setText("midpointFillValue", "LEGACY");
    $("midpointFillValue").className = "warn";
    setText("executionModelValue", "LEGACY V2");
    return;
  }
  const method = v5.methodology || {};
  setText("truthTitle", "PAPER V5 is canonical");
  setText("truthState", "PASS V5");
  $("truthState").className = "badge good";
  setText("truthCopy", "Midpoint fills are disabled. Orders consume arrival-book L2 as FAK, include per-market taker fees, allow partial/no-fill, and resolve WIN/LOSS only after terminal market evidence.");
  setText("midpointFillValue", method.midpoint_fills === false ? "FALSE" : "INVALID");
  $("midpointFillValue").className = method.midpoint_fills === false ? "good" : "bad";
  setText("executionModelValue", method.execution_model || "V5");
}

function renderSnapshot(snapshot) {
  const trial = snapshot.trial || {};
  const v5 = snapshot.paper_v5?.status === "PASS" ? snapshot.paper_v5 : null;
  const canonical = v5 || trial;
  const portfolio = canonical.portfolio || {};
  const totals = canonical.totals || {};
  const system = trial.system || {};
  const accounting = canonical.accounting_watchdog || trial.accounting_watchdog || {};
  const research = snapshot.research || {};
  const latency = snapshot.latency || research.latency || {};
  const source = snapshot.source || {};
  const manifest = snapshot.manifest || {};

  const initial = Number(portfolio.initial_bankroll || 0);
  const equity = Number(portfolio.equity || 0);
  const equityReturn = initial ? (equity - initial) / initial : 0;
  const denominator = Number(v5 ? totals.predictions : totals.signals) || 0;
  const filled = Number(totals.filled_orders || 0);
  const fillRate = denominator ? filled / denominator : 0;
  const wins = Number(v5?.totals?.wins || 0);
  const losses = Number(v5?.totals?.losses || 0);
  const accuracy = v5?.totals?.accuracy;

  setText("equityValue", money(equity));
  setText("equityReturn", `${equityReturn >= 0 ? "+" : ""}${pct(equityReturn)} from initial`);
  setText("realizedValue", money(portfolio.realized_pnl));
  setText("unrealizedValue", money(portfolio.unrealized_pnl));
  setText("drawdownValue", pct(portfolio.drawdown));
  setText("fillSignalValue", `${number(filled)} / ${number(denominator)}`);
  setText("fillRateValue", `${pct(fillRate)} executable fill rate`);
  setText("settledValue", v5 ? `${number(wins)} / ${number(losses)}` : "— / —");
  setText("openPositionValue", v5
    ? `${accuracy == null ? "Accuracy UNPROVEN" : `${pct(accuracy)} accuracy`} · ${number(totals.open_positions)} open`
    : "V2 midpoint cohort — noncanonical");

  setText("modeValue", canonical.safety?.trading_mode || "PAPER");
  setText("shaValue", String(source.github_sha || "—").slice(0, 8));
  setText("evidenceValue", source.evidence_generation || "—");
  setText("runValue", source.github_run_id || "—");
  setText("ageValue", relativeAge(snapshot.snapshot_at));
  setText("geoValue", String(system.geoblock || "UNKNOWN").toUpperCase());
  setStatus("accountingValue", accounting.state || "UNKNOWN");
  setStatus("healthAccounting", accounting.state || "UNKNOWN");
  setStatus("healthLatency", latency.watchdog?.state || (latency.feed_errors?.length ? "RED" : "GREEN"));
  setStatus("healthResearch", snapshot.research_v4?.edge_status || research.watchdog_state || "YELLOW");
  setText("footerVersion", v5 ? `${v5.methodology?.execution_model || "V5"} · ${manifest.risk_version || "RISK"}` : `${manifest.scoring_version || "—"} · LEGACY V2`);

  renderTruth(v5);
  renderWallets(v5?.selected_wallets || trial.selected_wallets);
  renderOrders(v5?.recent_orders || trial.recent_orders);
  renderPositions(v5?.open_positions || trial.open_positions);
  renderLatency(latency);
  renderReferences(research);

  const pill = $("snapshotStatus");
  pill.className = v5 ? "status-pill good" : "status-pill warn";
  pill.querySelector("span").textContent = v5 ? "V5 TRUTH ONLINE" : "LEGACY ONLY";
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
    if (snapshot?.trial?.run?.status !== "PASS") throw new Error("legacy evidence anchor is not PASS");
    if (snapshot.paper_v5 && snapshot.paper_v5.status !== "PASS") throw new Error("published V5 is not PASS evidence");
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
