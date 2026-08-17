import re
from datetime import datetime, timedelta, timezone
from html import escape as _escape

from aiogram.types import Message

MOSCOW_TZ = timezone(timedelta(hours=3))  # MSK, без перехода на летнее/зимнее время с 2014 года


def moscow_time_str() -> str:
    """Текущее московское время в формате ЧЧ:ММ, для фишки 'часы в имени/фамилии'."""
    return datetime.now(MOSCOW_TZ).strftime("%H:%M")

DURATION_RE = re.compile(r"(\d+)\s*(d|h|m|s)", re.IGNORECASE)
UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(raw: str) -> int | None:
    """
    '1h30m' -> 5400 ; '2d' -> 172800 ; '' или None -> None (навсегда)
    Бросает ValueError, если строка не пустая, но не распознана.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    matches = DURATION_RE.findall(raw)
    if not matches:
        raise ValueError(f"Не могу разобрать длительность: {raw!r}")
    total = 0
    for value, unit in matches:
        total += int(value) * UNIT_SECONDS[unit.lower()]
    return total


def human_duration(seconds) -> str:
    if seconds is None:
        return "навсегда"
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            amount, seconds = divmod(seconds, size)
            parts.append(f"{amount}{unit}")
    return " ".join(parts) or "0s"


def truncate(text: str, limit: int = 800) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def media_label(message: Message) -> str | None:
    if message.photo:
        return "📷 Фото"
    if message.video:
        return "🎥 Видео"
    if message.voice:
        return "🎤 Голосовое"
    if message.video_note:
        return "🎥 Видеосообщение"
    if message.document:
        return "📎 Файл"
    if message.sticker:
        return "🖼 Стикер"
    if message.animation:
        return "🎞 GIF"
    if message.audio:
        return "🎵 Аудио"
    if message.location:
        return "📍 Геолокация"
    if message.contact:
        return "👤 Контакт"
    if message.poll:
        return "📊 Опрос"
    return None


# Типы медиа, которые бот умеет пересылать заново по file_id (send_photo,
# send_video, ...). Стикеры/геолокацию/контакт/опрос не кэшируем как файл —
# их либо бессмысленно пересылать отдельно (спойлер уже утерян), либо не
# применимо. Достаточно фото/видео/голосовых/видеосообщений/файлов/GIF/аудио.
def media_file(message: Message) -> tuple[str | None, str | None]:
    """
    Возвращает (kind, file_id) для последующей пересылки через
    bot.send_<kind>(chat_id=..., <kind>=file_id). (None, None), если во
    сообщении нет поддерживаемого медиа.
    """
    if message.photo:
        return "photo", message.photo[-1].file_id  # последний = самое большое разрешение
    if message.video:
        return "video", message.video.file_id
    if message.voice:
        return "voice", message.voice.file_id
    if message.video_note:
        return "video_note", message.video_note.file_id
    if message.document:
        return "document", message.document.file_id
    if message.animation:
        return "animation", message.animation.file_id
    if message.audio:
        return "audio", message.audio.file_id
    return None, None


def html_escape(text: str) -> str:
    return _escape(text or "", quote=False)


def quote_html(text: str) -> str:
    """HTML blockquote — визуальная цитата, как в самом Telegram."""
    text = text if text else "(без текста)"
    return f"<blockquote>{html_escape(text)}</blockquote>"


def mention_html(user_id, name: str, username: str | None) -> str:
    """
    Кликабельное упоминание собеседника: @username (Имя) со ссылкой на t.me/username,
    либо mention по id, если username не задан.
    """
    safe_name = html_escape(name or "без имени")
    if username:
        return f'<a href="https://t.me/{username}">@{username}</a> ({safe_name})'
    if user_id:
        return f'<a href="tg://user?id={user_id}">{safe_name}</a>'
    return safe_name


def sender_info(message: Message) -> dict:
    user = message.from_user
    if user is None:
        return {"id": None, "name": "неизвестно", "username": None}
    name = user.first_name or "без имени"
    if user.last_name:
        name = f"{name} {user.last_name}"
    return {"id": user.id, "name": name, "username": user.username}


def chat_info(message: Message) -> dict:
    chat = message.chat
    title = getattr(chat, "title", None)
    if title:
        return {"id": chat.id, "name": title, "username": chat.username}
    first = getattr(chat, "first_name", None) or "личный чат"
    if getattr(chat, "last_name", None):
        first = f"{first} {chat.last_name}"
    return {"id": chat.id, "name": first, "username": chat.username}
