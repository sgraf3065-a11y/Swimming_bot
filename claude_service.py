from __future__ import annotations
import logging
import anthropic
from config import ANTHROPIC_API_KEY, SYSTEM_PROMPT
from database import get_history, save_message

logger = logging.getLogger(__name__)
_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


async def chat(telegram_id: int, user_text: str) -> str:
    save_message(telegram_id, "user", user_text)
    history = get_history(telegram_id)

    response = await _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=history,
    )
    reply = response.content[0].text
    save_message(telegram_id, "assistant", reply)
    return reply


def parse_booking(response_text: str) -> dict | None:
    """Если ответ Claude содержит BOOKING:... — распарсить и вернуть dict."""
    for line in response_text.splitlines():
        if line.strip().startswith("BOOKING:"):
            data = line.strip()[len("BOOKING:"):].strip()
            parts = [p.strip() for p in data.split("|")]
            if len(parts) >= 5:
                return {
                    "name": parts[0],
                    "age": parts[1],
                    "goal": parts[2],
                    "date": parts[3],   # ГГГГ-ММ-ДД
                    "time": parts[4],   # ЧЧ:ММ
                }
    return None


def clean_response(text: str) -> str:
    """Убрать строку BOOKING:... из текста ответа перед отправкой клиенту."""
    lines = [l for l in text.splitlines() if not l.strip().startswith("BOOKING:")]
    return "\n".join(lines).strip()
