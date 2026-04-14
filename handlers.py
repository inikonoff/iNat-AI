"""
Telegram bot handlers.
"""

from __future__ import annotations
import logging
import time
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import utils.db as db
from utils.image import resize_to_limit, extract_location
from utils.inat import score_image, parse_top_result
from utils.groq_client import describe_organism
from config import ADMIN_IDS, SCORE_THRESHOLD

logger = logging.getLogger(__name__)

# Антифлуд: минимальный интервал между запросами от одного пользователя (секунды)
FLOOD_INTERVAL = 10
# Порог уверенности для показа альтернатив
ALTERNATIVES_THRESHOLD = 0.70

# Словарь для хранения времени последнего запроса: {user_id: timestamp}
_last_request: dict[int, float] = {}


# ── Helpers ───────────────────────────────────────────────────

def main_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        ["📷 Отправь фото"],
        ["🔍 Поиск вида", "⭐ Избранное"],
        ["📊 Моя статистика", "ℹ️ Помощь"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def admin_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        ["📷 Отправь фото"],
        ["🔍 Поиск вида", "⭐ Избранное"],
        ["📊 Моя статистика", "ℹ️ Помощь"],
        ["🛠 Админ-панель"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_threshold() -> float:
    try:
        return float(db.get_setting("score_threshold", str(SCORE_THRESHOLD)))
    except Exception:
        return SCORE_THRESHOLD


def is_bot_active() -> bool:
    return db.get_setting("bot_active", "true").lower() == "true"


def check_flood(user_id: int) -> tuple[bool, int]:
    """
    Возвращает (разрешено, секунд_осталось).
    Обновляет время последнего запроса если разрешено.
    """
    now = time.time()
    last = _last_request.get(user_id, 0)
    elapsed = now - last
    if elapsed < FLOOD_INTERVAL:
        remaining = int(FLOOD_INTERVAL - elapsed) + 1
        return False, remaining
    _last_request[user_id] = now
    return True, 0


def build_alternatives_text(inat_result: dict) -> str:
    """Формирует строку с топ-3 альтернативами если уверенность ниже порога."""
    alts = inat_result.get("all_results", [])[1:3]  # 2-й и 3-й результаты
    if not alts:
        return ""
    lines = ["\n\n🔄 *Возможные альтернативы:*"]
    for r in alts:
        score_pct = int(r["score"] * 100)
        name = r.get("common") or r.get("name") or "—"
        latin = r.get("name", "")
        lines.append(f"• {name} _({latin})_ — {score_pct}%")
    return "\n".join(lines)


# ── Commands ──────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name, user.last_name)

    keyboard = admin_keyboard() if is_admin(user.id) else main_menu_keyboard()
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я помогу определить живой организм по фотографии — "
        "насекомое, растение, гриб, птицу, паука и многое другое.\n\n"
        "📷 Просто отправь фото — и я расскажу, что это за вид, "
        "чем питается, опасен ли и много интересного.\n\n"
        "Используй кнопки меню ниже 👇",
        reply_markup=keyboard,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🌿 *Nature ID Bot — помощь*\n\n"
        "*Основные команды:*\n"
        "/start — перезапустить бота\n"
        "/help — эта справка\n"
        "/search [название] — поиск вида\n"
        "/favorites — моё избранное\n"
        "/stats — моя статистика\n\n"
        "*Как пользоваться:*\n"
        "Отправь фото живого организма — насекомого, растения, гриба, "
        "птицы, паука — бот определит вид и расскажет о нём подробно.\n\n"
        "*Лимиты:*\n"
        "До 20 запросов в сутки (сбрасывается в полночь UTC).\n"
        "Не чаще одного фото в 10 секунд.\n\n"
        "Если есть вопросы — свяжись с администратором."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db_user = db.get_user(user.id)
    if not db_user:
        await update.message.reply_text("Сначала напиши /start")
        return

    used = db_user.get("requests_today", 0)
    limit = db_user.get("daily_limit", 20)
    total = (
        db.get_client()
        .table("requests")
        .select("id", count="exact")
        .eq("telegram_id", user.id)
        .execute()
        .count or 0
    )

    await update.message.reply_text(
        f"📊 *Твоя статистика*\n\n"
        f"Запросов сегодня: {used} / {limit}\n"
        f"Всего запросов: {total}\n"
        f"Дата регистрации: {str(db_user.get('created_at', ''))[:10]}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    favs = db.get_favorites(user.id)
    if not favs:
        await update.message.reply_text(
            "⭐ У тебя пока нет избранных видов.\n"
            "После определения нажми кнопку «В избранное»."
        )
        return

    lines = [f"⭐ *Избранные виды* ({len(favs)}):\n"]
    for i, f in enumerate(favs[:20], 1):
        name = f.get("taxon_common_name") or f.get("taxon_name", "—")
        latin = f.get("taxon_name", "")
        lines.append(f"{i}. {name} _({latin})_")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text(
            "Используй: /search [название]\nПример: /search Apis mellifera"
        )
        return

    from utils.inat import search_taxa
    await update.message.reply_text(f"🔍 Ищу «{query}»...")
    results = search_taxa(query, per_page=5)
    if not results:
        await update.message.reply_text("Ничего не найдено. Попробуй другое название.")
        return

    lines = [f"🔍 *Результаты поиска для «{query}»:*\n"]
    for r in results:
        name = r.get("common_name") or "—"
        latin = r.get("name", "")
        rank = r.get("rank", "")
        obs = r.get("observations_count", 0)
        wiki = r.get("wikipedia_url", "")
        line = f"• *{name}* _{latin}_ [{rank}]\n  Наблюдений: {obs:,}"
        if wiki:
            line += f"\n  [Wikipedia]({wiki})"
        lines.append(line)

    await update.message.reply_text(
        "\n\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


# ── Photo handler ─────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message

    # В группах реагируем только на фото с тегом #определи
    # В личке бот отвечает на любое фото
    if message.chat.type in ("group", "supergroup"):
        caption = (message.caption or "").lower()
        if "#определи" not in caption:
            return

    # Ensure user exists
    db.upsert_user(user.id, user.username, user.first_name, user.last_name)

    # Global bot_active check
    if not is_bot_active():
        await update.message.reply_text(
            "🔧 Бот временно на техническом обслуживании. Попробуй позже."
        )
        return

    # Ban check
    if db.is_banned(user.id):
        await update.message.reply_text("🚫 Ваш аккаунт заблокирован.")
        return

    # Антифлуд
    if not is_admin(user.id):
        allowed_flood, remaining = check_flood(user.id)
        if not allowed_flood:
            await update.message.reply_text(
                f"⏱ Подожди {remaining} сек. перед следующим запросом."
            )
            return

    # Daily limit check
    allowed, used, limit = db.check_and_increment_daily(user.id)
    if not allowed:
        await update.message.reply_text(
            f"⏳ Лимит на сегодня исчерпан ({used}/{limit} запросов).\n"
            "Лимит сбрасывается в полночь UTC."
        )
        return

    thinking_msg = await update.message.reply_text("🔬 Анализирую фото...")

    start_time = time.time()
    image_bytes: bytes | None = None
    size_before = size_after = 0

    try:
        # Download photo (best quality)
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        buf = BytesIO()
        await file.download_to_memory(buf)
        image_bytes = buf.getvalue()

        # Extract EXIF location before resize (resize strips EXIF)
        location = extract_location(image_bytes)

        # Resize
        image_bytes, size_before, size_after = resize_to_limit(image_bytes)

        # iNaturalist CV
        threshold = get_threshold()
        inat_response = score_image(image_bytes)
        inat_result = parse_top_result(inat_response, threshold) if inat_response else None

        # Groq Vision
        groq_text = describe_organism(image_bytes, inat_result, location)

        elapsed_ms = int((time.time() - start_time) * 1000)

        if groq_text is None:
            await thinking_msg.edit_text(
                "❌ Не удалось получить ответ от AI. Попробуй ещё раз."
            )
            db.log_request(
                user.id, None, None, None, None, None,
                size_before, size_after, elapsed_ms,
                success=False, error_text="Groq returned None",
            )
            return

        # ── Формируем заголовок ───────────────────────────────
        alternatives_text = ""

        if inat_result:
            score_pct = int(inat_result["score"] * 100)
            display_name = inat_result.get("taxon_common_name") or inat_result["taxon_name"]
            header = (
                f"🌿 *{display_name}*\n"
                f"_({inat_result['taxon_name']})_\n"
                f"Уверенность iNaturalist: {score_pct}%\n\n"
            )
            # Показываем альтернативы если уверенность невысокая
            if inat_result["score"] < ALTERNATIVES_THRESHOLD:
                alternatives_text = build_alternatives_text(inat_result)

        elif inat_response is None:
            # iNaturalist был недоступен — Groq работал самостоятельно
            header = (
                "🤖 *Распознано нейросетью*\n"
                "_iNaturalist был недоступен, результат может быть менее точным_\n\n"
            )
        else:
            # iNaturalist ответил, но уверенность ниже порога
            header = "🔍 *Вид не определён с достаточной уверенностью*\n\n"

        full_text = header + groq_text + alternatives_text

        # ── Inline кнопки ─────────────────────────────────────
        buttons = []
        if inat_result:
            if inat_result.get("wikipedia_url"):
                buttons.append(
                    InlineKeyboardButton("📖 Wikipedia", url=inat_result["wikipedia_url"])
                )
            buttons.append(
                InlineKeyboardButton(
                    "⭐ В избранное",
                    callback_data=f"fav:{inat_result['taxon_name']}:{inat_result.get('taxon_id', '')}",
                )
            )

        markup = InlineKeyboardMarkup([buttons]) if buttons else None

        await thinking_msg.edit_text(
            full_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=markup,
        )

        db.log_request(
            telegram_id=user.id,
            taxon_name=inat_result["taxon_name"] if inat_result else None,
            taxon_common_name=inat_result.get("taxon_common_name") if inat_result else None,
            taxon_id=inat_result.get("taxon_id") if inat_result else None,
            score=inat_result["score"] if inat_result else None,
            groq_response=groq_text[:2000],
            image_size_before=size_before,
            image_size_after=size_after,
            response_time_ms=elapsed_ms,
            success=True,
        )

    except Exception as e:
        logger.exception(f"handle_photo error: {e}")
        await thinking_msg.edit_text("❌ Произошла ошибка. Попробуй ещё раз.")
        elapsed_ms = int((time.time() - start_time) * 1000)
        db.log_request(
            user.id, None, None, None, None, None,
            size_before, size_after, elapsed_ms,
            success=False, error_text=str(e)[:500],
        )


# ── Unsupported message types ─────────────────────────────────

async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отвечает на стикеры, документы, голосовые и прочее."""
    await update.message.reply_text(
        "📷 Отправь фотографию — я определю вид живого организма на ней.\n"
        "Другие типы файлов не поддерживаются."
    )


# ── Callback: Add to favorites ────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = query.from_user

    data = query.data or ""

    if data.startswith("fav:"):
        parts = data.split(":", 2)
        taxon_name = parts[1] if len(parts) > 1 else ""
        taxon_id_str = parts[2] if len(parts) > 2 else ""

        if not taxon_name:
            await query.answer("Ошибка: нет данных о виде", show_alert=True)
            return

        db_req = (
            db.get_client()
            .table("requests")
            .select("taxon_common_name,taxon_rank")
            .eq("telegram_id", user.id)
            .eq("taxon_name", taxon_name)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        common = None
        rank = None
        if db_req.data:
            common = db_req.data[0].get("taxon_common_name")
            rank = db_req.data[0].get("taxon_rank")

        taxon_id = int(taxon_id_str) if taxon_id_str.isdigit() else None

        added = db.add_favorite(
            telegram_id=user.id,
            taxon_name=taxon_name,
            taxon_common_name=common,
            taxon_id=taxon_id,
            taxon_rank=rank,
            wikipedia_url=None,
            photo_url=None,
        )
        if added:
            await query.answer(f"⭐ {taxon_name} добавлен в избранное!", show_alert=False)
        else:
            await query.answer("Уже в избранном.", show_alert=False)


# ── Text button dispatcher ────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""

    if text == "ℹ️ Помощь":
        await cmd_help(update, context)
    elif text == "📊 Моя статистика":
        await cmd_stats(update, context)
    elif text == "⭐ Избранное":
        await cmd_favorites(update, context)
    elif text == "🔍 Поиск вида":
        await update.message.reply_text(
            "Используй команду: /search [название]\nПример: /search Apis mellifera"
        )
    elif text == "📷 Отправь фото":
        await update.message.reply_text("Отправь мне фотографию — я определю вид 📷")
    elif text == "🛠 Админ-панель" and is_admin(update.effective_user.id):
        from config import PORT
        await update.message.reply_text(
            f"Панель администратора доступна по адресу вашего сервера на порту {PORT}.\n"
            "Путь: /admin"
        )
    else:
        await update.message.reply_text(
            "Отправь фото или воспользуйся меню 👇",
            reply_markup=(
                admin_keyboard() if is_admin(update.effective_user.id)
                else main_menu_keyboard()
            ),
        )