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
  const stateClass = (input) => {
    const state = String(input || "").toUpperCase();
    if (["PASS", "GREEN", "CAPTURED", "RECONSTRUCTED", "EXACT_EQUIVALENT"].includes(state)) return "good";
    if (["RED", "FAILED", "ERROR", "INVALID"].includes(state)) return "bad";
    return "warn";
  };

  function ensurePanel() {
    if (document.getElementById("researchV4Panel")) return;
    const latency = document.getElementById("latency");
    if (!latency) return;
    const wrapper = document.createElement("div");
    wrapper.id = "researchV4Panel";
    wrapper.innerHTML = `
      <div class="section-head">
        <div><span class="kicker">RESEARCH LAB V4 / OPERATIONAL</span><h2>Real L2 evidence, still inside PAPER</h2></div>
        <p>Public market data only. V4 records order-book evidence and cross-venue candidates without placing orders or rewriting V2/V3 state.</p>
      </div>
      <article class="panel research-v4-hero">
        <div class="panel-head">
          <div><span class="kicker">LATEST V4 CAPTURE</span><h2 id="v4Target">Awaiting operational evidence</h2></div>
          <span id="v4State" class="state-tag warn">NO DATA</span>
        </div>
        <div class="v4-grid">
          <div class="v4-stat"><span>RAW WEBSOCKET</span><b id="v4Raw">0</b><small>source records</small></div>
          <div class="v4-stat"><span>NORMALIZED TAPE</span><b id="v4Events">0</b><small>deterministic L2 events</small></div>
          <div class="v4-stat"><span>BOOK DELTAS</span><b id="v4Deltas">0</b><small id="v4Snapshots">0 snapshots</small></div>
          <div class="v4-stat"><span>TRADES</span><b id="v4Trades">0</b><small>observed in capture window</small></div>
          <div class="v4-stat"><span>KALSHI SCAN</span><b id="v4Kalshi">0</b><small>public markets scanned</small></div>
          <div class="v4-stat"><span>EXACT MATCHES</span><b id="v4Exact">0</b><small>parity execution disabled</small></div>
          <div class="v4-stat"><span>FIDELITY</span><b id="v4Fidelity">—</b><small>aggregate order book</small></div>
          <div class="v4-stat"><span>EDGE STATUS</span><b id="v4Edge">—</b><small>research claim state</small></div>
        </div>
        <div class="v4-foot">
          <span id="v4Continuity">Continuity: —</span>
          <span id="v4Safety">PAPER_SHADOW_ONLY · order placement disabled</span>
        </div>
      </article>`;

    const v3 = document.getElementById("researchV3Panel");
    latency.insertBefore(wrapper, v3 || null);
  }

  function set(id, text) {
    const node = document.getElementById(id);
    if (node) node.textContent = text;
  }

  function render(snapshot) {
    ensurePanel();
    const v4 = snapshot?.research_v4 || {};
    const tape = v4.l2_tape_v4 || {};
    const cross = v4.cross_venue_v4 || {};
    const safety = v4.safety || {};
    const target = v4.target || {};

    set("v4Target", target.question || "No active V4 target in this snapshot");
    set("v4Raw", number(tape.raw_records));
    set("v4Events", number(tape.normalized_events));
    set("v4Deltas", number(tape.deltas));
    set("v4Snapshots", `${number(tape.snapshots)} snapshots`);
    set("v4Trades", number(tape.trades));
    set("v4Kalshi", number(cross.markets_scanned));
    set("v4Exact", number(cross.exact_equivalents));
    set("v4Fidelity", tape.fidelity || "—");
    set("v4Edge", v4.edge_status || "UNPROVEN");
    set("v4Continuity", `Continuity: ${tape.continuity || "—"}`);
    set("v4Safety", `${safety.mode || "PAPER_SHADOW_ONLY"} · order placement ${safety.order_placement === false ? "disabled" : "unknown"}`);

    const state = document.getElementById("v4State");
    if (state) {
      const label = v4.research_state || v4.status || "NO DATA";
      state.textContent = label;
      state.className = `state-tag ${stateClass(label)}`;
    }
  }

  async function refresh() {
    try {
      const response = await fetch(`/data/snapshot.json?t=${Date.now()}`, {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      render(await response.json());
    } catch (error) {
      console.error("Research V4 panel refresh failed", error);
    }
  }

  ensurePanel();
  refresh();
  setInterval(refresh, 60000);
})();
