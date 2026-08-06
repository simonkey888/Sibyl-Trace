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

async function refreshAiPanel() {
  ensureAiPanel();
  try {
    const response = await fetch("/api/v1/dashboard", { headers: { Accept: "application/json" } });
    if (!response.ok) return;
    const payload = await response.json();
    renderAiAnalysis(payload.ai_analysis);
  } catch (error) {
    console.debug("AI advisory panel unavailable", error);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", refreshAiPanel, { once: true });
} else {
  refreshAiPanel();
}
setInterval(refreshAiPanel, AI_REFRESH_MS);
