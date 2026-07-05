import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]

# Куда слать отчёты об удалённых/изменённых сообщениях.
# "me" = Избранное (Saved Messages). Можно указать chat_id канала/чата.
LOG_CHAT = os.environ.get("LOG_CHAT", "me")

# Префикс команд (аналог "." в savemod)
CMD_PREFIX = os.environ.get("CMD_PREFIX", ".")

# Путь к файлу базы (на Railway используйте volume, иначе кэш обнулится при рестарте)
DB_PATH = os.environ.get("DB_PATH", "limeeye.db")

# Сколько последних сообщений на чат хранить в кэше (антипереполнение базы)
CACHE_LIMIT_PER_CHAT = int(os.environ.get("CACHE_LIMIT_PER_CHAT", "500"))
