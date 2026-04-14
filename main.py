"""
main.py — Entry point for Nature ID Bot.

Starts:
  1. Flask admin panel + /health endpoint (background thread)
  2. Telegram bot (main thread, blocking)

Handles SIGTERM gracefully (important for Render.com).
"""

from __future__ import annotations
import logging
import signal
import sys
import threading

from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from config import TELEGRAM_BOT_TOKEN, PORT
from bot.handlers import (
    cmd_start, cmd_help, cmd_stats, cmd_favorites, cmd_search,
    handle_photo, handle_callback, handle_text, handle_unsupported,
)


# ── Логгер с фильтром токена ──────────────────────────────────

class _TokenFilter(logging.Filter):
    """Скрывает токен бота из всех логов."""
    def __init__(self, token: str):
        super().__init__()
        self._token = token

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = str(record.msg).replace(self._token, "<TOKEN>")
        if record.args:
            try:
                record.args = tuple(
                    str(a).replace(self._token, "<TOKEN>") for a in record.args
                )
            except Exception:
                pass
        return True


logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Применяем фильтр ко всем логгерам которые могут светить URL
_token_filter = _TokenFilter(TELEGRAM_BOT_TOKEN)
for _name in ("httpx", "telegram", "telegram.ext", "__main__"):
    logging.getLogger(_name).addFilter(_token_filter)


# ── Flask thread ──────────────────────────────────────────────

def run_flask():
    from admin.app import app
    logger.info(f"Starting Flask admin on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True, use_reloader=False)


# ── Telegram bot ──────────────────────────────────────────────

def build_application() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CommandHandler("favorites", cmd_favorites))
    app.add_handler(CommandHandler("search",    cmd_search))

    # Фото (сжатое Telegram'ом) — основной способ отправки
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Документ-изображение (отправлено как файл без сжатия)
    # Определяем по MIME-типу, а не по способу загрузки
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_photo))

    # Неподдерживаемые типы — всё остальное кроме изображений
    app.add_handler(MessageHandler(
        (filters.Document.ALL & ~filters.Document.IMAGE) |
        filters.Sticker.ALL |
        filters.VOICE |
        filters.VIDEO |
        filters.AUDIO,
        handle_unsupported,
    ))

    # Callback кнопки (inline keyboard)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Текстовые кнопки (reply keyboard) и обычные сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app


# ── SIGTERM handler ───────────────────────────────────────────

def handle_sigterm(signum, frame):
    logger.info("SIGTERM received — initiating graceful shutdown")


# ── Main ──────────────────────────────────────────────────────

def main():
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT,  handle_sigterm)

    # Start Flask in daemon thread
    flask_thread = threading.Thread(target=run_flask, daemon=True, name="flask-admin")
    flask_thread.start()
    logger.info("Flask admin thread started")

    # Build and run Telegram bot
    telegram_app = build_application()
    logger.info("Starting Telegram bot polling")

    telegram_app.run_polling(
        allowed_updates=["message", "callback_query"],
        stop_signals=[signal.SIGTERM, signal.SIGINT],
        close_loop=False,
    )

    logger.info("Bot stopped cleanly")


if __name__ == "__main__":
    main()
