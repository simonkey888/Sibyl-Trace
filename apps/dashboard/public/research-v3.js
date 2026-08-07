(() => {
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
  const number = (value, digits = 0) => Number(value || 0).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  const value = (input, digits = 4) => input == null ? "—" : Number(input).toFixed(digits);
  const stateClass = (input) => {
    const state = String(input || "").toUpperCase();
    if (["PASS", "GREEN", "CAPTURED", "ANALYZED", "REPLAYED", "QUIET"].includes(state)) return "good";
    if (["RED", "FAILED", "ERROR", "HALTED"].includes(state)) return "bad";
    return "warn";
  };

  function ensurePanel() {
    if (document.getElementById("researchV3Panel")) return;
    const latency = document.getElementById("latency");
    if (!latency) return;
    const wrapper = document.createElement("div");
    wrapper.id = "researchV3Panel";
    wrapper.innerHTML = `
      <div class="section-head">
        <div><span class="kicker">RESEARCH LAB V3</span><h2>Queue-aware microstructure</h2></div>
        <p>Additional PAPER research. It never rewrites V2 fills and has no order-placement surface.</p>
      </div>
      <div class="metric-grid latency-metrics">
        <article class="metric"><span>V3 EVENTS</span><strong id="v3Events">0</strong><small>current capture</small></article>
        <article class="metric"><span>JOURNAL</span><strong id="v3Journal">0</strong><small>durable evidence rows</small></article>
        <article class="metric"><span>QUEUE PROBES</span><strong id="v3Queue">0</strong><small>no-lookahead replay</small></article>
        <article class="metric"><span>FUTURES FEED</span><strong id="v3Futures">0</strong><small>optional public observations</small></article>
      </div>
      <div class="split">
        <article class="panel">
          <div class="panel-head"><div><span class="kicker">MICROSTRUCTURE</span><h2>Depth / microprice / toxicity</h2></div></div>
          <div id="v3Micro" class="card-stack"><div class="empty">Awaiting Research V3 evidence.</div></div>
        </article>
        <article class="panel">
          <div class="panel-head"><div><span class="kicker">MARKET-MAKING PAPER</span><h2>Deterministic quote lab</h2></div></div>
          <div id="v3Maker" class="card-stack"><div class="empty">Awaiting Research V3 evidence.</div></div>
        </article>
      </div>
      <article class="panel">
        <div class="panel-head"><div><span class="kicker">REPLAY</span><h2>Queue-aware execution audit</h2></div><span id="v3ReplayState" class="state-tag warn">NO DATA</span></div>
        <div id="v3Replay" class="matrix"></div>
      </article>`;
    latency.appendChild(wrapper);
  }

  function render(snapshot) {
    ensurePanel();
    const v3 = snapshot?.research_v3 || {};
    const micro = v3.microstructure_v3 || {};
    const maker = v3.market_making_v3 || {};
    const replay = v3.replay_v3 || {};
    const features = v3.cross_market_features_v3 || {};
    const sources = features.sources || {};

    const set = (id, text) => {
      const node = document.getElementById(id);
      if (node) node.textContent = text;
    };
    set("v3Events", number(v3.events));
    set("v3Journal", number(v3.journal_rows));
    set("v3Queue", number(replay.queue_probes));
    set("v3Futures", number(sources.BINANCE_FUTURES?.events));

    const microNode = document.getElementById("v3Micro");
    const microAssets = Array.isArray(micro.assets) ? micro.assets : [];
    if (microNode) {
      microNode.innerHTML = microAssets.length ? microAssets.map((asset) => {
        const metrics = asset.metrics || {};
        const take = asset.l1_take_buy || {};
        return `
          <div class="source-card">
            <div class="source-main"><strong>${escapeHtml(String(asset.asset_id || "asset").slice(0, 12))}</strong><small>queue model: ${escapeHtml(micro.queue_model || "—")}</small></div>
            <div class="score-cluster">
              <span><i>µ</i><b>${value(metrics.microprice)}</b></span>
              <span><i>I</i><b>${value(metrics.imbalance, 3)}</b></span>
              <span><i>T</i><b>${value(asset.toxicity, 5)}</b></span>
              <span><i>D</i><b>${value(take.filled, 2)}</b></span>
            </div>
          </div>`;
      }).join("") : '<div class="empty">No two-sided depth captured in this V3 window.</div>';
    }

    const makerNode = document.getElementById("v3Maker");
    const makerAssets = Array.isArray(maker.assets) ? maker.assets : [];
    if (makerNode) {
      makerNode.innerHTML = makerAssets.length ? makerAssets.map((asset) => `
        <div class="order-card">
          <span class="state-tag ${stateClass(asset.regime)}">${escapeHtml(asset.regime || "UNKNOWN")}</span>
          <div><strong>${escapeHtml(String(asset.asset_id || "asset").slice(0, 12))}</strong><small>FV ${value(asset.fair_value)} · reservation ${value(asset.reservation_price)}</small></div>
          <div class="order-result"><b>${value(asset.bid_price)} / ${value(asset.ask_price)}</b><small>${value(asset.bid_size, 2)} / ${value(asset.ask_size, 2)} shares</small></div>
        </div>`).join("") : '<div class="empty">No maker quote decision available. No synthetic quote is fabricated.</div>';
    }

    const replayState = document.getElementById("v3ReplayState");
    if (replayState) {
      replayState.textContent = replay.status || "NO DATA";
      replayState.className = `state-tag ${stateClass(replay.status)}`;
    }
    const replayNode = document.getElementById("v3Replay");
    if (replayNode) {
      const items = [
        ["Events replayed", replay.event_count],
        ["Book events", replay.book_events],
        ["Trade events", replay.trade_events],
        ["Queue filled", replay.queue_filled],
        ["Queue partial", replay.queue_partial],
        ["Invariant violations", (replay.invariant_violations || []).length],
      ];
      replayNode.innerHTML = items.map(([name, amount]) => `<div><span>${escapeHtml(name)}</span><b>${number(amount)}</b><small>V3</small></div>`).join("");
    }
  }

  async function refresh() {
    try {
      const response = await fetch(`/data/snapshot.json?t=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) return;
      render(await response.json());
    } catch (error) {
      console.error("Research V3 panel refresh failed", error);
    }
  }

  ensurePanel();
  refresh();
  setInterval(refresh, 60000);
})();
