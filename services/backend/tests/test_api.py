import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("APP_ENV", "development")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_dashboard_and_live_gate() -> None:
    with TestClient(app) as client:
        dashboard = client.get("/api/v1/dashboard")
        assert dashboard.status_code == 200
        payload = dashboard.json()
        assert payload["system"]["mode"] == "READ_ONLY"
        assert "GLOBAL=60% SHORT" in payload["system"]["score_contract"]
        semantics = payload["system"]["score_semantics"]
        assert semantics["kind"] == "HEURISTIC_QUALITY_RANKING"
        assert semantics["global_formula"] == "0.60*SHORT+0.40*LONG"
        assert semantics["history_basis"] == "DECIDED_OUTCOMES"
        assert semantics["calibrated_probability"] is False
        assert semantics["expected_return_claim"] is False
        assert semantics["alpha_claim"] is False
        assert semantics["win_rate_denominator"] == "wins_plus_losses"
        assert semantics["break_even_scoring_weight"] == 0
        assert semantics["break_even_reported_in_closed_count"] is True
        assert payload["portfolio"]["initial_bankroll"] == 300

        readiness = client.get("/api/v1/live/readiness")
        assert readiness.status_code == 200
        assert readiness.json()["ready"] is False
