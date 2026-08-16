from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    app_version: str = "dev"
    database_url: str = "sqlite:///./sibyl.db"
    initial_bankroll_usd: float = Field(default=300.0, gt=0)
    scan_interval_seconds: int = Field(default=3600, ge=60)
    watch_interval_seconds: int = Field(default=5, ge=2)
    activity_lookback_seconds: int = Field(default=120, ge=120, le=86400)
    activity_fetch_limit: int = Field(default=500, ge=100, le=5000)
    candidate_limit: int = Field(default=20, ge=3, le=50)
    tracked_wallet_limit: int = Field(default=3, ge=1, le=10)
    source_strategy_activity_limit: int = Field(default=10_000, ge=30, le=10_000)
    source_strategy_min_trade_count: int = Field(default=30, ge=5, le=1000)
    source_strategy_min_paired_conditions: int = Field(default=2, ge=1, le=50)
    source_strategy_max_paired_trade_fraction: float = Field(
        default=0.25,
        gt=0.0,
        le=1.0,
    )
    mark_interval_seconds: int = Field(default=30, ge=10, le=3600)
    risk_max_signal_age_seconds: int = Field(default=30, ge=1, le=21600)
    admin_token: str = "development-admin-token"
    gateway_shared_secret: str = "development-gateway-secret"
    cors_origins: str = ""
    trading_mode: Literal["READ_ONLY", "PAPER"] = "READ_ONLY"
    paper_trading_enabled: bool = False
    live_trading_enabled: bool = False
    cost_authorized_usd: float = Field(default=0.0, ge=0.0, le=0.0)

    evidence_generation: str = "LEGACY_V1"
    research_enabled: bool = False
    latency_lab_enabled: bool = False
    latency_capture_seconds: float = Field(default=15.0, ge=2.0, le=60.0)
    latency_requested_shares: float = Field(default=5.0, gt=0.0, le=100.0)
    reference_research_enabled: bool = False
    reference_usernames: str = "djdjdjekekek,okkokok"
    sports_fair_price_enabled: bool = False
    weather_research_enabled: bool = False

    ai_analysis_enabled: bool = False
    ai_analysis_interval_seconds: int = Field(default=21600, ge=900)
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"
    openai_api_base: str = "https://api.openai.com/v1"

    data_api_base: str = "https://data-api.polymarket.com"
    gamma_api_base: str = "https://gamma-api.polymarket.com"
    clob_api_base: str = "https://clob.polymarket.com"
    geoblock_url: str = "https://polymarket.com/api/geoblock"
    http_timeout_seconds: float = Field(default=12.0, ge=2, le=60)

    @field_validator("live_trading_enabled")
    @classmethod
    def reject_live_mode(cls, value: bool) -> bool:
        if value:
            raise ValueError("LIVE trading is not available in Sibyl Trace")
        return value

    @model_validator(mode="after")
    def reject_unsafe_configuration(self) -> "Settings":
        if self.cost_authorized_usd != 0:
            raise ValueError(
                "Sibyl Trace PAPER research is restricted to COST_AUTHORIZED_USD=0"
            )
        if self.trading_mode == "PAPER" and not self.paper_trading_enabled:
            raise ValueError("PAPER mode requires PAPER_TRADING_ENABLED=true")
        if self.research_enabled and self.trading_mode != "PAPER":
            raise ValueError("research capture requires explicit PAPER mode")

        if self.app_env.lower() != "production":
            return self

        secrets = {
            "admin_token": self.admin_token,
            "gateway_shared_secret": self.gateway_shared_secret,
        }
        weak_prefixes = ("development-", "replace-with-", "change-me")
        for name, value in secrets.items():
            normalized = value.strip().lower()
            if (
                len(value) < 32
                or not normalized
                or any(normalized.startswith(prefix) for prefix in weak_prefixes)
            ):
                raise ValueError(
                    f"{name} must be a non-placeholder secret of at least 32 characters"
                )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def reference_username_list(self) -> list[str]:
        return [
            value.strip()
            for value in self.reference_usernames.split(",")
            if value.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
