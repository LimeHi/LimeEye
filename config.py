import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота от @BotFather (у бота обязательно должен быть включён Business Mode:
# @BotFather -> /mybots -> выбрать бота -> Bot Settings -> Business Mode -> Turn on)
BOT_TOKEN = os.environ["BOT_TOKEN"]

# Префикс команд (.mute, .unmute, ...)
CMD_PREFIX = os.environ.get("CMD_PREFIX", ".")

DB_PATH = os.environ.get("DB_PATH", "limeeye.db")

# Сколько последних сообщений на чат хранить в кэше (для save/edit mod)
CACHE_LIMIT_PER_CHAT = int(os.environ.get("CACHE_LIMIT_PER_CHAT", "500"))

# Сколько дней хранить сообщения в кэше, прежде чем удалить автоматически
# (кэш нужен только для save/edit-отчётов "по горячим следам")
CACHE_MAX_AGE_DAYS = float(os.environ.get("CACHE_MAX_AGE_DAYS", "1"))
