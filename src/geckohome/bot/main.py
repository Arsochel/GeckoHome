import asyncio
import json
import logging
import signal
from datetime import datetime

from geckohome.logging_config import setup_logging
setup_logging()

log = logging.getLogger(__name__)

from telegram import BotCommand
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from geckohome.config import TELEGRAM_BOT_TOKEN
from geckohome.database import init_db, load_last_feeding
from geckohome.bot.handlers import cmd_start, cmd_status, button_handler, message_handler

BOT_LOG_PORT = 8765


async def _log_server():
    """Маленький HTTP сервер для получения событий от main.py."""
    async def handle(reader, writer):
        try:
            headers_raw = await reader.readuntil(b"\r\n\r\n")
            content_length = 0
            for line in headers_raw.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    content_length = int(line.split(b":", 1)[1].strip())
            body = await reader.readexactly(content_length)
            payload = json.loads(body)
            msg = payload.get("msg", "")
            if msg:
                print(msg)
        except Exception as e:
            log.error("log server error: %s", e)
        finally:
            writer.close()

    try:
        server = await asyncio.start_server(handle, "127.0.0.1", BOT_LOG_PORT)
        await server.serve_forever()
    except (asyncio.CancelledError, Exception):
        pass


async def main():
    await init_db()
    await load_last_feeding()
    from geckohome.services import tuya
    await tuya.warm_lamp_cache()
    await tuya.warm_sensor_cache()

    async def _error_handler(update, context):
        if isinstance(context.error, (NetworkError, TimedOut)):
            log.warning("network hiccup: %s", context.error)
        else:
            log.error("unhandled error", exc_info=context.error)

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .connect_timeout(10)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(10)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_error_handler(_error_handler)

    await app.initialize()

    await app.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("status", "Статус террариума"),
    ])

    stop_event = asyncio.Event()

    def _on_signal():
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            # Windows
            signal.signal(sig, lambda s, f: stop_event.set())

    await app.start()
    await app.updater.start_polling(
        drop_pending_updates=False,
        allowed_updates=["message", "callback_query"],
        timeout=20,
    )

    asyncio.create_task(_log_server())

    log.info("started")

    await stop_event.wait()

    log.info("stopping...")
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    log.info("stopped")


def run() -> None:
    """Console-script entry point (``geckohome-bot``)."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
