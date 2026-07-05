import time
import aiosqlite

from config import DB_PATH, CACHE_LIMIT_PER_CHAT

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages_cache (
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
    PRIMARY KEY (chat_id, msg_id)
);

CREATE TABLE IF NOT EXISTS muted_chats (
    chat_id INTEGER PRIMARY KEY,
    until_ts INTEGER,      -- NULL = навсегда
    muted_at INTEGER
);
"""


class Storage:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def init(self):
        self._db = await aiosqlite.connect(self.path)
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        await self._migrate()

    async def _migrate(self):
        # На случай апгрейда с более старой версии базы, где этих колонок ещё не было
        for stmt in (
            "ALTER TABLE messages_cache ADD COLUMN sender_username TEXT",
            "ALTER TABLE messages_cache ADD COLUMN chat_username TEXT",
        ):
            try:
                await self._db.execute(stmt)
                await self._db.commit()
            except Exception:
                pass  # колонка уже существует

    async def close(self):
        if self._db:
            await self._db.close()

    # ---------- кэш сообщений ----------

    async def cache_message(self, chat_id, msg_id, sender_id, sender_name, sender_username,
                             chat_name, chat_username, text, media_type):
        await self._db.execute(
            """INSERT INTO messages_cache
               (chat_id, msg_id, sender_id, sender_name, sender_username,
                chat_name, chat_username, text, media_type, date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_id, msg_id) DO UPDATE SET
                 sender_id=excluded.sender_id,
                 sender_name=excluded.sender_name,
                 sender_username=excluded.sender_username,
                 chat_name=excluded.chat_name,
                 chat_username=excluded.chat_username,
                 text=excluded.text,
                 media_type=excluded.media_type,
                 date=excluded.date
            """,
            (chat_id, msg_id, sender_id, sender_name, sender_username,
             chat_name, chat_username, text, media_type, int(time.time())),
        )
        await self._db.commit()
        await self._trim(chat_id)

    async def _trim(self, chat_id):
        # оставляем только последние CACHE_LIMIT_PER_CHAT сообщений на чат
        await self._db.execute(
            """DELETE FROM messages_cache
               WHERE chat_id = ? AND msg_id NOT IN (
                   SELECT msg_id FROM messages_cache
                   WHERE chat_id = ?
                   ORDER BY msg_id DESC
                   LIMIT ?
               )""",
            (chat_id, chat_id, CACHE_LIMIT_PER_CHAT),
        )
        await self._db.commit()

    async def get_cached(self, chat_id, msg_id):
        cur = await self._db.execute(
            "SELECT chat_id, msg_id, sender_id, sender_name, sender_username, "
            "chat_name, chat_username, text, media_type, date "
            "FROM messages_cache WHERE chat_id = ? AND msg_id = ?",
            (chat_id, msg_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        keys = ["chat_id", "msg_id", "sender_id", "sender_name", "sender_username",
                "chat_name", "chat_username", "text", "media_type", "date"]
        return dict(zip(keys, row))

    async def clear_chat_cache(self, chat_id):
        await self._db.execute("DELETE FROM messages_cache WHERE chat_id = ?", (chat_id,))
        await self._db.commit()

    async def clear_all_cache(self):
        await self._db.execute("DELETE FROM messages_cache")
        await self._db.commit()

    # ---------- замьюченные чаты ----------

    async def mute_chat(self, chat_id, duration_seconds: int | None):
        until_ts = int(time.time()) + duration_seconds if duration_seconds else None
        await self._db.execute(
            "INSERT INTO muted_chats (chat_id, until_ts, muted_at) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET until_ts=excluded.until_ts, muted_at=excluded.muted_at",
            (chat_id, until_ts, int(time.time())),
        )
        await self._db.commit()

    async def unmute_chat(self, chat_id):
        await self._db.execute("DELETE FROM muted_chats WHERE chat_id = ?", (chat_id,))
        await self._db.commit()

    async def is_muted(self, chat_id) -> bool:
        cur = await self._db.execute(
            "SELECT until_ts FROM muted_chats WHERE chat_id = ?", (chat_id,)
        )
        row = await cur.fetchone()
        if not row:
            return False
        until_ts = row[0]
        if until_ts is not None and until_ts < time.time():
            # срок мьюта истёк — снимаем
            await self.unmute_chat(chat_id)
            return False
        return True

    async def list_muted(self):
        cur = await self._db.execute("SELECT chat_id, until_ts FROM muted_chats")
        return await cur.fetchall()
