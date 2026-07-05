"""
Запускать ОДИН РАЗ локально (не на Railway!), чтобы получить SESSION_STRING.
Потребует интерактивный ввод номера телефона и кода из Telegram (и пароля 2FA, если включён).

Использование:
    python session_login.py

После входа скрипт напечатает строку сессии — её нужно сохранить
как переменную окружения SESSION_STRING (и больше никому не показывать —
это фактически равнозначно паролю от аккаунта).
"""
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(input("API_ID (с my.telegram.org): ").strip())
API_HASH = input("API_HASH (с my.telegram.org): ").strip()

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print("\n=== SESSION_STRING (сохрани и никому не показывай) ===\n")
    print(client.session.save())
    print("\n=======================================================")
