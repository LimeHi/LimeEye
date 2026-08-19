# commands.py
import asyncio
import ast
import logging
import math
import operator
import time
from datetime import datetime, timezone
from io import BytesIO

import aiohttp
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile

from utils import parse_duration, human_duration, chat_info, sender_info, html_escape
from tictactoe import new_board, render_text, render_keyboard
import rps as rps_engine
import hangman as hangman_engine

log = logging.getLogger("LimeEye")

# Юзернейм бота — выставляется один раз при старте (main.py, после bot.get_me()),
# чтобы можно было подставлять его в текст сообщения о муте без циклического импорта.
BOT_USERNAME: str | None = None

MUTE_NOTIFY_CALLBACK = "mutebtn:unmute"


def set_bot_username(username: str | None) -> None:
    global BOT_USERNAME
    BOT_USERNAME = username


def _bot_tag() -> str:
    return f"@{BOT_USERNAME}" if BOT_USERNAME else "@LimeEyeBot"


def _is_business_peer_invalid(exc: Exception) -> bool:
    """True, если Telegram отклонил отправку через business_connection_id
    из-за настроек доступа бизнес-бота к этому конкретному чату
    (Настройки → Telegram Business → Чат-боты → «Какие чаты доступны»)."""
    return "BUSINESS_PEER_INVALID" in str(exc)


_BUSINESS_PEER_INVALID_HINT = (
    "⚠️ Telegram не даёт боту писать в этот чат через бизнес-подключение "
    "(BUSINESS_PEER_INVALID).\n"
    "Проверь в Telegram: Настройки → Telegram Business → Чат-боты → «Какие чаты "
    "доступны» — этот чат, похоже, не входит в разрешённый список (например, "
    "включено «только новые чаты», а это старый диалог, либо чат в исключениях)."
)

def _mute_notify_text(unmuted: bool = False) -> str:
    if unmuted:
        return f"🔊 <b>Мьют снят.</b>\nБот: {_bot_tag()}"
    return (
        "🔇 <b>Этот чат замьючен.</b>\n"
        "Входящие сообщения удаляются автоматически.\n"
        f"Бот: {_bot_tag()}"
    )


def _mute_notify_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔊 Размьютить", callback_data=MUTE_NOTIFY_CALLBACK)]
    ])


async def send_mute_notify(bot, storage, bc_id, chat_id) -> None:
    """Шлёт в сам чат (не в личку владельцу) сообщение "чат замьючен" с кнопкой
    «Размьютить» и закрепляет его. Используется .mute, когда фишка включена в
    настройках. Если сообщение для этого чата уже отправлено и закреплено —
    повторно не шлёт (чтобы не спамить при .mute поверх уже активного мьюта)."""
    if bot is None:
        return
    existing = await storage.get_mute_notify_message_id(bc_id, chat_id)
    if existing:
        return
    try:
        sent = await bot.send_message(
            business_connection_id=bc_id,
            chat_id=chat_id,
            text=_mute_notify_text(),
            reply_markup=_mute_notify_kb(),
        )
    except TelegramAPIError:
        log.exception("Не удалось отправить сообщение о муте в чат %s (bc=%s)", chat_id, bc_id)
        return

    await storage.set_mute_notify_message_id(bc_id, chat_id, sent.message_id)

    try:
        await bot.pin_chat_message(
            business_connection_id=bc_id,
            chat_id=chat_id,
            message_id=sent.message_id,
            disable_notification=True,
        )
    except TelegramAPIError:
        log.exception(
            "Не удалось закрепить сообщение о муте в чате %s (bc=%s) — "
            "проверь право «Закрепление сообщений» в Business-подключении.",
            chat_id, bc_id,
        )


async def clear_mute_notify(bot, storage, bc_id, chat_id, message_id: int | None = None) -> None:
    """Открепляет и редактирует сообщение "чат замьючен" при .unmute (или нажатии
    кнопки «Размьютить»)."""
    if bot is None:
        return
    if message_id is None:
        message_id = await storage.get_mute_notify_message_id(bc_id, chat_id)
    if not message_id:
        return

    try:
        await bot.unpin_chat_message(
            business_connection_id=bc_id,
            chat_id=chat_id,
            message_id=message_id,
        )
    except TelegramAPIError:
        log.exception("Не удалось открепить сообщение о муте в чате %s (bc=%s)", chat_id, bc_id)

    try:
        await bot.edit_message_text(
            business_connection_id=bc_id,
            chat_id=chat_id,
            message_id=message_id,
            text=_mute_notify_text(unmuted=True),
        )
    except TelegramAPIError:
        pass  # сообщение могли удалить вручную — не критично

    await storage.set_mute_notify_message_id(bc_id, chat_id, None)


HELP_TEXT = """<b>LimeEye — команды</b>
Пишутся прямо в чате с собеседником (не боту), с префиксом «.».

<code>.mute [время]</code> — глушить входящие сообщения в этом чате (удалять их сразу).
   Без аргумента — навсегда. Пример: <code>.mute 1h30m</code>, <code>.mute 2d</code>, <code>.mute</code>
   Если в «🎛 Фишки» включено «🔇 Сообщение о муте» — при мьюте бот дополнительно
   шлёт в этот же чат закреплённое сообщение с кнопкой «Размьютить».
<code>.unmute</code> — снять мьют с этого чата (сообщение о муте, если было, открепится).
<code>.nomute текст</code> — отправить сообщение от лица бота (оригинальная команда удаляется).
<code>.anim текст</code> — отправить сообщение с эффектом "печатает" (typing + постепенное появление слов).
<code>.spam N текст</code> — отправить "текст" N раз подряд (максимум 50 за раз).
<code>.cal выражение</code> — калькулятор, ответ приходит прямо в этот чат.
   Пример: <code>.cal (2 + 3) * 4 / 7</code>, <code>.cal sqrt(2) + pi</code>
<code>.short ссылка</code> — сократить длинную ссылку, ответ приходит прямо в этот чат.
<code>.export</code> — выгрузить всю кэшированную переписку этого чата в .txt-файл себе в личку.
<code>.currency СУММА ИЗ В</code> — конвертер валют (курс на сегодня), ответ приходит прямо в этот чат.
   Пример: <code>.currency 100 USD RUB</code>
<code>.info</code> — информация об аккаунте собеседника (id, юзернейм, имя, примерная дата регистрации),
   ответ приходит тебе в личные сообщения с ботом.
<code>.rps</code> — камень-ножницы-бумага с собеседником прямо в чате (кнопки под сообщением).
<code>.rps stop</code> — досрочно завершить текущий раунд в этом чате.
<code>.hangman</code> — виселица: собеседник отгадывает слово, которое ты загадал через
   <code>/hangman слово</code> в чате с ботом (если не загадал — бот возьмёт случайное).
<code>.hangman stop</code> — досрочно завершить текущую игру в этом чате.
<code>.tic</code> — начать игру в крестики-нолики с собеседником прямо в чате (кнопки под сообщением).
<code>.tic stop</code> — досрочно завершить текущую игру в этом чате.
<code>.help</code> — это сообщение.

📸 <b>Медиа-перехват:</b> просто ответьте на исчезающее фото/видео (или любое медиа) знаком точки <code>.</code> или текстом, оканчивающимся на две точки <code>..</code> (например, «Ого..»). Бот автоматически и незаметно скачает его вам в личные сообщения. Ваше сообщение при этом не удаляется, чтобы не вызывать подозрений у пользователей модифицированных клиентов.

Список замьюченных чатов смотри командой /muted прямо в чате с ботом (не здесь).
Кэш сообщений (для save/edit-отчётов) очищается автоматически сам через несколько дней.

Учти: для <code>.mute</code> и очистки самой команды из чата нужно, чтобы при
подключении бота в Settings → Автоматизация чатов было включено
право «Delete messages» (можно удалять чужие сообщения).
Для фишки «🔇 Сообщение о муте» (закреп сообщения при мьюте) дополнительно нужно
право «Закрепление сообщений» (Pin messages) — без него сообщение отправится, но
не закрепится.
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

    if await storage.get_mute_notify_enabled(bc_id):
        await send_mute_notify(bot, storage, bc_id, chat_id)

    return f"🔇 Чат{who} замьючен ({label}). Входящие сообщения будут удаляться."


async def cmd_unmute(chat_id, args, storage, bc_id, message=None, bot=None) -> str:
    notify_message_id = await storage.get_mute_notify_message_id(bc_id, chat_id)
    await storage.unmute_chat(bc_id, chat_id)
    if notify_message_id:
        await clear_mute_notify(bot, storage, bc_id, chat_id, message_id=notify_message_id)

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
    lines = []
    for muted_chat_id, until_ts, chat_name, chat_username in rows:
        label = _muted_row_label(muted_chat_id, chat_name, chat_username)
        if until_ts:
            remaining = int(until_ts - time.time())
            if remaining <= 0:
                # срок истёк — снимаем мьют вместо показа "осталось 0s"
                await storage.unmute_chat(bc_id, muted_chat_id)
                continue
            lines.append(f"• {label} — ещё {human_duration(remaining)}")
        else:
            lines.append(f"• {label} — навсегда")
    if not lines:
        return "Замьюченных чатов нет."
    return "🔇 <b>Замьюченные чаты:</b>\n" + "\n".join(lines)


async def cmd_nomute(chat_id, args, storage, bc_id, message=None, bot=None) -> str | None:
    text = (args or "").strip()
    if not text:
        return "⚠️ Укажи текст: <code>.nomute привет</code>"
    if bot is None:
        return "⚠️ Команда недоступна (нет доступа к боту)."

    try:
        await bot.send_message(
            business_connection_id=bc_id,
            chat_id=chat_id,
            text=text,
            parse_mode=None,
        )
    except TelegramAPIError as e:
        log.exception("Ошибка .nomute в чате %s (bc=%s)", chat_id, bc_id)
        if _is_business_peer_invalid(e):
            return _BUSINESS_PEER_INVALID_HINT
        return "⚠️ Не удалось отправить сообщение (смотри логи)."

    return None


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
    except TelegramAPIError as e:
        log.exception("Ошибка .anim в чате %s (bc=%s)", chat_id, bc_id)
        if _is_business_peer_invalid(e):
            return _BUSINESS_PEER_INVALID_HINT
        return "⚠️ Не удалось отправить анимацию (смотри логи)."
    except Exception:
        log.exception("Ошибка .anim в чате %s (bc=%s)", chat_id, bc_id)
        return "⚠️ Не удалось отправить анимацию (смотри логи)."

    return None


SPAM_MAX_COUNT = 50
SPAM_DELAY_SECONDS = 0.35


async def cmd_spam(chat_id, args, storage, bc_id, message=None, bot=None) -> str | None:
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
                parse_mode=None,
            )
            sent_count += 1
            if sent_count < count:
                await asyncio.sleep(SPAM_DELAY_SECONDS)
    except TelegramAPIError as e:
        log.exception("Ошибка .spam в чате %s (bc=%s) после %s из %s сообщений", chat_id, bc_id, sent_count, count)
        if _is_business_peer_invalid(e):
            return f"⚠️ Отправлено {sent_count} из {count}.\n\n{_BUSINESS_PEER_INVALID_HINT}"
        return f"⚠️ Отправлено {sent_count} из {count} — дальше упёрлось в ошибку (смотри логи)."

    return None


_CAL_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_CAL_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_CAL_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
    "min": min,
    "max": max,
}
_CAL_CONSTS = {
    "pi": math.pi,
    "e": math.e,
}
_CAL_MAX_POWER_EXPONENT = 1000


class CalError(Exception):
    pass


def _cal_eval_node(node):
    if isinstance(node, ast.Expression):
        return _cal_eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise CalError("Разрешены только числа.")
    if isinstance(node, ast.BinOp):
        op = _CAL_BIN_OPS.get(type(node.op))
        if op is None:
            raise CalError("Неподдерживаемая операция.")
        left = _cal_eval_node(node.left)
        right = _cal_eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _CAL_MAX_POWER_EXPONENT:
            raise CalError("Слишком большая степень.")
        try:
            return op(left, right)
        except ZeroDivisionError:
            raise CalError("Деление на ноль.")
    if isinstance(node, ast.UnaryOp):
        op = _CAL_UNARY_OPS.get(type(node.op))
        if op is None:
            raise CalError("Неподдерживаемая операция.")
        return op(_cal_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _CAL_FUNCS:
            raise CalError("Неизвестная функция.")
        if node.keywords:
            raise CalError("Именованные аргументы не поддерживаются.")
        args = [_cal_eval_node(a) for a in node.args]
        try:
            return _CAL_FUNCS[node.func.id](*args)
        except (ValueError, TypeError, OverflowError) as e:
            raise CalError(f"Ошибка в функции {node.func.id}: {e}")
    if isinstance(node, ast.Name):
        if node.id in _CAL_CONSTS:
            return _CAL_CONSTS[node.id]
        raise CalError(f"Неизвестное имя: {node.id}")
    raise CalError("Неподдерживаемое выражение.")


def cal_evaluate(expr: str) -> float:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise CalError("Не удалось разобрать выражение — проверь синтаксис.")
    return _cal_eval_node(tree)


def _cal_format(value) -> str:
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.10g}"
    return str(value)


async def _send_result_to_chat(bot, bc_id, chat_id, text: str, error_prefix: str) -> str | None:
    if bot is None:
        return "⚠️ Команда недоступна (нет доступа к боту)."
    try:
        await bot.send_message(business_connection_id=bc_id, chat_id=chat_id, text=text)
    except TelegramAPIError as e:
        log.exception("%s: не удалось отправить результат в чат %s (bc=%s)", error_prefix, chat_id, bc_id)
        if _is_business_peer_invalid(e):
            return _BUSINESS_PEER_INVALID_HINT
        return "⚠️ Не удалось отправить результат в чат (смотри логи)."
    return None


async def cmd_cal(chat_id, args, storage, bc_id, message=None, bot=None) -> str | None:
    expr = (args or "").strip()
    if not expr:
        return (
            "⚠️ Формат: <code>.cal выражение</code>\n"
            "Пример: <code>.cal (2 + 3) * 4 / 7</code>, <code>.cal sqrt(2) + pi</code>"
        )

    try:
        result = cal_evaluate(expr)
    except CalError as e:
        return f"⚠️ {e}"
    except RecursionError:
        return "⚠️ Выражение слишком сложное."
    except Exception:
        log.exception(".cal: не удалось посчитать выражение %r", expr)
        return "⚠️ Не удалось посчитать (проверь выражение)."

    text = f"🧮 <code>{html_escape(expr)}</code> = <b>{html_escape(_cal_format(result))}</b>"
    return await _send_result_to_chat(bot, bc_id, chat_id, text, ".cal")


HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)


SHORTENER_UA = "LimeEyeBot/1.0 (+https://github.com/limeeye; contact via Telegram)"


async def _shorten_isgd(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(
            "https://is.gd/create.php",
            params={"format": "simple", "url": url},
            headers={"User-Agent": SHORTENER_UA},
        ) as resp:
            text = (await resp.text()).strip()
    except Exception:
        log.exception(".short: не удалось обратиться к is.gd (url=%r)", url)
        return None

    if not text.startswith("http"):
        log.warning(".short: is.gd вернул ошибку (url=%r): %s", url, text[:200])
        return None

    return text


async def _shorten_tinyurl(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(
            "https://tinyurl.com/api-create.php",
            params={"url": url},
            headers={"User-Agent": SHORTENER_UA},
        ) as resp:
            text = (await resp.text()).strip()
    except Exception:
        log.exception(".short: не удалось обратиться к tinyurl (url=%r)", url)
        return None

    if not text.startswith("http"):
        log.warning(".short: tinyurl вернул неожиданный ответ (url=%r): %s", url, text[:200])
        return None

    return text


async def cmd_short(chat_id, args, storage, bc_id, message=None, bot=None) -> str | None:
    url = (args or "").strip()
    if not url:
        return "⚠️ Формат: <code>.short ссылка</code>\nПример: <code>.short https://example.com/очень/длинная/ссылка</code>"
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        short = await _shorten_isgd(session, url)
        if not short:
            short = await _shorten_tinyurl(session, url)

    if not short:
        return "⚠️ Не удалось сократить ссылку (оба сервиса недоступны, смотри логи)."

    text = f"🔗 {html_escape(short)}"
    return await _send_result_to_chat(bot, bc_id, chat_id, text, ".short")


async def cmd_export(chat_id, args, storage, bc_id, message=None, bot=None) -> str:
    if bot is None:
        return "⚠️ Команда недоступна."

    conn = await storage.get_connection(bc_id)
    if not conn:
        return "⚠️ Не найдено подключение."

    rows = await storage.export_chat_cache(bc_id, chat_id)
    if not rows:
        return "⚠️ В кэше этого чата пока ничего нет."

    lines = []
    for row in rows:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["date"])) if row["date"] else "??"
        who = row["sender_name"] or str(row["sender_id"] or "?")
        text = row["text"] or ""
        media = f" [{row['media_type']}]" if row["media_type"] else ""
        lines.append(f"[{ts}] {who}: {text}{media}")

    body = "\n".join(lines)
    chat_label = rows[0]["chat_name"] or str(chat_id)
    filename = f"export_{chat_label}_{int(time.time())}.txt".replace(" ", "_").replace("/", "_")

    try:
        await bot.send_document(
            chat_id=conn["owner_chat_id"],
            document=BufferedInputFile(body.encode("utf-8"), filename=filename),
            caption=f"📦 Экспорт переписки: {html_escape(chat_label)} ({len(rows)} сообщений)",
        )
    except TelegramAPIError:
        log.exception("Не удалось отправить .export файл (bc=%s, chat=%s)", bc_id, chat_id)
        return "⚠️ Не удалось отправить файл экспорта (смотри логи)."

    return None


async def cmd_currency(chat_id, args, storage, bc_id, message=None, bot=None) -> str | None:
    parts = (args or "").strip().split()
    if len(parts) != 3:
        return (
            "⚠️ Формат: <code>.currency СУММА ИЗ В</code>\n"
            "Пример: <code>.currency 100 USD RUB</code>, <code>.currency 50 EUR USD</code>"
        )

    amount_str, from_code, to_code = parts
    from_code = from_code.upper()
    to_code = to_code.upper()

    try:
        amount = float(amount_str.replace(",", "."))
    except ValueError:
        return "⚠️ Сумма должна быть числом. Пример: <code>.currency 100 USD RUB</code>"

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            async with session.get(
                f"https://open.er-api.com/v6/latest/{from_code}",
            ) as resp:
                if resp.status != 200:
                    err_body = await resp.text()
                    log.warning(".currency: open.er-api вернул %s: %s", resp.status, err_body[:300])
                    return f"⚠️ Не удалось получить курс — проверь коды валют ({from_code} → {to_code})."
                data = await resp.json()
    except Exception:
        log.exception(".currency: ошибка запроса к open.er-api (%s -> %s)", from_code, to_code)
        return "⚠️ Не удалось получить курс валют (сервис недоступен, смотри логи)."

    if data.get("result") != "success":
        err_type = data.get("error-type", "unknown")
        return f"⚠️ Не нашёл курс для {from_code} → {to_code} (код ошибки: {html_escape(str(err_type))})."

    rates = data.get("rates") or {}
    if to_code not in rates:
        return f"⚠️ Не нашёл курс {from_code} → {to_code}. Проверь коды валют (ISO, например USD, EUR, RUB)."

    converted = amount * rates[to_code]
    rate_date = data.get("time_last_update_utc", "")

    text = (
        f"💱 {amount:g} {from_code} = <b>{converted:,.2f} {to_code}</b>\n"
        f"<i>курс на {html_escape(rate_date)}</i>"
    )
    return await _send_result_to_chat(bot, bc_id, chat_id, text, ".currency")


async def cmd_rps(chat_id, args, storage, bc_id, message=None, bot=None) -> str | None:
    if bot is None or message is None:
        return "⚠️ Игра недоступна (нет доступа к боту)."

    arg = (args or "").strip().lower()
    if arg in ("stop", "cancel", "стоп"):
        existing = await storage.get_rps_game(bc_id, chat_id)
        if not existing:
            return "Игра в этом чате не запущена."
        await storage.delete_rps_game(bc_id, chat_id)

        outcome = "не найдено (уже удалено?)"
        if existing.get("message_id"):
            try:
                await bot.delete_business_messages(
                    business_connection_id=bc_id,
                    message_ids=[existing["message_id"]],
                )
                outcome = "сообщение с игрой удалено из чата"
            except TelegramAPIError:
                log.info(
                    "Не удалось удалить .rps-сообщение %s в чате %s (нет прав?), "
                    "обновляю текст вместо удаления", existing["message_id"], chat_id,
                )
                try:
                    stopped_text = (
                        "🪨📄✂️ <b>Камень-ножницы-бумага</b>\n"
                        f"🅧 {html_escape(existing['x_name'])}  vs  🅞 {html_escape(existing['o_name'])}\n\n"
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
                        "Не удалось обновить .rps-сообщение %s в чате %s после stop",
                        existing["message_id"], chat_id,
                    )
                    outcome = "не удалось ни удалить, ни обновить сообщение (смотри логи)"

        return f"⏹ Игра остановлена ({outcome})."

    x_name = sender_info(message)["name"]
    o_name = chat_info(message)["name"]
    text = rps_engine.render_text(x_name, o_name, None, None, finished=False)
    keyboard = rps_engine.render_keyboard(finished=False)

    try:
        sent = await bot.send_message(
            business_connection_id=bc_id,
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
        )
    except Exception:
        log.exception("Не удалось отправить игровое поле .rps в чат %s (bc=%s)", chat_id, bc_id)
        return "⚠️ Не удалось начать игру (смотри логи)."

    await storage.start_rps_game(
        business_connection_id=bc_id,
        chat_id=chat_id,
        x_user_id=message.from_user.id,
        x_name=x_name,
        o_user_id=chat_id,
        o_name=o_name,
        message_id=sent.message_id,
    )
    return None


async def cmd_hangman(chat_id, args, storage, bc_id, message=None, bot=None) -> str | None:
    if bot is None or message is None:
        return "⚠️ Игра недоступна (нет доступа к боту)."

    arg = (args or "").strip().lower()
    if arg in ("stop", "cancel", "стоп"):
        existing = await storage.get_hangman_game(bc_id, chat_id)
        if not existing:
            return "Игра в этом чате не запущена."
        await storage.delete_hangman_game(bc_id, chat_id)

        outcome = "не найдено (уже удалено?)"
        if existing.get("message_id"):
            try:
                await bot.delete_business_messages(
                    business_connection_id=bc_id,
                    message_ids=[existing["message_id"]],
                )
                outcome = "сообщение с игрой удалено из чата"
            except TelegramAPIError:
                log.info(
                    "Не удалось удалить .hangman-сообщение %s в чате %s (нет прав?), "
                    "обновляю текст вместо удаления", existing["message_id"], chat_id,
                )
                try:
                    stopped_text = "🪢 <b>Виселица</b>\n\n⏹ Игра остановлена."
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
                        "Не удалось обновить .hangman-сообщение %s в чате %s после stop",
                        existing["message_id"], chat_id,
                    )
                    outcome = "не удалось ни удалить, ни обновить сообщение (смотри логи)"

        return f"⏹ Игра остановлена ({outcome})."

    x_name = sender_info(message)["name"]
    o_name = chat_info(message)["name"]

    conn = await storage.get_connection(bc_id)
    owner_chat_id = conn["owner_chat_id"] if conn else None
    word = await storage.pop_pending_hangman_word(owner_chat_id) if owner_chat_id else None
    if not word:
        word = hangman_engine.new_word()

    text = hangman_engine.render_text(word, set(), set(), x_name, o_name, status="playing")
    keyboard = hangman_engine.render_keyboard(word, set(), finished=False)

    try:
        sent = await bot.send_message(
            business_connection_id=bc_id,
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
        )
    except Exception:
        log.exception("Не удалось отправить игровое поле .hangman в чат %s (bc=%s)", chat_id, bc_id)
        return "⚠️ Не удалось начать игру (смотри логи)."

    await storage.start_hangman_game(
        business_connection_id=bc_id,
        chat_id=chat_id,
        word=word,
        x_user_id=message.from_user.id,
        x_name=x_name,
        o_user_id=chat_id,
        o_name=o_name,
        message_id=sent.message_id,
    )
    return None


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


# Приблизительная оценка даты регистрации аккаунта по его user_id.
# Telegram официально не отдаёт дату регистрации через Bot API — это лишь
# грубая интерполяция по общедоступным данным о примерном соответствии
# диапазонов id и периодов регистрации. Может ощутимо ошибаться.
_ID_DATE_TABLE = [
    (100_000_000, datetime(2013, 8, 1, tzinfo=timezone.utc)),
    (200_000_000, datetime(2014, 8, 1, tzinfo=timezone.utc)),
    (300_000_000, datetime(2015, 5, 1, tzinfo=timezone.utc)),
    (400_000_000, datetime(2016, 3, 1, tzinfo=timezone.utc)),
    (500_000_000, datetime(2016, 8, 1, tzinfo=timezone.utc)),
    (600_000_000, datetime(2017, 2, 1, tzinfo=timezone.utc)),
    (700_000_000, datetime(2017, 8, 1, tzinfo=timezone.utc)),
    (800_000_000, datetime(2018, 3, 1, tzinfo=timezone.utc)),
    (900_000_000, datetime(2018, 8, 1, tzinfo=timezone.utc)),
    (1_000_000_000, datetime(2019, 2, 1, tzinfo=timezone.utc)),
    (1_100_000_000, datetime(2019, 7, 1, tzinfo=timezone.utc)),
    (1_200_000_000, datetime(2019, 10, 1, tzinfo=timezone.utc)),
    (1_300_000_000, datetime(2020, 2, 1, tzinfo=timezone.utc)),
    (1_400_000_000, datetime(2020, 5, 1, tzinfo=timezone.utc)),
    (1_500_000_000, datetime(2020, 8, 1, tzinfo=timezone.utc)),
    (1_600_000_000, datetime(2020, 10, 1, tzinfo=timezone.utc)),
    (1_700_000_000, datetime(2021, 1, 1, tzinfo=timezone.utc)),
    (1_800_000_000, datetime(2021, 3, 1, tzinfo=timezone.utc)),
    (1_900_000_000, datetime(2021, 5, 1, tzinfo=timezone.utc)),
    (2_000_000_000, datetime(2021, 8, 1, tzinfo=timezone.utc)),
    (3_000_000_000, datetime(2022, 3, 1, tzinfo=timezone.utc)),
    (4_000_000_000, datetime(2022, 8, 1, tzinfo=timezone.utc)),
    (5_000_000_000, datetime(2023, 1, 1, tzinfo=timezone.utc)),
    (6_000_000_000, datetime(2023, 8, 1, tzinfo=timezone.utc)),
    (7_000_000_000, datetime(2024, 3, 1, tzinfo=timezone.utc)),
    (8_000_000_000, datetime(2024, 10, 1, tzinfo=timezone.utc)),
]


def _interp_date(lo_id, lo_date, hi_id, hi_date, user_id) -> datetime:
    frac = (user_id - lo_id) / (hi_id - lo_id)
    delta = hi_date - lo_date
    return lo_date + delta * frac


def estimate_registration_date(user_id: int) -> datetime | None:
    if not user_id or user_id <= 0:
        return None

    first_id, first_date = _ID_DATE_TABLE[0]
    if user_id <= first_id:
        return first_date

    last_id, last_date = _ID_DATE_TABLE[-1]
    if user_id >= last_id:
        return last_date

    for (lo_id, lo_date), (hi_id, hi_date) in zip(_ID_DATE_TABLE, _ID_DATE_TABLE[1:]):
        if lo_id <= user_id <= hi_id:
            return _interp_date(lo_id, lo_date, hi_id, hi_date, user_id)
    return None


async def cmd_info(chat_id, args, storage, bc_id, message=None, bot=None) -> str:
    user_id = chat_id
    username = None
    name = None

    if message is not None:
        chat = message.chat
        username = getattr(chat, "username", None)
        first = getattr(chat, "first_name", None)
        last = getattr(chat, "last_name", None)
        if first or last:
            name = " ".join(p for p in (first, last) if p)

    # Пробуем получить более свежие данные через getChat, но не роняем
    # команду, если это не удалось (например, нет прав/чат недоступен).
    if bot is not None:
        try:
            fresh = await bot.get_chat(chat_id, business_connection_id=bc_id)
            username = getattr(fresh, "username", None) or username
            fresh_first = getattr(fresh, "first_name", None)
            fresh_last = getattr(fresh, "last_name", None)
            if fresh_first or fresh_last:
                name = " ".join(p for p in (fresh_first, fresh_last) if p)
        except Exception:
            log.exception(".info: не удалось получить getChat (chat_id=%s, bc=%s)", chat_id, bc_id)

    reg_date = estimate_registration_date(user_id)
    reg_str = reg_date.strftime("%B %Y") if reg_date else "не удалось оценить"

    lines = [
        "📇 <b>Информация об аккаунте</b>",
        f"🆔 ID: <code>{user_id}</code>",
        f"👤 Имя: {html_escape(name) if name else 'не задано'}",
        f"🔗 Юзернейм: @{html_escape(username)}" if username else "🔗 Юзернейм: не задан",
        f"📅 Дата регистрации (оценочно): {html_escape(reg_str)}",
        "",
        "<i>Дата регистрации — приблизительная оценка по id, Telegram её официально не раскрывает.</i>",
    ]
    return "\n".join(lines)


async def cmd_help(chat_id, args, storage, bc_id, message=None, bot=None) -> str:
    return HELP_TEXT


COMMANDS = {
    "mute": cmd_mute,
    "unmute": cmd_unmute,
    "nomute": cmd_nomute,
    "anim": cmd_anim,
    "spam": cmd_spam,
    "cal": cmd_cal,
    "short": cmd_short,
    "export": cmd_export,
    "currency": cmd_currency,
    "info": cmd_info,
    "rps": cmd_rps,
    "hangman": cmd_hangman,
    "tic": cmd_tic,
    "help": cmd_help,
}


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
            {
                "key": "notify",
                "button": "🔇 Сообщение о муте",
                "title": "🔇 Сообщение о муте",
                "desc": (
                    "Отдельная фишка, включается/выключается в «🎛 Фишки» → «🔇 Сообщение "
                    "о муте» (в личном чате с ботом).\n\n"
                    "Пока включена: каждый раз при <code>.mute</code> бот отправляет прямо в "
                    "сам чат сообщение о том, что чат замьючен (с упоминанием бота) и кнопкой "
                    "«🔊 Размьютить», и закрепляет его. Кнопку может нажать только владелец "
                    "аккаунта — при нажатии (или при <code>.unmute</code>) сообщение открепляется "
                    "и обновляется на «мьют снят».\n\n"
                    "Требует право «Закрепление сообщений» в Business-подключении — без него "
                    "сообщение отправится, но не закрепится."
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
        "key": "nomute",
        "button": "💬 .nomute",
        "title": "💬 .nomute текст",
        "desc": (
            "Бот отправляет указанный текст от своего имени. "
            "Оригинальное сообщение с командой удаляется. "
            "Может использоваться для обхода некоторых видов ограничений в чате."
        ),
        "subs": [],
    },
    {
        "key": "trap",
        "button": "📸 Медиа-перехват",
        "title": "📸 Медиа-перехват (Скрытое сохранение)",
        "desc": (
            "Автоматическое и незаметное сохранение медиафайлов.\n\n"
            "Ответьте на исчезающее фото, видео, голосовое сообщение или другой файл "
            "одним символом точки <code>.</code> или любым текстом, заканчивающимся двумя точками <code>..</code> "
            "(например, «Понятно..»).\n\n"
            "Бот скачает файл и отправит его в ваш личный чат с ботом. При этом ваше сообщение "
            "останется в чате. Это гарантирует полную скрытность: собеседник (даже использующий "
            "Ayugram или другие модифицированные клиенты) увидит лишь обычный ответ и ничего не заподозрит."
        ),
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
        "key": "cal",
        "button": "🧮 .cal",
        "title": "🧮 .cal выражение",
        "desc": (
            "Калькулятор. Считает выражение и присылает ответ прямо в этот чат.\n\n"
            "Поддерживает: <code>+ - * / // % **</code>, скобки, и функции "
            "<code>sqrt, abs, round, sin, cos, tan, log, log10, log2, exp, floor, ceil, "
            "factorial, min, max</code>, а также константы <code>pi</code> и <code>e</code>."
        ),
        "subs": [
            {
                "key": "usage",
                "button": "Пример использования",
                "title": ".cal выражение",
                "desc": (
                    "<code>.cal (2 + 3) * 4 / 7</code>\n"
                    "<code>.cal sqrt(2) + pi</code>\n"
                    "<code>.cal 2 ** 10</code>"
                ),
            },
        ],
    },
    {
        "key": "short",
        "button": "🔗 .short",
        "title": "🔗 .short ссылка",
        "desc": "Сокращает длинную ссылку (is.gd, а если он недоступен — TinyURL). Ответ (короткая ссылка) приходит прямо в этот чат.",
        "subs": [
            {
                "key": "usage",
                "button": "Пример использования",
                "title": ".short ссылка",
                "desc": "<code>.short https://example.com/очень/длинный/путь</code>",
            },
        ],
    },
    {
        "key": "export",
        "button": "📦 .export",
        "title": "📦 .export",
        "desc": (
            "Выгружает всю переписку этого чата, что успела попасть в локальный кэш "
            "(для save/edit-отчётов), в один .txt-файл и присылает его тебе в личку с ботом. "
            "Удобно как бэкап — на случай, если собеседник почистит историю у себя.\n\n"
            "Учти: кэш хранит ограниченное число сообщений на чат и сам стирается через "
            "несколько дней — экспорт вытащит только то, что ещё есть в кэше на момент вызова."
        ),
        "subs": [],
    },
    {
        "key": "currency",
        "button": "💱 .currency",
        "title": "💱 .currency СУММА ИЗ В",
        "desc": (
            "Конвертер валют (без ключей и лимитов), поддерживает ~160 валют, включая RUB. "
            "Ответ приходит прямо в этот чат.\n\n"
            "Коды валют — трёхбуквенные (ISO 4217): USD, EUR, RUB, GBP и т.д."
        ),
        "subs": [
            {
                "key": "usage",
                "button": "Пример использования",
                "title": ".currency СУММА ИЗ В",
                "desc": "<code>.currency 100 USD RUB</code>\n<code>.currency 50 EUR USD</code>",
            },
        ],
    },
    {
        "key": "info",
        "button": "📇 .info",
        "title": "📇 .info",
        "desc": (
            "Показывает информацию об аккаунте собеседника в этом чате: id, юзернейм, имя и "
            "примерную дату регистрации. Ответ приходит не в чат, а тебе в личные сообщения с ботом.\n\n"
            "⚠️ Дата регистрации — грубая оценка по диапазону id (Telegram официально её не раскрывает), "
            "может ощутимо отличаться от реальной."
        ),
        "subs": [],
    },
    {
        "key": "rps",
        "button": "🪨 .rps",
        "title": "🪨📄✂️ .rps",
        "desc": (
            "Камень-ножницы-бумага с собеседником прямо в чате: под сообщением появляются "
            "три кнопки. Оба выбирают втайне друг от друга — выбор виден только тебе самому "
            "(всплывающей подсказкой), пока не выберут оба. Как только оба готовы, бот "
            "раскрывает оба варианта и результат. После раунда появится кнопка "
            "«🔄 Играть снова»."
        ),
        "subs": [
            {
                "key": "stop",
                "button": ".rps stop — остановить раунд",
                "title": ".rps stop",
                "desc": (
                    "Досрочно завершает текущий раунд в этом чате: убирает сообщение с игрой "
                    "(или, если прав на удаление нет, помечает его как остановленное) — после "
                    "этого можно начать заново командой <code>.rps</code>."
                ),
            },
        ],
    },
    {
        "key": "hangman",
        "button": "🪢 .hangman",
        "title": "🪢 .hangman",
        "desc": (
            "Виселица. Отгадывает только <b>собеседник</b> — нажимает буквы на клавиатуре "
            "под сообщением. Слово загадываешь ты сам: напиши боту в личном чате "
            "<code>/hangman слово</code> — оно будет использовано в следующей игре, начатой "
            "командой <code>.hangman</code> в любом чате (одноразово). Если слово заранее не "
            "загадано, бот возьмёт случайное из встроенного словаря. Правильная буква "
            "открывается на своём месте, неверная — добавляет часть к рисунку виселицы. "
            "6 ошибок — и игра проиграна; все буквы открыты — победа. Слово в любом случае "
            "показывается целиком по завершении, с кнопкой «🔄 Играть снова» (для новой "
            "игры тоже можно заранее загадать слово через <code>/hangman</code>)."
        ),
        "subs": [
            {
                "key": "stop",
                "button": ".hangman stop — остановить игру",
                "title": ".hangman stop",
                "desc": (
                    "Досрочно завершает текущую игру в этом чате: убирает сообщение с игрой "
                    "(или, если прав на удаление нет, помечает его как остановленное) — после "
                    "этого можно начать заново командой <code>.hangman</code>."
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
