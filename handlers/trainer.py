import logging
from datetime import datetime, timedelta
import pytz
from telegram import Update
from telegram.ext import ContextTypes
from config import TRAINER_CHAT_ID, TIMEZONE, COLOR_PAID_CASH, COLOR_PAID_DIRECT, COLOR_NO_PAY
from calendar_service import (get_events_for_date, get_events_range,
                               find_today_event_by_name, update_event_color,
                               cancel_event, get_free_slots)

logger = logging.getLogger(__name__)
TZ = pytz.timezone(TIMEZONE)

COLOR_ICON = {"1": "🔵", "10": "🟢", "6": "🟠", "5": "🟡"}


def _is_trainer(update: Update) -> bool:
    return update.effective_user.id == TRAINER_CHAT_ID


def _fmt_events(events: list, header: str) -> str:
    lines = [header]
    found = False
    for ev in events:
        if ev.get("status") == "cancelled":
            continue
        s = ev["start"].get("dateTime", "")
        if not s:
            continue
        dt = datetime.fromisoformat(s)
        icon = COLOR_ICON.get(ev.get("colorId", "1"), "⚪")
        lines.append(f"{icon} {dt.strftime('%H:%M')} — {ev.get('summary', '?')}")
        found = True
    if not found:
        lines.append("Записей нет")
    return "\n".join(lines)


# ── Команды ──────────────────────────────────────────────────────────────────

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_trainer(update):
        return
    d = datetime.now(TZ).date()
    events = get_events_for_date(d)
    await update.message.reply_text(_fmt_events(events, f"📅 Сегодня {d.strftime('%d.%m.%Y')}:\n"))


async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_trainer(update):
        return
    d = datetime.now(TZ).date() + timedelta(days=1)
    events = get_events_for_date(d)
    await update.message.reply_text(_fmt_events(events, f"📅 Завтра {d.strftime('%d.%m.%Y')}:\n"))


async def cmd_income(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_trainer(update):
        return
    args = context.args or []
    period = args[0] if args else "day"
    now = datetime.now(TZ)

    if period == "week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0)
        label = "эту неделю"
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0)
        label = now.strftime("%B %Y")
    else:
        start = now.replace(hour=0, minute=0, second=0)
        label = f"сегодня ({now.strftime('%d.%m')})"

    events = get_events_range(start, now)
    cash = [e for e in events if e.get("colorId") == "10"]
    direct = [e for e in events if e.get("colorId") == "6"]
    no_pay = [e for e in events if e.get("colorId") == "5"]

    total = len(cash) * 1000 + len(direct) * 1800
    msg = (
        f"💰 Доход за {label}:\n\n"
        f"🟢 Касса: {len(cash)} × 1 000р = {len(cash) * 1000}р\n"
        f"🟠 Напрямую: {len(direct)} × 1 800р = {len(direct) * 1800}р\n"
        f"🟡 Не оплатили: {len(no_pay)}\n\n"
        f"📊 Итого: {total}р"
    )
    await update.message.reply_text(msg)


async def cmd_free(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_trainer(update):
        return
    today = datetime.now(TZ).date()
    tomorrow = today + timedelta(days=1)
    lines = []
    for d, label in [(today, "Сегодня"), (tomorrow, "Завтра")]:
        slots = get_free_slots(d)
        if slots:
            lines.append(f"{label} ({d.strftime('%d.%m')}):")
            lines += [f"  {s.strftime('%H:%M')}–{e.strftime('%H:%M')}" for s, e in slots[:8]]
    if lines:
        await update.message.reply_text("🕐 Свободные слоты:\n\n" + "\n".join(lines))
    else:
        await update.message.reply_text("Свободных слотов нет.")


async def cmd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_trainer(update):
        return
    if not context.args:
        await update.message.reply_text("Укажите имя: /confirm Иван")
        return
    name = " ".join(context.args)
    ev = find_today_event_by_name(name)
    if not ev:
        await update.message.reply_text(f"Не нашёл «{name}» на сегодня.")
        return
    await update.message.reply_text(
        f"✅ {ev['summary']} — пришёл\n\n"
        f"Отметьте оплату:\n"
        f"/paid_cash {name} — касса (1 000р)\n"
        f"/paid_direct {name} — напрямую (1 800р)\n"
        f"/no_pay {name} — не оплатил"
    )


async def cmd_paid_cash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_trainer(update):
        return
    await _set_color(update, context, COLOR_PAID_CASH, "🟢 Оплачено (касса 1 000р)")


async def cmd_paid_direct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_trainer(update):
        return
    await _set_color(update, context, COLOR_PAID_DIRECT, "🟠 Оплачено напрямую (1 800р)")


async def cmd_no_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_trainer(update):
        return
    await _set_color(update, context, COLOR_NO_PAY, "🟡 Не оплачено")


async def _set_color(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     color_id: str, label: str):
    if not context.args:
        await update.message.reply_text("Укажите имя клиента")
        return
    name = " ".join(context.args)
    ev = find_today_event_by_name(name)
    if not ev:
        await update.message.reply_text(f"Не нашёл «{name}» на сегодня.")
        return
    update_event_color(ev["id"], color_id)
    await update.message.reply_text(f"{label}: {ev['summary']}")


async def cmd_cancel_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_trainer(update):
        return
    if not context.args:
        await update.message.reply_text("Укажите имя: /cancel_client Иван")
        return
    name = " ".join(context.args)
    ev = find_today_event_by_name(name)
    if not ev:
        await update.message.reply_text(f"Не нашёл «{name}» на сегодня.")
        return
    cancel_event(ev["id"])
    await update.message.reply_text(f"❌ Отменено: {ev['summary']}")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_trainer(update):
        return
    now = datetime.now(TZ)
    today = now.date()
    tomorrow = today + timedelta(days=1)

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
        f"📊 ИТОГИ {today.strftime('%d.%m.%Y')}\n\n"
        f"✅ Проведено: {len(cash) + len(direct)}\n"
        f"💰 Заработано:\n"
        f"   🟢 Касса: {len(cash)} × 1 000р = {len(cash) * 1000}р\n"
        f"   🟠 Напрямую: {len(direct)} × 1 800р = {len(direct) * 1800}р\n"
        f"   📊 Итого: {total}р"
    )
    if no_pay:
        msg += f"\n\n⚠️ Не оплатили: {', '.join(no_pay)}"
    if cancelled_names:
        msg += f"\n❌ Отменено: {', '.join(cancelled_names)}"
    if tmr_lines:
        msg += f"\n\n📅 ЗАВТРА {tomorrow.strftime('%d.%m')}:\n" + "\n".join(tmr_lines)
    else:
        msg += f"\n\n📅 Завтра записей нет."

    await update.message.reply_text(msg)
