from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from utils import html_escape

EMPTY = "."
SYMBOLS = {"X": "❌", "O": "⭕", EMPTY: "▫️"}

WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),  # строки
    (0, 3, 6), (1, 4, 7), (2, 5, 8),  # столбцы
    (0, 4, 8), (2, 4, 6),             # диагонали
]


def new_board() -> str:
    return EMPTY * 9


def check_result(board: str) -> str | None:
    """Возвращает 'X', 'O', 'draw' или None (игра продолжается)."""
    for a, b, c in WIN_LINES:
        if board[a] != EMPTY and board[a] == board[b] == board[c]:
            return board[a]
    if EMPTY not in board:
        return "draw"
    return None


def apply_move(board: str, index: int, mark: str) -> str:
    return board[:index] + mark + board[index + 1:]


def other_mark(mark: str) -> str:
    return "O" if mark == "X" else "X"


def render_text(x_name: str, o_name: str, turn: str, result: str | None) -> str:
    header = "❌⭕ <b>Крестики-нолики</b>\n"
    players = f"❌ {html_escape(x_name)}  vs  ⭕ {html_escape(o_name)}\n\n"
    if result is None:
        turn_name = x_name if turn == "X" else o_name
        status = f"Ход: {SYMBOLS[turn]} {html_escape(turn_name)}"
    elif result == "draw":
        status = "🤝 Ничья!"
    else:
        winner_name = x_name if result == "X" else o_name
        status = f"🏆 Победа: {SYMBOLS[result]} {html_escape(winner_name)}!"
    return header + players + status


def render_keyboard(board: str, finished: bool) -> InlineKeyboardMarkup:
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            cell = board[i]
            label = SYMBOLS[cell] if cell != EMPTY else " "
            callback_data = "ttt:noop" if finished else f"ttt:{i}"
            row.append(InlineKeyboardButton(text=label, callback_data=callback_data))
        rows.append(row)
    if finished:
        rows.append([InlineKeyboardButton(text="🔄 Играть снова", callback_data="ttt:restart")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
