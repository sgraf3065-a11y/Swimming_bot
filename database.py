import sqlite3
from datetime import datetime, timedelta
import pytz
from config import DB_PATH

_TZ = pytz.timezone("Europe/Moscow")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        name TEXT,
        phone TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        client_name TEXT NOT NULL,
        calendar_event_id TEXT,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        status TEXT DEFAULT 'planned',
        color_id TEXT DEFAULT '1',
        reminder_24h_sent INTEGER DEFAULT 0,
        reminder_2h_sent INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    conn.commit()
    conn.close()


# ── Клиенты ──────────────────────────────────────────────────────────────────

def upsert_client(telegram_id: int, name: str = None, phone: str = None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id FROM clients WHERE telegram_id = ?", (telegram_id,))
    row = c.fetchone()
    if row:
        if name:
            c.execute("UPDATE clients SET name = ? WHERE telegram_id = ?", (name, telegram_id))
    else:
        c.execute("INSERT INTO clients (telegram_id, name, phone) VALUES (?, ?, ?)",
                  (telegram_id, name, phone))
    conn.commit()
    conn.close()


# ── Бронирования ─────────────────────────────────────────────────────────────

def save_booking(telegram_id: int, client_name: str, calendar_event_id: str,
                 start_time: str, end_time: str) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO bookings
                 (telegram_id, client_name, calendar_event_id, start_time, end_time)
                 VALUES (?, ?, ?, ?, ?)""",
              (telegram_id, client_name, calendar_event_id, start_time, end_time))
    booking_id = c.lastrowid
    conn.commit()
    conn.close()
    return booking_id


def get_client_bookings(telegram_id: int):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("""SELECT * FROM bookings
                 WHERE telegram_id = ? AND start_time > ? AND status != 'cancelled'
                 ORDER BY start_time LIMIT 5""",
              (telegram_id, now))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_bookings_needing_reminder(window_start_hours: float, window_end_hours: float):
    """Вернуть записи, до старта которых осталось от window_start до window_end часов."""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now(_TZ)
    t_start = (now + timedelta(hours=window_start_hours)).isoformat()
    t_end = (now + timedelta(hours=window_end_hours)).isoformat()
    c.execute("""SELECT * FROM bookings
                 WHERE start_time >= ? AND start_time <= ?
                 AND status = 'planned'""",
              (t_start, t_end))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def mark_reminder_sent(booking_id: int, kind: str):
    conn = get_conn()
    c = conn.cursor()
    col = "reminder_24h_sent" if kind == "24h" else "reminder_2h_sent"
    c.execute(f"UPDATE bookings SET {col} = 1 WHERE id = ?", (booking_id,))
    conn.commit()
    conn.close()


def cancel_booking_by_event(event_id: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE bookings SET status = 'cancelled' WHERE calendar_event_id = ?", (event_id,))
    conn.commit()
    conn.close()


def cancel_booking(booking_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
    conn.commit()
    conn.close()


def get_booking_by_id(booking_id: int) -> dict | None:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


# ── Диалоги с Claude ─────────────────────────────────────────────────────────

def get_history(telegram_id: int, limit: int = 20):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT role, content FROM conversations
                 WHERE telegram_id = ?
                 ORDER BY created_at DESC LIMIT ?""",
              (telegram_id, limit))
    rows = [{"role": r["role"], "content": r["content"]} for r in c.fetchall()]
    conn.close()
    return list(reversed(rows))


def save_message(telegram_id: int, role: str, content: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO conversations (telegram_id, role, content) VALUES (?, ?, ?)",
              (telegram_id, role, content))
    # Храним только последние 40 сообщений на пользователя
    c.execute("""DELETE FROM conversations WHERE telegram_id = ? AND id NOT IN (
                     SELECT id FROM conversations WHERE telegram_id = ?
                     ORDER BY created_at DESC LIMIT 40)""",
              (telegram_id, telegram_id))
    conn.commit()
    conn.close()


def clear_history(telegram_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM conversations WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()
