import logging
from datetime import time
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from curl_request import CurlRequest

from config import TELEGRAM_BOT_TOKEN, TIMEZONE
from database import init_db
from handlers.client import (
    cmd_start, cmd_help, cmd_info, cmd_slots, cmd_my,
    make_book_handler, handle_text,
    cmd_cancel_booking, cb_cancel_booking,
)
from handlers.trainer import (
    cmd_today, cmd_tomorrow, cmd_income, cmd_free,
    cmd_confirm, cmd_paid_cash, cmd_paid_direct, cmd_no_pay,
    cmd_cancel_client, cmd_report,
)
from notifications import job_morning_slots, job_evening_report, job_reminders

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
TZ = pytz.timezone(TIMEZONE)


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок — логируем, не падаем."""
    from telegram.error import Conflict
    err = context.error
    if isinstance(err, Conflict):
        logger.warning("Conflict (другой экземпляр бота): %s", err)
    else:
        logger.error("Unhandled error: %s", err, exc_info=err)


async def post_init(app: Application):
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("start",  "Начать работу с ботом"),
        BotCommand("slots",  "Свободные места на неделю"),
        BotCommand("book",   "Записаться на тренировку"),
        BotCommand("my",     "Мои записи"),
        BotCommand("cancel", "Отменить запись"),
        BotCommand("info",   "Цены и адрес бассейна"),
        BotCommand("help",   "Как пользоваться ботом"),
    ])

    jq = app.job_queue

    # 08:00 МСК — свободные слоты в канале
    jq.run_daily(job_morning_slots, time=time(8, 0, tzinfo=TZ), name="morning_slots")

    # 21:00 МСК — вечерний отчёт тренеру
    jq.run_daily(job_evening_report, time=time(21, 0, tzinfo=TZ), name="evening_report")

    # Каждые 15 минут — напоминания клиентам
    jq.run_repeating(job_reminders, interval=900, first=60, name="reminders")

    logger.info("Scheduled jobs registered")


def main():
    init_db()
    logger.info("Database initialized")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(CurlRequest())
        .get_updates_request(CurlRequest())
        .post_init(post_init)
        .build()
    )

    # ── Клиентские обработчики ────────────────────────────────────────────────
    app.add_handler(make_book_handler())                                # /book (диалог)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("info", cmd_info))
    app.add_handler(CommandHandler("slots", cmd_slots))
    app.add_handler(CommandHandler("my", cmd_my))
    app.add_handler(CommandHandler("cancel", cmd_cancel_booking))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(cb_cancel_booking, pattern=r"^cxl_"))

    # ── Команды тренера ───────────────────────────────────────────────────────
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("income", cmd_income))
    app.add_handler(CommandHandler("free", cmd_free))
    app.add_handler(CommandHandler("confirm", cmd_confirm))
    app.add_handler(CommandHandler("paid_cash", cmd_paid_cash))
    app.add_handler(CommandHandler("paid_direct", cmd_paid_direct))
    app.add_handler(CommandHandler("no_pay", cmd_no_pay))
    app.add_handler(CommandHandler("cancel_client", cmd_cancel_client))
    app.add_handler(CommandHandler("report", cmd_report))

    # ── Свободный текст → Claude ──────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(error_handler)

    logger.info("Bot started, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
