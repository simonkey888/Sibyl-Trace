import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import verify_admin, verify_gateway
from app.config import Settings


STRONG_ADMIN = "a" * 32
STRONG_GATEWAY = "g" * 32


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "trading_mode": "READ_ONLY",
        "admin_token": STRONG_ADMIN,
        "gateway_shared_secret": STRONG_GATEWAY,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_gateway_fails_closed() -> None:
    settings = production_settings()
    with pytest.raises(HTTPException) as error:
        verify_gateway(settings, None)
    assert error.value.status_code == 401
    verify_gateway(settings, STRONG_GATEWAY)


def test_admin_control_requires_exact_token() -> None:
    settings = Settings(admin_token="owner-secret")
    with pytest.raises(HTTPException) as error:
        verify_admin(settings, "wrong")
    assert error.value.status_code == 403
    verify_admin(settings, "owner-secret")


def test_live_configuration_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(live_trading_enabled=True)


def test_default_mode_is_read_only() -> None:
    assert Settings().trading_mode == "READ_ONLY"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("admin_token", "development-admin-token"),
        ("admin_token", "too-short"),
        ("gateway_shared_secret", "development-gateway-secret"),
        ("gateway_shared_secret", "too-short"),
    ],
)
def test_production_rejects_default_or_short_secrets(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        production_settings(**{field: value})


def test_production_rejects_direct_paper_start() -> None:
    with pytest.raises(ValidationError):
        production_settings(trading_mode="PAPER")
