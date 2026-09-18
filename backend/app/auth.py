"""Single-admin authentication.

One user exists: the admin. No registration, no roles. Login exchanges the
admin credentials for an opaque random session token held in memory; every
other /api route takes that token in `X-Access-Key`.

V1 limitation: the token store is in-process, so a restart signs the admin
out. That is acceptable for a single-instance internal tool.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request, status

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Constant delay on a failed login, to blunt brute forcing.
FAILED_LOGIN_DELAY_SECONDS = 0.75

INVALID_CREDENTIALS_DETAIL = "Invalid username or password."
INVALID_KEY_DETAIL = "Invalid or missing access key."
MISSING_API_KEY_DETAIL = "Invalid or missing API key."


class SessionStore:
    def __init__(self, ttl_hours: int) -> None:
        self._ttl = ttl_hours * 3600
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        for token in [t for t, expiry in self._tokens.items() if expiry <= now]:
            del self._tokens[token]

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._tokens[token] = now + self._ttl
        return token

    def is_valid(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            expiry = self._tokens.get(token)
            return expiry is not None and expiry > now

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._tokens.pop(token, None)

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()


_sessions: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _sessions
    if _sessions is None:
        _sessions = SessionStore(get_settings().SESSION_TTL_HOURS)
    return _sessions


def reset_session_store() -> None:
    """Used by tests; also rebuilds the store with the current TTL setting."""
    global _sessions
    _sessions = None


async def authenticate(username: str, password: str, settings: Settings) -> str:
    """Return a fresh session token, or raise 401 after a constant delay.

    Both fields are compared with `secrets.compare_digest`, and both are
    always compared, so a wrong username and a wrong password take the same
    path.
    """
    username_ok = secrets.compare_digest(username, settings.ADMIN_USERNAME)
    password_ok = secrets.compare_digest(password, settings.ADMIN_PASSWORD)
    if not (username_ok and password_ok):
        await asyncio.sleep(FAILED_LOGIN_DELAY_SECONDS)
        logger.warning("failed login attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS_DETAIL
        )
    return get_session_store().issue()


def _origin_of(url_like: str) -> str:
    parts = urlsplit(url_like)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}".rstrip("/")


def enforce_api_key(request: Request, settings: Settings) -> None:
    """In prod, callers outside ALLOWED_ORIGINS must present X-API-Key.

    The frontend is deployed as its own Render service on its own origin, so
    every browser call to this API is cross-origin and always carries an
    `Origin` header. That makes the check simple: an `Origin` listed in
    ALLOWED_ORIGINS is the app itself and passes; anything else — curl, a
    script, another site — has to carry the key.

    This is a nuisance gate, not a secret: see the README.
    """
    if not settings.is_prod:
        return

    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if origin and origin in settings.allowed_origins:
        return

    supplied = request.headers.get("x-api-key") or ""
    if supplied and secrets.compare_digest(supplied, settings.API_KEY):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=MISSING_API_KEY_DETAIL
    )


async def require_access_key(
    request: Request,
    x_access_key: str | None = Header(default=None, alias="X-Access-Key"),
) -> str:
    """Dependency for every /api route except /api/health and /api/login."""
    settings = get_settings()
    enforce_api_key(request, settings)
    if not get_session_store().is_valid(x_access_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_KEY_DETAIL
        )
    return x_access_key or ""
