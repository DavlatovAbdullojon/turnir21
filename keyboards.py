from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class MenuCb(CallbackData, prefix="m"):
    action: str


class MatchCb(CallbackData, prefix="match"):
    action: str
    match_id: int


class ReportCb(CallbackData, prefix="rep"):
    match_id: int
    winner: int  # 1 = reporter, 2 = opponent
    score: str   # В callback_data нельзя ':' -> используем '2_0' / '2_1'


class ConfirmCb(CallbackData, prefix="cfm"):
    action: str
    match_id: int


class AdminCb(CallbackData, prefix="adm"):
    action: str
    value: str = ""


def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📝 Регистрация", callback_data=MenuCb(action="register").pack())
    kb.button(text="✅ Я на месте", callback_data=MenuCb(action="checkin").pack())
    kb.button(text="👤 Мой статус", callback_data=MenuCb(action="status").pack())
    kb.button(text="📈 Статистика", callback_data=MenuCb(action="stats").pack())
    kb.button(text="🏓 Матчи", callback_data=MenuCb(action="matches").pack())
    kb.button(text="🧩 Сетка", callback_data=MenuCb(action="bracket").pack())
    kb.button(text="📜 Правила", callback_data=MenuCb(action="rules").pack())
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()


def admin_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🟢 Открыть регистрацию", callback_data=AdminCb(action="reg_open", value="1").pack())
    kb.button(text="🔴 Закрыть регистрацию", callback_data=AdminCb(action="reg_open", value="0").pack())
    kb.button(text="🟢 Открыть чек-ин", callback_data=AdminCb(action="checkin_open", value="1").pack())
    kb.button(text="🔴 Закрыть чек-ин", callback_data=AdminCb(action="checkin_open", value="0").pack())
    kb.button(text="🚀 Старт турнира", callback_data=AdminCb(action="start").pack())
    kb.button(text="📊 Статус турнира", callback_data=AdminCb(action="status").pack())
    kb.button(text="🛠 Решить спор", callback_data=AdminCb(action="resolve_list").pack())
    kb.button(text="▶️ Назначить матчи", callback_data=AdminCb(action="force_assign").pack())
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


def match_call_kb(match_id: int, can_delay: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Я рядом", callback_data=MatchCb(action="ready", match_id=match_id).pack())
    if can_delay:
        kb.button(text="⏳ Нужна задержка", callback_data=MatchCb(action="delay", match_id=match_id).pack())
    kb.adjust(1)
    return kb.as_markup()


def match_playing_kb(match_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🏁 Сообщить результат", callback_data=MatchCb(action="report", match_id=match_id).pack())
    kb.adjust(1)
    return kb.as_markup()


def report_kb(match_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Я выиграл 2:0", callback_data=ReportCb(match_id=match_id, winner=1, score="2_0").pack())
    kb.button(text="✅ Я выиграл 2:1", callback_data=ReportCb(match_id=match_id, winner=1, score="2_1").pack())
    kb.button(text="🏳️ Соперник выиграл 2:0", callback_data=ReportCb(match_id=match_id, winner=2, score="2_0").pack())
    kb.button(text="🏳️ Соперник выиграл 2:1", callback_data=ReportCb(match_id=match_id, winner=2, score="2_1").pack())
    kb.adjust(1)
    return kb.as_markup()


def confirm_kb(match_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=ConfirmCb(action="ok", match_id=match_id).pack())
    kb.button(text="❗ Оспорить", callback_data=ConfirmCb(action="dispute", match_id=match_id).pack())
    kb.adjust(2)
    return kb.as_markup()


def admin_review_list_kb(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for match_id, title in items[:10]:
        kb.button(text=title, callback_data=AdminCb(action="resolve_match", value=str(match_id)).pack())
    kb.button(text="⬅️ Назад", callback_data=AdminCb(action="back").pack())
    kb.adjust(1)
    return kb.as_markup()


def admin_resolve_match_kb(match_id: int, p1_label: str, p1_id: int, p2_label: str, p2_id: int) -> InlineKeyboardMarkup:
    # В callback_data нельзя ':' -> используем '_' -> "matchId_winnerId"
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"🏆 Победитель: {p1_label}",
        callback_data=AdminCb(action="resolve_win", value=f"{match_id}_{p1_id}").pack(),
    )
    kb.button(
        text=f"🏆 Победитель: {p2_label}",
        callback_data=AdminCb(action="resolve_win", value=f"{match_id}_{p2_id}").pack(),
    )
    kb.button(text="⬅️ Назад", callback_data=AdminCb(action="resolve_list").pack())
    kb.adjust(1)
    return kb.as_markup()
