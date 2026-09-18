"""Global sliding-window search quota.

Deliberately global rather than per-IP: there is one shared login, so the
limit protects the upstream endpoints, not individual users.

V1 limitation: state is in-process. A single Render instance is assumed, and
the quota resets on restart or redeploy. Moving to more than one instance
means moving this to Redis.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, max_searches: int, window_minutes: int) -> None:
        self._max = max_searches
        self._window = window_minutes * 60
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    @property
    def window_minutes(self) -> int:
        return self._window // 60

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._hits and self._hits[0] <= cutoff:
            self._hits.popleft()

    def snapshot(self) -> tuple[int, int]:
        """(searches_remaining, retry_after_seconds).

        `retry_after_seconds` is 0 whenever quota is available.
        """
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            remaining = max(0, self._max - len(self._hits))
            if remaining > 0 or not self._hits:
                return remaining, 0
            retry_after = int(max(1, round(self._hits[0] + self._window - now)))
            return 0, retry_after

    def try_consume(self) -> tuple[bool, int, int]:
        """Consume one unit if available.

        Returns (allowed, searches_remaining, retry_after_seconds).
        """
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if len(self._hits) >= self._max:
                retry_after = int(max(1, round(self._hits[0] + self._window - now)))
                return False, 0, retry_after
            self._hits.append(now)
            return True, max(0, self._max - len(self._hits)), 0

    def refund(self) -> None:
        """Give back the most recent unit.

        Used when a run fails before any Google request succeeded — a run that
        never reached the upstream should not cost quota.
        """
        with self._lock:
            if self._hits:
                self._hits.pop()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
