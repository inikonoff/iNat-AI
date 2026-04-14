"""
iNaturalist API wrapper.
Handles CV scoring, taxa search, observations.

Authentication flow:
  Режим 1 — JWT напрямую (текущий):
    INAT_API_KEY содержит JWT (eyJ...) — используется напрямую.
    JWT живёт 24 часа. Бот логирует предупреждение за час до истечения.
    Обновление — вручную: зайти на inaturalist.org/users/api_token
    и обновить переменную на Render.com.

  Режим 2 — OAuth токен (когда будет доступен):
    INAT_API_KEY содержит OAuth токен — JWT получается и обновляется
    автоматически каждые 23 часа.

Key rotation: при 429 переключаемся на следующий ключ.
"""

from __future__ import annotations
import logging
import threading
import time
from typing import Any

import httpx

from config import INAT_API_KEYS, INAT_CV_URL, INAT_TAXA_URL, INAT_OBS_URL
from utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)

# ── Константы ─────────────────────────────────────────────────

TIMEOUT = 20.0
APP_NAME = "InsectIDBot"
CONTACT  = "github.com/insectidbot"

USER_AGENT = f"{APP_NAME}/1.0 ({CONTACT})"

JWT_TOKEN_URL = "https://www.inaturalist.org/users/api_token"
JWT_LIFETIME  = 23 * 3600


def _is_jwt(token: str) -> bool:
    """JWT выглядит как три base64-блока разделённых точками: eyJ..."""
    parts = token.split(".")
    return len(parts) == 3 and token.startswith("eyJ")


# ── JWT кеш ───────────────────────────────────────────────────

class _JWTCache:
    """
    Хранит JWT для каждого ключа.
    Если ключ уже является JWT — использует его напрямую.
    Если ключ является OAuth токеном — обменивает на JWT автоматически.
    """

    def __init__(self):
        self._lock   = threading.Lock()
        self._tokens: dict[str, tuple[str, float]] = {}

    def get(self, key: str) -> str | None:
        # Если сам ключ — JWT, используем напрямую
        if _is_jwt(key):
            return self._use_jwt_directly(key)

        # Иначе — OAuth токен, получаем JWT через API
        with self._lock:
            entry = self._tokens.get(key)
            if entry and time.time() < entry[1]:
                return entry[0]
        return self._refresh_via_oauth(key)

    def _use_jwt_directly(self, jwt: str) -> str:
        """Используем JWT напрямую, логируем время до истечения."""
        with self._lock:
            entry = self._tokens.get(jwt)
            if entry:
                remaining = entry[1] - time.time()
                if remaining > 0:
                    if remaining < 3600:
                        logger.warning(
                            f"iNat JWT истекает менее чем через час! "
                            f"Обнови INAT_API_KEY на Render.com: "
                            f"зайди на inaturalist.org/users/api_token"
                        )
                    return jwt
                else:
                    logger.error(
                        "iNat JWT истёк! Зайди на inaturalist.org/users/api_token, "
                        "скопируй новый токен и обнови INAT_API_KEY на Render.com."
                    )
                    return jwt  # возвращаем всё равно — API сам вернёт 401

            # Первый раз — сохраняем с временем жизни 23 часа от сейчас
            expires_at = time.time() + JWT_LIFETIME
            self._tokens[jwt] = (jwt, expires_at)
            logger.info("iNat: JWT используется напрямую (срок ~23ч с момента запуска)")
            return jwt

    def _refresh_via_oauth(self, oauth_key: str) -> str | None:
        """Получаем JWT через OAuth токен."""
        try:
            resp = httpx.get(
                JWT_TOKEN_URL,
                headers={
                    "Authorization": f"Bearer {oauth_key}",
                    "User-Agent": USER_AGENT,
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            jwt = resp.json().get("api_token")
            if not jwt:
                logger.error("iNat JWT response missing api_token field")
                return None
            with self._lock:
                self._tokens[oauth_key] = (jwt, time.time() + JWT_LIFETIME)
            logger.info(f"iNat JWT refreshed via OAuth for key ...{oauth_key[-6:]}")
            return jwt
        except Exception as e:
            logger.error(f"iNat JWT refresh failed: {e}")
            return None

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._tokens.pop(key, None)

    def info(self, key: str) -> str:
        with self._lock:
            entry = self._tokens.get(key)
        if not entry:
            return "не инициализирован"
        remaining = max(0, int(entry[1] - time.time()))
        if remaining == 0:
            return "⚠️ истёк"
        return f"{remaining // 3600}h {(remaining % 3600) // 60}m"

    def info(self, oauth_key: str) -> str:
        with self._lock:
            entry = self._tokens.get(oauth_key)
        if not entry:
            return "не получен"
        remaining = max(0, int(entry[1] - time.time()))
        return f"{remaining // 3600}h {(remaining % 3600) // 60}m"


_jwt_cache = _JWTCache()
_rotator   = KeyRotator(INAT_API_KEYS, service="iNaturalist")


# ── Заголовки ─────────────────────────────────────────────────

def _auth_headers(oauth_key: str) -> dict:
    """Заголовки с JWT для защищённых эндпоинтов (CV)."""
    jwt = _jwt_cache.get(oauth_key)
    headers = {"User-Agent": USER_AGENT}
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"
    return headers


def _public_headers() -> dict:
    """Заголовки для публичных GET-запросов."""
    return {"User-Agent": USER_AGENT}


def _handle_rate_limit(oauth_key: str) -> None:
    _jwt_cache.invalidate(oauth_key)
    _rotator.mark_limited(oauth_key)


# ── CV: определение вида по фото ──────────────────────────────

def score_image(image_bytes: bytes) -> dict | None:
    """
    Отправляет фото в iNaturalist Computer Vision.
    Возвращает сырой dict ответа или None при ошибке.
    CV-эндпоинт требует JWT.
    """
    for _ in range(len(INAT_API_KEYS) + 1):
        oauth_key = _rotator.get()
        headers = _auth_headers(oauth_key)
        try:
            resp = httpx.post(
                INAT_CV_URL,
                headers=headers,
                files={"image": ("photo.jpg", image_bytes, "image/jpeg")},
                timeout=TIMEOUT,
            )
            if resp.status_code == 429:
                logger.warning("iNaturalist CV 429 — rotating key")
                _handle_rate_limit(oauth_key)
                continue
            if resp.status_code == 401:
                logger.warning("iNaturalist CV 401 — invalidating JWT and retrying")
                _jwt_cache.invalidate(oauth_key)
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            logger.error("iNaturalist CV timeout")
            _rotator.mark_failed(oauth_key, cooldown=5)
        except Exception as e:
            logger.error(f"iNaturalist CV error: {e}")
            _rotator.mark_failed(oauth_key)
    return None


# ── Парсинг результата CV ─────────────────────────────────────

def parse_top_result(response: dict, threshold: float = 0.40) -> dict | None:
    """
    Извлекает лучший результат из ответа CV.
    Возвращает dict с данными таксона или None если ниже порога.
    """
    results = response.get("results", [])
    if not results:
        return None
    top   = results[0]
    score = top.get("score", 0)
    if score < threshold:
        return None
    taxon = top.get("taxon", {})
    return {
        "taxon_id":          taxon.get("id"),
        "taxon_name":        taxon.get("name"),
        "taxon_common_name": taxon.get("preferred_common_name"),
        "taxon_rank":        taxon.get("rank"),
        "score":             round(score, 3),
        "wikipedia_url":     taxon.get("wikipedia_url"),
        "photo_url":         _get_photo_url(taxon),
        "ancestors":         [a.get("name") for a in taxon.get("ancestors", [])],
        "all_results": [
            {
                "name":   r.get("taxon", {}).get("name"),
                "common": r.get("taxon", {}).get("preferred_common_name"),
                "score":  round(r.get("score", 0), 3),
            }
            for r in results[:5]
        ],
    }


def _get_photo_url(taxon: dict) -> str | None:
    photos = taxon.get("taxon_photos") or []
    if photos:
        return photos[0].get("photo", {}).get("medium_url")
    default_photo = taxon.get("default_photo")
    if default_photo:
        return default_photo.get("medium_url")
    return None


# ── Публичные GET-запросы ─────────────────────────────────────

def search_taxa(query: str, per_page: int = 10) -> list[dict]:
    """Поиск таксонов по названию."""
    try:
        resp = httpx.get(
            INAT_TAXA_URL,
            params={
                "q":        query,
                "per_page": per_page,
                "rank":     "species,genus,family",
            },
            headers=_public_headers(),
            timeout=TIMEOUT,
        )
        if resp.status_code == 429:
            logger.warning("iNat search_taxa 429")
            return []
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [
            {
                "id":                 r.get("id"),
                "name":               r.get("name"),
                "common_name":        r.get("preferred_common_name"),
                "rank":               r.get("rank"),
                "observations_count": r.get("observations_count"),
                "wikipedia_url":      r.get("wikipedia_url"),
                "photo_url":          _get_photo_url(r),
            }
            for r in results
        ]
    except Exception as e:
        logger.error(f"search_taxa error: {e}")
        return []


def get_taxon_by_id(taxon_id: int) -> dict | None:
    """Получить таксон по ID."""
    try:
        resp = httpx.get(
            f"{INAT_TAXA_URL}/{taxon_id}",
            headers=_public_headers(),
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0] if results else None
    except Exception as e:
        logger.error(f"get_taxon_by_id error: {e}")
        return None


def get_observations(
    taxon_id: int,
    place_id: int | None = None,
    per_page: int = 200,          # iNat рекомендует использовать максимум
) -> list[dict]:
    """Получить последние наблюдения вида."""
    params: dict[str, Any] = {
        "taxon_id": taxon_id,
        "per_page": min(per_page, 200),
        "order":    "desc",
        "order_by": "created_at",
        "has[]":    "photos",
    }
    if place_id:
        params["place_id"] = place_id
    try:
        resp = httpx.get(
            INAT_OBS_URL,
            params=params,
            headers=_public_headers(),
            timeout=TIMEOUT,
        )
        if resp.status_code == 429:
            logger.warning("iNat get_observations 429")
            return []
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        logger.error(f"get_observations error: {e}")
        return []


# ── Статус ключей (для админки) ───────────────────────────────

def get_key_status() -> list[dict]:
    status = _rotator.status()
    for entry in status:
        hint = entry["key_hint"].lstrip(".")
        for oauth_key in INAT_API_KEYS:
            if oauth_key.endswith(hint):
                entry["jwt_expires_in"] = _jwt_cache.info(oauth_key)
                break
    return status
