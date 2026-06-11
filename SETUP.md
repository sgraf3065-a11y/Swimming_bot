# Swimming Bot — Инструкция по запуску

## Структура проекта

```
swimming_bot/
├── main.py               # точка входа
├── config.py             # настройки из .env
├── database.py           # SQLite (клиенты, записи, диалоги)
├── calendar_service.py   # Google Calendar API
├── claude_service.py     # Claude AI (диалог с клиентами)
├── notifications.py      # напоминания и вечерний отчёт
├── handlers/
│   ├── client.py         # команды клиентов + /book диалог
│   └── trainer.py        # команды тренера
├── setup.py              # первичная аутентификация Google
├── requirements.txt
└── .env.example
```

---

## Шаг 1 — Создать Telegram-бота

1. Написать [@BotFather](https://t.me/BotFather) → `/newbot`
2. Получить **BOT_TOKEN**
3. Узнать свой Telegram ID: написать [@userinfobot](https://t.me/userinfobot)

---

## Шаг 2 — Подключить Google Calendar API

1. Перейти на [console.cloud.google.com](https://console.cloud.google.com)
2. Создать проект → **Enable APIs** → найти **Google Calendar API** → включить
3. **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
   - Application type: **Desktop app**
4. Скачать JSON → переименовать в `credentials.json` → положить в папку `swimming_bot/`

---

## Шаг 3 — Настроить окружение

```bash
cd swimming_bot
cp .env.example .env
# Заполнить .env своими значениями

pip install -r requirements.txt
```

---

## Шаг 4 — Первичная аутентификация Google

```bash
python setup.py
```

Откроется браузер → войти в аккаунт sgraf3065@gmail.com → разрешить доступ.
Файл `token.json` создастся автоматически.

---

## Шаг 5 — Запуск

```bash
python main.py
```

---

## Команды клиента

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и меню |
| `/slots` | Свободные места на неделю |
| `/book` | Записаться (диалог с кнопками) |
| `/my` | Мои записи |
| `/info` | Цены и адрес |
| _любой текст_ | AI-диалог через Claude |

## Команды тренера

| Команда | Описание |
|---------|----------|
| `/today` | Расписание на сегодня |
| `/tomorrow` | Расписание на завтра |
| `/income [day/week/month]` | Доход за период |
| `/free` | Свободные слоты |
| `/confirm [имя]` | Отметить клиента пришедшим |
| `/paid_cash [имя]` | 🟢 Оплачено через кассу |
| `/paid_direct [имя]` | 🟠 Оплачено напрямую |
| `/no_pay [имя]` | 🟡 Не оплатил |
| `/cancel_client [имя]` | ❌ Отменить запись |
| `/report` | Отчёт за сегодня |

## Цвета в Google Calendar

| Цвет | Значение |
|------|----------|
| 🔵 Синий | Запланировано |
| 🟢 Зелёный | Проведено + касса (1 000р) |
| 🟠 Оранжевый | Проведено + напрямую (1 800р) |
| 🟡 Жёлтый | Проведено + не оплачено |
| Удалено | Отменено |

## Автоматические задачи

- **08:00** — публикация свободных слотов в канале
- **За 24 часа** — напоминание клиенту с запросом ДА/НЕТ
- **За 2 часа** — финальное напоминание клиенту
- **21:00** — вечерний отчёт тренеру
