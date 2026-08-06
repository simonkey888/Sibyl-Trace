import json

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
