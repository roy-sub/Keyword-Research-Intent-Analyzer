"""Shared fixtures.

Every test runs against a fully faked upstream: no test touches Google or
Gemini, and no test needs real credentials.
"""

from __future__ import annotations

import json
import os

import pytest

# Set before any app module is imported, so get_settings() sees them.
os.environ.setdefault("MODE", "dev")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("SUGGEST_DELAY_SECONDS", "0.01")
os.environ.setdefault("RATE_LIMIT_MAX_SEARCHES", "10")

import app.main as main  # noqa: E402
from app.analysis import AnalysisResult  # noqa: E402
from app.cache import TTLCache  # noqa: E402
from app.config import get_settings, reset_settings_cache  # noqa: E402
from app.markets import Market  # noqa: E402
from app.rate_limit import RateLimiter  # noqa: E402
from app.suggest.base import SourceResult  # noqa: E402

TEST_MARKDOWN = "## 1. Search Intent Classification\nInformational dominates."


class FakeProvider:
    """A SuggestProvider that returns scripted results and counts calls."""

    def __init__(self, results: list[SourceResult]) -> None:
        self.results = results
        self.calls = 0
        self.last_market: Market | None = None

    async def fetch(self, topic: str, market: Market) -> list[SourceResult]:
        self.calls += 1
        self.last_market = market
        return list(self.results)


class FakeAnalyzer:
    def __init__(self, markdown: str = TEST_MARKDOWN,
                 provider: str = "anthropic", model: str = "claude-opus-5") -> None:
        self.markdown = markdown
        self.provider = provider
        self.model = model
        self.calls = 0
        self.last_keywords: list[str] = []

    async def analyse(
        self, topic: str, keywords: list[str], market: Market | None = None
    ) -> AnalysisResult:
        self.calls += 1
        self.last_keywords = keywords
        self.last_market = market
        return AnalysisResult(markdown=self.markdown, provider=self.provider, model=self.model)


def suggest_body(suggestions: list[str], query: str = "q") -> bytes:
    return json.dumps([query, suggestions]).encode("utf-8")


@pytest.fixture(autouse=True)
def clean_settings():
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def default_results() -> list[SourceResult]:
    return [
        SourceResult("villa rentals", "seed", ["Villa Rentals Italy", "villa rentals greece"]),
        SourceResult("villa rentals a", "alphabet", ["villa rentals amalfi", "villa rentals italy"]),
        SourceResult("how villa rentals", "question", ["how villa rentals work"]),
    ]


@pytest.fixture
def client(default_results, monkeypatch):
    """A TestClient with fake upstreams wired into app state.

    Yields (client, provider, analyzer) so tests can assert on call counts.
    """
    from fastapi.testclient import TestClient

    from app.auth import reset_session_store

    settings = get_settings()
    provider = FakeProvider(default_results)
    analyzer = FakeAnalyzer()

    app = main.create_app(settings)

    with TestClient(app) as test_client:
        # The lifespan built real objects; swap in the fakes.
        main.state.provider = provider
        main.state.analyzer = analyzer
        main.state.limiter = RateLimiter(
            settings.RATE_LIMIT_MAX_SEARCHES, settings.RATE_LIMIT_WINDOW_MINUTES
        )
        main.state.cache = TTLCache(settings.CACHE_TTL_MINUTES, settings.CACHE_MAX_ENTRIES)
        reset_session_store()
        yield test_client, provider, analyzer


@pytest.fixture
def token(client):
    test_client, _provider, _analyzer = client
    response = test_client.post(
        "/api/login", json={"username": "admin", "password": "test-password"}
    )
    assert response.status_code == 200
    return response.json()["access_key"]


@pytest.fixture
def auth(token):
    return {"X-Access-Key": token}
