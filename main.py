# main.py
import asyncio
import logging
from io import BytesIO

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BufferedInputFile

from config import BOT_TOKEN, CMD_PREFIX, CACHE_MAX_AGE_DAYS, CHANNEL_USERNAME
from storage import Storage
from commands import (
    COMMANDS, cmd_muted, HELP_TEXT,
    build_help_root_text, build_help_root_kb,
    build_help_cmd_text, build_help_cmd_kb,
    build_help_sub_text, build_help_sub_kb,
)
from utils import truncate, media_label, media_file, sender_info, chat_info, quote_html, mention_html, html_escape
from tictactoe import EMPTY, new_board, apply_move, check_result, other_mark, render_text, render_keyboard
import rps as rps_engine
import hangman as hangman_engine

BOT_NAME = "LimeEye"
BOT_USERNAME: str | None = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(BOT_NAME)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
storage = Storage()


async def notify_owner(owner_chat_id: int, text: str):
    if not owner_chat_id:
        return
    try:
        await bot.send_message(owner_chat_id, text, disable_web_page_preview=True)
    except TelegramAPIError:
        log.exception("Не удалось отправить сообщение владельцу (chat_id=%s)", owner_chat_id)


async def send_recovered_media(owner_chat_id: int, media_kind: str | None, media_file_id: str | None):
    if not owner_chat_id or not media_kind or not media_file_id:
        return
    method = getattr(bot, f"send_{media_kind}", None)
    if method is None:
        return
    try:
        await method(chat_id=owner_chat_id, **{media_kind: media_file_id})
    except TelegramAPIError:
        log.exception(
            "Не удалось переслать восстановленный файл (%s) владельцу (chat_id=%s)",
            media_kind, owner_chat_id,
        )


async def try_delete_business(business_connection_id: str, message_id: int) -> bool:
    try:
        await bot.delete_business_messages(
            business_connection_id=business_connection_id,
            message_ids=[message_id],
        )
        return True
    except TelegramAPIError:
        log.exception("Не удалось удалить сообщение %s в подключении %s", message_id, business_connection_id)
        return False


async def handle_photo_trap(message: types.Message, owner_chat_id: int, bc_id: str):
    reply = message.reply_to_message
    if not reply:
        return

    media_id = None
    media_type = None
    filename = "file"

    if reply.photo:
        media_id = reply.photo[-1].file_id
        media_type = "photo"
        filename = "photo.jpg"
    elif reply.video:
        media_id = reply.video.file_id
        media_type = "video"
        filename = "video.mp4"
    elif reply.voice:
        media_id = reply.voice.file_id
        media_type = "voice"
        filename = "voice.ogg"
    elif reply.video_note:
        media_id = reply.video_note.file_id
        media_type = "video_note"
        filename = "video_note.mp4"
    elif reply.animation:
        media_id = reply.animation.file_id
        media_type = "animation"
        filename = "animation.mp4"
    elif reply.document:
        media_id = reply.document.file_id
        media_type = "document"
        filename = reply.document.file_name or "document"
    elif reply.audio:
        media_id = reply.audio.file_id
        media_type = "audio"
        filename = reply.audio.file_name or "audio.mp3"
    else:
        return

    method = getattr(bot, f"send_{media_type}", None)
    if not method:
        return

    try:
        file_buffer = BytesIO()
        await bot.download(media_id, destination=file_buffer)
        file_buffer.seek(0)
        
        input_file = BufferedInputFile(file_buffer.getvalue(), filename=filename)
        
        kwargs = {
            "chat_id": owner_chat_id,
            media_type: input_file,
            "caption": "📸 <b>Фото Ловушка (сохранённое медиа)</b>"
        }
        if reply.caption:
            kwargs["caption"] += f"\n\nПодпись: {html_escape(reply.caption)}"
            
        await method(**kwargs)
    except TelegramAPIError:
        log.exception("Фото Ловушка: не удалось сохранить медиа из чата (bc=%s)", bc_id)


SUBSCRIBE_TEXT = (
    "🔒 Чтобы пользоваться ботом, подпишись на наш канал:\n"
    f"👉 @{CHANNEL_USERNAME}\n\n"
    "После подписки нажми кнопку «✅ Я подписался» ниже."
)


def build_subscribe_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [types.InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")],
    ])


async def is_subscribed(user_id: int) -> bool:
    if not CHANNEL_USERNAME:
        return True
    try:
        member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
        return member.status not in ("left", "kicked")
    except TelegramAPIError:
        # Бот не смог проверить подписку (например, не админ канала) — не блокируем пользователя.
        log.exception("Не удалось проверить подписку на канал @%s для user_id=%s", CHANNEL_USERNAME, user_id)
        return True


@dp.callback_query(F.data == "check_sub")
async def on_check_sub(callback: types.CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.answer("✅ Подписка подтверждена, спасибо!", show_alert=True)
        try:
            await callback.message.delete()
        except TelegramAPIError:
            pass
    else:
        await callback.answer("❌ Пока не вижу подписки. Подпишись и попробуй снова.", show_alert=True)


@dp.message()
async def on_direct_message(message: types.Message):
    if not message.text:
        return

    if CHANNEL_USERNAME and message.text.startswith(("/start", "/help", "/muted", "/hangman")):
        if not await is_subscribed(message.from_user.id):
            await message.answer(SUBSCRIBE_TEXT, reply_markup=build_subscribe_kb())
            return

    if message.text.startswith("/start"):
        bc_ids = await storage.get_owner_connections(message.chat.id)
        if not bc_ids:
            username_line = f"@{BOT_USERNAME}" if BOT_USERNAME else BOT_NAME
            kb_rows = []
            if BOT_USERNAME:
                kb_rows.append([
                    types.InlineKeyboardButton(text="⚙️ Открыть настройки Telegram", url="tg://settings/edit")
                ])
            kb_rows.append([
                types.InlineKeyboardButton(text="📋 Список команд", callback_data="help:root")
            ])
            kb = types.InlineKeyboardMarkup(inline_keyboard=kb_rows)
            await message.answer(
                "👋 Добро пожаловать!\n\n"
                "🆓 Наш бот полностью бесплатный\n\n"
                "🔒 Никаких логов на сторону — всё хранится только в твоей базе, доступ только у тебя\n\n"
                "🔥 Возможности бота\n"
                "<blockquote>"
                "🗑 Отслеживание удалённых сообщений\n"
                "✏️ Отслеживание изменённых сообщений\n"
                "📸 Сохранение одноразовых фотографий и видео\n"
                "🆕 Новые функции и команды"
                "</blockquote>\n\n"
                "❓ Как подключить бота\n"
                "<blockquote>"
                f"1️⃣ Скопируй юзернейм бота: <code>{username_line}</code>\n"
                "2️⃣ Открой Telegram → Настройки → Telegram Business → Чат-боты "
                "(или жми кнопку «Открыть настройки Telegram» ниже)\n"
                "3️⃣ Вставь юзернейм в поиск, выбери бота и включи право «Удаление сообщений» "
                "— без него не будет работать .mute\n"
                "4️⃣ Готово! Отчёты об удалённых/изменённых сообщениях и ответы на команды "
                "будут приходить сюда же"
                "</blockquote>",
                reply_markup=kb,
            )
            return

        kb = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="📋 Список команд", callback_data="help:root")
        ]])
        await message.answer(
            f"👋 {BOT_NAME} запущен.\n\n"
            "Подключи меня к своему аккаунту: Настройки → Автоматизация чатов, "
            "выбери меня и включи право «Удаление сообщений», если хочешь пользоваться .mute.\n\n"
            "Отчёты об удалённых/изменённых сообщениях и ответы на команды будут приходить сюда же.",
            reply_markup=kb,
        )
        return

    if message.text.startswith("/help"):
        await message.answer(HELP_TEXT)
        return

    if message.text.startswith("/muted"):
        bc_ids = await storage.get_owner_connections(message.chat.id)
        if not bc_ids:
            await message.answer("Пока нет активных подключений Business-аккаунта.")
            return
        replies = []
        for bc_id in bc_ids:
            reply = await cmd_muted(None, "", storage, bc_id)
            replies.append(reply)
        await message.answer("\n\n".join(replies))
        return

    if message.text.startswith("/hangman"):
        bc_ids = await storage.get_owner_connections(message.chat.id)
        if not bc_ids:
            await message.answer("Пока нет активных подключений Business-аккаунта.")
            return

        raw = message.text[len("/hangman"):].strip()
        if not raw:
            pending = await storage.get_pending_hangman_word(message.chat.id)
            if pending:
                await message.answer(
                    f"🪢 Уже загадано слово из {len(pending)} букв — оно будет использовано "
                    "в следующей игре <code>.hangman</code>.\n"
                    "Чтобы загадать другое слово вместо него, напиши: <code>/hangman слово</code>"
                )
            else:
                await message.answer(
                    "🪢 Напиши слово вот так: <code>/hangman слово</code> — оно будет "
                    "использовано в следующей игре <code>.hangman</code> в любом чате "
                    "(одноразово, отгадывать будет собеседник, не ты)."
                )
            return

        word, error = hangman_engine.validate_custom_word(raw)
        if error:
            await message.answer(error)
            return

        await storage.set_pending_hangman_word(message.chat.id, word)
        await message.answer(
            f"🪢 Слово из {len(word)} букв загадано и ждёт своей игры.\n"
            "Напиши <code>.hangman</code> в чате с собеседником, чтобы начать."
        )
        return


@dp.business_connection()
async def on_business_connection(bc: types.BusinessConnection):
    rights = bc.rights
    can_reply = bool(getattr(rights, "can_reply", None) or getattr(bc, "can_reply", False))
    can_read = bool(getattr(rights, "can_read_messages", False))
    can_delete_sent = bool(
        getattr(rights, "can_delete_sent_messages", None)
        or getattr(rights, "can_delete_outgoing_messages", False)
    )
    can_delete_all = bool(getattr(rights, "can_delete_all_messages", False))

    await storage.upsert_connection(
        business_connection_id=bc.id,
        owner_user_id=bc.user.id,
        owner_chat_id=bc.user_chat_id,
        can_reply=can_reply,
        can_read_messages=can_read,
        can_delete_sent_messages=can_delete_sent,
        can_delete_all_messages=can_delete_all,
        is_enabled=bc.is_enabled,
    )
    log.info(
        "Business connection %s: enabled=%s delete_all=%s delete_sent=%s",
        bc.id, bc.is_enabled, can_delete_all, can_delete_sent,
    )
    if bc.is_enabled:
        note = "✅ Подключение активно."
        if not can_delete_all:
            note += (
                "\n⚠️ Право «Удаление сообщений» не выдано — команда .mute и очистка "
                "команд из чата работать не будут, но save/edit-отчёты работают."
            )
        await notify_owner(bc.user_chat_id, f"{BOT_NAME}\n{note}")
    else:
        await notify_owner(
            bc.user_chat_id,
            f"🔌 {BOT_NAME}\n"
            "Бот отключён от Telegram Business.\n"
            "Пока подключение не восстановлено, save/edit-отчёты, .mute и команды работать не будут."
        )


@dp.business_message()
async def on_business_message(message: types.Message):
    bc_id = message.business_connection_id
    chat_id = message.chat.id
    conn = await storage.get_connection(bc_id)

    is_owner = conn is not None and message.from_user and message.from_user.id == conn["owner_user_id"]

    if is_owner and message.reply_to_message:
        asyncio.create_task(handle_photo_trap(message, conn["owner_chat_id"], bc_id))

    if is_owner and message.text and message.text.startswith(CMD_PREFIX):
        body = message.text[len(CMD_PREFIX):].strip()
        if body:
            parts = body.split(maxsplit=1)
            name = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            handler = COMMANDS.get(name)
            if handler:
                if conn["can_delete_all_messages"] or conn["can_delete_sent_messages"]:
                    asyncio.create_task(try_delete_business(bc_id, message.message_id))
                
                try:
                    reply = await handler(chat_id, args, storage, bc_id, message=message, bot=bot)
                except Exception:
                    log.exception("Ошибка выполнения команды %s", name)
                    reply = f"⚠️ Ошибка при выполнении .{name}, смотри логи."
                if reply:
                    await notify_owner(conn["owner_chat_id"], reply)
                return

    if not is_owner and conn and await storage.is_muted(bc_id, chat_id):
        if conn["can_delete_all_messages"]:
            await try_delete_business(bc_id, message.message_id)
        return

    sender = sender_info(message)
    chat = chat_info(message)
    kind, file_id = media_file(message)
    await storage.cache_message(
        business_connection_id=bc_id,
        chat_id=chat_id,
        msg_id=message.message_id,
        sender_id=sender["id"],
        sender_name=sender["name"],
        sender_username=sender["username"],
        chat_name=chat["name"],
        chat_username=chat["username"],
        text=message.text or message.caption or "",
        media_type=media_label(message),
        media_kind=kind,
        media_file_id=file_id,
    )


@dp.edited_business_message()
async def on_edited_business_message(message: types.Message):
    bc_id = message.business_connection_id
    chat_id = message.chat.id
    conn = await storage.get_connection(bc_id)
    if not conn:
        return

    cached = await storage.get_cached(bc_id, chat_id, message.message_id)
    old_text = cached["text"] if cached else None
    new_text = message.text or message.caption or ""

    if old_text is not None and old_text != new_text:
        who = mention_html(cached["sender_id"], cached["sender_name"], cached["sender_username"])
        report = (
            f"✏️ <b>Изменённое сообщение</b>\n"
            f"Чат: {cached['chat_name']}\n"
            f"От: {who}\n\n"
            f"<b>Было:</b>\n{quote_html(truncate(old_text))}\n"
            f"<b>Стало:</b>\n{quote_html(truncate(new_text))}"
        )
        await notify_owner(conn["owner_chat_id"], report)

    sender = sender_info(message)
    chat = chat_info(message)
    kind, file_id = media_file(message)
    await storage.cache_message(
        business_connection_id=bc_id,
        chat_id=chat_id,
        msg_id=message.message_id,
        sender_id=sender["id"],
        sender_name=sender["name"],
        sender_username=sender["username"],
        chat_name=chat["name"],
        chat_username=chat["username"],
        text=new_text,
        media_type=media_label(message),
        media_kind=kind,
        media_file_id=file_id,
    )


@dp.deleted_business_messages()
async def on_deleted_business_messages(event: types.BusinessMessagesDeleted):
    bc_id = event.business_connection_id
    chat_id = event.chat.id
    conn = await storage.get_connection(bc_id)
    if not conn:
        return

    for msg_id in event.message_ids:
        cached = await storage.get_cached(bc_id, chat_id, msg_id)
        if not cached:
            continue
        who = mention_html(cached["sender_id"], cached["sender_name"], cached["sender_username"])
        media = f"\n{cached['media_type']}" if cached["media_type"] else ""
        report = (
            f"🗑 <b>Удалённое сообщение</b>\n"
            f"Чат: {cached['chat_name']}\n"
            f"От: {who}\n\n"
            f"{quote_html(truncate(cached['text']))}{media}"
        )
        await notify_owner(conn["owner_chat_id"], report)
        await send_recovered_media(conn["owner_chat_id"], cached.get("media_kind"), cached.get("media_file_id"))


@dp.callback_query(F.data.startswith("help:"))
async def on_help_callback(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    message = callback.message
    if message is None:
        await callback.answer()
        return

    try:
        if parts[1] == "root":
            await message.edit_text(build_help_root_text(), reply_markup=build_help_root_kb())

        elif parts[1] == "cmd" and len(parts) == 3:
            cmd_key = parts[2]
            text = build_help_cmd_text(cmd_key)
            kb = build_help_cmd_kb(cmd_key)
            if text is None or kb is None:
                await callback.answer("Команда не найдена.", show_alert=True)
                return
            await message.edit_text(text, reply_markup=kb)

        elif parts[1] == "sub" and len(parts) == 4:
            cmd_key, sub_key = parts[2], parts[3]
            text = build_help_sub_text(cmd_key, sub_key)
            if text is None:
                await callback.answer("Не найдено.", show_alert=True)
                return
            await message.edit_text(text, reply_markup=build_help_sub_kb(cmd_key))

    except TelegramAPIError:
        log.exception("Не удалось обновить меню-справку (data=%s)", callback.data)

    await callback.answer()


@dp.callback_query(F.data.startswith("ttt:"))
async def on_tic_callback(callback: types.CallbackQuery):
    data = callback.data
    message = callback.message
    bc_id = getattr(message, "business_connection_id", None) if message else None

    if message is None or not bc_id:
        await callback.answer()
        return

    chat_id = message.chat.id

    if data == "ttt:noop":
        await callback.answer()
        return

    game = await storage.get_game(bc_id, chat_id)
    if not game:
        await callback.answer("Игра не найдена или уже завершена.", show_alert=True)
        return

    user_id = callback.from_user.id

    if data == "ttt:restart":
        if game["status"] != "finished":
            await callback.answer("Игра ещё не закончена.", show_alert=True)
            return
        if user_id not in (game["x_user_id"], game["o_user_id"]):
            await callback.answer("Ты не участвуешь в этой игре.", show_alert=True)
            return

        board = new_board()
        await storage.update_game_board(bc_id, chat_id, board, "X", "playing")
        text = render_text(game["x_name"], game["o_name"], "X", None)
        keyboard = render_keyboard(board, finished=False)
        try:
            await bot.edit_message_text(
                business_connection_id=bc_id, chat_id=chat_id,
                message_id=message.message_id, text=text, reply_markup=keyboard,
            )
        except TelegramAPIError:
            log.exception("Не удалось перезапустить игру в чате %s", chat_id)
        await callback.answer("Новая игра!")
        return

    if game["status"] == "finished":
        await callback.answer("Игра уже завершена — нажми «Играть снова».", show_alert=True)
        return

    if game.get("message_id") and message.message_id != game["message_id"]:
        await callback.answer("Это старая игра, начни новую через .tic", show_alert=True)
        return

    try:
        index = int(data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
    if not (0 <= index <= 8):
        await callback.answer()
        return

    turn = game["turn"]
    expected_user = game["x_user_id"] if turn == "X" else game["o_user_id"]
    if user_id != expected_user:
        if user_id not in (game["x_user_id"], game["o_user_id"]):
            await callback.answer("Ты не участвуешь в этой игре.", show_alert=True)
        else:
            await callback.answer("Сейчас не твой ход.", show_alert=True)
        return

    board = game["board"]
    if board[index] != EMPTY:
        await callback.answer("Клетка уже занята.", show_alert=True)
        return

    board = apply_move(board, index, turn)
    result = check_result(board)

    if result is None:
        next_turn = other_mark(turn)
        await storage.update_game_board(bc_id, chat_id, board, next_turn, "playing")
        text = render_text(game["x_name"], game["o_name"], next_turn, None)
        keyboard = render_keyboard(board, finished=False)
    else:
        await storage.update_game_board(bc_id, chat_id, board, turn, "finished")
        text = render_text(game["x_name"], game["o_name"], turn, result)
        keyboard = render_keyboard(board, finished=True)

    try:
        await bot.edit_message_text(
            business_connection_id=bc_id, chat_id=chat_id,
            message_id=message.message_id, text=text, reply_markup=keyboard,
        )
    except TelegramAPIError:
        log.exception("Не удалось обновить доску игры в чате %s", chat_id)
    await callback.answer()


@dp.callback_query(F.data.startswith("rps:"))
async def on_rps_callback(callback: types.CallbackQuery):
    data = callback.data
    message = callback.message
    bc_id = getattr(message, "business_connection_id", None) if message else None

    if message is None or not bc_id:
        await callback.answer()
        return

    chat_id = message.chat.id
    game = await storage.get_rps_game(bc_id, chat_id)
    if not game:
        await callback.answer("Игра не найдена или уже завершена.", show_alert=True)
        return

    user_id = callback.from_user.id

    if data == "rps:restart":
        if game["status"] != "finished":
            await callback.answer("Игра ещё не закончена.", show_alert=True)
            return
        if user_id not in (game["x_user_id"], game["o_user_id"]):
            await callback.answer("Ты не участвуешь в этой игре.", show_alert=True)
            return

        await storage.reset_rps_game(bc_id, chat_id)
        text = rps_engine.render_text(game["x_name"], game["o_name"], None, None, finished=False)
        keyboard = rps_engine.render_keyboard(finished=False)
        try:
            await bot.edit_message_text(
                business_connection_id=bc_id, chat_id=chat_id,
                message_id=message.message_id, text=text, reply_markup=keyboard,
            )
        except TelegramAPIError:
            log.exception("Не удалось перезапустить .rps в чате %s", chat_id)
        await callback.answer("Новый раунд!")
        return

    if game["status"] == "finished":
        await callback.answer("Раунд уже завершён — нажми «Играть снова».", show_alert=True)
        return

    if game.get("message_id") and message.message_id != game["message_id"]:
        await callback.answer("Это старая игра, начни новую через .rps", show_alert=True)
        return

    if user_id not in (game["x_user_id"], game["o_user_id"]):
        await callback.answer("Ты не участвуешь в этой игре.", show_alert=True)
        return

    try:
        choice = data.split(":", 2)[2]
    except IndexError:
        await callback.answer()
        return
    if choice not in rps_engine.CHOICES:
        await callback.answer()
        return

    side = "x_choice" if user_id == game["x_user_id"] else "o_choice"
    if game[side]:
        await callback.answer(f"Ты уже выбрал: {rps_engine.CHOICES[game[side]]}", show_alert=True)
        return

    await storage.set_rps_choice(bc_id, chat_id, side, choice)
    game[side] = choice

    both_ready = bool(game["x_choice"] and game["o_choice"])
    if both_ready:
        await storage.finish_rps_game(bc_id, chat_id)
    text = rps_engine.render_text(
        game["x_name"], game["o_name"], game["x_choice"], game["o_choice"], finished=both_ready,
    )
    keyboard = rps_engine.render_keyboard(finished=both_ready)

    try:
        await bot.edit_message_text(
            business_connection_id=bc_id, chat_id=chat_id,
            message_id=message.message_id, text=text, reply_markup=keyboard,
        )
    except TelegramAPIError:
        log.exception("Не удалось обновить .rps в чате %s", chat_id)

    await callback.answer(f"Твой выбор: {rps_engine.CHOICES[choice]}")


@dp.callback_query(F.data.startswith("hm:"))
async def on_hangman_callback(callback: types.CallbackQuery):
    data = callback.data
    message = callback.message
    bc_id = getattr(message, "business_connection_id", None) if message else None

    if message is None or not bc_id:
        await callback.answer()
        return

    chat_id = message.chat.id

    if data == "hm:noop":
        await callback.answer()
        return

    game = await storage.get_hangman_game(bc_id, chat_id)
    if not game:
        await callback.answer("Игра не найдена или уже завершена.", show_alert=True)
        return

    user_id = callback.from_user.id
    if user_id not in (game["x_user_id"], game["o_user_id"]):
        await callback.answer("Ты не участвуешь в этой игре.", show_alert=True)
        return

    if data == "hm:restart":
        if game["status"] == "playing":
            await callback.answer("Игра ещё не закончена.", show_alert=True)
            return

        conn = await storage.get_connection(bc_id)
        owner_chat_id = conn["owner_chat_id"] if conn else None
        word = await storage.pop_pending_hangman_word(owner_chat_id) if owner_chat_id else None
        if not word:
            word = hangman_engine.new_word()
        await storage.start_hangman_game(
            bc_id, chat_id, word,
            game["x_user_id"], game["x_name"], game["o_user_id"], game["o_name"],
            message.message_id,
        )
        text = hangman_engine.render_text(word, set(), set(), game["x_name"], game["o_name"], status="playing")
        keyboard = hangman_engine.render_keyboard(word, set(), finished=False)
        try:
            await bot.edit_message_text(
                business_connection_id=bc_id, chat_id=chat_id,
                message_id=message.message_id, text=text, reply_markup=keyboard,
            )
        except TelegramAPIError:
            log.exception("Не удалось перезапустить .hangman в чате %s", chat_id)
        await callback.answer("Новое слово!")
        return

    if game["status"] != "playing":
        await callback.answer("Игра уже завершена — нажми «Играть снова».", show_alert=True)
        return

    if user_id == game["x_user_id"]:
        await callback.answer("Ты загадал(а) слово — отгадывает собеседник.", show_alert=True)
        return

    if game.get("message_id") and message.message_id != game["message_id"]:
        await callback.answer("Это старая игра, начни новую через .hangman", show_alert=True)
        return

    try:
        letter = data.split(":", 2)[2]
    except IndexError:
        await callback.answer()
        return

    word = game["word"]
    guessed = set(game["guessed"])
    wrong = set(game["wrong"])

    if letter in guessed or letter in wrong:
        await callback.answer("Эта буква уже использована.", show_alert=True)
        return

    if letter in word:
        guessed.add(letter)
    else:
        wrong.add(letter)

    if all(l in guessed for l in word):
        status = "won"
    elif len(wrong) >= hangman_engine.MAX_WRONG:
        status = "lost"
    else:
        status = "playing"

    guessed_str = "".join(sorted(guessed))
    wrong_str = "".join(sorted(wrong))
    await storage.apply_hangman_guess(bc_id, chat_id, guessed_str, wrong_str, status)

    finished = status != "playing"
    text = hangman_engine.render_text(word, guessed, wrong, game["x_name"], game["o_name"], status=status)
    keyboard = hangman_engine.render_keyboard(word, guessed | wrong, finished=finished)

    try:
        await bot.edit_message_text(
            business_connection_id=bc_id, chat_id=chat_id,
            message_id=message.message_id, text=text, reply_markup=keyboard,
        )
    except TelegramAPIError:
        log.exception("Не удалось обновить .hangman в чате %s", chat_id)

    await callback.answer()


CACHE_PURGE_INTERVAL_SECONDS = 6 * 3600


async def cache_purge_loop():
    max_age_seconds = int(CACHE_MAX_AGE_DAYS * 86400)
    while True:
        try:
            removed = await storage.purge_old_cache(max_age_seconds)
            if removed:
                log.info("Автоочистка кэша: удалено %s старых сообщений", removed)
        except Exception:
            log.exception("Ошибка автоочистки кэша")
        await asyncio.sleep(CACHE_PURGE_INTERVAL_SECONDS)


async def main():
    await storage.init()
    me = await bot.get_me()
    global BOT_USERNAME
    BOT_USERNAME = me.username
    log.info("%s запущен как @%s", BOT_NAME, me.username)

    await bot.set_my_commands([
        BotCommand(command="start", description="Информация о боте"),
        BotCommand(command="help", description="Все команды бота (для чатов с собеседниками)"),
        BotCommand(command="muted", description="Список замьюченных чатов"),
        BotCommand(command="hangman", description="Загадать слово для .hangman"),
    ])

    bg_task = asyncio.create_task(cache_purge_loop())

    try:
        await dp.start_polling(bot)
    finally:
        bg_task.cancel()
        await storage.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
