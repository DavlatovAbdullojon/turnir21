import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from db import Database
from keyboards import ConfirmCb, MatchCb, MenuCb, ReportCb, confirm_kb, main_menu_kb, report_kb
from services.bracket import render_bracket_text
from services.matchmaking import MatchmakingService

log = logging.getLogger("user")
user_router = Router()


class RegStates(StatesGroup):
    waiting_name = State()


RULES_TEXT = (
    "📜 **Правила турнира (B1 — олимпийка)**\n\n"
    "• Формат: single elimination (проиграл — вылетел)\n"
    "• Максимум 16 участников\n"
    "• 2 стола, расписания нет — матчи идут очередью\n"
    "• Вызов на матч: **5 минут** на подтверждение\n\n"
    "⏳ **Задержка / неявка**\n"
    "• 1 раз можно нажать «⏳ Нужна задержка» — матч уходит вниз очереди и будет вызван снова минимум через 5 минут\n"
    "• Если игрок НЕ подтвердил за 5 минут и задержка ещё не использована — считается как задержка (перенос +5 минут)\n"
    "• 2-я задержка/неявка = **техпоражение**\n"
    "• Если оба не подтвердили и никто не нажал задержку — матч уходит в **разбор админом**\n\n"
    "🏁 **Результат**\n"
    "• После матча победитель нажимает «🏁 Сообщить результат»\n"
    "• Соперник подтверждает или оспаривает\n"
    "• Оспорил → разбор админом"
)


@user_router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer("🏓 Меню турнира:", reply_markup=main_menu_kb())


@user_router.callback_query(MenuCb.filter(F.action == "rules"))
async def cb_rules(query: CallbackQuery) -> None:
    await query.answer()
    # без Markdown parse_mode, поэтому просто текст
    await query.message.answer(RULES_TEXT.replace("**", ""))


@user_router.callback_query(MenuCb.filter(F.action == "register"))
async def cb_register(query: CallbackQuery, state: FSMContext, db: Database) -> None:
    await query.answer()
    st = await db.get_tournament_state()

    if st.reg_open != 1:
        await query.message.answer(
            "🔴 Регистрация закрыта. Ты всё равно можешь отправить имя — добавлю в лист ожидания."
        )

    await state.set_state(RegStates.waiting_name)
    await query.message.answer("📝 Отправь своё имя/ник одним сообщением:")


@user_router.message(RegStates.waiting_name)
async def reg_name(message: Message, state: FSMContext, db: Database) -> None:
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("⚠️ Имя слишком короткое. Попробуй ещё раз.")
        return

    st = await db.get_tournament_state()
    existing = await db.get_player_by_tg(message.from_user.id)

    # менять имя после закрытия регистрации нельзя
    if existing and st.reg_open != 1:
        await state.clear()
        await message.answer(f"🔴 Регистрация закрыта. Имя изменить нельзя. Текущее имя: {existing.name}")
        return

    if existing:
        is_waitlist = existing.is_waitlist
    else:
        is_waitlist = 0
        if st.reg_open != 1:
            is_waitlist = 1
        elif await db.count_registered_main() >= 16:
            is_waitlist = 1

    p = await db.upsert_player(tg_id=message.from_user.id, name=name, is_waitlist=is_waitlist)
    await state.clear()

    if p.is_waitlist:
        await message.answer(f"✅ Готово! Ты в **листе ожидания**. Имя: {p.name}".replace("**", ""))
    else:
        await message.answer(f"✅ Готово! Ты зарегистрирован. Имя: {p.name}")


@user_router.callback_query(MenuCb.filter(F.action == "checkin"))
async def cb_checkin(query: CallbackQuery, db: Database) -> None:
    await query.answer()
    st = await db.get_tournament_state()
    if st.checkin_open != 1:
        await query.message.answer("🔴 Чек-ин сейчас закрыт. Админ откроет его перед турниром.")
        return

    p = await db.get_player_by_tg(query.from_user.id)
    if not p:
        await query.message.answer("⚠️ Сначала зарегистрируйся: 📝 Регистрация")
        return

    await db.set_checked_in(query.from_user.id, 1)
    await query.message.answer("✅ Чек-ин принят: ты на месте!")


@user_router.callback_query(MenuCb.filter(F.action == "status"))
async def cb_status(query: CallbackQuery, db: Database) -> None:
    await query.answer()

    p = await db.get_player_by_tg(query.from_user.id)
    if not p:
        await query.message.answer("⚠️ Ты ещё не зарегистрировался. Нажми 📝 Регистрация и отправь имя.")
        return

    lines = [
        f"👤 Имя: {p.name}",
        f"📋 Список: {'лист ожидания' if p.is_waitlist else 'основной'}",
        f"✅ Check-in: {'да' if p.checked_in else 'нет'}",
        f"⏳ Задержка использована: {'да' if p.delay_count else 'нет'}",
        f"📌 Статус: {p.status}",
    ]

    m = await db.get_active_match_for_player(p.id)
    if m:
        lines.append("")
        lines.append(f"🏓 Активный матч #{m.id} (R{m.round})")
        if m.table_id:
            lines.append(f"Стол: {m.table_id}")
        if m.status == "called":
            lines.append(f"Дедлайн подтверждения: {m.deadline_at}")
        lines.append(f"Статус матча: {m.status}")

    await query.message.answer("\n".join(lines))


@user_router.callback_query(MenuCb.filter(F.action == "bracket"))
async def cb_bracket(query: CallbackQuery, db: Database) -> None:
    await query.answer()
    text = await render_bracket_text(db)
    await query.message.answer(text)


@user_router.callback_query(MenuCb.filter(F.action == "matches"))
async def cb_matches(query: CallbackQuery, db: Database) -> None:
    await query.answer()
    p = await db.get_player_by_tg(query.from_user.id)
    if not p:
        await query.message.answer("⚠️ Сначала зарегистрируйся.")
        return

    m = await db.get_active_match_for_player(p.id)
    if not m:
        await query.message.answer("⏳ Пока нет активного матча. Жди вызова.")
        return

    p1 = await db.get_player_by_id(m.p1_id)
    p2 = await db.get_player_by_id(m.p2_id)
    p1n = p1.name if p1 else f"#{m.p1_id}"
    p2n = p2.name if p2 else f"#{m.p2_id}"

    text = (
        f"🏓 Матч #{m.id} (R{m.round})\n"
        f"{p1n} vs {p2n}\n"
        f"Статус: {m.status}\n"
        f"Стол: {m.table_id or '-'}\n"
    )
    if m.status == "called":
        text += f"Дедлайн: {m.deadline_at}\n"

    await query.message.answer(text)


# ---- матч: подтверждение / задержка / результат ----

@user_router.callback_query(MatchCb.filter(F.action == "ready"))
async def cb_match_ready(query: CallbackQuery, db: Database, mm: MatchmakingService) -> None:
    await query.answer()
    data = MatchCb.unpack(query.data)
    match_id = int(data["match_id"]) if isinstance(data, dict) else int(data.match_id)

    p = await db.get_player_by_tg(query.from_user.id)
    if not p:
        await query.message.answer("⚠️ Ты не зарегистрирован.")
        return

    ok = await db.set_player_ready_flag(match_id, p.id)
    if not ok:
        await query.message.answer("⚠️ Нельзя подтвердить: матч уже не в стадии вызова или не твой матч.")
        return

    await query.message.answer("✅ Отметил: ты рядом.")
    await mm.try_start_match_if_both_ready(match_id)


@user_router.callback_query(MatchCb.filter(F.action == "delay"))
async def cb_match_delay(query: CallbackQuery, db: Database, mm: MatchmakingService) -> None:
    await query.answer()
    data = MatchCb.unpack(query.data)
    match_id = int(data["match_id"]) if isinstance(data, dict) else int(data.match_id)

    p = await db.get_player_by_tg(query.from_user.id)
    if not p:
        await query.message.answer("⚠️ Ты не зарегистрирован.")
        return

    if p.delay_count != 0:
        await query.message.answer("❌ Задержка уже была использована.")
        return

    from datetime import datetime, timedelta
    not_before = (datetime.utcnow() + timedelta(minutes=5)).replace(microsecond=0).isoformat()
    changed = await db.consume_delay_and_postpone_called_match(match_id, p.id, not_before)
    if not changed:
        await query.message.answer("⚠️ Не удалось перенести матч (возможно, уже начался или не твой матч).")
        return

    m = await db.get_match(match_id)
    if m:
        op_id = m.p2_id if p.id == m.p1_id else m.p1_id
        op = await db.get_player_by_id(op_id)
        if op:
            await query.bot.send_message(op.tg_id, f"⏳ Матч #{match_id} перенесён на +5 минут (задержка соперника).")

    await query.message.answer("⏳ Задержка применена. Матч будет вызван снова минимум через 5 минут.")
    await mm.assign_free_tables()


@user_router.callback_query(MatchCb.filter(F.action == "report"))
async def cb_report(query: CallbackQuery, db: Database) -> None:
    await query.answer()
    data = MatchCb.unpack(query.data)
    match_id = int(data["match_id"]) if isinstance(data, dict) else int(data.match_id)

    p = await db.get_player_by_tg(query.from_user.id)
    if not p:
        await query.message.answer("⚠️ Ты не зарегистрирован.")
        return

    m = await db.get_match(match_id)
    if not m or m.status != "playing":
        await query.message.answer("⚠️ Сейчас нельзя сообщить результат (матч не идёт).")
        return

    if p.id not in (m.p1_id, m.p2_id):
        await query.message.answer("⚠️ Это не твой матч.")
        return

    await query.message.answer("🏁 Выбери результат:", reply_markup=report_kb(match_id))


@user_router.callback_query(ReportCb.filter())
async def cb_report_pick(query: CallbackQuery, db: Database) -> None:
    await query.answer()
    data = ReportCb.unpack(query.data)

    if isinstance(data, dict):
        match_id = int(data["match_id"])
        winner_flag = int(data["winner"])
        score_token = str(data["score"])
    else:
        match_id = int(data.match_id)
        winner_flag = int(data.winner)
        score_token = str(data.score)

    score_display = score_token.replace("_", ":")

    m = await db.get_match(match_id)
    if not m or m.status != "playing":
        await query.message.answer("⚠️ Матч уже не принимает результаты.")
        return

    reporter = await db.get_player_by_tg(query.from_user.id)
    if not reporter or reporter.id not in (m.p1_id, m.p2_id):
        await query.message.answer("⚠️ Это не твой матч.")
        return

    opponent_id = m.p2_id if reporter.id == m.p1_id else m.p1_id
    winner_id = reporter.id if winner_flag == 1 else opponent_id

    await db.set_match_reported(m.id, winner_id=winner_id, reported_by=reporter.id, score=score_display)

    opp = await db.get_player_by_id(opponent_id)
    if opp:
        await query.bot.send_message(
            opp.tg_id,
            f"🏁 По матчу #{m.id} заявлен результат: {score_display}.\n"
            "Подтверди или оспорь:",
            reply_markup=confirm_kb(m.id),
        )

    await query.message.answer("✅ Результат отправлен сопернику на подтверждение.")


@user_router.callback_query(ConfirmCb.filter())
async def cb_confirm(query: CallbackQuery, db: Database, mm: MatchmakingService) -> None:
    await query.answer()
    data = ConfirmCb.unpack(query.data)
    if isinstance(data, dict):
        action = data["action"]
        match_id = int(data["match_id"])
    else:
        action = data.action
        match_id = int(data.match_id)

    p = await db.get_player_by_tg(query.from_user.id)
    if not p:
        await query.message.answer("⚠️ Ты не зарегистрирован.")
        return

    m = await db.get_match(match_id)
    if not m or m.status != "reported":
        await query.message.answer("⚠️ Сейчас нельзя подтвердить/оспорить (матч не в стадии подтверждения).")
        return

    # подтверждать должен соперник reported_by
    if m.reported_by == p.id:
        await query.message.answer("⚠️ Подтверждение должен сделать соперник, а не тот кто отправил результат.")
        return

    if p.id not in (m.p1_id, m.p2_id):
        await query.message.answer("⚠️ Это не твой матч.")
        return

    if action == "ok":
        ok = await db.set_match_confirmed(match_id)
        if not ok:
            await query.message.answer("⚠️ Не удалось подтвердить (возможно, уже решено).")
            return

        await query.message.answer("✅ Подтверждено.")
        await mm.close_match_and_advance(match_id)
        return

    if action == "dispute":
        await db.set_match_admin_review_free_table(match_id)
        await query.message.answer("❗ Результат оспорен. Матч отправлен на разбор админу.")
        await mm.assign_free_tables()
        return

    await query.message.answer("⚠️ Неизвестное действие.")
