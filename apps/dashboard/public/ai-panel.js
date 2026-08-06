const AI_REFRESH_MS = 30000;

function ensureAiPanel() {
  if (document.getElementById("aiRiskPanel")) return;
  const style = document.createElement("style");
  style.textContent = `
    .ai-risk-panel{margin:0 0 12px;border-color:rgba(150,117,255,.24);background:linear-gradient(145deg,rgba(25,20,43,.96),rgba(7,17,24,.96))}
    .ai-risk-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:15px}
    .ai-risk-meta{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
    .ai-risk-badge{border:1px solid rgba(150,117,255,.28);background:rgba(150,117,255,.1);color:#bba7ff;border-radius:999px;padding:5px 8px;font:700 7px ui-monospace,monospace;letter-spacing:.08em}
    .ai-risk-grid{display:grid;grid-template-columns:minmax(0,1.5fr) repeat(3,minmax(0,1fr));gap:12px}
    .ai-risk-summary,.ai-risk-column{border:1px solid var(--line);background:rgba(0,0,0,.12);border-radius:10px;padding:13px;min-height:105px}
    .ai-risk-summary p,.ai-risk-column li{color:#aebfc4;font-size:10px;line-height:1.55}
    .ai-risk-summary p{margin:8px 0 0}.ai-risk-column h3{margin:0 0 8px;color:#748b94;font:700 8px ui-monospace,monospace;letter-spacing:.11em;text-transform:uppercase}
    .ai-risk-column ul{margin:0;padding-left:16px}.ai-risk-empty{color:#52666e;font:9px ui-monospace,monospace;text-transform:uppercase;letter-spacing:.1em}
    @media(max-width:1100px){.ai-risk-grid{grid-template-columns:1fr 1fr}}@media(max-width:560px){.ai-risk-grid{grid-template-columns:1fr}.ai-risk-head{flex-direction:column}.ai-risk-meta{justify-content:flex-start}}
  `;
  document.head.appendChild(style);

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
