"""Prompt construction and Gemini error handling (no live model call)."""

from __future__ import annotations

import pytest

from app.analysis import AnalysisError, GeminiAnalyzer, build_prompt
from app.config import Settings


def make_settings(**overrides) -> Settings:
    base = dict(ADMIN_PASSWORD="pw", GEMINI_API_KEY="secret-key", GEMINI_TIMEOUT_SECONDS=0.2)
    base.update(overrides)
    return Settings(**base)


def test_prompt_contains_topic_count_and_the_four_sections():
    prompt = build_prompt("villa rentals", ["villa italy", "villa greece"], 600)
    assert "villa rentals" in prompt
    assert "contains 2 unique keywords" in prompt
    assert "- villa italy" in prompt
    for heading in (
        "## 1. Search Intent Classification",
        "## 2. Top 5 Topic Clusters",
        "## 3. Search Patterns and Attribute Analysis",
        "## 4. Content Opportunities and Question Insights",
    ):
        assert heading in prompt


def test_prompt_truncates_and_says_so():
    keywords = [f"kw {i}" for i in range(50)]
    prompt = build_prompt("t", keywords, 10)
    assert "truncated to the first 10" in prompt
    assert "full set contains 50" in prompt
    assert "- kw 9" in prompt
    assert "- kw 10\n" not in prompt


def test_prompt_never_contains_credentials():
    settings = make_settings()
    prompt = build_prompt("villa", ["villa italy"], 600)
    assert settings.GEMINI_API_KEY not in prompt


@pytest.mark.asyncio
async def test_empty_response_raises_after_one_retry(monkeypatch):
    analyzer = GeminiAnalyzer(make_settings())
    calls = {"n": 0}

    async def fake_generate(prompt: str) -> str:
        calls["n"] += 1
        return "   "

    monkeypatch.setattr(analyzer, "_generate", fake_generate)
    monkeypatch.setattr("app.analysis.asyncio.sleep", _noop_sleep)

    with pytest.raises(AnalysisError) as excinfo:
        await analyzer.analyse("villa", ["villa italy"])
    assert "empty or blocked" in str(excinfo.value)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_transient_failure_is_retried_once_then_succeeds(monkeypatch):
    analyzer = GeminiAnalyzer(make_settings())
    calls = {"n": 0}

    async def fake_generate(prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("upstream 503 with a secret-looking body")
        return "## 1. Search Intent Classification\nok"

    monkeypatch.setattr(analyzer, "_generate", fake_generate)
    monkeypatch.setattr("app.analysis.asyncio.sleep", _noop_sleep)

    result = await analyzer.analyse("villa", ["villa italy"])
    assert result.startswith("## 1.")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_upstream_body_never_leaks_into_the_error(monkeypatch):
    analyzer = GeminiAnalyzer(make_settings())

    async def fake_generate(prompt: str) -> str:
        raise RuntimeError("API key AIzaSyLEAKED rejected by upstream")

    monkeypatch.setattr(analyzer, "_generate", fake_generate)
    monkeypatch.setattr("app.analysis.asyncio.sleep", _noop_sleep)

    with pytest.raises(AnalysisError) as excinfo:
        await analyzer.analyse("villa", ["villa italy"])
    assert "AIzaSyLEAKED" not in str(excinfo.value)
    assert str(excinfo.value) == "the model could not be reached"


async def _noop_sleep(seconds: float) -> None:
    return None
