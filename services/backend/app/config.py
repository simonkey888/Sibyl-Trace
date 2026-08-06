from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    app_version: str = "dev"
    database_url: str = "sqlite:///./sibyl.db"
    initial_bankroll_usd: float = Field(default=300.0, gt=0)
    scan_interval_seconds: int = Field(default=3600, ge=60)
    watch_interval_seconds: int = Field(default=5, ge=2)
    candidate_limit: int = Field(default=20, ge=3, le=50)
    tracked_wallet_limit: int = Field(default=3, ge=1, le=10)
    mark_interval_seconds: int = Field(default=30, ge=10, le=3600)
    admin_token: str = "development-admin-token"
    gateway_shared_secret: str = "development-gateway-secret"
    cors_origins: str = ""
    trading_mode: Literal["READ_ONLY", "PAPER"] = "PAPER"
    live_trading_enabled: bool = False

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
            raise ValueError("LIVE trading is not available in Sibyl Trace V1")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
