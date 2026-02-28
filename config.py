import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

_admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in _admin_ids_raw.split(",") if x.strip().isdigit()
]

# ── Groq ──────────────────────────────────────────────────────
def _collect_keys(base_name: str) -> list[str]:
    """Collect key + optional numbered backups: KEY, KEY_2, KEY_3 ..."""
    keys = []
    base = os.getenv(base_name, "").strip()
    if base:
        keys.append(base)
    i = 2
    while True:
        k = os.getenv(f"{base_name}_{i}", "").strip()
        if not k:
            break
        keys.append(k)
        i += 1
    return keys

GROQ_API_KEYS: list[str] = _collect_keys("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# ── iNaturalist ───────────────────────────────────────────────
INAT_API_KEYS: list[str] = _collect_keys("INAT_API_KEY")
INAT_CV_URL = "https://api.inaturalist.org/v1/computervision/score_image"
INAT_TAXA_URL = "https://api.inaturalist.org/v1/taxa"
INAT_OBS_URL = "https://api.inaturalist.org/v1/observations"

# ── Supabase ──────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# ── Admin panel ───────────────────────────────────────────────
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "change_me_in_production")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

# ── Bot behaviour ─────────────────────────────────────────────
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.40"))
RESIZE_MAX_KB = int(os.getenv("RESIZE_MAX_KB", "500"))
DAILY_LIMIT_DEFAULT = int(os.getenv("DAILY_LIMIT_DEFAULT", "20"))

# ── Server ────────────────────────────────────────────────────
PORT = int(os.getenv("PORT", "8080"))
ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
