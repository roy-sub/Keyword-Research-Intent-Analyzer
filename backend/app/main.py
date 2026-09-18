"""FastAPI application: routes and lifespan wiring.

This service is API-only. The frontend is a separate Render service with its
own origin, so the browser reaches this API cross-origin and CORS plus
ALLOWED_ORIGINS are what let it through.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.analysis import AllProvidersFailed, AnalysisChain, AnalysisError
from app.auth import (
    authenticate,
    get_session_store,
    require_access_key,
    reset_session_store,
)
from app.cache import TTLCache
from app.markets import Market
from app.config import ConfigError, Settings, configure_logging, get_settings
from app.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    MarketInfo,
    ReportExportRequest,
    ServiceInfo,
    StatusResponse,
)
from app.pdf import render_report_pdf
from app.rate_limit import RateLimiter
from app.suggest.base import SuggestProvider, merge_results
from app.suggest.google import GoogleSuggestProvider, build_client

logger = logging.getLogger(__name__)

MAX_TOPIC_LENGTH = 100


class AppState:
    """Process-wide singletons, built once in the lifespan handler."""

    http_client = None
    provider: SuggestProvider | None = None
    analyzer: AnalysisChain | None = None
    limiter: RateLimiter | None = None
    cache: TTLCache | None = None


state = AppState()


def build_state(settings: Settings) -> None:
    state.http_client = build_client(settings)
    state.provider = GoogleSuggestProvider(state.http_client, settings)
    state.analyzer = AnalysisChain(settings)
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
        "started mode=%s ai_chain=%s limit=%s/%smin cache_ttl=%smin",
        settings.MODE,
        ",".join(settings.configured_providers) or "none",
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
        title="Keyword Analyzer API",
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
            service="keyword-analyzer-api",
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
            markets=[MarketInfo(**m.as_dict()) for m in current.enabled_markets],
            default_market=current.resolve_market(None).code,
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

        market = current.resolve_market(body.market)
        cache_key = TTLCache.key(topic, market.code)

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
                run_analysis(topic, market, current, remaining, started),
                timeout=current.run_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error("run timed out topic=%r", topic)
            raise HTTPException(status_code=504, detail="The run timed out.")
        except AllProvidersFailed as exc:
            return JSONResponse(
                status_code=502,
                content={
                    "detail": "The AI analysis could not be completed.",
                    "provider_failures": [f.as_dict() for f in exc.failures],
                },
            )
        except HTTPException:
            raise

        state.cache.set(cache_key, payload)
        log_run(topic, payload, cache_hit=False)
        return payload


    @app.post("/api/export/report.pdf")
    async def export_report_pdf(
        body: ReportExportRequest,
        _: str = Depends(require_access_key),
    ) -> Response:
        """Typeset a report the client already has into a PDF.

        Rendering happens here rather than in the browser so the document gets
        real pagination, running heads and table layout — none of which a
        client-side canvas dump would give us. It costs no quota and makes no
        upstream call.
        """
        try:
            pdf = await asyncio.to_thread(
                render_report_pdf,
                topic=body.topic,
                analysis_markdown=body.analysis_markdown,
                generated_at=body.generated_at,
                provider_label=body.provider_label,
                model=body.model,
                market_label=body.market_label,
                total_keywords=body.total_keywords,
                queries_succeeded=body.queries_succeeded,
                queries_attempted=body.queries_attempted,
            )
        except Exception:
            logger.exception("pdf render failed topic=%r", body.topic)
            raise HTTPException(status_code=500, detail="The PDF could not be built.")

        filename = pdf_filename(body.topic, body.generated_at)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


async def run_analysis(
    topic: str, market: Market, settings: Settings, remaining: int, started: float
) -> AnalyzeResponse:
    """Collect, merge, analyse. Quota is refunded if nothing was collected."""
    results = await state.provider.fetch(topic, market)
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
        analysis = await state.analyzer.analyse(
            topic, [k.keyword for k in keywords], market=market
        )
    except AllProvidersFailed:
        # Subclasses AnalysisError, so it must be re-raised ahead of the
        # generic handler below; the route turns it into a body that carries
        # one reason per provider.
        raise
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
        analysis_markdown=analysis.markdown,
        analysis_provider=analysis.provider,
        analysis_model=analysis.model,
        market=market.code,
        market_label=market.label,
    )



def pdf_filename(topic: str, generated_at: str) -> str:
    """ASCII-only, quote-free name — it is interpolated into a header."""
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:48] or "report"
    stamp = re.sub(r"[^0-9]", "", generated_at)[:8]
    parts = ["intent-report", slug] + ([stamp] if stamp else [])
    return "-".join(parts) + ".pdf"


def log_run(topic: str, payload: AnalyzeResponse, cache_hit: bool) -> None:
    """One structured line per run. Never logs credentials."""
    logger.info(
        "run topic=%r duration=%.1fs queries=%d/%d keywords=%d ai=%s/%s cached=%s "
        "quota_remaining=%d",
        topic,
        payload.duration_seconds,
        payload.queries_succeeded,
        payload.queries_attempted,
        payload.total_keywords,
        payload.analysis_provider,
        payload.analysis_model,
        cache_hit,
        payload.searches_remaining,
    )


app = create_app()
