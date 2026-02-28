"""
Database helpers — thin wrappers around Supabase client.
All functions are synchronous (called from async bot via run_in_executor
where needed, or directly from Flask admin).
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_SERVICE_KEY, DAILY_LIMIT_DEFAULT

logger = logging.getLogger(__name__)

_client: Optional[Client] = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


# ── Users ─────────────────────────────────────────────────────

def upsert_user(telegram_id: int, username: str | None,
                first_name: str | None, last_name: str | None) -> dict:
    db = get_client()
    result = db.rpc("upsert_user", {
        "p_telegram_id": telegram_id,
        "p_username": username or "",
        "p_first_name": first_name or "",
        "p_last_name": last_name or "",
    }).execute()
    return result.data


def get_user(telegram_id: int) -> dict | None:
    db = get_client()
    result = db.table("users").select("*").eq("telegram_id", telegram_id).execute()
    return result.data[0] if result.data else None


def is_banned(telegram_id: int) -> bool:
    user = get_user(telegram_id)
    return user.get("is_banned", False) if user else False


def check_and_increment_daily(telegram_id: int) -> tuple[bool, int, int]:
    """
    Returns (allowed, used_today, daily_limit).
    Resets counter if a new UTC day has started.
    """
    db = get_client()
    user = get_user(telegram_id)
    if not user:
        return False, 0, 0

    limit = user.get("daily_limit", DAILY_LIMIT_DEFAULT)
    used = user.get("requests_today", 0)
    reset_at = user.get("limit_reset_at")

    # Check if we need to reset
    if reset_at:
        reset_dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if now.date() > reset_dt.date():
            db.table("users").update(
                {"requests_today": 0, "limit_reset_at": now.isoformat()}
            ).eq("telegram_id", telegram_id).execute()
            used = 0

    if used >= limit:
        return False, used, limit

    db.table("users").update(
        {"requests_today": used + 1, "last_active_at": datetime.now(timezone.utc).isoformat()}
    ).eq("telegram_id", telegram_id).execute()

    return True, used + 1, limit


def set_user_ban(telegram_id: int, banned: bool) -> None:
    get_client().table("users").update({"is_banned": banned}).eq("telegram_id", telegram_id).execute()


def set_user_limit(telegram_id: int, limit: int) -> None:
    get_client().table("users").update({"daily_limit": limit}).eq("telegram_id", telegram_id).execute()


def get_all_users(limit: int = 200, offset: int = 0) -> list[dict]:
    db = get_client()
    result = db.table("users").select("*").order("last_active_at", desc=True).range(offset, offset + limit - 1).execute()
    return result.data or []


def count_users() -> int:
    db = get_client()
    result = db.table("users").select("id", count="exact").execute()
    return result.count or 0


# ── Requests ──────────────────────────────────────────────────

def log_request(
    telegram_id: int,
    taxon_name: str | None,
    taxon_common_name: str | None,
    taxon_id: int | None,
    score: float | None,
    groq_response: str | None,
    image_size_before: int | None,
    image_size_after: int | None,
    response_time_ms: int | None,
    success: bool = True,
    error_text: str | None = None,
) -> None:
    try:
        get_client().table("requests").insert({
            "telegram_id": telegram_id,
            "taxon_name": taxon_name,
            "taxon_common_name": taxon_common_name,
            "taxon_id": taxon_id,
            "score": score,
            "groq_response": groq_response,
            "image_size_before": image_size_before,
            "image_size_after": image_size_after,
            "response_time_ms": response_time_ms,
            "success": success,
            "error_text": error_text,
        }).execute()
    except Exception as e:
        logger.error(f"Failed to log request: {e}")


def get_stats() -> dict:
    db = get_client()
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    today = (now - timedelta(hours=24)).isoformat()
    week = (now - timedelta(days=7)).isoformat()

    total_req = db.table("requests").select("id", count="exact").execute().count or 0
    today_req = db.table("requests").select("id", count="exact").gte("created_at", today).execute().count or 0
    week_req = db.table("requests").select("id", count="exact").gte("created_at", week).execute().count or 0
    total_users = count_users()
    errors = db.table("requests").select("id", count="exact").eq("success", False).execute().count or 0

    # Top taxa
    top_taxa_raw = db.table("requests").select("taxon_name").not_.is_("taxon_name", "null").gte("created_at", week).execute().data or []
    from collections import Counter
    taxa_counter = Counter(r["taxon_name"] for r in top_taxa_raw)
    top_taxa = taxa_counter.most_common(10)

    return {
        "total_requests": total_req,
        "requests_today": today_req,
        "requests_week": week_req,
        "total_users": total_users,
        "errors": errors,
        "top_taxa": top_taxa,
    }


def get_recent_requests(limit: int = 50) -> list[dict]:
    db = get_client()
    result = db.table("requests").select("*, users(username, first_name)").order("created_at", desc=True).limit(limit).execute()
    return result.data or []


# ── Favorites ─────────────────────────────────────────────────

def add_favorite(telegram_id: int, taxon_name: str, taxon_common_name: str | None,
                 taxon_id: int | None, taxon_rank: str | None,
                 wikipedia_url: str | None, photo_url: str | None) -> bool:
    try:
        get_client().table("favorites").insert({
            "telegram_id": telegram_id,
            "taxon_id": taxon_id,
            "taxon_name": taxon_name,
            "taxon_common_name": taxon_common_name,
            "taxon_rank": taxon_rank,
            "wikipedia_url": wikipedia_url,
            "photo_url": photo_url,
        }).execute()
        return True
    except Exception:
        return False  # unique constraint — already saved


def remove_favorite(telegram_id: int, taxon_name: str) -> None:
    get_client().table("favorites").delete().eq("telegram_id", telegram_id).eq("taxon_name", taxon_name).execute()


def get_favorites(telegram_id: int) -> list[dict]:
    db = get_client()
    result = db.table("favorites").select("*").eq("telegram_id", telegram_id).order("added_at", desc=True).execute()
    return result.data or []


# ── Settings ──────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    db = get_client()
    result = db.table("settings").select("value").eq("key", key).execute()
    return result.data[0]["value"] if result.data else default


def set_setting(key: str, value: str) -> None:
    get_client().table("settings").upsert(
        {"key": key, "value": value, "updated_at": datetime.now(timezone.utc).isoformat()}
    ).execute()


def get_all_settings() -> dict:
    db = get_client()
    result = db.table("settings").select("*").execute()
    return {r["key"]: r for r in (result.data or [])}
