from utils import parse_duration, human_duration

HELP_TEXT = """**LimeEye — команды**

`.mute [время]` — глушить входящие сообщения в этом чате (удалять их сразу).
   Без аргумента — навсегда. Пример: `.mute 1h30m`, `.mute 2d`, `.mute`
`.unmute` — снять мьют с этого чата.
`.muted` — список замьюченных чатов.
`.clean` — очистить кэш сообщений (для .save/.edit отчётов) этого чата.
`.ping` — проверка, что юзербот жив.
`.help` — это сообщение.
"""


async def cmd_mute(event, args, storage, client):
    chat_id = event.chat_id
    try:
        seconds = parse_duration(args)
    except ValueError as e:
        await event.edit(f"⚠️ {e}")
        return
    await storage.mute_chat(chat_id, seconds)
    label = human_duration(seconds) if seconds else "навсегда"
    await event.edit(f"🔇 Чат замьючен ({label}). Входящие сообщения будут удаляться.")


async def cmd_unmute(event, args, storage, client):
    await storage.unmute_chat(event.chat_id)
    await event.edit("🔊 Мьют снят с этого чата.")


async def cmd_muted(event, args, storage, client):
    rows = await storage.list_muted()
    if not rows:
        await event.edit("Замьюченных чатов нет.")
        return
    lines = []
    for chat_id, until_ts in rows:
        try:
            entity = await client.get_entity(chat_id)
            name = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(chat_id)
            username = getattr(entity, "username", None)
            if username:
                name = f"{name} (@{username})"
        except Exception:
            name = str(chat_id)
        if until_ts:
            import time
            remaining = max(0, int(until_ts - time.time()))
            lines.append(f"• {name} — ещё {human_duration(remaining)}")
        else:
            lines.append(f"• {name} — навсегда")
    await event.edit("🔇 **Замьюченные чаты:**\n" + "\n".join(lines))


async def cmd_clean(event, args, storage, client):
    await storage.clear_chat_cache(event.chat_id)
    await event.edit("🧹 Кэш сообщений этого чата очищен.")


async def cmd_ping(event, args, storage, client):
    await event.edit("🏓 pong")


async def cmd_help(event, args, storage, client):
    await event.edit(HELP_TEXT)


COMMANDS = {
    "mute": cmd_mute,
    "unmute": cmd_unmute,
    "muted": cmd_muted,
    "clean": cmd_clean,
    "ping": cmd_ping,
    "help": cmd_help,
}
