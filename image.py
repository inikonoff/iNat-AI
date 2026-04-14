"""
Image resizing utility + EXIF location extraction.
Resizes an image so its file size does not exceed RESIZE_MAX_KB.
Works by iteratively reducing JPEG quality / dimensions.
"""

from __future__ import annotations
import io
import logging
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

from config import RESIZE_MAX_KB

logger = logging.getLogger(__name__)

MAX_BYTES = RESIZE_MAX_KB * 1024


def resize_to_limit(image_bytes: bytes, max_kb: int | None = None) -> tuple[bytes, int, int]:
    """
    Returns (resized_bytes, original_size, final_size).
    Keeps aspect ratio. Output is always JPEG.
    """
    max_bytes = (max_kb * 1024) if max_kb else MAX_BYTES
    original_size = len(image_bytes)

    if original_size <= max_bytes:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        result = buf.getvalue()
        return result, original_size, len(result)

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size

    # Step 1: Try reducing JPEG quality first (faster, no resampling)
    for quality in range(85, 20, -5):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            logger.debug(f"Resized via quality={quality}: {original_size} → {len(data)} bytes")
            return data, original_size, len(data)

    # Step 2: Also reduce dimensions
    scale = 0.9
    while scale > 0.2:
        new_w = int(w * scale)
        new_h = int(h * scale)
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        for quality in range(85, 20, -10):
            buf = io.BytesIO()
            resized.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= max_bytes:
                logger.debug(f"Resized via scale={scale:.1f} q={quality}: {original_size} → {len(data)} bytes")
                return data, original_size, len(data)
        scale -= 0.1

    # Fallback
    buf = io.BytesIO()
    img.resize((int(w * 0.3), int(h * 0.3)), Image.LANCZOS).save(buf, format="JPEG", quality=20)
    data = buf.getvalue()
    logger.warning(f"Could not reach target size. Final: {len(data)} bytes")
    return data, original_size, len(data)


# ── EXIF геолокация ───────────────────────────────────────────

def extract_location(image_bytes: bytes) -> str | None:
    """
    Извлекает GPS-координаты из EXIF и возвращает строку вида
    "55.7558° N, 37.6173° E" или None если геотег отсутствует.
    Большинство фото из Telegram не содержат EXIF — это нормально.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        exif_data = img._getexif()
        if not exif_data:
            return None

        # Найти тег GPSInfo
        gps_info_raw = None
        for tag_id, value in exif_data.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag == "GPSInfo":
                gps_info_raw = value
                break

        if not gps_info_raw:
            return None

        # Декодировать GPS теги
        gps = {}
        for key, val in gps_info_raw.items():
            gps[GPSTAGS.get(key, key)] = val

        lat = _dms_to_decimal(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
        lon = _dms_to_decimal(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))

        if lat is None or lon is None:
            return None

        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"
        return f"{abs(lat):.4f}° {lat_dir}, {abs(lon):.4f}° {lon_dir}"

    except Exception as e:
        logger.debug(f"EXIF extraction failed (normal for most photos): {e}")
        return None


def _dms_to_decimal(dms, ref) -> float | None:
    """Конвертирует градусы/минуты/секунды в десятичные градусы."""
    if not dms or not ref:
        return None
    try:
        degrees = float(dms[0])
        minutes = float(dms[1])
        seconds = float(dms[2])
        decimal = degrees + minutes / 60 + seconds / 3600
        if ref in ("S", "W"):
            decimal = -decimal
        return decimal
    except Exception:
        return None
