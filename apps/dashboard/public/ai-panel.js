const AI_REFRESH_MS = 30000;

function ensureAiStylesheet() {
  if (document.querySelector('link[data-sibyl-ai-styles]')) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "/ai-panel.css";
  link.dataset.sibylAiStyles = "true";
  document.head.appendChild(link);
}

function ensureAiPanel() {
  ensureAiStylesheet();
  if (document.getElementById("aiRiskPanel")) return;
  const panel = document.createElement("article");
  panel.id = "aiRiskPanel";
  panel.className = "panel ai-risk-panel";
  panel.innerHTML = `
    <div class="ai-risk-head">
      <div><span class="panel-kicker">GPT-5.6 ADVISORY</span><h2>Read-only risk brief</h2></div>
      <div class="ai-risk-meta"><span class="ai-risk-badge" id="aiModel">DISABLED</span><span class="ai-risk-badge" id="aiRegime">NO REPORT</span><span class="ai-risk-badge" id="aiConfidence">—</span></div>
    </div>
    <div class="ai-risk-grid">
      <div class="ai-risk-summary"><span class="panel-kicker">SUMMARY</span><p id="aiSummary" class="ai-risk-empty">Advisory analysis is disabled or has not produced a report.</p><p class="muted" id="aiCreated"></p></div>
      <div class="ai-risk-column"><h3>Source risks</h3><ul id="aiSourceRisks"></ul></div>
      <div class="ai-risk-column"><h3>Anomalies</h3><ul id="aiAnomalies"></ul></div>
      <div class="ai-risk-column"><h3>Recommendations</h3><ul id="aiRecommendations"></ul></div>
    </div>`;
  const mainGrid = document.querySelector("#overview .main-grid");
  mainGrid?.insertAdjacentElement("afterend", panel);
}

function ensureScorePanel() {
  ensureAiStylesheet();
  if (document.getElementById("scoreMatrixPanel")) return;
  const panel = document.createElement("article");
  panel.id = "scoreMatrixPanel";
  panel.className = "panel score-matrix-panel";
  panel.innerHTML = `
    <div class="panel-head score-matrix-head">
      <div><span class="panel-kicker">MULTI-HORIZON SOURCE QUALITY</span><h2>SHORT / LONG / GLOBAL / EDGE</h2></div>
      <p>GLOBAL drives PAPER risk. EDGE measures execution copyability after observed price movement; it is not outcome alpha.</p>
    </div>
    <div class="score-contract" id="scoreContract"></div>
    <div class="score-matrix-grid" id="scoreMatrixGrid"></div>`;
  const table = document.querySelector("#wallets .table-panel");
  table?.insertAdjacentElement("beforebegin", panel);
}

function setList(id, values) {
  const list = document.getElementById(id);
  if (!list) return;
  list.replaceChildren();
  const items = Array.isArray(values) && values.length ? values : ["No findings reported."];
  for (const value of items) {
    const item = document.createElement("li");
    item.textContent = String(value);
    list.appendChild(item);
  }
}

function renderAiAnalysis(analysis) {
  ensureAiPanel();
  if (!analysis?.report) return;
  const report = analysis.report;
  document.getElementById("aiModel").textContent = String(analysis.model || "GPT-5.6").toUpperCase();
  document.getElementById("aiRegime").textContent = String(report.regime || "UNKNOWN").replaceAll("_", " ").toUpperCase();
  document.getElementById("aiConfidence").textContent = `${Math.round(Number(report.confidence || 0) * 100)}% CONFIDENCE`;
  const summary = document.getElementById("aiSummary");
  summary.textContent = String(report.summary || "No summary returned.");
  summary.className = "";
  document.getElementById("aiCreated").textContent = analysis.created_at ? `Generated ${new Date(analysis.created_at).toLocaleString()} · advisory only` : "Advisory only";
  setList("aiSourceRisks", report.source_risks);
  setList("aiAnomalies", report.anomalies);
  setList("aiRecommendations", report.recommendations);
}

function scoreValue(value) {
  return value == null ? "—" : Number(value).toFixed(1);
}

function metric(label, value, detail = "") {
  const node = document.createElement("div");
  node.className = "score-metric";
  const name = document.createElement("span");
  name.textContent = label;
  const score = document.createElement("b");
  score.textContent = scoreValue(value);
  const small = document.createElement("small");
  small.textContent = detail;
  node.append(name, score, small);
  return node;
}

function renderScoreMatrix(wallets, contract) {
  ensureScorePanel();
  const contractNode = document.getElementById("scoreContract");
  contractNode.textContent = String(
    contract || "GLOBAL=60% SHORT + 40% LONG; EDGE=execution copyability",
  );
  const grid = document.getElementById("scoreMatrixGrid");
  grid.replaceChildren();
  const selected = (Array.isArray(wallets) ? wallets : [])
    .filter((wallet) => wallet.selected)
    .sort((left, right) => Number(right.global_score || 0) - Number(left.global_score || 0));
  if (!selected.length) {
    const empty = document.createElement("p");
    empty.className = "ai-risk-empty";
    empty.textContent = "No tracked wallet has a score matrix yet.";
    grid.appendChild(empty);
    return;
  }
  for (const wallet of selected) {
    const card = document.createElement("section");
    card.className = "score-matrix-card";
    const title = document.createElement("div");
    title.className = "score-source";
    const strong = document.createElement("strong");
    strong.textContent = String(wallet.username || wallet.address || "Anonymous");
    const small = document.createElement("small");
    small.textContent = `${Number(wallet.closed_count || 0)} closed · EDGE n=${Number(wallet.execution_edge_sample_size || 0)}`;
    title.append(strong, small);
    const metrics = document.createElement("div");
    metrics.className = "score-metrics";
    metrics.append(
      metric("SHORT", wallet.short_score, `n=${Number(wallet.short_sample_size || 0)}`),
      metric("LONG", wallet.long_score, `n=${Number(wallet.long_sample_size || 0)}`),
      metric("GLOBAL", wallet.global_score, "risk input"),
      metric(
        "EDGE",
        wallet.execution_edge_score,
        wallet.average_execution_edge == null
          ? "neutral"
          : `avg ${(Number(wallet.average_execution_edge) * 100).toFixed(2)}¢`,
      ),
    );
    card.append(title, metrics);
    grid.appendChild(card);
  }
}

async function refreshAiPanel() {
  ensureAiPanel();
  ensureScorePanel();
  try {
    const response = await fetch("/api/v1/dashboard", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return;
    const payload = await response.json();
    renderAiAnalysis(payload.ai_analysis);
    renderScoreMatrix(payload.wallets, payload.system?.score_contract);
  } catch (error) {
    console.debug("Advisory panels unavailable", error);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", refreshAiPanel, { once: true });
} else {
  refreshAiPanel();
}
setInterval(refreshAiPanel, AI_REFRESH_MS);
