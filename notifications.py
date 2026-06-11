import logging
from datetime import datetime, timedelta
import pytz
from telegram import Bot
from config import TRAINER_CHAT_ID, TRAINER_CHANNEL_ID, TIMEZONE
from calendar_service import get_events_for_date, get_free_slots
from database import get_bookings_needing_reminder, mark_reminder_sent

logger = logging.getLogger(__name__)
TZ = pytz.timezone(TIMEZONE)


async def job_morning_slots(context):
    """08:00 — публикация свободных слотов в канале тренера."""
    bot: Bot = context.bot
    today = datetime.now(TZ).date()
    try:
        free = get_free_slots(today)
        if not free:
            text = f"📅 На сегодня ({today.strftime('%d.%m')}) свободных мест нет."
        else:
            slots_str = "\n".join(f"🕐 {s.strftime('%H:%M')} – {e.strftime('%H:%M')}"
                                  for s, e in free[:10])
            text = (
                f"📅 СВОБОДНЫЕ МЕСТА НА СЕГОДНЯ {today.strftime('%d.%m.%Y')}\n\n"
                f"{slots_str}\n\n"
                f"Записаться: @sergey_pool_bot\n"
                f"Тренер Сергей | СК Раменки"
            )
        if TRAINER_CHANNEL_ID:
            await bot.send_message(chat_id=TRAINER_CHANNEL_ID, text=text)
        # Также тренеру в личку
        await bot.send_message(chat_id=TRAINER_CHAT_ID, text=text)
    except Exception as e:
        logger.error(f"job_morning_slots: {e}")


async def job_evening_report(context):
    """21:00 — вечерний отчёт тренеру."""
    bot: Bot = context.bot
    now = datetime.now(TZ)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    try:
        events = get_events_for_date(today)
        cash, direct, no_pay, cancelled_names = [], [], [], []

        for ev in events:
            name = ev.get("summary", "?")
            color = ev.get("colorId", "1")
            status = ev.get("status", "")
            if status == "cancelled":
                cancelled_names.append(name)
            elif color == "10":
                cash.append(name)
            elif color == "6":
                direct.append(name)
            elif color == "5":
                no_pay.append(name)

        total = len(cash) * 1000 + len(direct) * 1800

        # Расписание на завтра
        tmr_events = get_events_for_date(tomorrow)
        tmr_lines = []
        for ev in tmr_events:
            if ev.get("status") == "cancelled":
                continue
            s = ev["start"].get("dateTime", "")
            if s:
                dt = datetime.fromisoformat(s)
                tmr_lines.append(f"   {dt.strftime('%H:%M')} — {ev.get('summary', '?')}")

        msg = (
            f"📊 ИТОГИ СЕГОДНЯ {today.strftime('%d.%m.%Y')}\n\n"
            f"✅ Проведено: {len(cash) + len(direct)}\n"
            f"💰 Заработано:\n"
            f"   - Касса: {len(cash)} × 1 000р = {len(cash) * 1000}р\n"
            f"   - Напрямую: {len(direct)} × 1 800р = {len(direct) * 1800}р\n"
            f"   - Итого: {total}р"
        )
        if no_pay:
            msg += f"\n\n⚠️ Не оплатили: {', '.join(no_pay)}"
        if cancelled_names:
            msg += f"\n❌ Не пришли: {', '.join(cancelled_names)}"
        if tmr_lines:
            msg += f"\n\n📅 ЗАВТРА {tomorrow.strftime('%d.%m.%Y')}:\n" + "\n".join(tmr_lines)
        else:
            msg += f"\n\n📅 Завтра записей нет."

        await bot.send_message(chat_id=TRAINER_CHAT_ID, text=msg)
    except Exception as e:
        logger.error(f"job_evening_report: {e}")


async def job_reminders(context):
    """Каждые 15 минут — проверка и отправка напоминаний клиентам."""
    bot: Bot = context.bot

    # Напоминание за 24 часа (окно: 23..25 ч до тренировки)
    for booking in get_bookings_needing_reminder(23, 25):
        if booking["reminder_24h_sent"]:
            continue
        if not booking["telegram_id"]:
            continue
        try:
            dt = datetime.fromisoformat(booking["start_time"])
            time_str = dt.astimezone(TZ).strftime("%H:%M")
            await bot.send_message(
                chat_id=booking["telegram_id"],
                text=(
                    f"Сергей напоминает: завтра в {time_str} у вас тренировка.\n"
                    f"Вы придёте? Ответьте ДА или НЕТ 🏊"
                )
            )
            mark_reminder_sent(booking["id"], "24h")
        except Exception as e:
            logger.error(f"24h reminder error: {e}")

    # Напоминание за 2 часа (окно: 1.5..2.5 ч)
    for booking in get_bookings_needing_reminder(1.5, 2.5):
        if booking["reminder_2h_sent"]:
            continue
        if not booking["telegram_id"]:
            continue
        try:
            await bot.send_message(
                chat_id=booking["telegram_id"],
                text=(
                    f"Напоминание: через 2 часа тренировка с тренером Сергеем.\n"
                    f"📍 СК Раменки, ул. Раменки д.19 стр.1"
                )
            )
            mark_reminder_sent(booking["id"], "2h")
        except Exception as e:
            logger.error(f"2h reminder error: {e}")
