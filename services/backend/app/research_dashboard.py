from __future__ import annotations

from html import escape
from typing import Any


def _value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def render_research_dashboard(report: dict[str, Any]) -> str:
    research = report.get("research", {}) if isinstance(report, dict) else {}
    accounting = report.get("accounting_watchdog", {}) if isinstance(report, dict) else {}
    latency = research.get("latency", {}) if isinstance(research, dict) else {}
    totals = research.get("totals", {}) if isinstance(research, dict) else {}
    references = research.get("reference_research", {}) if isinstance(research, dict) else {}
    traders = references.get("traders", {}) if isinstance(references, dict) else {}
    generation = escape(str(report.get("evidence_generation") or "UNKNOWN"))
    watchdog = escape(str(research.get("watchdog_state") or "YELLOW"))
    accounting_state = escape(str(accounting.get("state") or "UNKNOWN"))

    trader_rows = []
    for username, payload in sorted(traders.items()):
        overall = payload.get("overall", {}) if isinstance(payload, dict) else {}
        trader_rows.append(
            "<tr>"
            f"<td>{escape(str(username))}</td>"
            f"<td>{escape(str(payload.get('status', 'UNKNOWN')))}</td>"
            f"<td>{escape(_value(payload.get('sample_size')))}</td>"
            f"<td>{escape(_value(overall.get('win_rate')))}</td>"
            f"<td>{escape(_value(overall.get('payoff_ratio')))}</td>"
            f"<td>{escape(_value(overall.get('expectancy_r')))}</td>"
            "</tr>"
        )
    if not trader_rows:
        trader_rows.append('<tr><td colspan="6">No reference reconstruction yet.</td></tr>')

    hypotheses = research.get("preregistered_hypotheses", [])
    hypothesis_items = "".join(
        f"<li><code>{escape(str(item))}</code></li>" for item in hypotheses
    ) or "<li>No preregistered hypothesis IDs in this artifact.</li>"
    executable = escape(_value(latency.get("executable_divergences", 0)))
    executable_edge = escape(_value(latency.get("average_executable_edge_per_share")))
    trader_table = "".join(trader_rows)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sibyl Trace — PAPER V2 Research</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;background:#0b1117;color:#e8eef5}}
main{{max-width:1180px;margin:auto;padding:32px}}
h1{{margin:0 0 6px}}p{{color:#a9b6c4}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.grid{{margin:24px 0}}
.card,section{{background:#111b24;border:1px solid #253443;border-radius:12px;padding:16px}}
.card b{{display:block;font-size:1.5rem;margin-top:8px}}
table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:9px;border-bottom:1px solid #253443}}
code{{color:#8bd5ff}}small{{color:#8ea0b2}}.warning{{border-left:4px solid #d6a84b}}
</style>
</head>
<body><main>
<small>GITHUB-ONLY · ZERO-DOLLAR · PAPER RESEARCH</small>
<h1>Sibyl Trace — Research Dashboard</h1>
<p>Evidence is descriptive until preregistered sample gates mature. LIVE execution is absent.</p>
<div class="grid">
<div class="card">Evidence generation<b>{generation}</b></div>
<div class="card">Global watchdog<b>{watchdog}</b></div>
<div class="card">Accounting<b>{accounting_state}</b></div>
<div class="card">Experiments<b>{escape(_value(totals.get('experiments', 0)))}</b></div>
<div class="card">Observations<b>{escape(_value(totals.get('observations', 0)))}</b></div>
<div class="card">Hypotheses<b>{escape(_value(totals.get('hypotheses', 0)))}</b></div>
</div>
<section><h2>BTC Latency Lab</h2>
<table><tbody>
<tr><th>Status</th><td>{escape(_value(latency.get('status')))}</td></tr>
<tr><th>Feed events</th><td>{escape(_value(latency.get('events', 0)))}</td></tr>
<tr><th>Divergences</th><td>{escape(_value(latency.get('divergences', 0)))}</td></tr>
<tr><th>Executable divergences</th><td>{executable}</td></tr>
<tr><th>Average lag ms</th><td>{escape(_value(latency.get('average_lag_ms')))}</td></tr>
<tr><th>Average executable edge/share</th><td>{executable_edge}</td></tr>
</tbody></table></section>
<section><h2>Reference trader reconstructions</h2><table>
<thead><tr><th>Trader</th><th>Status</th><th>n</th><th>Win rate</th>
<th>Payoff ratio</th><th>Expectancy R</th></tr></thead>
<tbody>{trader_table}</tbody></table></section>
<section><h2>Preregistered hypotheses</h2><ul>{hypothesis_items}</ul></section>
<section class="warning"><strong>Safety invariant</strong>
<p>No private key, signing, deposit, withdrawal, live order, paid API or billing action is
available from this artifact.</p></section>
</main></body></html>"""
