"""FastAPI application: routes and lifespan wiring.

This service is API-only. The frontend is a separate Render service with its
own origin, so the browser reaches this API cross-origin and CORS plus
ALLOWED_ORIGINS are what let it through.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.analysis import AnalysisError, GeminiAnalyzer
from app.auth import (
    authenticate,
    get_session_store,
    require_access_key,
    reset_session_store,
)
from app.cache import TTLCache
from app.config import ConfigError, Settings, configure_logging, get_settings
from app.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    ServiceInfo,
    StatusResponse,
)
from app.rate_limit import RateLimiter
from app.suggest.base import SuggestProvider, merge_results
from app.suggest.google import GoogleSuggestProvider, build_client

logger = logging.getLogger(__name__)

MAX_TOPIC_LENGTH = 100


class AppState:
    """Process-wide singletons, built once in the lifespan handler."""

    http_client = None
    provider: SuggestProvider | None = None
    analyzer: GeminiAnalyzer | None = None
    limiter: RateLimiter | None = None
    cache: TTLCache | None = None


state = AppState()


def build_state(settings: Settings) -> None:
    state.http_client = build_client(settings)
    state.provider = GoogleSuggestProvider(state.http_client, settings)
    state.analyzer = GeminiAnalyzer(settings)
    state.limiter = RateLimiter(
        settings.RATE_LIMIT_MAX_SEARCHES, settings.RATE_LIMIT_WINDOW_MINUTES
    )
    state.cache = TTLCache(settings.CACHE_TTL_MINUTES, settings.CACHE_MAX_ENTRIES)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)
    try:
        settings.validate_or_raise()
    except ConfigError as exc:
        # Fail fast and loudly rather than starting a half-configured service.
        logger.critical("%s", exc)
        print(f"STARTUP FAILED: {exc}", file=sys.stderr, flush=True)
        raise

    reset_session_store()
    build_state(settings)
    logger.info(
        "started mode=%s model=%s limit=%s/%smin cache_ttl=%smin",
        settings.MODE,
        settings.GEMINI_MODEL,
        settings.RATE_LIMIT_MAX_SEARCHES,
        settings.RATE_LIMIT_WINDOW_MINUTES,
        settings.CACHE_TTL_MINUTES,
    )
    try:
        yield
    finally:
        if state.http_client is not None:
            await state.http_client.aclose()
            state.http_client = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    docs_enabled = not settings.is_prod

    app = FastAPI(
        title="Refract API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Access-Key", "X-API-Key"],
    )

    register_routes(app, settings)
    return app


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_routes(app: FastAPI, settings: Settings) -> None:
    @app.get("/", response_model=ServiceInfo)
    async def root() -> ServiceInfo:
        """A small descriptor, so hitting the API host in a browser explains
        itself instead of returning a bare 404. The UI lives elsewhere."""
        return ServiceInfo(
            service="refract-api",
            status="ok",
            docs="/docs" if not get_settings().is_prod else None,
        )

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """No auth, no upstream calls — Render's health check hits this."""
        return HealthResponse(status="ok")

    @app.post("/api/login", response_model=LoginResponse)
    async def login(request: Request, body: LoginRequest) -> LoginResponse:
        from app.auth import enforce_api_key

        enforce_api_key(request, get_settings())
        token = await authenticate(body.username, body.password, get_settings())
        logger.info("admin logged in")
        return LoginResponse(ok=True, access_key=token)

    @app.post("/api/logout", response_model=LogoutResponse)
    async def logout(access_key: str = Depends(require_access_key)) -> LogoutResponse:
        get_session_store().revoke(access_key)
        return LogoutResponse(ok=True)

    @app.get("/api/status", response_model=StatusResponse)
    async def read_status(_: str = Depends(require_access_key)) -> StatusResponse:
        current = get_settings()
        remaining, retry_after = state.limiter.snapshot()
        return StatusResponse(
            searches_remaining=remaining,
            window_minutes=current.RATE_LIMIT_WINDOW_MINUTES,
            retry_after_seconds=retry_after,
            request_delay_seconds=current.SUGGEST_DELAY_SECONDS,
            expected_queries=current.expected_queries,
        )

    @app.post("/api/analyze", response_model=AnalyzeResponse)
    async def analyze(
        body: AnalyzeRequest,
        _: str = Depends(require_access_key),
    ):
        current = get_settings()
        topic = " ".join(body.topic.split())

        if not topic:
            raise HTTPException(status_code=400, detail="Topic must not be empty.")
        if len(topic) > MAX_TOPIC_LENGTH:
            raise HTTPException(
                status_code=400, detail="Topic must be 100 characters or fewer."
            )

        cache_key = TTLCache.key(topic, current.SUGGEST_LANG, current.SUGGEST_COUNTRY)

        # A cache hit costs no quota and makes no upstream call.
        cached = state.cache.get(cache_key)
        if cached is not None:
            remaining, _retry = state.limiter.snapshot()
            payload = cached.model_copy(
                update={"cached": True, "searches_remaining": remaining}
            )
            log_run(topic, payload, cache_hit=True)
            return payload

        allowed, remaining, retry_after = state.limiter.try_consume()
        if not allowed:
            # The contract puts retry_after_seconds alongside detail in the
            # body, which HTTPException cannot express, so the response is
            # built directly.
            logger.info("run rejected by quota topic=%r retry_after=%d", topic, retry_after)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Search limit reached.",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        started = time.monotonic()
        try:
            payload = await asyncio.wait_for(
                run_analysis(topic, current, remaining, started),
                timeout=current.run_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error("run timed out topic=%r", topic)
            raise HTTPException(status_code=504, detail="The run timed out.")
        except HTTPException:
            raise

        state.cache.set(cache_key, payload)
        log_run(topic, payload, cache_hit=False)
        return payload


async def run_analysis(
    topic: str, settings: Settings, remaining: int, started: float
) -> AnalyzeResponse:
    """Collect, merge, analyse. Quota is refunded if nothing was collected."""
    results = await state.provider.fetch(topic, settings.SUGGEST_LANG, settings.SUGGEST_COUNTRY)
    sources, keywords, failed = merge_results(results)

    if not sources:
        # Nothing reached us from Google: the run cost the upstream nothing
        # useful, so it should not cost quota either.
        state.limiter.refund()
        logger.error(
            "all suggest queries failed topic=%r attempted=%d", topic, len(results)
        )
        raise HTTPException(status_code=502, detail="Google returned no suggestions.")

    if not keywords:
        state.limiter.refund()
        raise HTTPException(status_code=502, detail="Google returned no suggestions.")

    try:
        markdown = await state.analyzer.analyse(topic, [k.keyword for k in keywords])
    except AnalysisError as exc:
        raise HTTPException(status_code=502, detail=f"AI analysis failed: {exc}")

    remaining_now, _retry = state.limiter.snapshot()
    return AnalyzeResponse(
        topic=topic,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        cached=False,
        duration_seconds=round(time.monotonic() - started, 1),
        total_keywords=len(keywords),
        queries_attempted=len(results),
        queries_succeeded=len(sources),
        failed_queries=failed,
        searches_remaining=remaining_now,
        sources=sources,
        keywords=keywords,
        analysis_markdown=markdown,
    )


def log_run(topic: str, payload: AnalyzeResponse, cache_hit: bool) -> None:
    """One structured line per run. Never logs credentials."""
    logger.info(
        "run topic=%r duration=%.1fs queries=%d/%d keywords=%d cached=%s quota_remaining=%d",
        topic,
        payload.duration_seconds,
        payload.queries_succeeded,
        payload.queries_attempted,
        payload.total_keywords,
        cache_hit,
        payload.searches_remaining,
    )


app = create_app()
