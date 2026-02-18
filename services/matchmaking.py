import asyncio
import logging

from aiogram import Bot

from db import Database, iso_plus_minutes, now_utc_iso


log = logging.getLogger("matchmaking")


class MatchmakingService:
    def __init__(self, bot: Bot, db: Database) -> None:
        self.bot = bot
        self.db = db
        self._assign_lock = asyncio.Lock()
        self._advance_lock = asyncio.Lock()

    async def assign_free_tables(self) -> None:
        await self.assign_next_match(1)
        await self.assign_next_match(2)

    async def assign_next_match(self, table_id: int) -> None:
        from keyboards import match_call_kb  # локальный импорт чтобы избежать циклов

        async with self._assign_lock:
            called_at = now_utc_iso()
            deadline_at = iso_plus_minutes(5)

            m = await self.db.assign_next_match_atomically(table_id, called_at, deadline_at)
            if not m:
                return

            p1 = await self.db.get_player_by_id(m.p1_id)
            p2 = await self.db.get_player_by_id(m.p2_id)
            if not p1 or not p2:
                log.error("Match %s has missing players -> admin_review", m.id)
                await self.db.set_match_admin_review_free_table(m.id)
                return

            msg = (
                f"📣 Вызов на матч #{m.id}\n"
                f"🏓 Стол: {table_id}\n"
                "Подтверди присутствие в течение 5 минут.\n"
                "Если нужна задержка (1 раз) — нажми ⏳ Нужна задержка."
            )

            await self.bot.send_message(
                p1.tg_id,
                msg,
                reply_markup=match_call_kb(m.id, can_delay=(p1.delay_count == 0)),
            )
            await self.bot.send_message(
                p2.tg_id,
                msg,
                reply_markup=match_call_kb(m.id, can_delay=(p2.delay_count == 0)),
            )
            log.info("Called match %s on table %s", m.id, table_id)

    async def try_start_match_if_both_ready(self, match_id: int) -> None:
        from keyboards import match_playing_kb

        m = await self.db.get_match(match_id)
        if not m or m.status != "called":
            return
        if m.p1_ready == 1 and m.p2_ready == 1:
            await self.db.set_match_playing(match_id)

            p1 = await self.db.get_player_by_id(m.p1_id)
            p2 = await self.db.get_player_by_id(m.p2_id)
            if not p1 or not p2:
                return

            text = (
                f"✅ Матч #{m.id} начался!\n"
                f"🏓 Стол: {m.table_id}\n"
                "После игры нажми 🏁 Сообщить результат."
            )
            await self.bot.send_message(p1.tg_id, text, reply_markup=match_playing_kb(m.id))
            await self.bot.send_message(p2.tg_id, text, reply_markup=match_playing_kb(m.id))
            log.info("Match %s -> playing", m.id)

    async def close_match_and_advance(self, match_id: int) -> None:
        m = await self.db.get_match(match_id)
        if not m:
            return
        if not m.winner_id:
            log.warning("Match %s closing without winner_id", match_id)
            return

        loser_id = m.p2_id if m.winner_id == m.p1_id else m.p1_id
        await self.db.mark_eliminated(loser_id)
        await self.db.set_match_confirmed_closed(match_id)

        await self._advance_bracket_if_ready(m.round)
        await self.assign_free_tables()

    async def _advance_bracket_if_ready(self, closed_round: int) -> None:
        async with self._advance_lock:
            if closed_round <= 0:
                return
            if not await self.db.round_is_complete(closed_round):
                return

            next_round = closed_round + 1
            if await self.db.round_exists(next_round):
                return

            winners = await self.db.list_winners_of_round(closed_round)
            if len(winners) <= 1:
                # Финал завершён. Ставим finished и делаем рассылку итогов один раз.
                st = await self.db.get_tournament_state()
                if st.state != "finished":
                    await self.db.set_state("finished")
                    await self._broadcast_tournament_finished()
                log.info("Tournament finished after round %s", closed_round)
                return

            if len(winners) % 2 != 0:
                log.warning("Odd winners count %s after round %s", len(winners), closed_round)
                return

            pairs = [(winners[i], winners[i + 1]) for i in range(0, len(winners), 2)]
            await self.db.create_round_matches(next_round, pairs)
            log.info("Created round %s with %s matches", next_round, len(pairs))

    async def handle_walkover_closed(self, round_no: int) -> None:
        await self._advance_bracket_if_ready(round_no)

    async def _broadcast_tournament_finished(self) -> None:
        """Рассылка итогов всем пользователям в БД."""
        from services.stats import compute_podium

        first, second, third = await compute_podium(self.db)

        lines = ["🏁 Турнир завершён!", "", "Поздравляем победителей 🎉", ""]

        if first:
            lines.append(f"🥇 1 место: {first.name}")
        if second:
            lines.append(f"🥈 2 место: {second.name}")

        if third:
            if len(third) == 1:
                lines.append(f"🥉 3 место: {third[0].name}")
            else:
                lines.append("🥉 3 место (совместное): " + ", ".join([p.name for p in third]))
        else:
            lines.append("🥉 3 место: —")

        lines.append("")
        lines.append("Спасибо всем за участие! 🏓")
        text = "\n".join(lines)

        tg_ids = await self.db.list_all_player_tg_ids()
        for tg_id in tg_ids:
            try:
                await self.bot.send_message(tg_id, text)
            except Exception:
                # Пользователь мог заблокировать бота / нет диалога — пропускаем
                log.exception("Failed to send finish message to tg_id=%s", tg_id)
