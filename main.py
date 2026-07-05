import asyncio
import logging

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from config import API_ID, API_HASH, SESSION_STRING, LOG_CHAT, CMD_PREFIX
from storage import Storage
from commands import COMMANDS
from utils import truncate, media_label, get_sender_info, get_chat_info, quote_html, mention_html

BOT_NAME = "LimeEye"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(BOT_NAME)

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
storage = Storage()


# ---------------------------------------------------------------------------
# Кэширование + мьют + диспетчер команд — всё в одном хендлере на новые сообщения
# ---------------------------------------------------------------------------
@client.on(events.NewMessage())
async def on_new_message(event):
    chat_id = event.chat_id

    # 1) Команды через префикс "." — реагируем только на СВОИ сообщения (outgoing)
    if event.out and event.raw_text and event.raw_text.startswith(CMD_PREFIX):
        body = event.raw_text[len(CMD_PREFIX):].strip()
        if body:
            parts = body.split(maxsplit=1)
            name = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            handler = COMMANDS.get(name)
            if handler:
                try:
                    await handler(event, args, storage, client)
                except Exception:
                    log.exception("Ошибка выполнения команды %s", name)
                    await event.edit(f"⚠️ Ошибка при выполнении .{name}, смотри логи.")
                return  # команду не кэшируем и не проверяем на мьют

    # 2) Если чат замьючен — удаляем входящее сообщение (не трогаем свои исходящие)
    if not event.out and await storage.is_muted(chat_id):
        try:
            await event.delete()
        except Exception:
            log.exception("Не удалось удалить сообщение в замьюченном чате %s", chat_id)
        return  # замьюченное сообщение не кэшируем

    # 3) Кэшируем сообщение (нужно, чтобы потом восстановить текст при удалении/правке)
    sender = await get_sender_info(event)
    chat = await get_chat_info(event)
    await storage.cache_message(
        chat_id=chat_id,
        msg_id=event.id,
        sender_id=sender["id"],
        sender_name=sender["name"],
        sender_username=sender["username"],
        chat_name=chat["name"],
        chat_username=chat["username"],
        text=event.raw_text or "",
        media_type=media_label(event.message),
    )


# ---------------------------------------------------------------------------
# Удалённые сообщения
# ---------------------------------------------------------------------------
@client.on(events.MessageDeleted())
async def on_message_deleted(event):
    chat_id = event.chat_id
    if chat_id is None:
        return  # Telegram иногда не даёт chat_id для приватных удалений — пропускаем
    for msg_id in event.deleted_ids:
        cached = await storage.get_cached(chat_id, msg_id)
        if not cached:
            continue  # не было в кэше — нечего показать

        who = mention_html(cached["sender_id"], cached["sender_name"], cached["sender_username"])
        media = f"\n{cached['media_type']}" if cached["media_type"] else ""
        report = (
            f"🗑 <b>Удалённое сообщение</b>\n"
            f"Чат: {cached['chat_name']}\n"
            f"От: {who}\n\n"
            f"{quote_html(truncate(cached['text']))}{media}"
        )
        try:
            await client.send_message(LOG_CHAT, report, parse_mode="html", link_preview=False)
        except Exception:
            log.exception("Не удалось отправить отчёт об удалении")


# ---------------------------------------------------------------------------
# Изменённые сообщения
# ---------------------------------------------------------------------------
@client.on(events.MessageEdited())
async def on_message_edited(event):
    chat_id = event.chat_id
    cached = await storage.get_cached(chat_id, event.id)
    old_text = cached["text"] if cached else None
    new_text = event.raw_text or ""

    if old_text is not None and old_text != new_text:
        who = mention_html(cached["sender_id"], cached["sender_name"], cached["sender_username"])
        report = (
            f"✏️ <b>Изменённое сообщение</b>\n"
            f"Чат: {cached['chat_name']}\n"
            f"От: {who}\n\n"
            f"<b>Было:</b>\n{quote_html(truncate(old_text))}\n"
            f"<b>Стало:</b>\n{quote_html(truncate(new_text))}"
        )
        try:
            await client.send_message(LOG_CHAT, report, parse_mode="html", link_preview=False)
        except Exception:
            log.exception("Не удалось отправить отчёт об изменении")

    # обновляем кэш новым текстом в любом случае
    sender = await get_sender_info(event)
    chat = await get_chat_info(event)
    await storage.cache_message(
        chat_id=chat_id,
        msg_id=event.id,
        sender_id=sender["id"],
        sender_name=sender["name"],
        sender_username=sender["username"],
        chat_name=chat["name"],
        chat_username=chat["username"],
        text=new_text,
        media_type=media_label(event.message),
    )


async def main():
    await storage.init()
    await client.start()
    me = await client.get_me()
    log.info("%s запущен как %s (id=%s)", BOT_NAME, me.username or me.first_name, me.id)
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(storage.close())
