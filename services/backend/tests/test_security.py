import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import verify_admin, verify_gateway
from app.config import Settings


def test_production_gateway_fails_closed() -> None:
    settings = Settings(app_env="production", gateway_shared_secret="expected")
    with pytest.raises(HTTPException) as error:
        verify_gateway(settings, None)
    assert error.value.status_code == 401
    verify_gateway(settings, "expected")


def test_admin_control_requires_exact_token() -> None:
    settings = Settings(admin_token="owner-secret")
    with pytest.raises(HTTPException) as error:
        verify_admin(settings, "wrong")
    assert error.value.status_code == 403
    verify_admin(settings, "owner-secret")


def test_live_configuration_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(live_trading_enabled=True)
