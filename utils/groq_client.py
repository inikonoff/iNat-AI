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


SYSTEM_PROMPT = """Ты — эксперт-натуралист и профессиональный биолог. Твоя задача — идентифицировать организмы по фото и данным iNaturalist.

При получении данных:
1. Основной приоритет — информация от iNaturalist (используй научное название на латыни). Если данные противоречивы, опирайся на визуальные признаки.
2. В начале укажи русское название и в скобках латинское.
3. Описание: кратко опиши внешний вид, размеры и ключевые отличительные черты, по которым можно узнать этот вид.
4. Биология и среда: расскажи об образе жизни, рационе и типичных местах обитания.
5. Польза и риски: обязательно укажи статус (съедобно/ядовито), опасность для человека, инвазивность или охранный статус (Красная книга).
   ВАЖНО: Если вид потенциально опасен или ядовит, добавь предупреждение, что информация носит ознакомительный характер.
6. Факт: добавь 1-2 необычных факта, которые удивят пользователя.

Правила оформления:
- Пиши живым, увлекательным языком, как в хорошем полевом определителе.
- НЕ используй markdown-заголовки (никаких # или ##).
- Используй жирный шрифт для выделения ключевых терминов и эмодзи в начале абзацев для визуальной структуры.
- Если объект на фото — не живой организм или данных недостаточно для точного определения, прямо скажи об этом."""


def _build_hint(inat_result: dict, location: str | None) -> str:
    """Формирует подсказку для Groq на основе топ-3 результатов iNaturalist."""
    lines = ["Данные iNaturalist (топ результаты CV-модели):"]

    for i, r in enumerate(inat_result.get("all_results", [])[:3], 1):
        score_pct = int(r["score"] * 100)
        name = r["name"] or "—"
        common = f" ({r['common']})" if r.get("common") else ""
        lines.append(f"  {i}. {name}{common} — уверенность {score_pct}%")

    # Основной результат с доп. деталями
    rank = inat_result.get("taxon_rank", "неизвестен")
    lines.append(f"\nТаксономический ранг лучшего результата: {rank}.")

    if location:
        lines.append(f"Географическое положение съёмки: {location}. Учитывай это при определении.")

    lines.append("\nОпиши организм подробно согласно инструкции.")
    return "\n".join(lines)


def describe_organism(
    image_bytes: bytes,
    inat_result: dict | None = None,
    location: str | None = None,
) -> str | None:
    """
    Запрашивает у Groq Vision описание организма на фото.
    inat_result — parsed result from iNaturalist (may be None).
    location    — строка с координатами или названием места (may be None).
    Returns text response or None on failure.
    """
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    if inat_result:
        user_text = _build_hint(inat_result, location)
    else:
        geo = f" Географическое положение: {location}." if location else ""
        user_text = (
            f"Определи, что изображено на фото.{geo} "
            "Опиши организм подробно согласно инструкции. "
            "Если не можешь определить — скажи честно."
        )

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

    for _ in range(len(GROQ_API_KEYS) + 1):
        key = _rotator.get()
        client = Groq(api_key=key)
        try:
            completion = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
                max_tokens=1200,
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
