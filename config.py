import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота от @BotFather (у бота обязательно должен быть включён Business Mode:
# @BotFather -> /mybots -> выбрать бота -> Bot Settings -> Business Mode -> Turn on)
BOT_TOKEN = os.environ["BOT_TOKEN"]

# Ключ шифрования чувствительных данных в БД (текст сообщений, имена, file_id и т.д.).
# Сгенерировать: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Обязателен: без него бот не запустится, чтобы данные случайно не легли в базу открытым текстом.
# Потеряете ключ — расшифровать старые данные будет уже нельзя, так что храните его так же
# бережно, как BOT_TOKEN (в .env / переменных окружения хостинга, не в репозитории).
DB_ENCRYPTION_KEY = os.environ.get("DB_ENCRYPTION_KEY")
if not DB_ENCRYPTION_KEY:
    raise RuntimeError(
        "Не задан DB_ENCRYPTION_KEY. Сгенерируйте его командой:\n"
        "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
        "и добавьте в .env как DB_ENCRYPTION_KEY=..."
    )

# Префикс команд (.mute, .unmute, ...)
CMD_PREFIX = os.environ.get("CMD_PREFIX", ".")

DB_PATH = os.environ.get("DB_PATH", "limeeye.db")

# Сколько последних сообщений на чат хранить в кэше (для save/edit mod)
CACHE_LIMIT_PER_CHAT = int(os.environ.get("CACHE_LIMIT_PER_CHAT", "500"))

# Сколько дней хранить сообщения в кэше, прежде чем удалить автоматически
# (кэш нужен только для save/edit-отчётов "по горячим следам")
CACHE_MAX_AGE_DAYS = float(os.environ.get("CACHE_MAX_AGE_DAYS", "1"))
