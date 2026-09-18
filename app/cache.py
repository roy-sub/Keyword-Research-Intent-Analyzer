"""TTL + LRU cache for completed analysis runs.

Keyed on the normalised topic plus the language/country the run used, so the
same topic collected for a different market is a separate entry.

V1 limitation: in-process. Cache contents are lost on restart or redeploy.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any


def normalise_topic(topic: str) -> str:
    return " ".join(topic.split()).casefold()


class TTLCache:
    def __init__(self, ttl_minutes: int, max_entries: int = 50) -> None:
        self._ttl = ttl_minutes * 60
        self._max = max_entries
        self._data: OrderedDict[tuple[str, str, str], tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(topic: str, lang: str, country: str) -> tuple[str, str, str]:
        return (normalise_topic(topic), lang.lower(), country.lower())

    def get(self, key: tuple[str, str, str]) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            stored_at, value = entry
            if now - stored_at > self._ttl:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: tuple[str, str, str], value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
