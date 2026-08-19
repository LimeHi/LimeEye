import time
import aiosqlite
from cryptography.fernet import Fernet, InvalidToken

from config import DB_PATH, CACHE_LIMIT_PER_CHAT, DB_ENCRYPTION_KEY

_fernet = Fernet(DB_ENCRYPTION_KEY.encode() if isinstance(DB_ENCRYPTION_KEY, str) else DB_ENCRYPTION_KEY)


def _enc(value: str | None) -> str | None:
    """Шифрует строку перед записью в БД. None остаётся None (не шифруем отсутствие данных)."""
    if value is None:
        return None
    return _fernet.encrypt(value.encode("utf-8")).decode("ascii")


def _dec(value: str | None) -> str | None:
    """Расшифровывает строку, прочитанную из БД.

    Устойчиво к старым/битым записям: если значение не похоже на токен Fernet
    (например, это данные из БД, созданной до включения шифрования), возвращаем
    как есть, а не роняем бота."""
    if value is None:
        return None
    try:
        return _fernet.decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return value


_MESSAGE_ENCRYPTED_FIELDS = (
    "sender_name", "sender_username", "chat_name", "chat_username", "text", "media_file_id",
)


def _decrypt_message_row(row: dict) -> dict:
    for field in _MESSAGE_ENCRYPTED_FIELDS:
        if field in row:
            row[field] = _dec(row[field])
    return row

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    business_connection_id TEXT PRIMARY KEY,
    owner_user_id INTEGER,
    owner_chat_id INTEGER,     -- личный чат бота с владельцем (для отчётов/ответов на команды)
    owner_name TEXT,
    owner_username TEXT,
    can_reply INTEGER,
    can_read_messages INTEGER,
    can_delete_sent_messages INTEGER,
    can_delete_all_messages INTEGER,
    is_enabled INTEGER,
    created_at INTEGER,
    updated_at INTEGER
);

-- Каждый уникальный пользователь, когда-либо нажавший /start в личке с ботом
-- (независимо от того, подключил он потом Business-аккаунт или нет).
-- Нужно только для админской статистики "сколько людей зашли в бота".
CREATE TABLE IF NOT EXISTS bot_users (
    user_id INTEGER PRIMARY KEY,
    chat_id INTEGER,
    name TEXT,
    username TEXT,
    first_seen INTEGER,
    last_seen INTEGER,
    starts_count INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS messages_cache (
    business_connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    msg_id INTEGER NOT NULL,
    sender_id INTEGER,
    sender_name TEXT,
    sender_username TEXT,
    chat_name TEXT,
    chat_username TEXT,
    text TEXT,
    media_type TEXT,
    media_kind TEXT,      -- photo/video/voice/video_note/document/animation/audio — для повторной отправки
    media_file_id TEXT,   -- file_id, чтобы переслать файл заново (send_photo/send_video/...)
    date INTEGER,
    PRIMARY KEY (business_connection_id, chat_id, msg_id)
);

CREATE TABLE IF NOT EXISTS muted_chats (
    business_connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    until_ts INTEGER,   -- NULL = навсегда
    muted_at INTEGER,
    chat_name TEXT,
    chat_username TEXT,
    notify_message_id INTEGER,  -- id закреплённого сообщения "чат замьючен" (для .unmute/открепления)
    PRIMARY KEY (business_connection_id, chat_id)
);

-- Фишка "Сообщение о муте": настройка на каждое business-подключение.
-- Если включена — .mute шлёт в сам чат сообщение с кнопкой «Размьютить» и закрепляет
-- его, а .unmute (или нажатие кнопки) открепляет.
CREATE TABLE IF NOT EXISTS mute_notify_settings (
    business_connection_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS tic_games (
    business_connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER,
    board TEXT NOT NULL,        -- 9 символов: '.', 'X', 'O'
    turn TEXT NOT NULL,         -- 'X' или 'O' — чей сейчас ход
    status TEXT NOT NULL,       -- 'playing' | 'finished'
    x_user_id INTEGER,
    x_name TEXT,
    o_user_id INTEGER,
    o_name TEXT,
    updated_at INTEGER,
    PRIMARY KEY (business_connection_id, chat_id)
);

CREATE TABLE IF NOT EXISTS rps_games (
    business_connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER,
    status TEXT NOT NULL,       -- 'playing' | 'finished'
    x_user_id INTEGER,
    x_name TEXT,
    x_choice TEXT,              -- 'rock' | 'paper' | 'scissors' | NULL
    o_user_id INTEGER,
    o_name TEXT,
    o_choice TEXT,
    updated_at INTEGER,
    PRIMARY KEY (business_connection_id, chat_id)
);

CREATE TABLE IF NOT EXISTS hangman_games (
    business_connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER,
    word TEXT NOT NULL,
    guessed TEXT NOT NULL DEFAULT '',   -- все открытые/угаданные буквы подряд, без разделителей
    wrong TEXT NOT NULL DEFAULT '',     -- неверно угаданные буквы подряд, без разделителей
    status TEXT NOT NULL,               -- 'playing' | 'won' | 'lost'
    x_user_id INTEGER,
    x_name TEXT,
    o_user_id INTEGER,
    o_name TEXT,
    updated_at INTEGER,
    PRIMARY KEY (business_connection_id, chat_id)
);

CREATE TABLE IF NOT EXISTS pending_hangman_words (
    owner_chat_id INTEGER PRIMARY KEY,   -- личный чат бота с владельцем, который загадал слово
    word TEXT NOT NULL,
    created_at INTEGER
);

-- Фишка "Часы в имени/фамилии": настройка на каждое business-подключение.
CREATE TABLE IF NOT EXISTS clock_settings (
    business_connection_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    target TEXT NOT NULL DEFAULT 'first',  -- 'first' (имя) | 'last' (фамилия)
    last_value TEXT,                       -- последнее выставленное значение времени (не шифруем, не чувствительно)
    updated_at INTEGER
);
"""

# Колонки, добавленные уже после первого релиза — накатываются на существующие
# базы отдельно от CREATE TABLE (SQLite не умеет ALTER TABLE ... IF NOT EXISTS).
MIGRATIONS = [
    ("muted_chats", "chat_name", "TEXT"),
    ("muted_chats", "chat_username", "TEXT"),
    ("messages_cache", "media_kind", "TEXT"),
    ("messages_cache", "media_file_id", "TEXT"),
    ("connections", "owner_name", "TEXT"),
    ("connections", "owner_username", "TEXT"),
    ("connections", "created_at", "INTEGER"),
    ("connections", "owner_first_name", "TEXT"),
    ("connections", "owner_last_name", "TEXT"),
    ("connections", "can_edit_name", "INTEGER"),
    ("bot_users", "blocked", "INTEGER DEFAULT 0"),
    ("muted_chats", "notify_message_id", "INTEGER"),
]


class Storage:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def init(self):
        self._db = await aiosqlite.connect(self.path)
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        await self._run_migrations()

    async def _run_migrations(self):
        for table, column, col_type in MIGRATIONS:
            try:
                await self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                await self._db.commit()
            except aiosqlite.OperationalError:
                pass  # колонка уже есть — база не первый раз запускается

    async def close(self):
        if self._db:
            await self._db.close()

    # ---------- бизнес-подключения ----------

    async def upsert_connection(self, business_connection_id, owner_user_id, owner_chat_id,
                                 can_reply, can_read_messages, can_delete_sent_messages,
                                 can_delete_all_messages, is_enabled,
                                 owner_name=None, owner_username=None,
                                 owner_first_name=None, owner_last_name=None,
                                 can_edit_name=False):
        now = int(time.time())
        await self._db.execute(
            """INSERT INTO connections
               (business_connection_id, owner_user_id, owner_chat_id, owner_name, owner_username,
                owner_first_name, owner_last_name, can_reply, can_read_messages,
                can_delete_sent_messages, can_delete_all_messages, can_edit_name,
                is_enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(business_connection_id) DO UPDATE SET
                 owner_user_id=excluded.owner_user_id,
                 owner_chat_id=excluded.owner_chat_id,
                 owner_name=excluded.owner_name,
                 owner_username=excluded.owner_username,
                 owner_first_name=excluded.owner_first_name,
                 owner_last_name=excluded.owner_last_name,
                 can_reply=excluded.can_reply,
                 can_read_messages=excluded.can_read_messages,
                 can_delete_sent_messages=excluded.can_delete_sent_messages,
                 can_delete_all_messages=excluded.can_delete_all_messages,
                 can_edit_name=excluded.can_edit_name,
                 is_enabled=excluded.is_enabled,
                 updated_at=excluded.updated_at
            """,
            (business_connection_id, owner_user_id, owner_chat_id, _enc(owner_name), _enc(owner_username),
             _enc(owner_first_name), _enc(owner_last_name),
             int(can_reply), int(can_read_messages), int(can_delete_sent_messages),
             int(can_delete_all_messages), int(can_edit_name), int(is_enabled), now, now),
        )
        await self._db.commit()

    async def get_connection(self, business_connection_id):
        cur = await self._db.execute(
            "SELECT business_connection_id, owner_user_id, owner_chat_id, can_reply, "
            "can_read_messages, can_delete_sent_messages, can_delete_all_messages, is_enabled, "
            "owner_first_name, owner_last_name, can_edit_name "
            "FROM connections WHERE business_connection_id = ?",
            (business_connection_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        keys = ["business_connection_id", "owner_user_id", "owner_chat_id", "can_reply",
                "can_read_messages", "can_delete_sent_messages", "can_delete_all_messages",
                "is_enabled", "owner_first_name", "owner_last_name", "can_edit_name"]
        data = dict(zip(keys, row))
        for k in ("can_reply", "can_read_messages", "can_delete_sent_messages",
                  "can_delete_all_messages", "is_enabled", "can_edit_name"):
            data[k] = bool(data[k])
        data["owner_first_name"] = _dec(data["owner_first_name"])
        data["owner_last_name"] = _dec(data["owner_last_name"])
        return data

    # ---------- кэш сообщений ----------

    async def cache_message(self, business_connection_id, chat_id, msg_id, sender_id,
                             sender_name, sender_username, chat_name, chat_username,
                             text, media_type, media_kind=None, media_file_id=None):
        await self._db.execute(
            """INSERT INTO messages_cache
               (business_connection_id, chat_id, msg_id, sender_id, sender_name, sender_username,
                chat_name, chat_username, text, media_type, media_kind, media_file_id, date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(business_connection_id, chat_id, msg_id) DO UPDATE SET
                 sender_id=excluded.sender_id,
                 sender_name=excluded.sender_name,
                 sender_username=excluded.sender_username,
                 chat_name=excluded.chat_name,
                 chat_username=excluded.chat_username,
                 text=excluded.text,
                 media_type=excluded.media_type,
                 media_kind=excluded.media_kind,
                 media_file_id=excluded.media_file_id,
                 date=excluded.date
            """,
            (business_connection_id, chat_id, msg_id, sender_id, _enc(sender_name), _enc(sender_username),
             _enc(chat_name), _enc(chat_username), _enc(text), media_type, media_kind,
             _enc(media_file_id), int(time.time())),
        )
        await self._db.commit()
        await self._trim(business_connection_id, chat_id)

    async def _trim(self, business_connection_id, chat_id):
        await self._db.execute(
            """DELETE FROM messages_cache
               WHERE business_connection_id = ? AND chat_id = ? AND msg_id NOT IN (
                   SELECT msg_id FROM messages_cache
                   WHERE business_connection_id = ? AND chat_id = ?
                   ORDER BY msg_id DESC
                   LIMIT ?
               )""",
            (business_connection_id, chat_id, business_connection_id, chat_id, CACHE_LIMIT_PER_CHAT),
        )
        await self._db.commit()

    async def get_cached(self, business_connection_id, chat_id, msg_id):
        cur = await self._db.execute(
            "SELECT business_connection_id, chat_id, msg_id, sender_id, sender_name, "
            "sender_username, chat_name, chat_username, text, media_type, media_kind, "
            "media_file_id, date "
            "FROM messages_cache WHERE business_connection_id = ? AND chat_id = ? AND msg_id = ?",
            (business_connection_id, chat_id, msg_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        keys = ["business_connection_id", "chat_id", "msg_id", "sender_id", "sender_name",
                "sender_username", "chat_name", "chat_username", "text", "media_type",
                "media_kind", "media_file_id", "date"]
        return _decrypt_message_row(dict(zip(keys, row)))

    async def clear_chat_cache(self, business_connection_id, chat_id):
        await self._db.execute(
            "DELETE FROM messages_cache WHERE business_connection_id = ? AND chat_id = ?",
            (business_connection_id, chat_id),
        )
        await self._db.commit()

    async def export_chat_cache(self, business_connection_id, chat_id):
        """Все закэшированные сообщения чата, от старых к новым — для .export."""
        cur = await self._db.execute(
            "SELECT business_connection_id, chat_id, msg_id, sender_id, sender_name, "
            "sender_username, chat_name, chat_username, text, media_type, media_kind, "
            "media_file_id, date "
            "FROM messages_cache WHERE business_connection_id = ? AND chat_id = ? "
            "ORDER BY date ASC, msg_id ASC",
            (business_connection_id, chat_id),
        )
        rows = await cur.fetchall()
        keys = ["business_connection_id", "chat_id", "msg_id", "sender_id", "sender_name",
                "sender_username", "chat_name", "chat_username", "text", "media_type",
                "media_kind", "media_file_id", "date"]
        return [_decrypt_message_row(dict(zip(keys, row))) for row in rows]

    async def purge_old_cache(self, max_age_seconds: int) -> int:
        """Удаляет из кэша сообщения старше max_age_seconds. Возвращает число
        удалённых строк — вызывается периодически вместо ручной .clean."""
        cutoff = int(time.time()) - max_age_seconds
        cur = await self._db.execute(
            "DELETE FROM messages_cache WHERE date < ?",
            (cutoff,),
        )
        await self._db.commit()
        return cur.rowcount

    # ---------- замьюченные чаты ----------

    async def mute_chat(self, business_connection_id, chat_id, duration_seconds: int | None,
                         chat_name: str | None = None, chat_username: str | None = None):
        until_ts = int(time.time()) + duration_seconds if duration_seconds else None
        await self._db.execute(
            """INSERT INTO muted_chats
               (business_connection_id, chat_id, until_ts, muted_at, chat_name, chat_username)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(business_connection_id, chat_id) DO UPDATE SET
                 until_ts=excluded.until_ts, muted_at=excluded.muted_at,
                 chat_name=excluded.chat_name, chat_username=excluded.chat_username""",
            (business_connection_id, chat_id, until_ts, int(time.time()), _enc(chat_name), _enc(chat_username)),
        )
        await self._db.commit()

    async def unmute_chat(self, business_connection_id, chat_id):
        await self._db.execute(
            "DELETE FROM muted_chats WHERE business_connection_id = ? AND chat_id = ?",
            (business_connection_id, chat_id),
        )
        await self._db.commit()

    async def set_mute_notify_message_id(self, business_connection_id, chat_id, message_id: int):
        """Запоминает id отправленного и закреплённого сообщения «чат замьючен»,
        чтобы потом (при .unmute) можно было его открепить/отредактировать."""
        await self._db.execute(
            "UPDATE muted_chats SET notify_message_id = ? "
            "WHERE business_connection_id = ? AND chat_id = ?",
            (message_id, business_connection_id, chat_id),
        )
        await self._db.commit()

    async def get_mute_notify_message_id(self, business_connection_id, chat_id) -> int | None:
        cur = await self._db.execute(
            "SELECT notify_message_id FROM muted_chats "
            "WHERE business_connection_id = ? AND chat_id = ?",
            (business_connection_id, chat_id),
        )
        row = await cur.fetchone()
        return row[0] if row and row[0] else None

    async def is_muted(self, business_connection_id, chat_id) -> bool:
        cur = await self._db.execute(
            "SELECT until_ts FROM muted_chats WHERE business_connection_id = ? AND chat_id = ?",
            (business_connection_id, chat_id),
        )
        row = await cur.fetchone()
        if not row:
            return False
        until_ts = row[0]
        if until_ts is not None and until_ts < time.time():
            await self.unmute_chat(business_connection_id, chat_id)
            return False
        return True

    async def list_muted(self, business_connection_id):
        cur = await self._db.execute(
            "SELECT chat_id, until_ts, chat_name, chat_username "
            "FROM muted_chats WHERE business_connection_id = ?",
            (business_connection_id,),
        )
        rows = await cur.fetchall()
        return [(chat_id, until_ts, _dec(chat_name), _dec(chat_username))
                for chat_id, until_ts, chat_name, chat_username in rows]

    # ---------- поиск подключений владельца (для команд в личке с ботом) ----------

    async def get_owner_connections(self, owner_chat_id):
        """Все business-подключения, репорты которых приходят в этот личный чат с ботом."""
        cur = await self._db.execute(
            "SELECT business_connection_id FROM connections "
            "WHERE owner_chat_id = ? AND is_enabled = 1",
            (owner_chat_id,),
        )
        rows = await cur.fetchall()
        return [row[0] for row in rows]

    # ---------- пользователи бота (/start) и админ-статистика ----------

    async def touch_user(self, user_id, chat_id, name, username):
        """Регистрирует /start пользователя: первый визит — insert, повторный — счётчик+1."""
        now = int(time.time())
        await self._db.execute(
            """INSERT INTO bot_users (user_id, chat_id, name, username, first_seen, last_seen, starts_count)
               VALUES (?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(user_id) DO UPDATE SET
                 chat_id=excluded.chat_id,
                 name=excluded.name,
                 username=excluded.username,
                 last_seen=excluded.last_seen,
                 starts_count=starts_count + 1,
                 blocked=0
            """,
            (user_id, chat_id, _enc(name), _enc(username), now, now),
        )
        await self._db.commit()

    async def count_users(self) -> int:
        cur = await self._db.execute("SELECT COUNT(*) FROM bot_users")
        row = await cur.fetchone()
        return row[0] if row else 0

    async def count_connections(self):
        """Возвращает (всего_когда-либо_подключений, сейчас_активных)."""
        cur = await self._db.execute("SELECT COUNT(*), COALESCE(SUM(is_enabled), 0) FROM connections")
        row = await cur.fetchone()
        return (row[0] or 0, row[1] or 0)

    async def list_connections(self, limit: int = 30, only_enabled: bool = False):
        """Последние подключения Business-аккаунта, для админ-панели."""
        where = "WHERE is_enabled = 1" if only_enabled else ""
        cur = await self._db.execute(
            f"""SELECT owner_user_id, owner_name, owner_username, is_enabled,
                       can_delete_all_messages, created_at, updated_at
                FROM connections {where}
                ORDER BY updated_at DESC
                LIMIT ?""",
            (limit,),
        )
        rows = await cur.fetchall()
        result = []
        for owner_user_id, owner_name, owner_username, is_enabled, can_delete_all, created_at, updated_at in rows:
            result.append({
                "owner_user_id": owner_user_id,
                "owner_name": _dec(owner_name),
                "owner_username": _dec(owner_username),
                "is_enabled": bool(is_enabled),
                "can_delete_all_messages": bool(can_delete_all),
                "created_at": created_at,
                "updated_at": updated_at,
            })
        return result

    async def list_recent_users(self, limit: int = 30):
        """Последние пользователи, нажимавшие /start, для админ-панели."""
        cur = await self._db.execute(
            """SELECT user_id, name, username, first_seen, last_seen, starts_count
               FROM bot_users
               ORDER BY last_seen DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cur.fetchall()
        result = []
        for user_id, name, username, first_seen, last_seen, starts_count in rows:
            result.append({
                "user_id": user_id,
                "name": _dec(name),
                "username": _dec(username),
                "first_seen": first_seen,
                "last_seen": last_seen,
                "starts_count": starts_count,
            })
        return result

    # ---------- рассылка ----------

    async def list_broadcast_chat_ids(self) -> list[int]:
        """Все chat_id пользователей, которые не заблокировали бота — получатели рассылки."""
        cur = await self._db.execute(
            "SELECT DISTINCT chat_id FROM bot_users WHERE blocked = 0 OR blocked IS NULL"
        )
        rows = await cur.fetchall()
        return [row[0] for row in rows]

    async def count_broadcast_recipients(self) -> int:
        cur = await self._db.execute(
            "SELECT COUNT(DISTINCT chat_id) FROM bot_users WHERE blocked = 0 OR blocked IS NULL"
        )
        row = await cur.fetchone()
        return row[0] if row else 0

    async def mark_user_blocked(self, chat_id: int):
        """Помечает пользователя как заблокировавшего бота — исключается из будущих рассылок."""
        await self._db.execute(
            "UPDATE bot_users SET blocked = 1 WHERE chat_id = ?", (chat_id,)
        )
        await self._db.commit()

    # ---------- крестики-нолики ----------

    async def start_game(self, business_connection_id, chat_id, board, turn,
                          x_user_id, x_name, o_user_id, o_name, message_id=None):
        await self._db.execute(
            """INSERT INTO tic_games
               (business_connection_id, chat_id, message_id, board, turn, status,
                x_user_id, x_name, o_user_id, o_name, updated_at)
               VALUES (?, ?, ?, ?, ?, 'playing', ?, ?, ?, ?, ?)
               ON CONFLICT(business_connection_id, chat_id) DO UPDATE SET
                 message_id=excluded.message_id,
                 board=excluded.board,
                 turn=excluded.turn,
                 status='playing',
                 x_user_id=excluded.x_user_id,
                 x_name=excluded.x_name,
                 o_user_id=excluded.o_user_id,
                 o_name=excluded.o_name,
                 updated_at=excluded.updated_at
            """,
            (business_connection_id, chat_id, message_id, board, turn,
             x_user_id, _enc(x_name), o_user_id, _enc(o_name), int(time.time())),
        )
        await self._db.commit()

    async def set_game_message_id(self, business_connection_id, chat_id, message_id):
        await self._db.execute(
            "UPDATE tic_games SET message_id = ? WHERE business_connection_id = ? AND chat_id = ?",
            (message_id, business_connection_id, chat_id),
        )
        await self._db.commit()

    async def get_game(self, business_connection_id, chat_id):
        cur = await self._db.execute(
            "SELECT business_connection_id, chat_id, message_id, board, turn, status, "
            "x_user_id, x_name, o_user_id, o_name "
            "FROM tic_games WHERE business_connection_id = ? AND chat_id = ?",
            (business_connection_id, chat_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        keys = ["business_connection_id", "chat_id", "message_id", "board", "turn", "status",
                "x_user_id", "x_name", "o_user_id", "o_name"]
        data = dict(zip(keys, row))
        data["x_name"] = _dec(data["x_name"])
        data["o_name"] = _dec(data["o_name"])
        return data

    async def update_game_board(self, business_connection_id, chat_id, board, turn, status):
        await self._db.execute(
            """UPDATE tic_games SET board = ?, turn = ?, status = ?, updated_at = ?
               WHERE business_connection_id = ? AND chat_id = ?""",
            (board, turn, status, int(time.time()), business_connection_id, chat_id),
        )
        await self._db.commit()

    async def delete_game(self, business_connection_id, chat_id):
        await self._db.execute(
            "DELETE FROM tic_games WHERE business_connection_id = ? AND chat_id = ?",
            (business_connection_id, chat_id),
        )
        await self._db.commit()

    # ---------- rps_games (камень-ножницы-бумага) ----------

    async def start_rps_game(self, business_connection_id, chat_id, x_user_id, x_name,
                              o_user_id, o_name, message_id):
        await self._db.execute(
            """INSERT INTO rps_games
               (business_connection_id, chat_id, message_id, status,
                x_user_id, x_name, x_choice, o_user_id, o_name, o_choice, updated_at)
               VALUES (?, ?, ?, 'playing', ?, ?, NULL, ?, ?, NULL, ?)
               ON CONFLICT(business_connection_id, chat_id) DO UPDATE SET
                   message_id=excluded.message_id, status='playing',
                   x_user_id=excluded.x_user_id, x_name=excluded.x_name, x_choice=NULL,
                   o_user_id=excluded.o_user_id, o_name=excluded.o_name, o_choice=NULL,
                   updated_at=excluded.updated_at""",
            (business_connection_id, chat_id, message_id, x_user_id, _enc(x_name),
             o_user_id, _enc(o_name), int(time.time())),
        )
        await self._db.commit()

    async def get_rps_game(self, business_connection_id, chat_id):
        cur = await self._db.execute(
            "SELECT business_connection_id, chat_id, message_id, status, "
            "x_user_id, x_name, x_choice, o_user_id, o_name, o_choice "
            "FROM rps_games WHERE business_connection_id = ? AND chat_id = ?",
            (business_connection_id, chat_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        keys = ["business_connection_id", "chat_id", "message_id", "status",
                "x_user_id", "x_name", "x_choice", "o_user_id", "o_name", "o_choice"]
        data = dict(zip(keys, row))
        data["x_name"] = _dec(data["x_name"])
        data["o_name"] = _dec(data["o_name"])
        return data

    async def set_rps_choice(self, business_connection_id, chat_id, side, choice):
        """side — 'x_choice' или 'o_choice'."""
        assert side in ("x_choice", "o_choice")
        await self._db.execute(
            f"UPDATE rps_games SET {side} = ?, updated_at = ? "
            "WHERE business_connection_id = ? AND chat_id = ?",
            (choice, int(time.time()), business_connection_id, chat_id),
        )
        await self._db.commit()

    async def finish_rps_game(self, business_connection_id, chat_id):
        await self._db.execute(
            "UPDATE rps_games SET status = 'finished', updated_at = ? "
            "WHERE business_connection_id = ? AND chat_id = ?",
            (int(time.time()), business_connection_id, chat_id),
        )
        await self._db.commit()

    async def reset_rps_game(self, business_connection_id, chat_id):
        await self._db.execute(
            "UPDATE rps_games SET status = 'playing', x_choice = NULL, o_choice = NULL, "
            "updated_at = ? WHERE business_connection_id = ? AND chat_id = ?",
            (int(time.time()), business_connection_id, chat_id),
        )
        await self._db.commit()

    async def delete_rps_game(self, business_connection_id, chat_id):
        await self._db.execute(
            "DELETE FROM rps_games WHERE business_connection_id = ? AND chat_id = ?",
            (business_connection_id, chat_id),
        )
        await self._db.commit()

    # ---------- hangman_games (виселица) ----------

    async def start_hangman_game(self, business_connection_id, chat_id, word,
                                  x_user_id, x_name, o_user_id, o_name, message_id):
        await self._db.execute(
            """INSERT INTO hangman_games
               (business_connection_id, chat_id, message_id, word, guessed, wrong, status,
                x_user_id, x_name, o_user_id, o_name, updated_at)
               VALUES (?, ?, ?, ?, '', '', 'playing', ?, ?, ?, ?, ?)
               ON CONFLICT(business_connection_id, chat_id) DO UPDATE SET
                   message_id=excluded.message_id, word=excluded.word,
                   guessed='', wrong='', status='playing',
                   x_user_id=excluded.x_user_id, x_name=excluded.x_name,
                   o_user_id=excluded.o_user_id, o_name=excluded.o_name,
                   updated_at=excluded.updated_at""",
            (business_connection_id, chat_id, message_id, _enc(word),
             x_user_id, _enc(x_name), o_user_id, _enc(o_name), int(time.time())),
        )
        await self._db.commit()

    async def get_hangman_game(self, business_connection_id, chat_id):
        cur = await self._db.execute(
            "SELECT business_connection_id, chat_id, message_id, word, guessed, wrong, status, "
            "x_user_id, x_name, o_user_id, o_name "
            "FROM hangman_games WHERE business_connection_id = ? AND chat_id = ?",
            (business_connection_id, chat_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        keys = ["business_connection_id", "chat_id", "message_id", "word", "guessed", "wrong",
                "status", "x_user_id", "x_name", "o_user_id", "o_name"]
        data = dict(zip(keys, row))
        data["word"] = _dec(data["word"])
        data["x_name"] = _dec(data["x_name"])
        data["o_name"] = _dec(data["o_name"])
        return data

    async def apply_hangman_guess(self, business_connection_id, chat_id, guessed, wrong, status):
        await self._db.execute(
            "UPDATE hangman_games SET guessed = ?, wrong = ?, status = ?, updated_at = ? "
            "WHERE business_connection_id = ? AND chat_id = ?",
            (guessed, wrong, status, int(time.time()), business_connection_id, chat_id),
        )
        await self._db.commit()

    async def delete_hangman_game(self, business_connection_id, chat_id):
        await self._db.execute(
            "DELETE FROM hangman_games WHERE business_connection_id = ? AND chat_id = ?",
            (business_connection_id, chat_id),
        )
        await self._db.commit()

    # ---------- pending_hangman_words (слово, загаданное владельцем в лс с ботом) ----------

    async def set_pending_hangman_word(self, owner_chat_id, word):
        await self._db.execute(
            """INSERT INTO pending_hangman_words (owner_chat_id, word, created_at)
               VALUES (?, ?, ?)
               ON CONFLICT(owner_chat_id) DO UPDATE SET
                   word=excluded.word, created_at=excluded.created_at""",
            (owner_chat_id, _enc(word), int(time.time())),
        )
        await self._db.commit()

    async def get_pending_hangman_word(self, owner_chat_id):
        cur = await self._db.execute(
            "SELECT word FROM pending_hangman_words WHERE owner_chat_id = ?",
            (owner_chat_id,),
        )
        row = await cur.fetchone()
        return _dec(row[0]) if row else None

    async def pop_pending_hangman_word(self, owner_chat_id):
        word = await self.get_pending_hangman_word(owner_chat_id)
        if word is not None:
            await self._db.execute(
                "DELETE FROM pending_hangman_words WHERE owner_chat_id = ?",
                (owner_chat_id,),
            )
            await self._db.commit()
        return word

    # ---------- фишка: часы в имени/фамилии ----------

    async def get_clock_settings(self, business_connection_id) -> dict:
        cur = await self._db.execute(
            "SELECT enabled, target, last_value FROM clock_settings WHERE business_connection_id = ?",
            (business_connection_id,),
        )
        row = await cur.fetchone()
        if not row:
            return {"enabled": False, "target": "first", "last_value": None}
        enabled, target, last_value = row
        return {"enabled": bool(enabled), "target": target or "first", "last_value": last_value}

    async def set_clock_config(self, business_connection_id, enabled: bool, target: str | None = None):
        """Включает/выключает часы и (опционально) меняет место (target: 'first'/'last').
        При любом изменении конфигурации last_value сбрасывается, чтобы фоновый цикл
        сразу же выставил актуальное время, не дожидаясь смены минуты."""
        if target is None:
            existing = await self.get_clock_settings(business_connection_id)
            target = existing["target"]
        now = int(time.time())
        await self._db.execute(
            """INSERT INTO clock_settings (business_connection_id, enabled, target, last_value, updated_at)
               VALUES (?, ?, ?, NULL, ?)
               ON CONFLICT(business_connection_id) DO UPDATE SET
                 enabled=excluded.enabled, target=excluded.target,
                 last_value=NULL, updated_at=excluded.updated_at""",
            (business_connection_id, int(enabled), target, now),
        )
        await self._db.commit()

    async def set_clock_last_value(self, business_connection_id, value: str):
        await self._db.execute(
            "UPDATE clock_settings SET last_value = ?, updated_at = ? WHERE business_connection_id = ?",
            (value, int(time.time()), business_connection_id),
        )
        await self._db.commit()

    async def list_enabled_clocks(self):
        """Все business-подключения, у которых включена фишка часов — для фонового цикла."""
        cur = await self._db.execute(
            "SELECT business_connection_id FROM clock_settings WHERE enabled = 1"
        )
        rows = await cur.fetchall()
        return [row[0] for row in rows]

    # ---------- фишка: сообщение о муте (закреп + кнопка «Размьютить») ----------

    async def get_mute_notify_enabled(self, business_connection_id) -> bool:
        cur = await self._db.execute(
            "SELECT enabled FROM mute_notify_settings WHERE business_connection_id = ?",
            (business_connection_id,),
        )
        row = await cur.fetchone()
        return bool(row[0]) if row else False

    async def set_mute_notify_enabled(self, business_connection_id, enabled: bool):
        now = int(time.time())
        await self._db.execute(
            """INSERT INTO mute_notify_settings (business_connection_id, enabled, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(business_connection_id) DO UPDATE SET
                 enabled=excluded.enabled, updated_at=excluded.updated_at""",
            (business_connection_id, int(enabled), now),
        )
        await self._db.commit()
