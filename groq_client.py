"""
Groq Vision wrapper with key rotation.
"""

from __future__ import annotations
import base64
import logging

from groq import Groq, RateLimitError

from config import GROQ_API_KEYS, GROQ_MODEL
from utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)

_rotator = KeyRotator(GROQ_API_KEYS, service="Groq")


SYSTEM_PROMPT = """Ты — эксперт-энтомолог и натуралист. Отвечаешь на русском языке.
Когда пользователь присылает фото насекомого, ты:
1. Если передана информация от iNaturalist — используй её как основу.
2. Даёшь краткое описание вида: внешний вид, размер, особенности.
3. Рассказываешь об образе жизни, питании, ареале.
4. Указываешь, опасен ли вид для человека или растений.
5. Добавляешь 1-2 интересных факта.
Отвечай структурированно, но живым языком. Не используй markdown-заголовки — только обычный текст с абзацами.
Если информация от iNaturalist не передана и ты сам не уверен в определении — честно скажи об этом."""


def describe_insect(
    image_bytes: bytes,
    inat_result: dict | None = None,
) -> str | None:
    """
    Ask Groq Vision to describe the insect.
    inat_result — parsed result from iNaturalist (may be None).
    Returns text response or None on failure.
    """
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    if inat_result:
        score_pct = int(inat_result["score"] * 100)
        hint = (
            f"iNaturalist определил насекомое как: {inat_result['taxon_name']}"
            + (f" ({inat_result['taxon_common_name']})" if inat_result.get("taxon_common_name") else "")
            + f", уверенность {score_pct}%."
            + f" Таксономический ранг: {inat_result.get('taxon_rank', 'неизвестен')}."
        )
        user_text = f"{hint}\n\nОпиши это насекомое подробно."
    else:
        user_text = "Определи, что изображено на фото. Если это насекомое — опиши его подробно. Если не можешь определить — скажи честно."

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                },
                {"type": "text", "text": user_text},
            ],
        }
    ]

    for attempt in range(len(GROQ_API_KEYS) + 1):
        key = _rotator.get()
        client = Groq(api_key=key)
        try:
            completion = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
                max_tokens=1024,
                temperature=0.4,
            )
            return completion.choices[0].message.content
        except RateLimitError:
            logger.warning("Groq RateLimit — rotating key")
            _rotator.mark_limited(key)
        except Exception as e:
            logger.error(f"Groq error: {e}")
            _rotator.mark_failed(key)
    return None


def get_key_status() -> list[dict]:
    return _rotator.status()
