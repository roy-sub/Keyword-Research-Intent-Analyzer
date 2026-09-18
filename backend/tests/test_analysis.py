"""Prompt construction and the Claude -> OpenAI -> Gemini fallback chain.

No provider SDK is called: each provider is replaced by a stub, so these tests
need no API keys and make no network requests.
"""

from __future__ import annotations

import asyncio

import pytest

from app.analysis import (
    AllProvidersFailed,
    AnalysisChain,
    AnalysisError,
    AnthropicProvider,
    GeminiProvider,
    OpenAIProvider,
    build_prompt,
    build_providers,
)
from app.config import Settings


def make_settings(**overrides) -> Settings:
    base = dict(
        ADMIN_PASSWORD="pw",
        ANTHROPIC_API_KEY="anthropic-key",
        OPENAI_API_KEY="openai-key",
        GEMINI_API_KEY="gemini-key",
        AI_TIMEOUT_SECONDS=0.5,
    )
    base.update(overrides)
    return Settings(**base)


class StubProvider:
    """Stands in for a real provider: succeeds, or fails with a given reason."""

    def __init__(self, name, model="m", result=None, error=None, configured=True, hang=False):
        self.name = name
        self.label = name.title()
        self.model = model
        self._result = result
        self._error = error
        self._configured = configured
        self._hang = hang
        self.calls = 0

    def is_configured(self):
        return self._configured

    def failure(self, reason):
        from app.analysis import ProviderFailure
        return ProviderFailure(self.name, self.label, self.model, reason)

    async def generate(self, prompt):
        self.calls += 1
        if self._hang:
            await asyncio.sleep(30)
        if self._error:
            raise AnalysisError(self._error)
        return self._result


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


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
    for key in (settings.ANTHROPIC_API_KEY, settings.OPENAI_API_KEY, settings.GEMINI_API_KEY):
        assert key not in prompt


# ---------------------------------------------------------------------------
# Chain composition
# ---------------------------------------------------------------------------


def test_default_order_is_claude_then_openai_then_gemini():
    providers = build_providers(make_settings())
    assert [p.name for p in providers] == ["anthropic", "openai", "gemini"]
    assert isinstance(providers[0], AnthropicProvider)
    assert isinstance(providers[1], OpenAIProvider)
    assert isinstance(providers[2], GeminiProvider)


def test_order_is_configurable():
    providers = build_providers(make_settings(AI_PROVIDER_ORDER="gemini,anthropic"))
    assert [p.name for p in providers] == ["gemini", "anthropic"]


def test_providers_carry_their_own_model():
    providers = build_providers(make_settings(
        ANTHROPIC_MODEL="claude-x", OPENAI_MODEL="gpt-x", GEMINI_MODEL="gemini-x"))
    assert [p.model for p in providers] == ["claude-x", "gpt-x", "gemini-x"]


def test_provider_without_a_key_is_not_configured():
    providers = build_providers(make_settings(OPENAI_API_KEY=""))
    assert providers[0].is_configured()
    assert not providers[1].is_configured()


# ---------------------------------------------------------------------------
# Fallback behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_is_used_when_it_succeeds():
    claude = StubProvider("anthropic", "claude-opus-5", result="# Report")
    openai_p = StubProvider("openai", result="wrong")
    gemini = StubProvider("gemini", result="wrong")

    result = await AnalysisChain(make_settings(), [claude, openai_p, gemini]).analyse("t", ["k"])

    assert result.markdown == "# Report"
    assert result.provider == "anthropic"
    assert result.model == "claude-opus-5"
    assert claude.calls == 1
    # the rest are never called
    assert openai_p.calls == 0 and gemini.calls == 0


@pytest.mark.asyncio
async def test_falls_back_to_openai_when_claude_fails():
    claude = StubProvider("anthropic", error="The API key was rejected.")
    openai_p = StubProvider("openai", "gpt-6-astra", result="# From OpenAI")
    gemini = StubProvider("gemini", result="wrong")

    result = await AnalysisChain(make_settings(), [claude, openai_p, gemini]).analyse("t", ["k"])

    assert result.provider == "openai"
    assert result.model == "gpt-6-astra"
    assert result.markdown == "# From OpenAI"
    assert gemini.calls == 0


@pytest.mark.asyncio
async def test_falls_through_to_gemini_when_both_fail():
    claude = StubProvider("anthropic", error="The account has insufficient credit.")
    openai_p = StubProvider("openai", error="Rate limit reached or quota exhausted.")
    gemini = StubProvider("gemini", "gemini-3.5-flash", result="# From Gemini")

    result = await AnalysisChain(make_settings(), [claude, openai_p, gemini]).analyse("t", ["k"])

    assert result.provider == "gemini"
    assert result.markdown == "# From Gemini"


@pytest.mark.asyncio
async def test_missing_key_is_skipped_without_calling_the_provider():
    claude = StubProvider("anthropic", configured=False)
    openai_p = StubProvider("openai", result="# From OpenAI")

    result = await AnalysisChain(make_settings(), [claude, openai_p]).analyse("t", ["k"])

    assert result.provider == "openai"
    assert claude.calls == 0


@pytest.mark.asyncio
async def test_a_hanging_provider_times_out_and_the_chain_continues():
    claude = StubProvider("anthropic", hang=True)
    openai_p = StubProvider("openai", result="# From OpenAI")

    settings = make_settings(AI_TIMEOUT_SECONDS=0.05)
    result = await AnalysisChain(settings, [claude, openai_p]).analyse("t", ["k"])

    assert result.provider == "openai"


# ---------------------------------------------------------------------------
# Every provider failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_failed_reports_one_reason_per_provider():
    claude = StubProvider("anthropic", "claude-opus-5", configured=False)
    openai_p = StubProvider("openai", "gpt-6-astra", error="Rate limit reached or quota exhausted.")
    gemini = StubProvider("gemini", "gemini-3.5-flash", error="The API key was rejected or lacks access.")

    with pytest.raises(AllProvidersFailed) as excinfo:
        await AnalysisChain(make_settings(), [claude, openai_p, gemini]).analyse("t", ["k"])

    failures = excinfo.value.failures
    assert [f.provider for f in failures] == ["anthropic", "openai", "gemini"]
    assert failures[0].reason == "No API key configured."
    assert failures[1].reason == "Rate limit reached or quota exhausted."
    assert failures[2].reason == "The API key was rejected or lacks access."
    assert [f.model for f in failures] == ["claude-opus-5", "gpt-6-astra", "gemini-3.5-flash"]


@pytest.mark.asyncio
async def test_failure_payload_is_serialisable_and_labelled():
    claude = StubProvider("anthropic", configured=False)
    with pytest.raises(AllProvidersFailed) as excinfo:
        await AnalysisChain(make_settings(), [claude]).analyse("t", ["k"])
    d = excinfo.value.failures[0].as_dict()
    assert set(d) == {"provider", "label", "model", "reason"}


@pytest.mark.asyncio
async def test_no_credential_ever_appears_in_a_failure_reason():
    settings = make_settings()
    providers = [
        StubProvider("anthropic", configured=False),
        StubProvider("openai", error="The API key was rejected."),
        StubProvider("gemini", error="The request failed unexpectedly."),
    ]
    with pytest.raises(AllProvidersFailed) as excinfo:
        await AnalysisChain(settings, providers).analyse("t", ["k"])
    blob = " ".join(f.reason for f in excinfo.value.failures)
    for key in (settings.ANTHROPIC_API_KEY, settings.OPENAI_API_KEY, settings.GEMINI_API_KEY):
        assert key not in blob


# ---------------------------------------------------------------------------
# Real provider adapters: error mapping, without touching the network
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_maps_auth_error_to_a_clean_reason(monkeypatch):
    import anthropic

    provider = AnthropicProvider("k", "claude-opus-5", 1.0)

    class FakeMessages:
        async def create(self, **kwargs):
            raise anthropic.AuthenticationError(
                "bad key sk-ant-SECRET", response=_resp(401), body=None
            )

    provider._client = type("C", (), {"messages": FakeMessages()})()
    with pytest.raises(AnalysisError) as excinfo:
        await provider.generate("p")
    assert str(excinfo.value) == "The API key was rejected."
    assert "SECRET" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_anthropic_refusal_is_reported_as_a_decline():
    provider = AnthropicProvider("k", "claude-opus-5", 1.0)

    class FakeMessages:
        async def create(self, **kwargs):
            return type("R", (), {"stop_reason": "refusal", "content": []})()

    provider._client = type("C", (), {"messages": FakeMessages()})()
    with pytest.raises(AnalysisError) as excinfo:
        await provider.generate("p")
    assert "declined" in str(excinfo.value)


@pytest.mark.asyncio
async def test_anthropic_reads_text_blocks_and_ignores_thinking():
    provider = AnthropicProvider("k", "claude-opus-5", 1.0)
    blocks = [
        type("B", (), {"type": "thinking", "thinking": "..."})(),
        type("B", (), {"type": "text", "text": "## 1. Search Intent Classification"})(),
    ]

    class FakeMessages:
        async def create(self, **kwargs):
            return type("R", (), {"stop_reason": "end_turn", "content": blocks})()

    provider._client = type("C", (), {"messages": FakeMessages()})()
    assert await provider.generate("p") == "## 1. Search Intent Classification"


@pytest.mark.asyncio
async def test_openai_empty_output_is_an_error():
    provider = OpenAIProvider("k", "gpt-6-astra", 1.0)

    class FakeResponses:
        async def create(self, **kwargs):
            return type("R", (), {"status": "completed", "output_text": "   "})()

    provider._client = type("C", (), {"responses": FakeResponses()})()
    with pytest.raises(AnalysisError) as excinfo:
        await provider.generate("p")
    assert "empty" in str(excinfo.value)


@pytest.mark.asyncio
async def test_openai_reads_output_text():
    provider = OpenAIProvider("k", "gpt-6-astra", 1.0)

    class FakeResponses:
        async def create(self, **kwargs):
            return type("R", (), {"status": "completed", "output_text": "# Report"})()

    provider._client = type("C", (), {"responses": FakeResponses()})()
    assert await provider.generate("p") == "# Report"


@pytest.mark.asyncio
async def test_gemini_blocked_response_is_an_error(monkeypatch):
    provider = GeminiProvider("k", "gemini-3.5-flash", 1.0)

    class FakeModels:
        async def generate_content(self, **kwargs):
            return type("R", (), {"text": ""})()

    provider._client = type("C", (), {"aio": type("A", (), {"models": FakeModels()})()})()
    with pytest.raises(AnalysisError) as excinfo:
        await provider.generate("p")
    assert "empty or blocked" in str(excinfo.value)


def _resp(status: int):
    import httpx
    return httpx.Response(status_code=status, request=httpx.Request("POST", "https://x"))


# ---------------------------------------------------------------------------
# Gemini failure classification (structured fields, not message echoing)
# ---------------------------------------------------------------------------


def _api_error(code: int, message: str):
    from google.genai import errors as gerrors
    return gerrors.APIError(code, {"error": {"message": message, "code": code}})


@pytest.mark.parametrize(
    "code,message,expected",
    [
        (400, "API key not valid. Please pass a valid API key.", "The API key was rejected."),
        (400, "Billing quota exceeded", "The account has insufficient quota."),
        (400, "Request contains an invalid argument.", "The request was rejected as invalid."),
        (403, "permission denied", "The API key was rejected or lacks access."),
        (429, "resource exhausted", "Rate limit reached or quota exhausted."),
        (503, "backend unavailable", "The API returned an error (HTTP 503)."),
    ],
)
def test_gemini_reasons_are_classified_from_status(code, message, expected):
    from app.analysis import _gemini_reason
    assert _gemini_reason(_api_error(code, message), "gemini-3.5-flash") == expected


def test_gemini_404_names_the_model():
    from app.analysis import _gemini_reason
    reason = _gemini_reason(_api_error(404, "not found"), "gemini-x")
    assert reason == "Model 'gemini-x' is not available to this key."


def test_gemini_reason_never_echoes_the_upstream_message():
    from app.analysis import _gemini_reason
    secret = "AIzaSyLEAKEDKEY123"
    reason = _gemini_reason(_api_error(400, f"API key not valid: {secret}"), "gemini-3.5-flash")
    assert secret not in reason
