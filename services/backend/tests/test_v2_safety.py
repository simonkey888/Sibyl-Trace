import pytest
from pydantic import ValidationError

from app.config import Settings


def test_cost_authorization_cannot_be_raised_above_zero() -> None:
    with pytest.raises(ValidationError):
        Settings(cost_authorized_usd=0.01)


def test_github_trial_cannot_enable_paid_ai_analysis() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="github-trial",
            trading_mode="PAPER",
            paper_trading_enabled=True,
            ai_analysis_enabled=True,
        )


def test_research_requires_explicit_paper_mode() -> None:
    with pytest.raises(ValidationError):
        Settings(research_enabled=True)


def test_activity_limit_tracks_current_public_api_offset_budget() -> None:
    with pytest.raises(ValidationError):
        Settings(activity_fetch_limit=5001)
    assert Settings(activity_fetch_limit=5000).activity_fetch_limit == 5000


def test_reference_research_usernames_are_parsed_without_hidden_defaults() -> None:
    settings = Settings(reference_usernames="djdjdjekekek, okkokok, ")
    assert settings.reference_username_list == ["djdjdjekekek", "okkokok"]
