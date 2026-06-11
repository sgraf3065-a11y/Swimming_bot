import logging
from datetime import datetime, timedelta, date as date_type
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler,
    CommandHandler, MessageHandler, CallbackQueryHandler, filters,
)
from config import TRAINER_CHAT_ID, TIMEZONE
from calendar_service import get_free_slots, create_booking, cancel_event
from claude_service import chat, parse_booking, clean_response
from database import save_booking, upsert_client, get_client_bookings, cancel_booking

logger = logging.getLogger(__name__)
TZ = pytz.timezone(TIMEZONE)

# Состояния ConversationHandler
CHOOSING_DATE, CHOOSING_TIME, ENTERING_NAME, ENTERING_TYPE = range(4)

RU_DAYS = {
    "Monday": "Пн", "Tuesday": "Вт", "Wednesday": "Ср",
    "Thursday": "Чт", "Friday": "Пт", "Saturday": "Сб", "Sunday": "Вс",
}


# ── Базовые команды ───────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_client(update.effective_user.id,
                  name=update.effective_user.full_name)
    await update.message.reply_text(
        f"Привет, {update.effective_user.first_name}! 👋\n\n"
        "Я помогаю записываться на тренировки к тренеру Сергею по плаванию в СК Раменки.\n\n"
        "Что хотите сделать?\n\n"
        "📅 /slots — свободные места на неделю\n"
        "✍️ /book — записаться на тренировку\n"
        "📋 /my — мои записи\n"
        "❌ /cancel — отменить запись\n"
        "ℹ️ /info — цены и адрес\n"
        "❓ /help — помощь\n\n"
        "Или просто напишите свой вопрос — я отвечу! 🏊"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Как пользоваться ботом:\n\n"
        "1️⃣ Нажмите /slots — увидите свободные часы\n"
        "2️⃣ Нажмите /book — запишитесь на удобное время\n"
        "3️⃣ После записи придёт подтверждение\n"
        "4️⃣ За день и за 2 часа до тренировки бот пришлёт напоминание\n"
        "5️⃣ Чтобы отменить — нажмите /cancel\n\n"
        "📋 /my — посмотреть свои записи\n"
        "ℹ️ /info — цены и адрес бассейна\n\n"
        "Есть вопрос? Просто напишите его — я отвечу, или передам тренеру Сергею."
    )


async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏊 Тренер Сергей Козулин | СК Раменки\n\n"
        "💰 Стоимость занятий:\n"
        "• Персональная 45 мин — 3 300 ₽ разово\n"
        "• Абонемент от 4 занятий — 3 000 ₽/занятие\n"
        "• Детская персональная — 2 600 ₽\n"
        "• Групповая — 1 200 ₽ разово / 900 ₽ абонемент\n"
        "• Сплит 2 взрослых — 5 600 ₽ с пары\n"
        "• Сплит 2 детей — 4 400 ₽ с пары\n\n"
        "📍 Москва, ул. Раменки д.19 стр.1, СК Раменки\n"
        "📞 +7 (499) 444-14-78\n\n"
        "✍️ Записаться: /book"
    )


async def cmd_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Смотрю расписание... ⏳")
    today = datetime.now(TZ).date()
    lines: list[str] = []
    for i in range(7):
        d = today + timedelta(days=i)
        try:
            free = get_free_slots(d)
        except Exception as e:
            logger.error(f"get_free_slots({d}): {e}")
            continue
        if free:
            day = RU_DAYS.get(d.strftime("%A"), d.strftime("%A"))
            slots_str = ", ".join(s.strftime("%H:%M") for s, _ in free[:6])
            lines.append(f"{day} {d.strftime('%d.%m')}: {slots_str}")
    if lines:
        await update.message.reply_text("📅 Свободные места на неделю:\n\n" + "\n".join(lines))
    else:
        await update.message.reply_text(
            "На ближайшую неделю свободных мест нет.\n"
            "Напишите — уточним у тренера!"
        )


async def cmd_my(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bookings = get_client_bookings(update.effective_user.id)
    if not bookings:
        await update.message.reply_text(
            "У вас нет предстоящих записей.\n\nЗаписаться: /book"
        )
        return
    lines = []
    for b in bookings:
        dt = datetime.fromisoformat(b["start_time"])
        lines.append(f"📅 {dt.strftime('%d.%m.%Y в %H:%M')} — {b['client_name']}")
    await update.message.reply_text("Ваши записи:\n\n" + "\n".join(lines))


# ── Запись через ConversationHandler ─────────────────────────────────────────

async def cmd_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).date()
    buttons = []
    for i in range(7):
        d = today + timedelta(days=i)
        day = RU_DAYS.get(d.strftime("%A"), "")
        label = f"{day} {d.strftime('%d.%m')}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"bdate_{d.isoformat()}")])
    await update.message.reply_text(
        "На какой день записаться?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOOSING_DATE


async def cb_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    d = date_type.fromisoformat(query.data.removeprefix("bdate_"))
    context.user_data["book_date"] = d
    await query.edit_message_text("Ищу слоты... ⏳")
    try:
        free = get_free_slots(d)
    except Exception as e:
        logger.error(e)
        await query.edit_message_text("Не удалось получить расписание. Попробуйте позже.")
        return ConversationHandler.END
    if not free:
        await query.edit_message_text(
            f"На {d.strftime('%d.%m')} свободных мест нет.\n\nВыберите другой день: /book"
        )
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(
            f"🕐 {s.strftime('%H:%M')} – {e.strftime('%H:%M')}",
            callback_data=f"btime_{s.strftime('%H:%M')}"
        )]
        for s, e in free[:12]
    ]
    await query.edit_message_text(
        f"Свободные слоты на {d.strftime('%d.%m')}:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return CHOOSING_TIME


async def cb_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["book_time"] = query.data.removeprefix("btime_")
    d = context.user_data["book_date"]
    t = context.user_data["book_time"]
    await query.edit_message_text(
        f"Выбрано: {d.strftime('%d.%m')} в {t}\n\nКак вас зовут?"
    )
    return ENTERING_NAME


async def msg_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["book_name"] = update.message.text.strip()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Взрослый", callback_data="btype_adult")],
        [InlineKeyboardButton("👶 Ребёнок", callback_data="btype_child")],
    ])
    await update.message.reply_text(
        f"Отлично, {context.user_data['book_name']}!\nКто будет тренироваться?",
        reply_markup=keyboard
    )
    return ENTERING_TYPE


async def cb_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    is_child = query.data == "btype_child"
    ud = context.user_data
    d: date_type = ud["book_date"]
    h, m = map(int, ud["book_time"].split(":"))
    start_dt = TZ.localize(datetime(d.year, d.month, d.day, h, m))
    end_dt = start_dt + timedelta(minutes=45)
    name: str = ud["book_name"]
    notes = "ребёнок" if is_child else "взрослый"

    try:
        event_id, _ = create_booking(
            client_name=name,
            start_dt=start_dt,
            end_dt=end_dt,
            telegram_id=update.effective_user.id,
            notes=notes,
        )
        save_booking(
            telegram_id=update.effective_user.id,
            client_name=name,
            calendar_event_id=event_id,
            start_time=start_dt.isoformat(),
            end_time=end_dt.isoformat(),
        )
        upsert_client(update.effective_user.id, name=name)

        username = update.effective_user.username
        user_ref = f"@{username}" if username else str(update.effective_user.id)
        trainer_msg = (
            f"🆕 Новая запись!\n"
            f"👤 {name} ({notes})\n"
            f"📅 {d.strftime('%d.%m.%Y')} в {ud['book_time']}\n"
            f"📱 {user_ref}"
        )
        await query.bot.send_message(chat_id=TRAINER_CHAT_ID, text=trainer_msg)

        await query.edit_message_text(
            f"✅ Записано!\n\n"
            f"📅 {d.strftime('%d.%m.%Y')} в {ud['book_time']}\n"
            f"📍 СК Раменки, ул. Раменки д.19 стр.1\n\n"
            f"Тренер Сергей ждёт вас! 🏊"
        )
    except Exception as e:
        logger.error(f"Booking error: {e}")
        await query.edit_message_text(
            "Что-то пошло не так 😔\n"
            "Напишите напрямую или попробуйте ещё раз: /book"
        )
    return ConversationHandler.END


async def book_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Запись отменена. Начать заново: /book")
    return ConversationHandler.END


def make_book_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("book", cmd_book)],
        states={
            CHOOSING_DATE: [CallbackQueryHandler(cb_date, pattern=r"^bdate_")],
            CHOOSING_TIME: [CallbackQueryHandler(cb_time, pattern=r"^btime_")],
            ENTERING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, msg_name)],
            ENTERING_TYPE: [CallbackQueryHandler(cb_type, pattern=r"^btype_")],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            CommandHandler("cancel", book_cancel),
        ],
        per_message=False,
    )


# ── Отмена записи клиентом ────────────────────────────────────────────────────

async def cmd_cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bookings = get_client_bookings(update.effective_user.id)
    if not bookings:
        await update.message.reply_text("У вас нет предстоящих записей.")
        return
    buttons = []
    for b in bookings:
        dt = datetime.fromisoformat(b["start_time"])
        label = f"❌ {dt.strftime('%d.%m в %H:%M')} — {b['client_name']}"
        buttons.append([InlineKeyboardButton(
            label,
            callback_data=f"cxl_{b['id']}|{b['calendar_event_id'] or ''}"
        )])
    buttons.append([InlineKeyboardButton("Не отменять", callback_data="cxl_nope")])
    await update.message.reply_text(
        "Какую запись отменить?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def cb_cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cxl_nope":
        await query.edit_message_text("Отмена не выполнена.")
        return

    payload = query.data.removeprefix("cxl_")
    booking_id_str, event_id = payload.split("|", 1)
    booking_id = int(booking_id_str)

    try:
        cancel_booking(booking_id)
        if event_id:
            cancel_event(event_id)
        username = update.effective_user.username
        await context.bot.send_message(
            chat_id=TRAINER_CHAT_ID,
            text=f"❌ Клиент отменил запись\n"
                 f"Telegram: {'@' + username if username else update.effective_user.id}"
        )
        await query.edit_message_text("✅ Запись отменена.\n\nЗаписаться снова: /book 🏊")
    except Exception as e:
        logger.error(f"cancel booking error: {e}")
        await query.edit_message_text("Не удалось отменить. Напишите тренеру напрямую.")


# ── Свободный диалог через Claude ────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    # Ответ на напоминание ДА/НЕТ
    low = text.lower()
    if low in ("да", "yes", "+", "приду"):
        await update.message.reply_text("Отлично, ждём! 🏊")
        return
    if low in ("нет", "no", "не приду", "не смогу"):
        await update.message.reply_text(
            "Понял, запись отменена. Если захотите перенести — /book 🤙"
        )
        username = update.effective_user.username
        await context.bot.send_message(
            chat_id=TRAINER_CHAT_ID,
            text=f"❌ Клиент отменил тренировку\n"
                 f"Telegram: {'@' + username if username else uid}"
        )
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )
    try:
        reply = await chat(uid, text)
        booking = parse_booking(reply)
        clean = clean_response(reply)

        if clean:
            await update.message.reply_text(clean)

        if booking:
            await _process_claude_booking(update, context, booking)

    except Exception as e:
        logger.error(f"Claude error: {e}")
        await update.message.reply_text(
            "Что-то пошло не так. Для записи используйте /book"
        )


async def _process_claude_booking(update: Update,
                                   context: ContextTypes.DEFAULT_TYPE,
                                   booking: dict):
    """Создать событие по данным из Claude-ответа."""
    try:
        from datetime import date as date_type
        d = date_type.fromisoformat(booking["date"])
        h, m = map(int, booking["time"].split(":"))
        start_dt = TZ.localize(datetime(d.year, d.month, d.day, h, m))
        end_dt = start_dt + timedelta(minutes=45)
        name = booking["name"]
        notes = f"возраст: {booking['age']}, цель: {booking['goal']}"

        event_id, _ = create_booking(
            client_name=name,
            start_dt=start_dt,
            end_dt=end_dt,
            telegram_id=update.effective_user.id,
            notes=notes,
        )
        save_booking(
            telegram_id=update.effective_user.id,
            client_name=name,
            calendar_event_id=event_id,
            start_time=start_dt.isoformat(),
            end_time=end_dt.isoformat(),
        )
        username = update.effective_user.username
        await context.bot.send_message(
            chat_id=TRAINER_CHAT_ID,
            text=f"🆕 Запись через AI-диалог!\n"
                 f"👤 {name} ({notes})\n"
                 f"📅 {d.strftime('%d.%m.%Y')} в {booking['time']}\n"
                 f"📱 {'@' + username if username else update.effective_user.id}"
        )
    except Exception as e:
        logger.error(f"Claude booking create error: {e}")
