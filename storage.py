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
    PRIMARY KEY (business_connection_id, chat_id)
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
"""

# Колонки, добавленные уже после первого релиза — накатываются на существующие
# базы отдельно от CREATE TABLE (SQLite не умеет ALTER TABLE ... IF NOT EXISTS).
MIGRATIONS = [
    ("muted_chats", "chat_name", "TEXT"),
    ("muted_chats", "chat_username", "TEXT"),
    ("messages_cache", "media_kind", "TEXT"),
    ("messages_cache", "media_file_id", "TEXT"),
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
            (business_connection_id, chat_id, msg_id, sender_id, sender_name, sender_username,
             chat_name, chat_username, text, media_type, media_kind, media_file_id, int(time.time())),
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
        return dict(zip(keys, row))

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
        return [dict(zip(keys, row)) for row in rows]

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
             x_user_id, x_name, o_user_id, o_name, int(time.time())),
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
        return dict(zip(keys, row))

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
            (business_connection_id, chat_id, message_id, x_user_id, x_name,
             o_user_id, o_name, int(time.time())),
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
        return dict(zip(keys, row))

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
            (business_connection_id, chat_id, message_id, word,
             x_user_id, x_name, o_user_id, o_name, int(time.time())),
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
        return dict(zip(keys, row))

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
            (owner_chat_id, word, int(time.time())),
        )
        await self._db.commit()

    async def get_pending_hangman_word(self, owner_chat_id):
        cur = await self._db.execute(
            "SELECT word FROM pending_hangman_words WHERE owner_chat_id = ?",
            (owner_chat_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def pop_pending_hangman_word(self, owner_chat_id):
        word = await self.get_pending_hangman_word(owner_chat_id)
        if word is not None:
            await self._db.execute(
                "DELETE FROM pending_hangman_words WHERE owner_chat_id = ?",
                (owner_chat_id,),
            )
            await self._db.commit()
        return word
