"""
Image resizing utility.
Resizes an image so its file size does not exceed RESIZE_MAX_KB.
Works by iteratively reducing JPEG quality / dimensions.
"""

from __future__ import annotations
import io
import logging
from PIL import Image

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
        # Already small enough — convert to JPEG once and return
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

    # Fallback — return best attempt at lowest quality
    buf = io.BytesIO()
    img.resize((int(w * 0.3), int(h * 0.3)), Image.LANCZOS).save(buf, format="JPEG", quality=20)
    data = buf.getvalue()
    logger.warning(f"Could not reach target size. Final: {len(data)} bytes")
    return data, original_size, len(data)
