import asyncio

from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from config import TELEGRAM_BOT_TOKEN
from database import init_db
from services import tuya
from bot.handlers import cmd_start, cmd_status, button_handler, message_handler


def main():
    tuya.connect()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
