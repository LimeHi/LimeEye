import re
from html import escape as _escape

DURATION_RE = re.compile(r"(\d+)\s*(d|h|m|s)", re.IGNORECASE)
UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(raw: str) -> int | None:
    """
    '1h30m' -> 5400 ; '2d' -> 172800 ; '' или None -> None (значит навсегда)
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


def human_duration(seconds: int) -> str:
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


def media_label(message) -> str | None:
    if not message.media:
        return None
    cls = message.media.__class__.__name__
    mapping = {
        "MessageMediaPhoto": "📷 Фото",
        "MessageMediaDocument": "📎 Файл",
        "MessageMediaWebPage": "🔗 Ссылка",
        "MessageMediaGeo": "📍 Геолокация",
        "MessageMediaContact": "👤 Контакт",
        "MessageMediaPoll": "📊 Опрос",
    }
    return mapping.get(cls, f"Медиа ({cls})")


def html_escape(text: str) -> str:
    return _escape(text or "", quote=False)


def quote_html(text: str) -> str:
    """Оборачивает текст в HTML-blockquote (визуальная 'цитата' как в самом Telegram)."""
    text = text if text else "(без текста)"
    return f"<blockquote>{html_escape(text)}</blockquote>"


def mention_html(user_id, name: str, username: str | None) -> str:
    """
    Кликабельное упоминание собеседника.
    Если есть username — отдельно показываем @username (кликабельная ссылка на t.me/username),
    и в скобках отображаемое имя. Если username нет — кликабельный mention по id (tg://user?id=...).
    """
    safe_name = html_escape(name or "без имени")
    if username:
        return f'<a href="https://t.me/{username}">@{username}</a> ({safe_name})'
    if user_id:
        return f'<a href="tg://user?id={user_id}">{safe_name}</a>'
    return safe_name


async def get_sender_info(event) -> dict:
    """Возвращает {'id', 'name', 'username'} отправителя."""
    try:
        sender = await event.get_sender()
    except Exception:
        sender = None
    if sender is None:
        return {"id": event.sender_id, "name": "неизвестно", "username": None}
    name = getattr(sender, "first_name", None) or getattr(sender, "title", None) or "без имени"
    last = getattr(sender, "last_name", None)
    if last:
        name = f"{name} {last}"
    username = getattr(sender, "username", None)
    return {"id": getattr(sender, "id", event.sender_id), "name": name, "username": username}


async def get_chat_info(event) -> dict:
    """Возвращает {'id', 'name', 'username'} чата/собеседника."""
    try:
        chat = await event.get_chat()
    except Exception:
        chat = None
    if chat is None:
        return {"id": event.chat_id, "name": "неизвестный чат", "username": None}
    title = getattr(chat, "title", None)
    username = getattr(chat, "username", None)
    if title:
        return {"id": chat.id, "name": title, "username": username}
    first = getattr(chat, "first_name", None) or "личный чат"
    last = getattr(chat, "last_name", None)
    if last:
        first = f"{first} {last}"
    return {"id": chat.id, "name": first, "username": username}
