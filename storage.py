import time
import aiosqlite

from config import DB_PATH, CACHE_LIMIT_PER_CHAT

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    business_connection_id TEXT PRIMARY KEY,
    owner_user_id INTEGER,
    owner_chat_id INTEGER,     -- личный чат бота с владельцем (для отчётов/ответов на команды)
    can_reply INTEGER,
    can_read_messages INTEGER,
    can_delete_sent_messages INTEGER,
    can_delete_all_messages INTEGER,
    is_enabled INTEGER,
    updated_at INTEGER
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
    PRIMARY KEY (business_connection_id, chat_id)
);
"""

# Колонки, добавленные уже после первого релиза — накатываются на существующие
# базы отдельно от CREATE TABLE (SQLite не умеет ALTER TABLE ... IF NOT EXISTS).
MIGRATIONS = [
    ("muted_chats", "chat_name", "TEXT"),
    ("muted_chats", "chat_username", "TEXT"),
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
                                 can_delete_all_messages, is_enabled):
        await self._db.execute(
            """INSERT INTO connections
               (business_connection_id, owner_user_id, owner_chat_id, can_reply,
                can_read_messages, can_delete_sent_messages, can_delete_all_messages,
                is_enabled, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(business_connection_id) DO UPDATE SET
                 owner_user_id=excluded.owner_user_id,
                 owner_chat_id=excluded.owner_chat_id,
                 can_reply=excluded.can_reply,
                 can_read_messages=excluded.can_read_messages,
                 can_delete_sent_messages=excluded.can_delete_sent_messages,
                 can_delete_all_messages=excluded.can_delete_all_messages,
                 is_enabled=excluded.is_enabled,
                 updated_at=excluded.updated_at
            """,
            (business_connection_id, owner_user_id, owner_chat_id, int(can_reply),
             int(can_read_messages), int(can_delete_sent_messages), int(can_delete_all_messages),
             int(is_enabled), int(time.time())),
        )
        await self._db.commit()

    async def get_connection(self, business_connection_id):
        cur = await self._db.execute(
            "SELECT business_connection_id, owner_user_id, owner_chat_id, can_reply, "
            "can_read_messages, can_delete_sent_messages, can_delete_all_messages, is_enabled "
            "FROM connections WHERE business_connection_id = ?",
            (business_connection_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        keys = ["business_connection_id", "owner_user_id", "owner_chat_id", "can_reply",
                "can_read_messages", "can_delete_sent_messages", "can_delete_all_messages",
                "is_enabled"]
        data = dict(zip(keys, row))
        for k in ("can_reply", "can_read_messages", "can_delete_sent_messages",
                  "can_delete_all_messages", "is_enabled"):
            data[k] = bool(data[k])
        return data

    # ---------- кэш сообщений ----------

    async def cache_message(self, business_connection_id, chat_id, msg_id, sender_id,
                             sender_name, sender_username, chat_name, chat_username,
                             text, media_type):
        await self._db.execute(
            """INSERT INTO messages_cache
               (business_connection_id, chat_id, msg_id, sender_id, sender_name, sender_username,
                chat_name, chat_username, text, media_type, date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(business_connection_id, chat_id, msg_id) DO UPDATE SET
                 sender_id=excluded.sender_id,
                 sender_name=excluded.sender_name,
                 sender_username=excluded.sender_username,
                 chat_name=excluded.chat_name,
                 chat_username=excluded.chat_username,
                 text=excluded.text,
                 media_type=excluded.media_type,
                 date=excluded.date
            """,
            (business_connection_id, chat_id, msg_id, sender_id, sender_name, sender_username,
             chat_name, chat_username, text, media_type, int(time.time())),
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
            "sender_username, chat_name, chat_username, text, media_type, date "
            "FROM messages_cache WHERE business_connection_id = ? AND chat_id = ? AND msg_id = ?",
            (business_connection_id, chat_id, msg_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        keys = ["business_connection_id", "chat_id", "msg_id", "sender_id", "sender_name",
                "sender_username", "chat_name", "chat_username", "text", "media_type", "date"]
        return dict(zip(keys, row))

    async def clear_chat_cache(self, business_connection_id, chat_id):
        await self._db.execute(
            "DELETE FROM messages_cache WHERE business_connection_id = ? AND chat_id = ?",
            (business_connection_id, chat_id),
        )
        await self._db.commit()

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
            (business_connection_id, chat_id, until_ts, int(time.time()), chat_name, chat_username),
        )
        await self._db.commit()

    async def unmute_chat(self, business_connection_id, chat_id):
        await self._db.execute(
            "DELETE FROM muted_chats WHERE business_connection_id = ? AND chat_id = ?",
            (business_connection_id, chat_id),
        )
        await self._db.commit()

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
        return await cur.fetchall()

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
