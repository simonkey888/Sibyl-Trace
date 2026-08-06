import hashlib
import json
import time
from typing import Literal

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AIAnalysis, PaperOrder, Signal, Wallet, WalletScoreProfile
from app.repository import audit, current_portfolio
from app.settlement_models import PaperSettlement


class AIReport(BaseModel):
    summary: str = Field(min_length=1, max_length=1000)
    regime: Literal["stable", "mixed", "elevated_risk", "insufficient_data"]
    confidence: float = Field(ge=0, le=1)
    source_risks: list[str] = Field(max_length=5)
    anomalies: list[str] = Field(max_length=5)
    recommendations: list[str] = Field(max_length=5)


REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
        "regime": {
            "type": "string",
            "enum": ["stable", "mixed", "elevated_risk", "insufficient_data"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "source_risks": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "anomalies": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "recommendations": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
    },
    "required": [
        "summary",
        "regime",
        "confidence",
        "source_risks",
        "anomalies",
        "recommendations",
    ],
}


class OpenAIAnalyst:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(timeout=max(settings.http_timeout_seconds, 30.0))

    @property
    def enabled(self) -> bool:
        return self.settings.ai_analysis_enabled and bool(self.settings.openai_api_key)

    def close(self) -> None:
        self.client.close()

    def run(self, db: Session) -> AIAnalysis | None:
        if not self.enabled:
            return None
        evidence = self._evidence(db)
        encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        input_hash = hashlib.sha256(encoded.encode()).hexdigest()
        latest_hash = db.scalar(
            select(AIAnalysis.input_hash).order_by(desc(AIAnalysis.id)).limit(1)
        )
        if latest_hash == input_hash:
            return None

        response = self._request(encoded)
        report = AIReport.model_validate_json(self._extract_output_text(response))
        usage = response.get("usage") or {}
        row = AIAnalysis(
            model=self.settings.openai_model,
            input_hash=input_hash,
            response_id=response.get("id"),
            report_json=report.model_dump_json(),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )
        db.add(row)
        audit(
            db,
            "ai_analysis_completed",
            f"GPT advisory report created with {self.settings.openai_model}",
            model=self.settings.openai_model,
            input_hash=input_hash,
            regime=report.regime,
            confidence=report.confidence,
        )
        db.commit()
        return row

    def _request(self, evidence_json: str) -> dict:
        body = {
            "model": self.settings.openai_model,
            "store": False,
            "reasoning": {"effort": "low"},
            "max_output_tokens": 900,
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "sibyl_wallet_risk_brief",
                    "strict": True,
                    "schema": REPORT_SCHEMA,
                },
            },
            "instructions": (
                "You are the read-only risk analyst for a prediction-market paper system. "
                "Assess source quality, score-horizon divergence, anomalies, concentration, "
                "latency, settlement maturity, and operational risk. Execution-edge score "
                "measures copyability after price movement and is not outcome alpha. Never "
                "authorize, size, or place a trade. Never claim profitability. Recommendations "
                "may only pause, investigate, collect more evidence, or retain paper-only "
                "controls. Use only the supplied evidence."
            ),
            "input": evidence_json,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.settings.openai_api_base.rstrip('/')}/responses"
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = self.client.post(url, headers=headers, json=body)
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("OpenAI response was not an object")
                return payload
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(2)
        raise RuntimeError("OpenAI advisory request failed") from last_error

    @staticmethod
    def _extract_output_text(response: dict) -> str:
        for item in response.get("output") or []:
            if item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if content.get("type") == "output_text" and content.get("text"):
                    return str(content["text"])
        raise ValueError("OpenAI response contained no output_text")

    def _evidence(self, db: Session) -> dict:
        wallets = list(
            db.scalars(
                select(Wallet)
                .where(Wallet.selected.is_(True))
                .order_by(desc(Wallet.score))
                .limit(10)
            )
        )
        profiles = {
            profile.wallet_address: profile
            for profile in db.scalars(
                select(WalletScoreProfile).where(
                    WalletScoreProfile.wallet_address.in_(
                        [wallet.address for wallet in wallets]
                    )
                )
            ).all()
        }
        signals = list(db.scalars(select(Signal).order_by(desc(Signal.id)).limit(40)))
        orders = list(db.scalars(select(PaperOrder).order_by(desc(PaperOrder.id)).limit(40)))
        portfolio = current_portfolio(db, self.settings.initial_bankroll_usd)
        source_ids = {
            wallet.address: hashlib.sha256(wallet.address.encode()).hexdigest()[:12]
            for wallet in wallets
        }
        return {
            "system": {
                "mode": self.settings.trading_mode,
                "live_available": False,
                "purpose": "risk advisory only",
                "score_contract": {
                    "short": "most recent 50 closed positions",
                    "long": "up to 200 closed positions",
                    "global": "60% short + 40% long",
                    "edge": "execution copyability, not outcome alpha",
                },
                "settled_positions": int(
                    db.scalar(select(func.count()).select_from(PaperSettlement)) or 0
                ),
            },
            "portfolio": portfolio,
            "wallets": [
                {
                    "source_id": source_ids[wallet.address],
                    "short_score": (
                        profiles[wallet.address].short_score
                        if wallet.address in profiles
                        else None
                    ),
                    "long_score": (
                        profiles[wallet.address].long_score
                        if wallet.address in profiles
                        else None
                    ),
                    "global_score": wallet.score,
                    "execution_edge_score": (
                        profiles[wallet.address].execution_edge_score
                        if wallet.address in profiles
                        else None
                    ),
                    "execution_edge_sample_size": (
                        profiles[wallet.address].execution_edge_sample_size
                        if wallet.address in profiles
                        else 0
                    ),
                    "average_execution_edge": (
                        profiles[wallet.address].average_execution_edge
                        if wallet.address in profiles
                        else None
                    ),
                    "win_rate": wallet.win_rate,
                    "profit_factor": wallet.profit_factor,
                    "realized_pnl": wallet.realized_pnl,
                    "closed_count": wallet.closed_count,
                    "top3_concentration": wallet.concentration,
                }
                for wallet in wallets
            ],
            "recent_signals": [
                {
                    "source_id": source_ids.get(
                        signal.wallet_address,
                        hashlib.sha256(signal.wallet_address.encode()).hexdigest()[:12],
                    ),
                    "market": signal.market_title,
                    "outcome": signal.outcome,
                    "side": signal.side,
                    "source_price": signal.source_price,
                    "source_usdc": signal.source_usdc,
                    "decision": signal.decision,
                    "reason": signal.decision_reason,
                }
                for signal in signals
            ],
            "recent_orders": [
                {
                    "market": order.market_title,
                    "side": order.side,
                    "status": order.status,
                    "filled_usd": order.filled_usd,
                    "slippage": order.slippage,
                    "reason": order.rejection_reason,
                }
                for order in orders
            ],
        }
