from utils import parse_duration, human_duration

HELP_TEXT = """<b>LimeEye — команды</b>
Пишутся прямо в чате с собеседником (не боту), с префиксом «.».

<code>.mute [время]</code> — глушить входящие сообщения в этом чате (удалять их сразу).
   Без аргумента — навсегда. Пример: <code>.mute 1h30m</code>, <code>.mute 2d</code>, <code>.mute</code>
<code>.unmute</code> — снять мьют с этого чата.
<code>.muted</code> — список замьюченных чатов.
<code>.clean</code> — очистить кэш сообщений (для save/edit-отчётов) этого чата.
<code>.ping</code> — проверка, что бот жив.
<code>.help</code> — это сообщение.

Учти: для <code>.mute</code>/<code>.clean</code> и очистки самой команды из чата
нужно, чтобы при подключении бота в Settings → Telegram Business → Chatbots
было включено право «Delete messages» (можно удалять чужие сообщения).
"""


async def cmd_mute(chat_id, args, storage, bc_id) -> str:
    try:
        seconds = parse_duration(args)
    except ValueError as e:
        return f"⚠️ {e}"
    await storage.mute_chat(bc_id, chat_id, seconds)
    label = human_duration(seconds) if seconds else "навсегда"
    return f"🔇 Чат замьючен ({label}). Входящие сообщения будут удаляться."


async def cmd_unmute(chat_id, args, storage, bc_id) -> str:
    await storage.unmute_chat(bc_id, chat_id)
    return "🔊 Мьют снят с этого чата."


async def cmd_muted(chat_id, args, storage, bc_id) -> str:
    rows = await storage.list_muted(bc_id)
    if not rows:
        return "Замьюченных чатов нет."
    import time
    lines = []
    for muted_chat_id, until_ts in rows:
        if until_ts:
            remaining = max(0, int(until_ts - time.time()))
            lines.append(f"• {muted_chat_id} — ещё {human_duration(remaining)}")
        else:
            lines.append(f"• {muted_chat_id} — навсегда")
    return "🔇 <b>Замьюченные чаты:</b>\n" + "\n".join(lines)


async def cmd_clean(chat_id, args, storage, bc_id) -> str:
    await storage.clear_chat_cache(bc_id, chat_id)
    return "🧹 Кэш сообщений этого чата очищен."


async def cmd_ping(chat_id, args, storage, bc_id) -> str:
    return "🏓 pong"


async def cmd_help(chat_id, args, storage, bc_id) -> str:
    return HELP_TEXT


COMMANDS = {
    "mute": cmd_mute,
    "unmute": cmd_unmute,
    "muted": cmd_muted,
    "clean": cmd_clean,
    "ping": cmd_ping,
    "help": cmd_help,
}
