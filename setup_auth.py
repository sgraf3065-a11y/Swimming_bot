"""
Альтернативный скрипт авторизации Google Calendar.
Использует curl для обмена токенов (обходит SSL-баг Python 3.9 + LibreSSL).
"""
import json
import os
import subprocess
import urllib.parse
import webbrowser
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

REDIRECT_URI = "http://localhost:8080"
SCOPE = "https://www.googleapis.com/auth/calendar"
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")

# Загружаем CLIENT_ID и CLIENT_SECRET из credentials.json (не коммитится в git)
_creds_file = os.path.join(os.path.dirname(__file__), "credentials.json")
with open(_creds_file) as _f:
    _creds = json.load(_f)
_installed = _creds.get("installed") or _creds.get("web") or {}
CLIENT_ID = _installed.get("client_id", "")
CLIENT_SECRET = _installed.get("client_secret", "")

auth_code = None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h1>✅ Авторизация успешна! Можно закрыть вкладку.</h1>".encode("utf-8"))
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    global auth_code

    # Запускаем локальный HTTP-сервер (без SSL)
    server = HTTPServer(("localhost", 8080), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.daemon = True
    thread.start()

    # Формируем URL авторизации
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(params)

    print("\nОткрываю браузер для авторизации Google...")
    webbrowser.open(auth_url)
    print("Если браузер не открылся, перейдите по ссылке вручную:")
    print(auth_url)
    print("\nОжидаю авторизации (до 2 минут)...")

    thread.join(timeout=120)

    if not auth_code:
        print("❌ Авторизация не завершена (таймаут)")
        return

    print("Код получен, обмениваю на токен...")

    # Обмен кода на токен через curl (не через Python SSL)
    result = subprocess.run([
        "curl", "-s", "-X", "POST",
        "https://oauth2.googleapis.com/token",
        "--data-urlencode", f"code={auth_code}",
        "--data-urlencode", f"client_id={CLIENT_ID}",
        "--data-urlencode", f"client_secret={CLIENT_SECRET}",
        "--data-urlencode", f"redirect_uri={REDIRECT_URI}",
        "--data-urlencode", "grant_type=authorization_code",
    ], capture_output=True, text=True)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"❌ Не удалось разобрать ответ: {result.stdout}")
        return

    if "error" in data:
        print(f"❌ Ошибка Google: {data.get('error_description', data)}")
        return

    # Сохраняем в формате, совместимом с google.oauth2.credentials.Credentials
    token_json = {
        "token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scopes": [SCOPE],
    }

    with open(TOKEN_FILE, "w") as f:
        json.dump(token_json, f, indent=2)

    print(f"✅ Токен сохранён: {TOKEN_FILE}")
    print("\nТеперь можно запускать бота: python3 main.py")


if __name__ == "__main__":
    main()
