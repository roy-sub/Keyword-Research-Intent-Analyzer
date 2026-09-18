"""MODE=prod behaviour: API key enforcement, disabled docs, fail-fast config."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.auth import reset_session_store
from app.cache import TTLCache
from app.config import ConfigError, Settings
from app.rate_limit import RateLimiter
from tests.conftest import FakeAnalyzer, FakeProvider

PROD_ORIGIN = "https://keyword-analyzer.onrender.com"


def prod_settings(**overrides) -> Settings:
    base = dict(
        MODE="prod",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD="test-password",
        GEMINI_API_KEY="key",
        API_KEY="super-secret-api-key",
        ALLOWED_ORIGINS=PROD_ORIGIN,
        SUGGEST_DELAY_SECONDS=0.0,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def prod_client(monkeypatch, default_results):
    settings = prod_settings()
    # require_access_key reads settings through get_settings(); point it here.
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)

    app = main.create_app(settings)
    with TestClient(app) as test_client:
        main.state.provider = FakeProvider(default_results)
        main.state.analyzer = FakeAnalyzer()
        main.state.limiter = RateLimiter(10, 60)
        main.state.cache = TTLCache(1440, 50)
        reset_session_store()
        yield test_client


def login(test_client, **headers) -> str:
    response = test_client.post(
        "/api/login",
        json={"username": "admin", "password": "test-password"},
        headers={"Origin": PROD_ORIGIN, **headers},
    )
    assert response.status_code == 200
    return response.json()["access_key"]


def test_cross_origin_request_without_api_key_is_rejected(prod_client):
    token = login(prod_client)
    response = prod_client.get(
        "/api/status",
        headers={"X-Access-Key": token, "Origin": "https://somewhere-else.example"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key."}


def test_cross_origin_request_with_api_key_is_allowed(prod_client):
    token = login(prod_client)
    response = prod_client.get(
        "/api/status",
        headers={
            "X-Access-Key": token,
            "Origin": "https://somewhere-else.example",
            "X-API-Key": "super-secret-api-key",
        },
    )
    assert response.status_code == 200


def test_wrong_api_key_is_rejected(prod_client):
    token = login(prod_client)
    response = prod_client.get(
        "/api/status",
        headers={
            "X-Access-Key": token,
            "Origin": "https://somewhere-else.example",
            "X-API-Key": "guessed",
        },
    )
    assert response.status_code == 401


def test_allowed_origin_needs_no_api_key(prod_client):
    token = login(prod_client)
    response = prod_client.get(
        "/api/status", headers={"X-Access-Key": token, "Origin": PROD_ORIGIN}
    )
    assert response.status_code == 200


def test_same_origin_browser_get_needs_no_api_key(prod_client):
    """A same-origin GET sends no Origin but always sends Sec-Fetch-Site."""
    token = login(prod_client)
    response = prod_client.get(
        "/api/status",
        headers={"X-Access-Key": token, "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 200


def test_scripted_caller_without_origin_or_api_key_is_rejected(prod_client):
    token = login(prod_client)
    response = prod_client.get("/api/status", headers={"X-Access-Key": token})
    assert response.status_code == 401


def test_login_itself_is_gated_by_the_api_key(prod_client):
    response = prod_client.post(
        "/api/login",
        json={"username": "admin", "password": "test-password"},
        headers={"Origin": "https://somewhere-else.example"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key."}


def test_docs_are_disabled_in_prod(prod_client):
    # /docs and /redoc fall through to the static mount, which has no such file
    assert prod_client.get("/openapi.json").status_code == 404
    assert prod_client.get("/docs").status_code == 404


def test_health_is_open_in_prod(prod_client):
    assert prod_client.get("/api/health").json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Fail-fast configuration
# ---------------------------------------------------------------------------


def test_missing_admin_password_fails_validation():
    settings = Settings(ADMIN_PASSWORD="", GEMINI_API_KEY="key")
    assert settings.missing_required() == ["ADMIN_PASSWORD"]
    with pytest.raises(ConfigError) as excinfo:
        settings.validate_or_raise()
    assert "ADMIN_PASSWORD" in str(excinfo.value)


def test_prod_requires_api_key_and_allowed_origins():
    settings = Settings(
        MODE="prod", ADMIN_PASSWORD="pw", GEMINI_API_KEY="key", API_KEY="", ALLOWED_ORIGINS=""
    )
    missing = settings.missing_required()
    assert any("API_KEY" in m for m in missing)
    assert any("ALLOWED_ORIGINS" in m for m in missing)


def test_dev_mode_allows_localhost_origins():
    settings = Settings(ADMIN_PASSWORD="pw", GEMINI_API_KEY="key")
    assert settings.cors_origin_regex is not None
    assert not settings.is_prod


def test_complete_config_validates():
    prod_settings().validate_or_raise()
