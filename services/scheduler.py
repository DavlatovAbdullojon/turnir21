import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot

from db import Database, Match
from services.matchmaking import MatchmakingService


log = logging.getLogger("scheduler")


class SchedulerService:
    def __init__(self, bot: Bot, db: Database, matchmaking: MatchmakingService) -> None:
        self.bot = bot
        self.db = db
        self.mm = matchmaking
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._check_deadlines()
            except Exception:
                log.exception("Ошибка в deadline-loop")
            await asyncio.sleep(12)

    async def _check_deadlines(self) -> None:
        expired = await self.db.list_called_expired()
        if not expired:
            return

        log.info("Expired called matches: %s", len(expired))
        for m in expired:
            await self._handle_expired_called(m)

        await self.mm.assign_free_tables()

    async def _handle_expired_called(self, m: Match) -> None:
        current = await self.db.get_match(m.id)
        if not current or current.status != "called":
            return
        m = current

        p1 = await self.db.get_player_by_id(m.p1_id)
        p2 = await self.db.get_player_by_id(m.p2_id)
        if not p1 or not p2:
            await self.db.set_match_admin_review_free_table(m.id)
            return

        # оба молчат
        if m.p1_ready == 0 and m.p2_ready == 0:
            await self.db.set_match_admin_review_free_table(m.id)
            await self.bot.send_message(p1.tg_id, f"⚠️ Матч #{m.id}: оба не подтвердили. Отправлено админу.")
            await self.bot.send_message(p2.tg_id, f"⚠️ Матч #{m.id}: оба не подтвердили. Отправлено админу.")
            return

        # один подтвердил, второй — нет
        missing = p1 if m.p1_ready == 0 else p2
        present = p2 if m.p1_ready == 0 else p1

        # если задержку ещё не тратил — тратим и откладываем матч на +5 минут
        if missing.delay_count == 0:
            not_before = (datetime.utcnow() + timedelta(minutes=5)).replace(microsecond=0).isoformat()
            changed = await self.db.consume_delay_and_postpone_called_match(m.id, missing.id, not_before)
            if not changed:
                return
            await self.bot.send_message(missing.tg_id, f"⏳ Матч #{m.id} перенесён на +5 минут. Задержка использована.")
            await self.bot.send_message(present.tg_id, f"⏳ Матч #{m.id} перенесён на +5 минут (соперник задержался).")
            return

        # иначе — техпоражение
        await self.db.set_match_closed_with_winner(m.id, winner_id=present.id)
        await self.db.mark_eliminated(missing.id)

        await self.bot.send_message(missing.tg_id, f"❌ Матч #{m.id}: техпоражение (2-я задержка/неявка).")
        await self.bot.send_message(present.tg_id, f"✅ Матч #{m.id}: победа по неявке соперника.")

        await self.mm.handle_walkover_closed(m.round)
