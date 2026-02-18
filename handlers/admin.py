import logging
import random
from typing import List, Tuple

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import Config
from db import Database
from keyboards import AdminCb, admin_menu_kb, admin_resolve_match_kb, admin_review_list_kb
from services.matchmaking import MatchmakingService

log = logging.getLogger("admin")
admin_router = Router()


def _is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.ADMIN_IDS


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message, config: Config) -> None:
    if not _is_admin(message.from_user.id, config):
        await message.answer("⛔ Нет доступа.")
        return
    await message.answer("🛠 Админ-панель:", reply_markup=admin_menu_kb())


@admin_router.callback_query(AdminCb.filter())
async def cb_admin(query: CallbackQuery, db: Database, mm: MatchmakingService, config: Config) -> None:
    await query.answer()
    if not _is_admin(query.from_user.id, config):
        await query.message.answer("⛔ Нет доступа.")
        return

    data = AdminCb.unpack(query.data)
    if isinstance(data, dict):
        action = data.get("action", "")
        value = data.get("value", "") or ""
    else:
        action = data.action
        value = data.value or ""

    if action == "back":
        await query.message.answer("🛠 Админ-панель:", reply_markup=admin_menu_kb())
        return

    if action == "reg_open":
        await db.set_reg_open(1 if value == "1" else 0)
        await query.message.answer("✅ Готово.")
        return

    if action == "checkin_open":
        await db.set_checkin_open(1 if value == "1" else 0)
        await query.message.answer("✅ Готово.")
        return

    if action == "status":
        counts = await db.tournament_counts()
        t = await db.get_tournament_state()

        table1_busy = await db.table_is_busy(1)
        table2_busy = await db.table_is_busy(2)

        text = (
            "📊 Статус турнира\n\n"
            f"Состояние: {t.state}\n"
            f"Регистрация: {'открыта' if t.reg_open else 'закрыта'}\n"
            f"Check-in: {'открыт' if t.checkin_open else 'закрыт'}\n\n"
            f"Всего игроков в базе: {counts['total']}\n"
            f"Зарегистрировано (основной список): {counts['main']}\n"
            f"В листе ожидания: {counts['waitlist']}\n"
            f"С check-in: {counts['checked_in']}\n\n"
            f"Стол 1: {'занят' if table1_busy else 'свободен'}\n"
            f"Стол 2: {'занят' if table2_busy else 'свободен'}\n"
        )
        await query.message.answer(text)
        return

    if action == "start":
        st = await db.get_tournament_state()
        if st.state not in ("idle", "reg", "checkin", "running", "finished"):
            await query.message.answer("⚠️ Нельзя стартовать в текущем состоянии.")
            return

        candidates = await db.list_checked_in_candidates()
        if len(candidates) < 2:
            await query.message.answer("⚠️ Нужно минимум 2 участника с check-in.")
            return

        # предпочтительно 16, иначе ближайшая степень двойки (8/4/2)
        size = 16 if len(candidates) >= 16 else 8 if len(candidates) >= 8 else 4 if len(candidates) >= 4 else 2
        selected = candidates[:size]
        ids = [p.id for p in selected]
        random.shuffle(ids)

        pairs: List[Tuple[int, int]] = [(ids[i], ids[i + 1]) for i in range(0, len(ids), 2)]

        await db.clear_matches()
        await db.reset_delays_and_status_for_selected(ids)
        await db.create_round_matches(1, pairs)
        await db.set_state("running")

        await query.message.answer(f"🚀 Турнир запущен на {size} участников. Создано матчей: {len(pairs)}")
        await mm.assign_free_tables()
        return

    if action == "force_assign":
        await mm.assign_free_tables()
        await query.message.answer("▶️ Пытаюсь назначить матчи на свободные столы.")
        return

    if action == "resolve_list":
        items = await db.list_admin_review_matches(limit=10)
        if not items:
            await query.message.answer("✅ Спорных матчей нет.")
            return

        titles = []
        for m in items:
            p1 = await db.get_player_by_id(m.p1_id)
            p2 = await db.get_player_by_id(m.p2_id)
            titles.append((m.id, f"Матч #{m.id} (R{m.round}): {p1.name if p1 else m.p1_id} vs {p2.name if p2 else m.p2_id}"))

        await query.message.answer("🛠 Выбери матч для решения:", reply_markup=admin_review_list_kb(titles))
        return

    if action == "resolve_match":
        try:
            match_id = int(value)
        except ValueError:
            await query.message.answer("⛔ Некорректный match_id.")
            return

        m = await db.get_match(match_id)
        if not m or m.status != "admin_review":
            await query.message.answer("⚠️ Матч не найден или уже не в admin_review.")
            return

        p1 = await db.get_player_by_id(m.p1_id)
        p2 = await db.get_player_by_id(m.p2_id)
        p1_label = p1.name if p1 else f"#{m.p1_id}"
        p2_label = p2.name if p2 else f"#{m.p2_id}"

        await query.message.answer(
            f"🛠 Матч #{m.id}: выбери победителя:",
            reply_markup=admin_resolve_match_kb(m.id, p1_label, m.p1_id, p2_label, m.p2_id),
        )
        return

    if action == "resolve_win":
        try:
            match_s, winner_s = value.split("_", 1)
            match_id = int(match_s)
            winner_id = int(winner_s)
        except Exception:
            await query.message.answer("⛔ Некорректные данные.")
            return

        m = await db.get_match(match_id)
        if not m or m.status != "admin_review":
            await query.message.answer("⚠️ Матч не найден или уже решён.")
            return

        if winner_id not in (m.p1_id, m.p2_id):
            await query.message.answer("⛔ Победитель не является участником матча.")
            return

        await db.set_match_closed_with_winner(match_id, winner_id=winner_id)
        loser_id = m.p2_id if winner_id == m.p1_id else m.p1_id
        await db.mark_eliminated(loser_id)

        await query.message.answer("✅ Решено админом. Матч закрыт.")
        await mm.handle_walkover_closed(m.round)
        await mm.assign_free_tables()
        return

    await query.message.answer("⚠️ Неизвестное действие админа.")
