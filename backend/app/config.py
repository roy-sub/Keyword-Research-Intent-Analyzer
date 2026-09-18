"""Application settings.

Everything is configured through environment variables, loaded from a local
`.env` file by pydantic-settings. Nothing that varies between environments —
the AI model, the rate limit, the pacing delay, the modifier word lists — is
hard-coded in the logic, so tuning the service is a config change and never a
code change.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Raised at startup when required configuration is missing."""


# Default modifier lists. They live here rather than inline in the collection
# logic so a different market/language can be supported by config alone.
DEFAULT_QUESTION_MODIFIERS = ["who", "what", "when", "where", "why", "how"]
DEFAULT_COMMERCIAL_MODIFIERS = ["best", "buy", "cheap", "near me"]

ALPHABET = [chr(c) for c in range(ord("a"), ord("z") + 1)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Mode -------------------------------------------------------------
    MODE: Literal["dev", "prod"] = "dev"

    # ---- Access -----------------------------------------------------------
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = ""
    SESSION_TTL_HOURS: int = 12
    API_KEY: str = ""
    ALLOWED_ORIGINS: str = ""

    # ---- AI ---------------------------------------------------------------
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash"
    GEMINI_TIMEOUT_SECONDS: float = 120.0
    MAX_KEYWORDS_IN_PROMPT: int = 600

    # ---- Collection -------------------------------------------------------
    SUGGEST_LANG: str = "en"
    SUGGEST_COUNTRY: str = "us"
    SUGGEST_DELAY_SECONDS: float = 1.0
    SUGGEST_TIMEOUT_SECONDS: float = 5.0
    SUGGEST_MAX_RETRIES: int = 2
    SUGGEST_QUESTION_MODIFIERS: str = ",".join(DEFAULT_QUESTION_MODIFIERS)
    SUGGEST_COMMERCIAL_MODIFIERS: str = ",".join(DEFAULT_COMMERCIAL_MODIFIERS)

    # ---- Limits and logging ----------------------------------------------
    RATE_LIMIT_MAX_SEARCHES: int = 10
    RATE_LIMIT_WINDOW_MINUTES: int = 60
    CACHE_TTL_MINUTES: int = 1440
    CACHE_MAX_ENTRIES: int = 50
    LOG_LEVEL: str = "INFO"

    @field_validator("MODE", mode="before")
    @classmethod
    def _normalise_mode(cls, v: object) -> object:
        return v.strip().lower() if isinstance(v, str) else v

    # ---- Derived ----------------------------------------------------------

    @property
    def is_prod(self) -> bool:
        return self.MODE == "prod"

    @property
    def allowed_origins(self) -> list[str]:
        """Parsed ALLOWED_ORIGINS, normalised to full origins.

        A bare hostname is promoted to https://<host>, so the value can be
        wired straight from Render's `fromService: property: host`, which
        yields a hostname with no scheme.
        """
        origins: list[str] = []
        for raw in self.ALLOWED_ORIGINS.split(","):
            value = raw.strip().rstrip("/")
            if not value:
                continue
            if "://" not in value:
                value = f"https://{value}"
            origins.append(value)
        return origins

    @property
    def question_modifiers(self) -> list[str]:
        return [m.strip() for m in self.SUGGEST_QUESTION_MODIFIERS.split(",") if m.strip()]

    @property
    def commercial_modifiers(self) -> list[str]:
        return [m.strip() for m in self.SUGGEST_COMMERCIAL_MODIFIERS.split(",") if m.strip()]

    @property
    def expected_queries(self) -> int:
        """Seed + a-z + question modifiers + commercial modifiers."""
        return 1 + len(ALPHABET) + len(self.question_modifiers) + len(self.commercial_modifiers)

    @property
    def run_timeout_seconds(self) -> float:
        """Total wall-clock budget for one /api/analyze run.

        Derived rather than configured separately so that raising the pacing
        delay or the per-request timeout cannot silently make every run time
        out. Worst case per query is delay + timeout on every attempt.
        """
        per_query = self.SUGGEST_DELAY_SECONDS + self.SUGGEST_TIMEOUT_SECONDS * (
            self.SUGGEST_MAX_RETRIES + 1
        )
        return per_query * self.expected_queries + self.GEMINI_TIMEOUT_SECONDS * 2 + 30.0

    @property
    def cors_origins(self) -> list[str]:
        return self.allowed_origins

    @property
    def cors_origin_regex(self) -> str | None:
        """In dev, allow any localhost / 127.0.0.1 port.

        The frontend is a separate service and is served on its own port
        locally (see frontend/README.md), so every browser call to the API is
        cross-origin even on a laptop.
        """
        if self.is_prod:
            return None
        return r"http://(localhost|127\.0\.0\.1)(:\d+)?"

    # ---- Validation -------------------------------------------------------

    def missing_required(self) -> list[str]:
        """Names of required variables that are unset, for fail-fast startup."""
        missing: list[str] = []
        if not self.ADMIN_PASSWORD.strip():
            missing.append("ADMIN_PASSWORD")
        if not self.GEMINI_API_KEY.strip():
            missing.append("GEMINI_API_KEY")
        if self.is_prod:
            if not self.API_KEY.strip():
                missing.append("API_KEY (required when MODE=prod)")
            if not self.allowed_origins:
                missing.append("ALLOWED_ORIGINS (required when MODE=prod)")
        return missing

    def validate_or_raise(self) -> None:
        missing = self.missing_required()
        if missing:
            raise ConfigError(
                "Missing required environment variable(s): "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill them in."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests that patch the environment between cases."""
    get_settings.cache_clear()


def configure_logging(settings: Settings) -> None:
    """Verbose in dev, concise in prod. Never logs credentials."""
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    fmt = (
        "%(asctime)s %(levelname)-7s %(name)s:%(lineno)d %(message)s"
        if not settings.is_prod
        else "%(asctime)s %(levelname)s %(message)s"
    )
    logging.basicConfig(level=level, format=fmt, force=True)
    if settings.is_prod:
        logging.getLogger("httpx").setLevel(logging.WARNING)
