import json

from app.render_dashboard import render_from_file
from app.research_dashboard import render_research_dashboard


def report() -> dict:
    return {
        "evidence_generation": "SIBYL_PAPER_V2",
        "accounting_watchdog": {"state": "GREEN"},
        "research": {
            "status": "COMPLETE",
            "watchdog_state": "YELLOW",
            "totals": {"experiments": 3, "hypotheses": 5, "observations": 12},
            "latency": {
                "status": "CAPTURED",
                "events": 100,
                "divergences": 4,
                "executable_divergences": 1,
                "average_lag_ms": 250.0,
                "average_executable_edge_per_share": 0.012,
            },
            "reference_research": {
                "traders": {
                    "okkokok": {
                        "status": "RECONSTRUCTED",
                        "sample_size": 120,
                        "overall": {
                            "win_rate": 0.6,
                            "payoff_ratio": 1.2,
                            "expectancy_r": 0.32,
                        },
                    }
                }
            },
            "preregistered_hypotheses": ["hypothesis-1"],
        },
    }


def test_dashboard_contains_research_metrics_and_no_live_controls() -> None:
    html = render_research_dashboard(report())
    assert "SIBYL_PAPER_V2" in html
    assert "Executable divergences" in html
    assert "okkokok" in html
    assert "LIVE execution is absent" in html
    assert "Emergency stop" not in html


def test_dashboard_escapes_reference_payload() -> None:
    payload = report()
    payload["research"]["reference_research"]["traders"] = {
        "<script>alert(1)</script>": {"status": "RECONSTRUCTED", "sample_size": 1}
    }
    html = render_research_dashboard(payload)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_cli_renderer_reads_report_and_writes_html(tmp_path) -> None:
    source = tmp_path / "report.json"
    output = tmp_path / "dashboard.html"
    source.write_text(json.dumps(report()), encoding="utf-8")
    render_from_file(source, output)
    assert output.is_file()
    assert "Research Dashboard" in output.read_text(encoding="utf-8")
