import asyncio
import logging
import time

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from utils import parse_duration, human_duration, chat_info, sender_info, html_escape
from tictactoe import new_board, render_text, render_keyboard

log = logging.getLogger("LimeEye")

HELP_TEXT = """<b>LimeEye — команды</b>
Пишутся прямо в чате с собеседником (не боту), с префиксом «.».

<code>.mute [время]</code> — глушить входящие сообщения в этом чате (удалять их сразу).
   Без аргумента — навсегда. Пример: <code>.mute 1h30m</code>, <code>.mute 2d</code>, <code>.mute</code>
<code>.unmute</code> — снять мьют с этого чата.
<code>.anim текст</code> — отправить сообщение с эффектом "печатает" (typing + постепенное появление слов).
<code>.spam N текст</code> — отправить "текст" N раз подряд (максимум 50 за раз).
<code>.tic</code> — начать игру в крестики-нолики с собеседником прямо в чате (кнопки под сообщением).
   Ты играешь ❌, собеседник — ⭕, ходите по очереди, нажимая на клетки.
<code>.tic stop</code> — досрочно завершить текущую игру в этом чате.
<code>.help</code> — это сообщение.

Список замьюченных чатов смотри командой /muted прямо в чате с ботом (не здесь).
Кэш сообщений (для save/edit-отчётов) очищается автоматически сам через
несколько дней — вручную чистить не нужно.

Учти: для <code>.mute</code> и очистки самой команды из чата нужно, чтобы при
подключении бота в Settings → Telegram Business → Chatbots было включено
право «Delete messages» (можно удалять чужие сообщения).
"""


def _muted_row_label(chat_id, chat_name, chat_username) -> str:
    if chat_username:
        label = f"@{chat_username}"
        if chat_name:
            label += f" ({html_escape(chat_name)})"
        return label
    if chat_name:
        return html_escape(chat_name)
    return str(chat_id)


async def cmd_mute(chat_id, args, storage, bc_id, message=None, bot=None) -> str:
    try:
        seconds = parse_duration(args)
    except ValueError as e:
        return f"⚠️ {e}"

    chat_name = chat_username = None
    if message is not None:
        info = chat_info(message)
        chat_name, chat_username = info["name"], info["username"]

    await storage.mute_chat(bc_id, chat_id, seconds, chat_name=chat_name, chat_username=chat_username)
    label = human_duration(seconds) if seconds else "навсегда"
    who = f" (@{chat_username})" if chat_username else (f" ({chat_name})" if chat_name else "")
    return f"🔇 Чат{who} замьючен ({label}). Входящие сообщения будут удаляться."


async def cmd_unmute(chat_id, args, storage, bc_id, message=None, bot=None) -> str:
    await storage.unmute_chat(bc_id, chat_id)

    who = ""
    if message is not None:
        info = chat_info(message)
        if info["username"]:
            who = f" (@{info['username']})"
        elif info["name"]:
            who = f" ({info['name']})"

    return f"🔊 Мьют снят с чата{who}."


async def cmd_muted(chat_id, args, storage, bc_id, message=None, bot=None) -> str:
    rows = await storage.list_muted(bc_id)
    if not rows:
        return "Замьюченных чатов нет."
    lines = []
    for muted_chat_id, until_ts, chat_name, chat_username in rows:
        label = _muted_row_label(muted_chat_id, chat_name, chat_username)
        if until_ts:
            remaining = max(0, int(until_ts - time.time()))
            lines.append(f"• {label} — ещё {human_duration(remaining)}")
        else:
            lines.append(f"• {label} — навсегда")
    return "🔇 <b>Замьюченные чаты:</b>\n" + "\n".join(lines)


async def cmd_anim(chat_id, args, storage, bc_id, message=None, bot=None) -> str:
    text = (args or "").strip()
    if not text:
        return "⚠️ Укажи текст: <code>.anim привет, как дела?</code>"
    if bot is None:
        return "⚠️ Анимация недоступна (нет доступа к боту)."

    words = text.split(" ")

    async def typing_pause(word: str):
        try:
            await bot.send_chat_action(business_connection_id=bc_id, chat_id=chat_id, action="typing")
        except Exception:
            pass
        # чуть больше задержка на длинные "слова" — выглядит естественнее
        await asyncio.sleep(min(1.2, 0.25 + 0.05 * len(word)))

    try:
        await typing_pause(words[0])
        sent = await bot.send_message(business_connection_id=bc_id, chat_id=chat_id, text=words[0])
        shown = words[0]
        for word in words[1:]:
            await typing_pause(word)
            shown = f"{shown} {word}"
            await bot.edit_message_text(
                business_connection_id=bc_id,
                chat_id=chat_id,
                message_id=sent.message_id,
                text=shown,
            )
    except Exception:
        return "⚠️ Не удалось отправить анимацию (смотри логи)."

    return None  # ничего не шлём владельцу отдельно — эффект уже виден в самом чате


SPAM_MAX_COUNT = 50
SPAM_DELAY_SECONDS = 0.35  # пауза между сообщениями, чтобы не словить flood-лимит


async def cmd_spam(chat_id, args, storage, bc_id, message=None, bot=None) -> str:
    if bot is None:
        return "⚠️ Команда недоступна (нет доступа к боту)."

    parts = (args or "").strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        return (
            "⚠️ Формат: <code>.spam N текст</code>, где N — число повторов "
            f"(максимум {SPAM_MAX_COUNT}). Пример: <code>.spam 5 привет</code>"
        )

    count = int(parts[0])
    text = parts[1]

    if count <= 0:
        return "⚠️ Число повторов должно быть больше нуля."
    if count > SPAM_MAX_COUNT:
        return f"⚠️ Максимум {SPAM_MAX_COUNT} сообщений за раз (запрошено {count})."

    sent_count = 0
    try:
        for _ in range(count):
            await bot.send_message(
                business_connection_id=bc_id,
                chat_id=chat_id,
                text=text,
                parse_mode=None,  # отправляем как есть, без разбора HTML-сущностей
            )
            sent_count += 1
            if sent_count < count:
                await asyncio.sleep(SPAM_DELAY_SECONDS)
    except TelegramAPIError:
        log.exception("Ошибка .spam в чате %s (bc=%s) после %s из %s сообщений", chat_id, bc_id, sent_count, count)
        return f"⚠️ Отправлено {sent_count} из {count} — дальше упёрлось в ошибку (смотри логи)."

    return f"📨 Отправлено {sent_count} сообщений."


async def cmd_tic(chat_id, args, storage, bc_id, message=None, bot=None) -> str | None:
    if bot is None or message is None:
        return "⚠️ Игра недоступна (нет доступа к боту)."

    arg = (args or "").strip().lower()
    if arg in ("stop", "cancel", "стоп"):
        existing = await storage.get_game(bc_id, chat_id)
        if not existing:
            return "Игра в этом чате не запущена."
        await storage.delete_game(bc_id, chat_id)

        outcome = "не найдено (уже удалено?)"
        if existing.get("message_id"):
            # Сначала пробуем удалить само игровое сообщение целиком — так в
            # чате не остаётся "зависшего" заголовка/статуса хода. Если прав
            # на удаление нет, тогда хотя бы обновляем текст на "игра
            # остановлена" и убираем кнопки.
            try:
                await bot.delete_business_messages(
                    business_connection_id=bc_id,
                    message_ids=[existing["message_id"]],
                )
                outcome = "сообщение с игрой удалено из чата"
            except TelegramAPIError:
                log.info(
                    "Не удалось удалить игровое сообщение %s в чате %s (нет прав?), "
                    "обновляю текст вместо удаления", existing["message_id"], chat_id,
                )
                try:
                    stopped_text = (
                        "❌⭕ <b>Крестики-нолики</b>\n"
                        f"❌ {html_escape(existing['x_name'])}  vs  ⭕ {html_escape(existing['o_name'])}\n\n"
                        "⏹ Игра остановлена."
                    )
                    await bot.edit_message_text(
                        business_connection_id=bc_id,
                        chat_id=chat_id,
                        message_id=existing["message_id"],
                        text=stopped_text,
                        reply_markup=None,
                    )
                    outcome = "нет права на удаление — заменил текст на «игра остановлена»"
                except TelegramAPIError:
                    log.exception(
                        "Не удалось обновить игровое сообщение %s в чате %s после stop",
                        existing["message_id"], chat_id,
                    )
                    outcome = "не удалось ни удалить, ни обновить сообщение (смотри логи)"

        return f"⏹ Игра остановлена ({outcome})."

    x_name = sender_info(message)["name"]
    o_name = chat_info(message)["name"]
    board = new_board()
    text = render_text(x_name, o_name, "X", None)
    keyboard = render_keyboard(board, finished=False)

    try:
        sent = await bot.send_message(
            business_connection_id=bc_id,
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
        )
    except Exception:
        log.exception("Не удалось отправить игровое поле .tic в чат %s (bc=%s)", chat_id, bc_id)
        return "⚠️ Не удалось начать игру (смотри логи)."

    await storage.start_game(
        business_connection_id=bc_id,
        chat_id=chat_id,
        board=board,
        turn="X",
        x_user_id=message.from_user.id,
        x_name=x_name,
        o_user_id=chat_id,
        o_name=o_name,
        message_id=sent.message_id,
    )
    return None


async def cmd_help(chat_id, args, storage, bc_id, message=None, bot=None) -> str:
    return HELP_TEXT


COMMANDS = {
    "mute": cmd_mute,
    "unmute": cmd_unmute,
    "anim": cmd_anim,
    "spam": cmd_spam,
    "tic": cmd_tic,
    "help": cmd_help,
}


# ---------------------------------------------------------------------------
# Интерактивная справка для /start: кнопочное меню с описанием каждой
# команды, а у команд с подкомандами (например .tic stop) — ещё один уровень
# кнопок с подробностями по каждой подкоманде.
# ---------------------------------------------------------------------------

HELP_ITEMS = [
    {
        "key": "mute",
        "button": "🔇 .mute",
        "title": "🔇 .mute [время]",
        "desc": (
            "Глушит входящие сообщения в текущем чате: всё, что пишет собеседник, "
            "бот сразу удаляет. Удобно, чтобы не получать уведомления от чата, "
            "не выходя из него и не блокируя человека.\n\n"
            "⚠️ Чтобы бот мог удалять сообщения, в настройках Business-подключения "
            "должно быть выдано право «Удаление сообщений»."
        ),
        "subs": [
            {
                "key": "forever",
                "button": "Без времени — навсегда",
                "title": ".mute — навсегда",
                "desc": "<code>.mute</code> — замьютить чат навсегда, до команды <code>.unmute</code>.",
            },
            {
                "key": "timed",
                "button": "С длительностью",
                "title": ".mute [время]",
                "desc": (
                    "<code>.mute 1h30m</code>, <code>.mute 2d</code> — замьютить на указанное "
                    "время. Поддерживаются единицы: <code>d</code> (дни), <code>h</code> (часы), "
                    "<code>m</code> (минуты), <code>s</code> (секунды) — их можно сочетать. "
                    "По истечении срока мьют снимется автоматически."
                ),
            },
        ],
    },
    {
        "key": "unmute",
        "button": "🔊 .unmute",
        "title": "🔊 .unmute",
        "desc": "Снимает мьют с текущего чата — входящие сообщения снова доходят как обычно.",
        "subs": [],
    },
    {
        "key": "anim",
        "button": "⌨️ .anim",
        "title": "⌨️ .anim текст",
        "desc": (
            "Отправляет сообщение с эффектом «печатает»: бот показывает статус typing "
            "и постепенно дописывает слова прямо в уже отправленном сообщении."
        ),
        "subs": [
            {
                "key": "usage",
                "button": "Пример использования",
                "title": ".anim текст",
                "desc": "<code>.anim привет, как дела?</code>",
            },
        ],
    },
    {
        "key": "spam",
        "button": "📨 .spam",
        "title": "📨 .spam N текст",
        "desc": (
            "Отправляет одно и то же сообщение N раз подряд в этот чат. "
            f"Максимум {SPAM_MAX_COUNT} сообщений за один вызов — если запросить больше, "
            "бот откажет и попросит уменьшить число."
        ),
        "subs": [
            {
                "key": "usage",
                "button": "Пример использования",
                "title": ".spam N текст",
                "desc": "<code>.spam 5 привет</code> — отправит «привет» 5 раз подряд.",
            },
            {
                "key": "limit",
                "button": "Ограничение",
                "title": "Лимит .spam",
                "desc": (
                    f"Больше {SPAM_MAX_COUNT} сообщений за раз отправить нельзя — это "
                    "защита от случайного флуда/бана аккаунта. Если нужно больше — "
                    "вызови команду ещё раз."
                ),
            },
        ],
    },
    {
        "key": "tic",
        "button": "❌⭕ .tic",
        "title": "❌⭕ .tic",
        "desc": (
            "Начинает игру в крестики-нолики с собеседником прямо в чате: под сообщением "
            "появляется игровое поле с кнопками. Владелец аккаунта играет ❌, собеседник — ⭕, "
            "ходите по очереди, нажимая на клетки. После завершения игры под полем появится "
            "кнопка «🔄 Играть снова»."
        ),
        "subs": [
            {
                "key": "stop",
                "button": ".tic stop — остановить игру",
                "title": ".tic stop",
                "desc": (
                    "Досрочно завершает текущую игру в этом чате: убирает игровое сообщение "
                    "из чата (или, если прав на удаление нет, помечает его как остановленное) "
                    "и сбрасывает состояние — после этого можно начать заново командой "
                    "<code>.tic</code>.\n\nСинонимы: <code>.tic cancel</code>, <code>.tic стоп</code>."
                ),
            },
        ],
    },
]

HELP_BY_KEY = {item["key"]: item for item in HELP_ITEMS}


def _find_sub(cmd_key: str, sub_key: str) -> dict | None:
    item = HELP_BY_KEY.get(cmd_key)
    if not item:
        return None
    for sub in item["subs"]:
        if sub["key"] == sub_key:
            return sub
    return None


def build_help_root_text() -> str:
    return (
        "<b>LimeEye — команды</b>\n\n"
        "Ниже список команд, которые пишутся прямо в чате с собеседником (с префиксом «.»). "
        "Нажми на любую, чтобы посмотреть подробное описание (а если у команды есть варианты "
        "использования — они тоже будут отдельными кнопками).\n\n"
        "Список замьюченных чатов смотри отдельно командой /muted прямо здесь, в чате с ботом."
    )


def build_help_root_kb() -> InlineKeyboardMarkup:
    rows = []
    row = []
    for item in HELP_ITEMS:
        row.append(InlineKeyboardButton(text=item["button"], callback_data=f"help:cmd:{item['key']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_help_cmd_text(cmd_key: str) -> str | None:
    item = HELP_BY_KEY.get(cmd_key)
    if not item:
        return None
    return f"<b>{item['title']}</b>\n\n{item['desc']}"


def build_help_cmd_kb(cmd_key: str) -> InlineKeyboardMarkup | None:
    item = HELP_BY_KEY.get(cmd_key)
    if not item:
        return None
    rows = []
    for sub in item["subs"]:
        rows.append([InlineKeyboardButton(
            text=sub["button"], callback_data=f"help:sub:{cmd_key}:{sub['key']}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Ко всем командам", callback_data="help:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_help_sub_text(cmd_key: str, sub_key: str) -> str | None:
    sub = _find_sub(cmd_key, sub_key)
    if not sub:
        return None
    return f"<b>{sub['title']}</b>\n\n{sub['desc']}"


def build_help_sub_kb(cmd_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"help:cmd:{cmd_key}")],
        [InlineKeyboardButton(text="◀️ Ко всем командам", callback_data="help:root")],
    ])
