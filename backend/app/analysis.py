"""AI analysis with provider fallback.

Three providers are tried in order — Claude, then OpenAI, then Gemini. If one
fails for any reason (no key, rejected key, exhausted balance, rate limit,
timeout, server error, empty or declined response) the next is tried. The
response records which provider and model actually produced the report.

Failure reasons are curated in this module: the caller is given a short,
plain-English cause per provider and never an upstream body, a traceback or
anything that could carry a credential.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings

logger = logging.getLogger(__name__)

# Shared across providers. The report is a few thousand tokens; this leaves
# generous headroom without inviting an HTTP timeout on a non-streaming call.
MAX_OUTPUT_TOKENS = 16000

PROVIDER_LABELS = {"anthropic": "Claude", "openai": "OpenAI", "gemini": "Gemini"}


class AnalysisError(RuntimeError):
    """Raised with a message that is safe to show the caller."""


@dataclass(frozen=True)
class ProviderFailure:
    """Why one provider could not produce the report."""

    provider: str
    label: str
    model: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "label": self.label,
            "model": self.model,
            "reason": self.reason,
        }


class AllProvidersFailed(AnalysisError):
    """Every configured provider failed. Carries one reason each."""

    def __init__(self, failures: list[ProviderFailure]) -> None:
        super().__init__("Every AI provider failed.")
        self.failures = failures


@dataclass(frozen=True)
class AnalysisResult:
    markdown: str
    provider: str
    model: str


SYSTEM_INSTRUCTION = (
    "You are an expert SEO strategist and search-intent analyst. You read raw "
    "keyword data from search autocomplete and explain what the people behind "
    "those searches actually want. You are specific and evidence-led: you cite "
    "the keywords that support each claim. Respond in Markdown only — no "
    "preamble, no closing remarks, no code fences around the whole answer."
)

PROMPT_TEMPLATE = """Analyse the keyword set below for the seed topic "{topic}".

{count_line}

Produce a Markdown report with exactly these four sections, using these
headings verbatim:

## 1. Search Intent Classification
Group the keywords into informational, transactional, commercial
investigation, and navigational intent. Give the approximate share of the set
each intent represents and quote representative keywords for each.

## 2. Top 5 Topic Clusters
The five strongest clusters in the data. For each: a name, what the searcher
is trying to do, and the keywords that belong to it.

## 3. Search Patterns and Attribute Analysis
The recurring modifiers, attributes, qualifiers, locations, price signals and
comparison patterns that show up across the set, and what each pattern implies
about the audience.

## 4. Content Opportunities and Question Insights
The questions being asked, the gaps they reveal, and the specific pages or
articles worth building — ordered by likely value.

Keywords:
{keywords}
"""


def build_prompt(topic: str, keywords: list[str], max_keywords: int) -> str:
    total = len(keywords)
    included = keywords[:max_keywords]
    if total > max_keywords:
        count_line = (
            f"The full set contains {total} unique keywords. The list below is "
            f"truncated to the first {max_keywords} for length; treat it as a "
            "representative sample and say so if it affects a conclusion."
        )
    else:
        count_line = f"The set contains {total} unique keywords, listed in full below."
    return PROMPT_TEMPLATE.format(
        topic=topic,
        count_line=count_line,
        keywords="\n".join(f"- {k}" for k in included),
    )


# ---------------------------------------------------------------------------
# Provider contract
# ---------------------------------------------------------------------------


class AnalysisProvider(Protocol):
    name: str
    model: str

    def is_configured(self) -> bool:
        """False when no API key is set — skipped without being called."""

    async def generate(self, prompt: str) -> str:
        """Return Markdown, or raise AnalysisError with a safe reason."""


class _BaseProvider:
    name = ""

    def __init__(self, api_key: str, model: str, timeout: float) -> None:
        self._api_key = (api_key or "").strip()
        self.model = model
        self._timeout = timeout
        self._client = None

    @property
    def label(self) -> str:
        return PROVIDER_LABELS.get(self.name, self.name.title())

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def failure(self, reason: str) -> ProviderFailure:
        return ProviderFailure(self.name, self.label, self.model, reason)


def _timeout_reason(seconds: float) -> str:
    return f"Timed out after {int(seconds)} seconds."


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------


class AnthropicProvider(_BaseProvider):
    name = "anthropic"

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic

            # The SDK already retries connection errors, 429 and 5xx. Keep that
            # bounded: with three providers behind this one, a long retry chain
            # costs more than moving on.
            self._client = AsyncAnthropic(
                api_key=self._api_key, timeout=self._timeout, max_retries=1
            )
        return self._client

    async def generate(self, prompt: str) -> str:
        import anthropic

        client = self._get_client()
        try:
            # Thinking is adaptive by default on current Claude models; the
            # response therefore carries thinking blocks alongside text.
            response = await client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=SYSTEM_INSTRUCTION,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError:
            raise AnalysisError("The API key was rejected.")
        except anthropic.PermissionDeniedError:
            raise AnalysisError("The API key is not permitted to use this model.")
        except anthropic.NotFoundError:
            raise AnalysisError(f"Model '{self.model}' is not available to this key.")
        except anthropic.RateLimitError:
            raise AnalysisError("Rate limit reached.")
        except anthropic.BadRequestError as exc:
            raise AnalysisError(_anthropic_bad_request_reason(exc))
        except anthropic.APITimeoutError:
            raise AnalysisError(_timeout_reason(self._timeout))
        except anthropic.APIConnectionError:
            raise AnalysisError("Could not reach the API.")
        except anthropic.APIStatusError as exc:
            raise AnalysisError(f"The API returned an error (HTTP {exc.status_code}).")
        except Exception as exc:  # noqa: BLE001 - upstream detail must not leak
            logger.warning("anthropic call failed kind=%s", type(exc).__name__)
            raise AnalysisError("The request failed unexpectedly.")

        if getattr(response, "stop_reason", None) == "refusal":
            raise AnalysisError("The model declined the request.")

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        if not text.strip():
            raise AnalysisError("The model returned an empty response.")
        return text.strip()


def _anthropic_bad_request_reason(exc: Exception) -> str:
    """Map the one 400 worth distinguishing — an exhausted balance."""
    message = str(getattr(exc, "message", "") or "").lower()
    if "credit" in message or "billing" in message:
        return "The account has insufficient credit."
    return "The request was rejected as invalid."


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class OpenAIProvider(_BaseProvider):
    name = "openai"

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self._api_key, timeout=self._timeout, max_retries=1
            )
        return self._client

    async def generate(self, prompt: str) -> str:
        import openai

        client = self._get_client()
        try:
            response = await client.responses.create(
                model=self.model,
                instructions=SYSTEM_INSTRUCTION,
                input=prompt,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
        except openai.AuthenticationError:
            raise AnalysisError("The API key was rejected.")
        except openai.PermissionDeniedError:
            raise AnalysisError("The API key is not permitted to use this model.")
        except openai.NotFoundError:
            raise AnalysisError(f"Model '{self.model}' is not available to this key.")
        except openai.RateLimitError:
            # OpenAI reports an exhausted balance as a 429 as well as a true
            # rate limit, so the wording has to cover both.
            raise AnalysisError("Rate limit reached or quota exhausted.")
        except openai.BadRequestError:
            raise AnalysisError("The request was rejected as invalid.")
        except openai.APITimeoutError:
            raise AnalysisError(_timeout_reason(self._timeout))
        except openai.APIConnectionError:
            raise AnalysisError("Could not reach the API.")
        except openai.APIStatusError as exc:
            raise AnalysisError(f"The API returned an error (HTTP {exc.status_code}).")
        except Exception as exc:  # noqa: BLE001
            logger.warning("openai call failed kind=%s", type(exc).__name__)
            raise AnalysisError("The request failed unexpectedly.")

        if getattr(response, "status", None) == "incomplete":
            raise AnalysisError("The model stopped before finishing the report.")

        text = (getattr(response, "output_text", "") or "").strip()
        if not text:
            raise AnalysisError("The model returned an empty response.")
        return text


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


class GeminiProvider(_BaseProvider):
    name = "gemini"

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def generate(self, prompt: str) -> str:
        from google.genai import types

        client = self._get_client()
        try:
            response = await client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
            )
        except Exception as exc:  # noqa: BLE001 - google-genai has no stable
            # public exception hierarchy to branch on, so classify by name and
            # never by message content, which can echo the request.
            raise AnalysisError(_gemini_reason(exc, self.model))

        text = (getattr(response, "text", "") or "").strip()
        if not text:
            # Gemini returns an empty body rather than an error when a safety
            # filter blocks the response.
            raise AnalysisError("The model returned an empty or blocked response.")
        return text


def _gemini_reason(exc: Exception, model: str) -> str:
    """Classify a google-genai failure from its structured fields.

    `APIError` carries `.code` (HTTP status) and `.status` (an enum-like
    string). Only one narrow message test is used — Google reports an invalid
    key as a 400 INVALID_ARGUMENT, indistinguishable by status alone from a
    genuinely malformed request — and the message itself is never returned.
    """
    from google.genai import errors as gerrors

    kind = type(exc).__name__

    if isinstance(exc, asyncio.TimeoutError) or "Timeout" in kind or "Deadline" in kind:
        return "Timed out."

    if isinstance(exc, gerrors.APIError):
        code = getattr(exc, "code", None)
        message = str(getattr(exc, "message", "") or "").lower()

        if code in (401, 403):
            return "The API key was rejected or lacks access."
        if code == 404:
            return f"Model '{model}' is not available to this key."
        if code == 429:
            return "Rate limit reached or quota exhausted."
        if code == 400:
            if "api key" in message:
                return "The API key was rejected."
            if "quota" in message or "billing" in message:
                return "The account has insufficient quota."
            return "The request was rejected as invalid."
        if isinstance(code, int) and code >= 500:
            return f"The API returned an error (HTTP {code})."
        if isinstance(code, int):
            return f"The API returned an error (HTTP {code})."

    logger.warning("gemini call failed kind=%s", kind)
    return "The request failed unexpectedly."


# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------


PROVIDER_CLASSES = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}


def build_providers(settings: Settings) -> list[AnalysisProvider]:
    """One provider per name in AI_PROVIDER_ORDER, in that order."""
    keys = {
        "anthropic": settings.ANTHROPIC_API_KEY,
        "openai": settings.OPENAI_API_KEY,
        "gemini": settings.GEMINI_API_KEY,
    }
    models = {
        "anthropic": settings.ANTHROPIC_MODEL,
        "openai": settings.OPENAI_MODEL,
        "gemini": settings.GEMINI_MODEL,
    }
    providers: list[AnalysisProvider] = []
    for name in settings.provider_order:
        cls = PROVIDER_CLASSES.get(name)
        if cls is None:
            continue
        providers.append(cls(keys[name], models[name], settings.AI_TIMEOUT_SECONDS))
    return providers


class AnalysisChain:
    """Tries each provider in turn and reports which one succeeded."""

    def __init__(self, settings: Settings, providers: list[AnalysisProvider] | None = None) -> None:
        self._settings = settings
        self.providers = providers if providers is not None else build_providers(settings)

    async def analyse(self, topic: str, keywords: list[str]) -> AnalysisResult:
        settings = self._settings
        prompt = build_prompt(topic, keywords, settings.MAX_KEYWORDS_IN_PROMPT)
        failures: list[ProviderFailure] = []

        for provider in self.providers:
            if not provider.is_configured():
                failures.append(provider.failure("No API key configured."))
                logger.info("ai provider skipped provider=%s reason=no-key", provider.name)
                continue

            try:
                text = await asyncio.wait_for(
                    provider.generate(prompt), timeout=settings.AI_TIMEOUT_SECONDS + 5
                )
            except AnalysisError as exc:
                failures.append(provider.failure(str(exc)))
                logger.warning(
                    "ai provider failed provider=%s model=%s reason=%s",
                    provider.name, provider.model, exc,
                )
                continue
            except asyncio.TimeoutError:
                failures.append(
                    provider.failure(_timeout_reason(settings.AI_TIMEOUT_SECONDS))
                )
                logger.warning("ai provider timed out provider=%s", provider.name)
                continue

            if failures:
                logger.info(
                    "ai provider succeeded after fallback provider=%s model=%s skipped=%d",
                    provider.name, provider.model, len(failures),
                )
            else:
                logger.info(
                    "ai provider succeeded provider=%s model=%s", provider.name, provider.model
                )
            return AnalysisResult(markdown=text, provider=provider.name, model=provider.model)

        logger.error(
            "all ai providers failed: %s",
            "; ".join(f"{f.label}: {f.reason}" for f in failures),
        )
        raise AllProvidersFailed(failures)
