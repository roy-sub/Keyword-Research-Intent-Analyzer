"""Gemini search-intent analysis.

The model name always comes from GEMINI_MODEL, so switching models is a
config change. No credential ever enters a prompt.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import Settings

logger = logging.getLogger(__name__)


class AnalysisError(RuntimeError):
    """Raised with a message that is safe to show the caller."""


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


class GeminiAnalyzer:
    """Thin async wrapper around google-genai with a timeout and one retry."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google import genai  # imported lazily so tests need no key

            self._client = genai.Client(api_key=self._settings.GEMINI_API_KEY)
        return self._client

    async def analyse(self, topic: str, keywords: list[str]) -> str:
        settings = self._settings
        prompt = build_prompt(topic, keywords, settings.MAX_KEYWORDS_IN_PROMPT)

        last_error = "no response from the model"
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(2.0)
            try:
                text = await asyncio.wait_for(
                    self._generate(prompt), timeout=settings.GEMINI_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                last_error = "the model timed out"
                logger.warning("gemini call timed out attempt=%d", attempt + 1)
                continue
            except Exception as exc:  # noqa: BLE001 - upstream body must not leak
                last_error = "the model could not be reached"
                logger.warning(
                    "gemini call failed attempt=%d kind=%s", attempt + 1, type(exc).__name__
                )
                continue

            if text and text.strip():
                return text.strip()
            last_error = "the model returned an empty or blocked response"
            logger.warning("gemini returned empty response attempt=%d", attempt + 1)

        raise AnalysisError(last_error)

    async def _generate(self, prompt: str) -> str:
        from google.genai import types

        client = self._get_client()
        response = await client.aio.models.generate_content(
            model=self._settings.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
        )
        return getattr(response, "text", "") or ""
