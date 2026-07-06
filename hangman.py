import random

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from utils import html_escape

# Небольшой встроенный словарь — можно свободно расширять списком ниже.
WORDS = [
    "ПРОГРАММА", "КОМПЬЮТЕР", "ТЕЛЕФОН", "КЛАВИАТУРА", "ИНТЕРНЕТ",
    "ПУТЕШЕСТВИЕ", "БИБЛИОТЕКА", "ВЕЛОСИПЕД", "ХОЛОДИЛЬНИК", "СКОВОРОДА",
    "МЕДВЕДЬ", "ЖИРАФ", "ПИНГВИН", "ДЕЛЬФИН", "БАБОЧКА",
    "ГИТАРА", "ПИАНИНО", "ХУДОЖНИК", "АРХИТЕКТОР", "КОСМОНАВТ",
    "ШОКОЛАД", "АРБУЗ", "ПОМИДОР", "КАРТОШКА", "МОРОЖЕНОЕ",
    "ЗЕРКАЛО", "ПОДУШКА", "ОДЕЯЛО", "ЗОНТИК", "ФОНАРИК",
    "ГОРИЗОНТ", "ВОДОПАД", "ВУЛКАН", "ПУСТЫНЯ", "ОСТРОВ",
]

ALPHABET = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"

MAX_WRONG = 6

# ASCII-виселица: индекс — число ошибок (0..MAX_WRONG)
_STAGES = [
    "+---+\n|   |\n    |\n    |\n    |\n=====",
    "+---+\n|   |\nO   |\n    |\n    |\n=====",
    "+---+\n|   |\nO   |\n|   |\n    |\n=====",
    "+---+\n|   |\nO   |\n/|   |\n    |\n=====",
    "+---+\n|   |\nO   |\n/|\\  |\n    |\n=====",
    "+---+\n|   |\nO   |\n/|\\  |\n/    |\n=====",
    "+---+\n|   |\nO   |\n/|\\  |\n/ \\  |\n=====",
]


def new_word() -> str:
    return random.choice(WORDS)


MIN_WORD_LEN = 3
MAX_WORD_LEN = 25


def validate_custom_word(raw: str) -> tuple[str | None, str | None]:
    """Проверяет слово, загаданное владельцем в ЛС с ботом.

    Возвращает (слово_в_верхнем_регистре, None) при успехе,
    либо (None, текст_ошибки) при провале.
    """
    word = (raw or "").strip().upper().replace("Ё", "Е")
    alphabet = ALPHABET.replace("Ё", "Е")
    if not word:
        return None, "⚠️ Пустое слово."
    if " " in word or "-" in word:
        return None, "⚠️ Слово должно быть одним словом, без пробелов и дефисов."
    if not all(letter in alphabet for letter in word):
        return None, "⚠️ Можно использовать только русские буквы (А-Я, без Ё — она заменится на Е)."
    if len(word) < MIN_WORD_LEN:
        return None, f"⚠️ Слово слишком короткое (минимум {MIN_WORD_LEN} буквы)."
    if len(word) > MAX_WORD_LEN:
        return None, f"⚠️ Слово слишком длинное (максимум {MAX_WORD_LEN} букв)."
    return word, None


def masked(word: str, guessed: set) -> str:
    return " ".join(letter if letter in guessed else "_" for letter in word)


def render_text(word: str, guessed: set, wrong_letters: set, x_name: str, o_name: str,
                 status: str) -> str:
    """status: 'playing' | 'won' | 'lost'"""
    wrong_count = len(wrong_letters)
    header = "🪢 <b>Виселица</b>\n"
    players = f"Загадал(а) слово: {html_escape(x_name)}. Отгадывает: {html_escape(o_name)}.\n\n"
    art = f"<pre>{_STAGES[min(wrong_count, MAX_WRONG)]}</pre>\n"
    word_line = f"<code>{masked(word, guessed)}</code>\n"
    wrong_line = f"Неверные буквы: {', '.join(sorted(wrong_letters)) or '—'}\n"
    attempts_line = f"Осталось попыток: {MAX_WRONG - wrong_count}\n\n"

    if status == "won":
        footer = f"🎉 Слово угадано: <b>{word}</b>!"
    elif status == "lost":
        footer = f"💀 Не угадали. Слово было: <b>{word}</b>."
    else:
        footer = ""

    return header + players + art + word_line + wrong_line + attempts_line + footer


def render_keyboard(word: str, guessed: set, finished: bool) -> InlineKeyboardMarkup:
    if finished:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Играть снова", callback_data="hm:restart")],
        ])

    rows = []
    row = []
    for i, letter in enumerate(ALPHABET, start=1):
        if letter in guessed:
            label = f"✓{letter}" if letter in word else f"✗{letter}"
            callback_data = "hm:noop"
        else:
            label = letter
            callback_data = f"hm:letter:{letter}"
        row.append(InlineKeyboardButton(text=label, callback_data=callback_data))
        if i % 8 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)
