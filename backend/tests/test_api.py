"""API behaviour: auth, quota, cache, error mapping, health."""

from __future__ import annotations

import pytest

import app.main as main
from app.rate_limit import RateLimiter
from app.suggest.base import SourceResult


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_needs_no_auth_and_no_upstream(client):
    test_client, provider, analyzer = client
    response = test_client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert provider.calls == 0
    assert analyzer.calls == 0


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_succeeds_and_issues_a_working_token(client):
    test_client, _p, _a = client
    response = test_client.post(
        "/api/login", json={"username": "admin", "password": "test-password"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert len(body["access_key"]) >= 32
    # the token actually works
    assert test_client.get(
        "/api/status", headers={"X-Access-Key": body["access_key"]}
    ).status_code == 200


def test_login_fails_on_wrong_password(client):
    test_client, _p, _a = client
    response = test_client.post(
        "/api/login", json={"username": "admin", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password."}


def test_login_fails_on_wrong_username(client):
    test_client, _p, _a = client
    response = test_client.post(
        "/api/login", json={"username": "nobody", "password": "test-password"}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password."}


def test_logout_invalidates_the_token(client, auth):
    test_client, _p, _a = client
    assert test_client.post("/api/logout", headers=auth).status_code == 200
    assert test_client.get("/api/status", headers=auth).status_code == 401


# ---------------------------------------------------------------------------
# Access key enforcement
# ---------------------------------------------------------------------------


def test_analyze_rejects_missing_token(client):
    test_client, provider, _a = client
    response = test_client.post("/api/analyze", json={"topic": "villa rentals"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing access key."}
    assert provider.calls == 0


def test_analyze_rejects_wrong_token(client):
    test_client, provider, _a = client
    response = test_client.post(
        "/api/analyze",
        json={"topic": "villa rentals"},
        headers={"X-Access-Key": "not-a-real-token"},
    )
    assert response.status_code == 401
    assert provider.calls == 0


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_reports_quota_and_pacing(client, auth):
    test_client, _p, _a = client
    body = test_client.get("/api/status", headers=auth).json()
    assert body["searches_remaining"] == 10
    assert body["window_minutes"] == 60
    assert body["retry_after_seconds"] == 0
    assert body["expected_queries"] == 37
    assert body["request_delay_seconds"] > 0


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------


def test_analyze_returns_the_full_contract(client, auth):
    test_client, provider, analyzer = client
    response = test_client.post(
        "/api/analyze", json={"topic": "villa rentals"}, headers=auth
    )
    assert response.status_code == 200
    body = response.json()

    assert body["topic"] == "villa rentals"
    assert body["cached"] is False
    assert body["queries_attempted"] == 3
    assert body["queries_succeeded"] == 3
    assert body["failed_queries"] == []
    assert body["total_keywords"] == 4
    assert body["searches_remaining"] == 9
    assert body["generated_at"].endswith("Z")
    assert body["analysis_markdown"].startswith("## 1. Search Intent Classification")

    # sources keep the producing query intact
    assert body["sources"][0] == {
        "query": "villa rentals",
        "type": "seed",
        "keywords": ["Villa Rentals Italy", "villa rentals greece"],
    }
    # keywords are the sorted, deduplicated union carrying every source
    assert [k["keyword"] for k in body["keywords"]] == [
        "how villa rentals work",
        "villa rentals amalfi",
        "villa rentals greece",
        "Villa Rentals Italy",
    ]
    italy = next(k for k in body["keywords"] if k["keyword"] == "Villa Rentals Italy")
    assert italy["sources"] == ["villa rentals", "villa rentals a"]
    assert italy["types"] == ["seed", "alphabet"]
    assert analyzer.last_keywords == [k["keyword"] for k in body["keywords"]]


def test_analyze_rejects_empty_topic(client, auth):
    test_client, provider, _a = client
    response = test_client.post("/api/analyze", json={"topic": "   "}, headers=auth)
    assert response.status_code == 400
    assert response.json() == {"detail": "Topic must not be empty."}
    assert provider.calls == 0


def test_analyze_rejects_overlong_topic(client, auth):
    test_client, provider, _a = client
    response = test_client.post("/api/analyze", json={"topic": "x" * 101}, headers=auth)
    assert response.status_code == 400
    assert provider.calls == 0


def test_partial_failure_returns_results_with_accurate_counts(client, auth):
    test_client, provider, _a = client
    provider.results = [
        SourceResult("villa rentals", "seed", ["villa rentals italy"]),
        SourceResult("villa rentals q", "alphabet", [], error="HTTP 429"),
    ]
    body = test_client.post(
        "/api/analyze", json={"topic": "villa rentals"}, headers=auth
    ).json()

    assert body["queries_attempted"] == 2
    assert body["queries_succeeded"] == 1
    assert body["failed_queries"] == ["villa rentals q"]
    assert body["total_keywords"] == 1


def test_all_queries_failing_returns_502_and_refunds_quota(client, auth):
    test_client, provider, analyzer = client
    provider.results = [
        SourceResult("villa rentals", "seed", [], error="HTTP 429"),
        SourceResult("villa rentals a", "alphabet", [], error="timeout"),
    ]
    response = test_client.post(
        "/api/analyze", json={"topic": "villa rentals"}, headers=auth
    )
    assert response.status_code == 502
    assert response.json() == {"detail": "Google returned no suggestions."}
    assert analyzer.calls == 0
    # the run never reached Google usefully, so it cost no quota
    assert test_client.get("/api/status", headers=auth).json()["searches_remaining"] == 10


def test_ai_failure_returns_502_with_a_clean_message(client, auth):
    test_client, _p, analyzer = client

    from app.analysis import AnalysisError

    async def failing(topic, keywords):
        raise AnalysisError("the model timed out")

    analyzer.analyse = failing
    response = test_client.post(
        "/api/analyze", json={"topic": "villa rentals"}, headers=auth
    )
    assert response.status_code == 502
    assert response.json() == {"detail": "AI analysis failed: the model timed out"}


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_blocks_the_eleventh_run(client, auth):
    test_client, _p, _a = client
    for index in range(10):
        response = test_client.post(
            "/api/analyze", json={"topic": f"topic {index}"}, headers=auth
        )
        assert response.status_code == 200, index
        assert response.json()["searches_remaining"] == 9 - index

    blocked = test_client.post("/api/analyze", json={"topic": "topic 11"}, headers=auth)
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["detail"] == "Search limit reached."
    assert body["retry_after_seconds"] > 0
    assert blocked.headers["Retry-After"] == str(body["retry_after_seconds"])


def test_status_reports_retry_after_when_exhausted(client, auth):
    test_client, _p, _a = client
    main.state.limiter = RateLimiter(1, 60)
    test_client.post("/api/analyze", json={"topic": "one"}, headers=auth)
    body = test_client.get("/api/status", headers=auth).json()
    assert body["searches_remaining"] == 0
    assert body["retry_after_seconds"] > 0


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_cache_hit_costs_no_quota_and_no_upstream_call(client, auth):
    test_client, provider, analyzer = client

    first = test_client.post(
        "/api/analyze", json={"topic": "villa rentals"}, headers=auth
    ).json()
    assert first["cached"] is False
    assert provider.calls == 1

    # same topic, different whitespace and casing -> same cache key
    second = test_client.post(
        "/api/analyze", json={"topic": "  Villa   Rentals  "}, headers=auth
    ).json()

    assert second["cached"] is True
    assert second["generated_at"] == first["generated_at"]
    assert second["keywords"] == first["keywords"]
    assert provider.calls == 1  # no upstream call
    assert analyzer.calls == 1
    assert second["searches_remaining"] == 9  # no quota consumed
    assert test_client.get("/api/status", headers=auth).json()["searches_remaining"] == 9


# ---------------------------------------------------------------------------
# Service descriptor
# ---------------------------------------------------------------------------


def test_root_returns_a_service_descriptor(client):
    """This service is API-only; the UI is a separate Render service."""
    test_client, provider, analyzer = client
    response = test_client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "keyword-suggest-intent-analyzer-api"
    assert body["status"] == "ok"
    assert provider.calls == 0
    assert analyzer.calls == 0


def test_no_static_files_are_served(client):
    """Nothing from the frontend folder is reachable through the API service."""
    test_client, _p, _a = client
    for path in ("/index.html", "/app.js", "/styles.css", "/config.js"):
        assert test_client.get(path).status_code == 404, path


# ---------------------------------------------------------------------------
# Dev CORS
#
# Regression guard. `python3 -m http.server` announces itself as
# "Serving HTTP on 0.0.0.0", so the frontend is commonly opened at
# http://0.0.0.0:5173. A localhost-only dev allowlist rejected that preflight
# with a 400, and the UI reported it as "Could not reach the server" — which
# reads like wrong credentials rather than a CORS problem.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://0.0.0.0:5173",
        "http://192.168.1.50:5173",
        "http://penguin.linux.test:5173",
    ],
)
def test_dev_accepts_preflight_from_any_local_origin(client, origin):
    test_client, _p, _a = client
    response = test_client.options(
        "/api/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200, f"{origin} preflight rejected"
    assert "access-control-allow-origin" in response.headers


@pytest.mark.parametrize("origin", ["http://0.0.0.0:5173", "http://192.168.1.50:5173"])
def test_dev_login_succeeds_from_any_local_origin(client, origin):
    test_client, _p, _a = client
    response = test_client.post(
        "/api/login",
        json={"username": "admin", "password": "test-password"},
        headers={"Origin": origin},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] in (origin, "*")


async def test_password_with_special_characters_round_trips():
    """A password containing % and other punctuation must survive settings
    parsing and compare_digest unchanged — `%` in particular looks like an
    interpolation sigil and is worth pinning down."""
    from app.auth import authenticate, get_session_store
    from app.config import Settings

    tricky = "pW%25rd$with#odd!chars&more"
    settings = Settings(ADMIN_USERNAME="admin", ADMIN_PASSWORD=tricky, GEMINI_API_KEY="k")
    assert settings.ADMIN_PASSWORD == tricky

    token = await authenticate("admin", tricky, settings)
    assert get_session_store().is_valid(token)
