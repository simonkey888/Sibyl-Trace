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
        assert payload["portfolio"]["initial_bankroll"] == 300

        readiness = client.get("/api/v1/live/readiness")
        assert readiness.status_code == 200
        assert readiness.json()["ready"] is False
