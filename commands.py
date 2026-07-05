import asyncio
import time

from utils import parse_duration, human_duration, chat_info, html_escape

HELP_TEXT = """<b>LimeEye — команды</b>
Пишутся прямо в чате с собеседником (не боту), с префиксом «.».

<code>.mute [время]</code> — глушить входящие сообщения в этом чате (удалять их сразу).
   Без аргумента — навсегда. Пример: <code>.mute 1h30m</code>, <code>.mute 2d</code>, <code>.mute</code>
<code>.unmute</code> — снять мьют с этого чата.
<code>.muted</code> — список замьюченных чатов (с юзернеймами).
<code>.clean</code> — очистить кэш сообщений (для save/edit-отчётов) этого чата.
<code>.anim текст</code> — отправить сообщение с эффектом "печатает" (typing + постепенное появление слов).
<code>.ping</code> — проверка, что бот жив.
<code>.help</code> — это сообщение.

Учти: для <code>.mute</code>/<code>.clean</code> и очистки самой команды из чата
нужно, чтобы при подключении бота в Settings → Telegram Business → Chatbots
было включено право «Delete messages» (можно удалять чужие сообщения).
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


async def cmd_clean(chat_id, args, storage, bc_id, message=None, bot=None) -> str:
    await storage.clear_chat_cache(bc_id, chat_id)
    return "🧹 Кэш сообщений этого чата очищен."


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


async def cmd_ping(chat_id, args, storage, bc_id, message=None, bot=None) -> str:
    return "🏓 pong"


async def cmd_help(chat_id, args, storage, bc_id, message=None, bot=None) -> str:
    return HELP_TEXT


COMMANDS = {
    "mute": cmd_mute,
    "unmute": cmd_unmute,
    "muted": cmd_muted,
    "clean": cmd_clean,
    "anim": cmd_anim,
    "ping": cmd_ping,
    "help": cmd_help,
}
