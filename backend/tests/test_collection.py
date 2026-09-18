"""Collection: merging, dedupe, source attribution, failures, pacing.

All HTTP is mocked via httpx.MockTransport — no live network call.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.suggest.base import SourceResult, build_query_set, dedupe_keywords, merge_results
from app.suggest.google import GoogleSuggestProvider
from tests.conftest import suggest_body


def make_settings(**overrides) -> Settings:
    base = dict(
        ADMIN_PASSWORD="pw",
        ANTHROPIC_API_KEY="key",
        SUGGEST_DELAY_SECONDS=0.0,
        SUGGEST_TIMEOUT_SECONDS=1.0,
        SUGGEST_MAX_RETRIES=1,
    )
    base.update(overrides)
    return Settings(**base)


def test_query_set_covers_seed_alphabet_and_modifiers():
    settings = make_settings()
    queries = build_query_set(
        "villa rentals",
        [chr(c) for c in range(ord("a"), ord("z") + 1)],
        settings.question_modifiers,
        settings.commercial_modifiers,
    )
    assert len(queries) == settings.expected_queries == 37
    types = [t for _q, t in queries]
    assert types.count("seed") == 1
    assert types.count("alphabet") == 26
    assert types.count("question") == 6
    assert types.count("commercial") == 4
    assert ("villa rentals", "seed") in queries
    assert ("villa rentals a", "alphabet") in queries
    assert ("how villa rentals", "question") in queries
    assert ("villa rentals near me", "commercial") in queries


def test_dedupe_is_case_insensitive_and_keeps_first_casing():
    assert dedupe_keywords(["Villa Italy", "villa italy", "  ", "  villa  greece "]) == [
        "Villa Italy",
        "villa greece",
    ]


def test_merge_records_sources_and_types_accurately():
    results = [
        SourceResult("villa", "seed", ["Villa Italy", "villa greece"]),
        SourceResult("villa a", "alphabet", ["villa amalfi", "villa italy"]),
        SourceResult("how villa", "question", ["villa italy"]),
    ]
    sources, keywords, failed = merge_results(results)

    assert failed == []
    # sources are preserved per query and never flattened
    assert [s.query for s in sources] == ["villa", "villa a", "how villa"]
    assert sources[0].keywords == ["Villa Italy", "villa greece"]

    by_keyword = {k.keyword: k for k in keywords}
    assert [k.keyword for k in keywords] == ["villa amalfi", "villa greece", "Villa Italy"]
    # first-seen casing wins, and every producing query/type is carried
    assert by_keyword["Villa Italy"].sources == ["villa", "villa a", "how villa"]
    assert by_keyword["Villa Italy"].types == ["seed", "alphabet", "question"]
    assert by_keyword["villa amalfi"].sources == ["villa a"]


def test_merge_reports_failed_queries():
    results = [
        SourceResult("villa", "seed", ["villa italy"]),
        SourceResult("villa q", "alphabet", [], error="HTTP 429"),
    ]
    sources, keywords, failed = merge_results(results)
    assert failed == ["villa q"]
    assert len(sources) == 1
    assert len(keywords) == 1


@pytest.mark.asyncio
async def test_provider_records_failures_without_failing_the_run():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["q"].endswith(" q"):
            return httpx.Response(429)
        return httpx.Response(200, content=suggest_body(["a suggestion"]))

    settings = make_settings(SUGGEST_MAX_RETRIES=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        results = await GoogleSuggestProvider(http, settings).fetch("villa", "en", "us")

    failures = [r for r in results if not r.ok]
    assert len(failures) == 1
    assert failures[0].query == "villa q"
    assert failures[0].error == "HTTP 429"
    assert len(results) == settings.expected_queries
    assert all(r.keywords == ["a suggestion"] for r in results if r.ok)


@pytest.mark.asyncio
async def test_provider_retries_then_gives_up():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, headers={"Retry-After": "0"})

    settings = make_settings(SUGGEST_MAX_RETRIES=2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = GoogleSuggestProvider(http, settings)
        result = await provider._fetch_one("villa", "seed", "en", "us")

    assert not result.ok
    assert result.error == "HTTP 503"
    assert attempts["n"] == 3  # initial + 2 retries


@pytest.mark.asyncio
async def test_provider_decodes_non_utf8_body():
    body = json.dumps(["villa", ["villa españa"]], ensure_ascii=False).encode("latin-1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body, headers={"Content-Type": "application/json"}
        )

    settings = make_settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await GoogleSuggestProvider(http, settings)._fetch_one(
            "villa", "seed", "en", "us"
        )

    assert result.ok
    assert result.keywords == ["villa españa"]


@pytest.mark.asyncio
async def test_requests_are_paced_and_sequential(monkeypatch):
    """A pacing delay sits between every pair of requests, and requests never
    overlap: the event log must read request, sleep, request, sleep, ..."""
    events: list[tuple[str, object]] = []

    async def fake_sleep(seconds: float) -> None:
        events.append(("sleep", seconds))

    async def handler(request: httpx.Request) -> httpx.Response:
        events.append(("request", request.url.params["q"]))
        return httpx.Response(200, content=suggest_body(["x"]))

    monkeypatch.setattr("app.suggest.google.asyncio.sleep", fake_sleep)
    settings = make_settings(SUGGEST_DELAY_SECONDS=1.5)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        results = await GoogleSuggestProvider(http, settings).fetch("villa", "en", "us")

    assert len(results) == settings.expected_queries

    kinds = [kind for kind, _ in events]
    expected = ["request"] + ["sleep", "request"] * (settings.expected_queries - 1)
    assert kinds == expected

    sleeps = [value for kind, value in events if kind == "sleep"]
    assert len(sleeps) == settings.expected_queries - 1
    # the configured delay, plus up to 30% jitter, every time
    assert all(1.5 <= s <= 1.5 * 1.3 for s in sleeps)
