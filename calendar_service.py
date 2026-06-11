from __future__ import annotations
import os
import ssl
import logging
from datetime import datetime, timedelta, date as date_type
import pytz
import requests
import urllib3
from requests.adapters import HTTPAdapter
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from config import (GOOGLE_CREDENTIALS_FILE, GOOGLE_TOKEN_FILE,
                    GOOGLE_CALENDAR_ID, TIMEZONE,
                    WORK_START_HOUR, WORK_END_HOUR,
                    SESSION_MINUTES, BUFFER_MINUTES,
                    COLOR_PLANNED)

logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/calendar"]
TZ = pytz.timezone(TIMEZONE)

# Подавляем InsecureRequestWarning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class _TolerantSSLAdapter(HTTPAdapter):
    """HTTPAdapter с SSL suppress_ragged_eofs для TLS 1.3."""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def _make_requests_session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    adapter = _TolerantSSLAdapter()
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


class _HttplibAdapter:
    """Минимальная httplib2.Http-совместимая обёртка вокруг requests.Session.

    googleapiclient ожидает объект с методом .request() возвращающим (response, content).
    """

    def __init__(self, session: requests.Session):
        self._session = session
        self.timeout = 30

    def request(self, uri, method="GET", body=None, headers=None,
                redirections=5, connection_type=None):
        try:
            resp = self._session.request(
                method=method, url=uri,
                data=body,
                headers=headers or {},
                timeout=self.timeout,
                allow_redirects=(redirections > 0),
            )

            class _Resp:
                status = resp.status_code
                reason = resp.reason

            return _Resp(), resp.content
        except requests.exceptions.SSLError:
            raise
        except requests.exceptions.Timeout as exc:
            raise TimeoutError(str(exc)) from exc


def get_service():
    """Вернуть авторизованный Google Calendar сервис через requests (обход SSL-бага)."""
    creds = None
    if os.path.exists(GOOGLE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            sess = _make_requests_session()
            creds.refresh(Request(session=sess))
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
            creds = flow.run_console()
        with open(GOOGLE_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    from google.auth.transport.requests import AuthorizedSession
    authed = AuthorizedSession(creds)
    adapter = _TolerantSSLAdapter()
    authed.mount("https://", adapter)
    authed.mount("http://", adapter)
    authed.verify = False

    return build("calendar", "v3", http=_HttplibAdapter(authed),
                 cache_discovery=False)


def get_events_for_date(d: date_type) -> list:
    service = get_service()
    day_start = TZ.localize(datetime(d.year, d.month, d.day, 0, 0, 0))
    day_end = TZ.localize(datetime(d.year, d.month, d.day, 23, 59, 59))
    result = service.events().list(
        calendarId=GOOGLE_CALENDAR_ID,
        timeMin=day_start.isoformat(),
        timeMax=day_end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return result.get("items", [])


def get_events_range(start_dt: datetime, end_dt: datetime) -> list:
    service = get_service()
    result = service.events().list(
        calendarId=GOOGLE_CALENDAR_ID,
        timeMin=start_dt.isoformat(),
        timeMax=end_dt.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return result.get("items", [])


def get_free_slots(d: date_type) -> list[tuple[datetime, datetime]]:
    """Вернуть список (start, end) свободных 45-минутных слотов на дату d."""
    events = get_events_for_date(d)

    # Собираем занятые интервалы в минутах от полуночи
    occupied: list[tuple[int, int]] = []
    for ev in events:
        if ev.get("status") == "cancelled":
            continue
        start_str = ev["start"].get("dateTime")
        end_str = ev["end"].get("dateTime")
        if not start_str or not end_str:
            continue
        s = datetime.fromisoformat(start_str)
        e = datetime.fromisoformat(end_str)
        occupied.append((s.hour * 60 + s.minute, e.hour * 60 + e.minute))

    # Сортируем и мерджим перекрывающиеся
    occupied.sort()
    merged: list[list[int]] = []
    for s, e in occupied:
        if merged and s < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    work_start = WORK_START_HOUR * 60          # 540 (09:00)
    work_end = WORK_END_HOUR * 60 + 30         # 1290 (21:30) — абсолютный конец
    last_start = WORK_END_HOUR * 60            # 1260 (21:00) — последний старт

    free: list[tuple[datetime, datetime]] = []
    cursor = work_start

    while cursor + SESSION_MINUTES <= work_end and cursor <= last_start:
        slot_end = cursor + SESSION_MINUTES

        # Проверяем конфликт с занятыми интервалами (учитываем буфер вокруг чужих событий)
        conflict = False
        for occ_s, occ_e in merged:
            # Слот [cursor, slot_end] конфликтует, если перекрывается с
            # расширенным занятым интервалом [occ_s - BUFFER, occ_e + BUFFER]
            if cursor < occ_e + BUFFER_MINUTES and slot_end > occ_s - BUFFER_MINUTES:
                conflict = True
                # Сдвигаем курсор сразу за конец занятого + буфер
                cursor = occ_e + BUFFER_MINUTES
                break

        if not conflict:
            s_dt = TZ.localize(datetime(d.year, d.month, d.day, cursor // 60, cursor % 60))
            e_dt = s_dt + timedelta(minutes=SESSION_MINUTES)
            free.append((s_dt, e_dt))
            cursor += SESSION_MINUTES + BUFFER_MINUTES  # следующий слот после буфера
        # если был conflict — cursor уже сдвинут внутри цикла

    return free


def create_booking(client_name: str, start_dt: datetime, end_dt: datetime,
                   telegram_id: int = None, notes: str = "") -> tuple[str, str]:
    """Создать событие в Google Calendar. Вернуть (event_id, html_link)."""
    service = get_service()
    description = f"Telegram ID: {telegram_id}"
    if notes:
        description += f"\n{notes}"

    event = {
        "summary": client_name,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE},
        "colorId": COLOR_PLANNED,
    }
    created = service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
    return created["id"], created.get("htmlLink", "")


def update_event_color(event_id: str, color_id: str):
    service = get_service()
    ev = service.events().get(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id).execute()
    ev["colorId"] = str(color_id)
    service.events().update(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id, body=ev).execute()


def cancel_event(event_id: str):
    service = get_service()
    service.events().delete(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id).execute()


def find_today_event_by_name(name: str) -> dict | None:
    """Найти событие на сегодня по имени клиента (частичное совпадение)."""
    today = datetime.now(TZ).date()
    events = get_events_for_date(today)
    name_lower = name.lower().strip()
    for ev in events:
        if ev.get("status") == "cancelled":
            continue
        if name_lower in ev.get("summary", "").lower():
            return ev
    return None
