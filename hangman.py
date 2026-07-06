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


def masked(word: str, guessed: set) -> str:
    return " ".join(letter if letter in guessed else "_" for letter in word)


def render_text(word: str, guessed: set, wrong_letters: set, x_name: str, o_name: str,
                 status: str) -> str:
    """status: 'playing' | 'won' | 'lost'"""
    wrong_count = len(wrong_letters)
    header = "🪢 <b>Виселица</b>\n"
    players = f"Играют: {html_escape(x_name)} и {html_escape(o_name)} (жмут по очереди, кто успеет)\n\n"
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
