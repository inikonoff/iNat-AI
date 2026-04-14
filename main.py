"""
main.py — Entry point for Insect Bot.

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

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ── Flask thread ──────────────────────────────────────────────

def run_flask():
    from admin.app import app
    logger.info(f"Starting Flask admin on port {PORT}")
    # Use threaded=True so Flask can handle concurrent requests
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True, use_reloader=False)


# ── Telegram bot ──────────────────────────────────────────────

def build_application() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("favorites", cmd_favorites))
    app.add_handler(CommandHandler("search", cmd_search))

    # Photo
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Unsupported types — stickers, documents, voice, video, etc.
    app.add_handler(MessageHandler(
        filters.Sticker.ALL | filters.Document.ALL |
        filters.VOICE | filters.VIDEO | filters.AUDIO,
        handle_unsupported,
    ))

    # Callback buttons (inline keyboard)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Text buttons (reply keyboard)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app


# ── SIGTERM handler (Render.com sends SIGTERM before killing) ──

_shutdown_event = threading.Event()


def handle_sigterm(signum, frame):
    logger.info("SIGTERM received — initiating graceful shutdown")
    _shutdown_event.set()


# ── Main ──────────────────────────────────────────────────────

def main():
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    # Start Flask in daemon thread
    flask_thread = threading.Thread(target=run_flask, daemon=True, name="flask-admin")
    flask_thread.start()
    logger.info("Flask admin thread started")

    # Build and run Telegram bot
    telegram_app = build_application()
    logger.info("Starting Telegram bot polling")

    # run_polling blocks until stop is called
    telegram_app.run_polling(
        allowed_updates=["message", "callback_query"],
        stop_signals=[signal.SIGTERM, signal.SIGINT],
        close_loop=False,
    )

    logger.info("Bot stopped cleanly")


if __name__ == "__main__":
    main()
