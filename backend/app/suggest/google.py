"""Google autocomplete collection.

The endpoint is unofficial: it is not documented, not guaranteed, rate-limits
aggressively from cloud IPs, and does not reliably return UTF-8. Every one of
those is handled here rather than allowed to surface as a 500.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random

import httpx

from app.config import ALPHABET, Settings
from app.models import SourceType
from app.suggest.base import SourceResult, build_query_set

logger = logging.getLogger(__name__)

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"

# A realistic desktop browser UA; the endpoint responds differently to
# obviously scripted clients.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _decode(response: httpx.Response) -> str:
    """Decode defensively.

    Google frequently serves this endpoint as latin-1 or an unlabelled legacy
    charset. Try the declared charset, then UTF-8, then latin-1 (which cannot
    fail), rather than raising on a mis-labelled body.
    """
    raw = response.content
    candidates: list[str] = []
    declared = response.charset_encoding
    if declared:
        candidates.append(declared)
    candidates += ["utf-8", "latin-1"]
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _parse(text: str) -> list[str]:
    """Suggestions live at index 1 of the returned array."""
    payload = json.loads(text)
    if not isinstance(payload, list) or len(payload) < 2:
        raise ValueError("unexpected payload shape")
    suggestions = payload[1]
    if not isinstance(suggestions, list):
        raise ValueError("unexpected suggestions shape")
    return [s for s in suggestions if isinstance(s, str)]


def _retry_after_seconds(response: httpx.Response, fallback: float) -> float:
    header = response.headers.get("retry-after")
    if not header:
        return fallback
    try:
        return max(0.0, min(float(header.strip()), 30.0))
    except ValueError:
        return fallback


class GoogleSuggestProvider:
    """Sequential, paced collector for Google autocomplete.

    Requests are deliberately never concurrent: the client requires a
    configurable delay between them (plus jitter) so the run looks like a
    person typing rather than a scraper.
    """

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def fetch(self, topic: str, lang: str, country: str) -> list[SourceResult]:
        settings = self._settings
        queries = build_query_set(
            topic,
            ALPHABET,
            settings.question_modifiers,
            settings.commercial_modifiers,
        )

        results: list[SourceResult] = []
        for index, (query, query_type) in enumerate(queries):
            if index > 0:
                await self._pace()
            results.append(await self._fetch_one(query, query_type, lang, country))
        return results

    async def _pace(self) -> None:
        """Configurable delay plus small random jitter, between every request."""
        delay = self._settings.SUGGEST_DELAY_SECONDS
        jitter = random.uniform(0, max(0.0, delay * 0.3))
        await asyncio.sleep(delay + jitter)

    async def _fetch_one(
        self, query: str, query_type: SourceType, lang: str, country: str
    ) -> SourceResult:
        settings = self._settings
        attempts = settings.SUGGEST_MAX_RETRIES + 1
        last_error = "unknown error"

        for attempt in range(attempts):
            if attempt > 0:
                # Exponential backoff, overridden by Retry-After when given.
                await asyncio.sleep(self._backoff_for_attempt(attempt))
            try:
                response = await self._client.get(
                    SUGGEST_URL,
                    params={"client": "firefox", "q": query, "hl": lang, "gl": country},
                    timeout=settings.SUGGEST_TIMEOUT_SECONDS,
                )
            except httpx.TimeoutException:
                last_error = "timeout"
                logger.warning("suggest query timed out query=%r attempt=%d", query, attempt + 1)
                continue
            except httpx.HTTPError as exc:
                last_error = f"transport error: {type(exc).__name__}"
                logger.warning(
                    "suggest query transport error query=%r attempt=%d kind=%s",
                    query,
                    attempt + 1,
                    type(exc).__name__,
                )
                continue

            if response.status_code in RETRYABLE_STATUS:
                last_error = f"HTTP {response.status_code}"
                wait = _retry_after_seconds(response, 0.0)
                logger.warning(
                    "suggest query rejected query=%r status=%d attempt=%d",
                    query,
                    response.status_code,
                    attempt + 1,
                )
                if wait and attempt < attempts - 1:
                    await asyncio.sleep(wait)
                continue

            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
                logger.warning(
                    "suggest query failed query=%r status=%d (not retryable)",
                    query,
                    response.status_code,
                )
                break

            try:
                keywords = _parse(_decode(response))
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = f"unreadable response: {exc}"
                logger.warning("suggest query unparseable query=%r", query)
                continue

            return SourceResult(query=query, type=query_type, keywords=keywords)

        return SourceResult(query=query, type=query_type, keywords=[], error=last_error)

    def _backoff_for_attempt(self, attempt: int) -> float:
        base = min(2.0 ** (attempt - 1), 8.0)
        return base + random.uniform(0, 0.25)


def build_client(settings: Settings) -> httpx.AsyncClient:
    """One client per process, created and closed in the lifespan handler."""
    return httpx.AsyncClient(
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": f"{settings.SUGGEST_LANG},en;q=0.8",
        },
        timeout=settings.SUGGEST_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
