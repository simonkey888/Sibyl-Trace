import json

import pytest

from app.ai import AIReport, OpenAIAnalyst
from app.config import Settings


def test_ai_layer_is_disabled_without_explicit_enablement_and_key() -> None:
    analyst = OpenAIAnalyst(Settings(ai_analysis_enabled=False, openai_api_key=""))
    try:
        assert analyst.enabled is False
    finally:
        analyst.close()


def test_extracts_structured_output_text() -> None:
    report = {
        "summary": "No material anomaly.",
        "regime": "stable",
        "confidence": 0.72,
        "source_risks": [],
        "anomalies": [],
        "recommendations": ["Retain paper-only controls."],
    }
    payload = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(report)}],
            }
        ]
    }
    text = OpenAIAnalyst._extract_output_text(payload)
    parsed = AIReport.model_validate_json(text)
    assert parsed.regime == "stable"
    assert parsed.confidence == 0.72


class FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "id": "resp_test",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "summary": "Insufficient evidence.",
                                    "regime": "insufficient_data",
                                    "confidence": 0.2,
                                    "source_risks": [],
                                    "anomalies": [],
                                    "recommendations": ["Collect more paper evidence."],
                                }
                            ),
                        }
                    ],
                }
            ],
        }


class FakeClient:
    def __init__(self) -> None:
        self.body: dict | None = None

    def post(self, _url: str, *, headers: dict, json: dict) -> FakeResponse:
        assert headers["Authorization"] == "Bearer test-key"
        self.body = json
        return FakeResponse()

    def close(self) -> None:
        return None


def test_ai_request_is_structurally_blocked_at_zero_cost() -> None:
    analyst = OpenAIAnalyst(
        Settings(ai_analysis_enabled=True, openai_api_key="test-key")
    )
    analyst.client.close()
    fake = FakeClient()
    analyst.client = fake
    try:
        assert analyst.enabled is False
        with pytest.raises(
            RuntimeError,
            match="billable_ai_blocked_by_zero_cost_authorization",
        ):
            analyst._request("{}")
    finally:
        analyst.close()
    assert fake.body is None
