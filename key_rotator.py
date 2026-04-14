"""
Key rotator — round-robin pool with automatic skip on 429/error.
Thread-safe. Shared between bot and admin panel.
"""

from __future__ import annotations
import logging
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class KeyEntry:
    key: str
    failed_until: float = 0.0   # epoch time after which key is usable again
    use_count: int = 0

    @property
    def is_available(self) -> bool:
        return time.time() >= self.failed_until


class KeyRotator:
    """
    Usage:
        rotator = KeyRotator(["key1", "key2", "key3"])
        key = rotator.get()
        try:
            # ... use key ...
        except RateLimitError:
            rotator.mark_limited(key, cooldown=60)
        except Exception:
            rotator.mark_failed(key, cooldown=10)
    """

    RATE_LIMIT_COOLDOWN = 65    # seconds to back off after 429
    ERROR_COOLDOWN = 10         # seconds to back off after other error

    def __init__(self, keys: list[str], service: str = "unknown"):
        self.service = service
        self._lock = threading.Lock()
        self._entries: list[KeyEntry] = [KeyEntry(k) for k in keys if k]
        if not self._entries:
            raise ValueError(f"No API keys provided for service '{service}'")
        self._index = 0
        logger.info(f"[{service}] KeyRotator initialized with {len(self._entries)} key(s)")

    def get(self) -> str:
        with self._lock:
            n = len(self._entries)
            for _ in range(n):
                entry = self._entries[self._index % n]
                self._index += 1
                if entry.is_available:
                    entry.use_count += 1
                    return entry.key
            # All keys are on cooldown — return least-blocked one anyway
            best = min(self._entries, key=lambda e: e.failed_until)
            wait = max(0.0, best.failed_until - time.time())
            if wait > 0:
                logger.warning(f"[{self.service}] All keys on cooldown. Waiting {wait:.1f}s")
                time.sleep(wait)
            best.use_count += 1
            return best.key

    def mark_limited(self, key: str, cooldown: float | None = None) -> None:
        cd = cooldown or self.RATE_LIMIT_COOLDOWN
        self._mark(key, cd, "rate-limited")

    def mark_failed(self, key: str, cooldown: float | None = None) -> None:
        cd = cooldown or self.ERROR_COOLDOWN
        self._mark(key, cd, "error")

    def _mark(self, key: str, cooldown: float, reason: str) -> None:
        with self._lock:
            for entry in self._entries:
                if entry.key == key:
                    entry.failed_until = time.time() + cooldown
                    logger.warning(f"[{self.service}] Key ...{key[-6:]} marked {reason} for {cooldown}s")
                    return

    def status(self) -> list[dict]:
        now = time.time()
        with self._lock:
            return [
                {
                    "key_hint": f"...{e.key[-6:]}",
                    "available": e.is_available,
                    "cooldown_remaining": max(0.0, round(e.failed_until - now, 1)),
                    "use_count": e.use_count,
                }
                for e in self._entries
                ]
            
