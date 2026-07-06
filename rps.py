from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from utils import html_escape

CHOICES = {
    "rock": "🪨 Камень",
    "paper": "📄 Бумага",
    "scissors": "✂️ Ножницы",
}

# what each choice beats
BEATS = {
    "rock": "scissors",
    "scissors": "paper",
    "paper": "rock",
}


def winner(x_choice: str, o_choice: str) -> str | None:
    """Возвращает 'X', 'O' или 'draw'. Оба выбора должны быть заданы."""
    if x_choice == o_choice:
        return "draw"
    return "X" if BEATS[x_choice] == o_choice else "O"


def render_text(x_name: str, o_name: str, x_choice: str | None, o_choice: str | None, finished: bool) -> str:
    header = "🪨📄✂️ <b>Камень-ножницы-бумага</b>\n"
    players = f"🅧 {html_escape(x_name)}  vs  🅞 {html_escape(o_name)}\n\n"

    if not finished:
        ready = []
        ready.append(f"{'✅' if x_choice else '⏳'} {html_escape(x_name)}")
        ready.append(f"{'✅' if o_choice else '⏳'} {html_escape(o_name)}")
        status = "Готовность:\n" + "\n".join(ready) + "\n\n<i>Выборы скрыты, пока не сделают оба игрока.</i>"
        return header + players + status

    result = winner(x_choice, o_choice)
    reveal = f"{CHOICES[x_choice]}  —  {CHOICES[o_choice]}\n\n"
    if result == "draw":
        status = "🤝 Ничья!"
    else:
        winner_name = x_name if result == "X" else o_name
        status = f"🏆 Победа: {html_escape(winner_name)}!"
    return header + players + reveal + status


def render_keyboard(finished: bool) -> InlineKeyboardMarkup:
    if finished:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Играть снова", callback_data="rps:restart")],
        ])
    row = [
        InlineKeyboardButton(text=CHOICES["rock"], callback_data="rps:choice:rock"),
        InlineKeyboardButton(text=CHOICES["paper"], callback_data="rps:choice:paper"),
        InlineKeyboardButton(text=CHOICES["scissors"], callback_data="rps:choice:scissors"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row])
