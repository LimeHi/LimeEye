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

# Юзернейм канала (без @) для обязательной подписки перед использованием бота.
# Бот должен быть администратором этого канала, иначе проверку подписки провести не получится.
# Если переменная не задана — проверка подписки отключена.
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "").strip().lstrip("@") or None

# Как часто (в секундах) фоновый цикл проверяет и обновляет "часы" в имени/фамилии
# у тех, кто включил эту фишку. Реальный вызов Telegram API происходит не чаще
# раза в минуту (когда меняется отображаемое ЧЧ:ММ) — интервал ниже влияет только
# на то, с какой задержкой бот заметит смену минуты.
CLOCK_UPDATE_INTERVAL_SECONDS = int(os.environ.get("CLOCK_UPDATE_INTERVAL_SECONDS", "20"))

# Telegram user_id владельца бота — только этому аккаунту доступна команда /admin
# (статистика: сколько людей запускало бота, кто подключил Business-аккаунт).
_admin_raw = os.environ.get("ADMIN_ID", "").strip()
ADMIN_ID = int(_admin_raw) if _admin_raw.isdigit() else None
