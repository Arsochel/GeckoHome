import asyncio
import json
from datetime import datetime

from telegram import BotCommand
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from config import TELEGRAM_BOT_TOKEN
from database import init_db, load_last_feeding
from services import tuya
from bot.handlers import cmd_start, cmd_status, button_handler, message_handler

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
            print(f"[Bot] log server error: {e}")
        finally:
            writer.close()

    try:
        server = await asyncio.start_server(handle, "127.0.0.1", BOT_LOG_PORT)
        await server.serve_forever()
    except (asyncio.CancelledError, Exception):
        pass


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(lambda l, ctx: None if "Task was destroyed" in ctx.get("message", "") else l.default_exception_handler(ctx))
    loop.run_until_complete(init_db())
    loop.run_until_complete(load_last_feeding())
    async def _error_handler(update, context):
        if isinstance(context.error, (NetworkError, TimedOut)):
            print(f"[Bot] network hiccup: {context.error}")
        else:
            import traceback
            print(f"[Bot] error: {context.error}\n{''.join(traceback.format_exception(context.error))}")

    async def _post_init(application):
        asyncio.create_task(_log_server())
        await application.bot.set_my_commands([
            BotCommand("start", "Главное меню"),
            BotCommand("status", "Статус террариума"),
        ])

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(True).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_error_handler(_error_handler)

    import atexit
    atexit.register(lambda: print("[Bot] stopped"))

    print("[Bot] started")
    app.run_polling()


if __name__ == "__main__":
    main()
