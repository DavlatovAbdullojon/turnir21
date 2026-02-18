from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
import logging


def now_utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def iso_plus_minutes(minutes: int) -> str:
    return (datetime.utcnow() + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


T = Any
log = logging.getLogger("db")


def dataclass_from_row(cls: T, row: aiosqlite.Row) -> T:
    """Создаёт dataclass из sqlite Row, игнорируя лишние колонки (например created_at)."""
    data = dict(row)
    allowed = {f.name for f in dataclasses.fields(cls)}
    filtered = {k: v for k, v in data.items() if k in allowed}
    return cls(**filtered)


@dataclass(slots=True)
class Player:
    id: int
    tg_id: int
    name: str
    registered_at: str
    is_waitlist: int
    checked_in: int
    delay_count: int
    status: str


@dataclass(slots=True)
class Match:
    id: int
    round: int
    p1_id: int
    p2_id: int
    winner_id: Optional[int]
    table_id: Optional[int]
    status: str
    called_at: Optional[str]
    deadline_at: Optional[str]
    reported_by: Optional[int]
    score: Optional[str]
    # В БД у тебя может быть created_at — мы его игнорируем через dataclass_from_row.
    p1_ready: int = 0
    p2_ready: int = 0


@dataclass(slots=True)
class TournamentState:
    id: int
    reg_open: int
    checkin_open: int
    state: str  # idle/reg/checkin/running/king/finished


@dataclass(slots=True)
class KingState:
    id: int
    is_active: int
    king_player_id: Optional[int]
    king_streak: int
    ends_at: Optional[str]


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.execute("PRAGMA busy_timeout=5000;")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("DB not connected")
        return self._conn

    async def init_schema(self) -> None:
        async with self._lock:
            await self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS players(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    is_waitlist INTEGER NOT NULL DEFAULT 0,
                    checked_in INTEGER NOT NULL DEFAULT 0,
                    delay_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'registered'
                );

                CREATE TABLE IF NOT EXISTS matches(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    round INTEGER NOT NULL,
                    p1_id INTEGER NOT NULL,
                    p2_id INTEGER NOT NULL,
                    winner_id INTEGER,
                    table_id INTEGER,
                    status TEXT NOT NULL,
                    called_at TEXT,
                    deadline_at TEXT,
                    reported_by INTEGER,
                    score TEXT,
                    p1_ready INTEGER NOT NULL DEFAULT 0,
                    p2_ready INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS tournament_state(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    reg_open INTEGER NOT NULL DEFAULT 0,
                    checkin_open INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'idle'
                );

                INSERT OR IGNORE INTO tournament_state(id, reg_open, checkin_open, state)
                VALUES (1, 0, 0, 'idle');

                CREATE TABLE IF NOT EXISTS king_state(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    is_active INTEGER NOT NULL DEFAULT 0,
                    king_player_id INTEGER,
                    king_streak INTEGER NOT NULL DEFAULT 0,
                    ends_at TEXT
                );

                INSERT OR IGNORE INTO king_state(id, is_active, king_player_id, king_streak, ends_at)
                VALUES (1, 0, NULL, 0, NULL);

                CREATE TABLE IF NOT EXISTS king_queue(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id INTEGER NOT NULL,
                    position INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);
                CREATE INDEX IF NOT EXISTS idx_matches_round ON matches(round);
                CREATE INDEX IF NOT EXISTS idx_players_tg ON players(tg_id);
                """
            )
            await self.conn.commit()

    # ---------- tournament state ----------

    async def get_tournament_state(self) -> TournamentState:
        async with self._lock:
            cur = await self.conn.execute("SELECT * FROM tournament_state WHERE id=1")
            row = await cur.fetchone()
            return dataclass_from_row(TournamentState, row)

    async def set_reg_open(self, value: int) -> None:
        async with self._lock:
            await self.conn.execute("UPDATE tournament_state SET reg_open=? WHERE id=1", (value,))
            await self.conn.commit()

    async def set_checkin_open(self, value: int) -> None:
        async with self._lock:
            await self.conn.execute("UPDATE tournament_state SET checkin_open=? WHERE id=1", (value,))
            await self.conn.commit()

    async def set_state(self, value: str) -> None:
        async with self._lock:
            await self.conn.execute("UPDATE tournament_state SET state=? WHERE id=1", (value,))
            await self.conn.commit()

    # ---------- players ----------

    async def get_player_by_tg(self, tg_id: int) -> Optional[Player]:
        async with self._lock:
            cur = await self.conn.execute("SELECT * FROM players WHERE tg_id=?", (tg_id,))
            row = await cur.fetchone()
            return dataclass_from_row(Player, row) if row else None

    async def get_player_by_id(self, pid: int) -> Optional[Player]:
        async with self._lock:
            cur = await self.conn.execute("SELECT * FROM players WHERE id=?", (pid,))
            row = await cur.fetchone()
            return dataclass_from_row(Player, row) if row else None

    async def count_registered_main(self) -> int:
        async with self._lock:
            cur = await self.conn.execute("SELECT COUNT(*) AS c FROM players WHERE is_waitlist=0")
            row = await cur.fetchone()
            return int(row["c"])

    async def upsert_player(self, tg_id: int, name: str, is_waitlist: int) -> Player:
        async with self._lock:
            cur = await self.conn.execute("SELECT * FROM players WHERE tg_id=?", (tg_id,))
            row = await cur.fetchone()
            existing = dataclass_from_row(Player, row) if row else None
            if existing:
                # имя можно менять до закрытия регистрации — проверяется в handler
                await self.conn.execute(
                    "UPDATE players SET name=?, is_waitlist=? WHERE tg_id=?",
                    (name, is_waitlist, tg_id),
                )
            else:
                await self.conn.execute(
                    """
                    INSERT INTO players(tg_id, name, registered_at, is_waitlist, checked_in, delay_count, status)
                    VALUES (?, ?, ?, ?, 0, 0, ?)
                    """,
                    (tg_id, name, now_utc_iso(), is_waitlist, "waitlist" if is_waitlist else "registered"),
                )
            await self.conn.commit()
            cur = await self.conn.execute("SELECT * FROM players WHERE tg_id=?", (tg_id,))
            row = await cur.fetchone()
            p = dataclass_from_row(Player, row) if row else None
            if not p:
                raise RuntimeError("upsert_player failed")
            return p

    async def set_checked_in(self, tg_id: int, value: int) -> None:
        async with self._lock:
            await self.conn.execute("UPDATE players SET checked_in=? WHERE tg_id=?", (value, tg_id))
            await self.conn.commit()

    async def reset_delays_and_status_for_selected(self, player_ids: List[int]) -> None:
        if not player_ids:
            return
        async with self._lock:
            q = ",".join(["?"] * len(player_ids))
            await self.conn.execute(
                f"UPDATE players SET delay_count=0, status='active' WHERE id IN ({q})",
                tuple(player_ids),
            )
            await self.conn.commit()

    async def mark_eliminated(self, player_id: int) -> None:
        async with self._lock:
            await self.conn.execute("UPDATE players SET status='eliminated' WHERE id=?", (player_id,))
            await self.conn.commit()

    async def list_checked_in_candidates(self) -> List[Player]:
        async with self._lock:
            cur = await self.conn.execute(
                """
                SELECT * FROM players
                WHERE checked_in=1
                ORDER BY is_waitlist ASC, registered_at ASC
                """
            )
            rows = await cur.fetchall()
            return [dataclass_from_row(Player, r) for r in rows]

    async def tournament_counts(self) -> Dict[str, int]:
        async with self._lock:
            cur = await self.conn.execute("SELECT COUNT(*) AS c FROM players")
            total = int((await cur.fetchone())["c"])
            cur = await self.conn.execute("SELECT COUNT(*) AS c FROM players WHERE checked_in=1")
            checked = int((await cur.fetchone())["c"])
            cur = await self.conn.execute("SELECT COUNT(*) AS c FROM players WHERE is_waitlist=1")
            wait = int((await cur.fetchone())["c"])
            cur = await self.conn.execute("SELECT COUNT(*) AS c FROM players WHERE is_waitlist=0")
            main = int((await cur.fetchone())["c"])
            return {"total": total, "checked_in": checked, "waitlist": wait, "main": main}

    async def increment_delay(self, player_id: int) -> None:
        async with self._lock:
            await self.conn.execute(
                "UPDATE players SET delay_count = MIN(delay_count + 1, 1) WHERE id=?",
                (player_id,),
            )
            await self.conn.commit()

    async def consume_delay_and_postpone_called_match(
        self,
        match_id: int,
        player_id: int,
        not_before: str,
    ) -> bool:
        """
        Атомарно тратит delay игрока и откладывает called-матч на not_before.
        """
        async with self._lock:
            try:
                await self.conn.execute("BEGIN IMMEDIATE")

                cur = await self.conn.execute(
                    """
                    SELECT m.id
                    FROM matches m
                    JOIN players p ON p.id=?
                    WHERE m.id=?
                      AND m.status='called'
                      AND p.delay_count=0
                      AND (? IN (m.p1_id, m.p2_id))
                    LIMIT 1
                    """,
                    (player_id, match_id, player_id),
                )
                row = await cur.fetchone()
                if not row:
                    await self.conn.rollback()
                    return False

                await self.conn.execute(
                    "UPDATE players SET delay_count=1 WHERE id=?",
                    (player_id,),
                )
                await self.conn.execute(
                    """
                    UPDATE matches
                    SET status='ready',
                        table_id=NULL,
                        called_at=?,
                        deadline_at=NULL,
                        p1_ready=0,
                        p2_ready=0
                    WHERE id=? AND status='called'
                    """,
                    (not_before, match_id),
                )
                await self.conn.commit()
                log.info("Delay consumed by player %s for match %s", player_id, match_id)
                return True
            except Exception:
                await self.conn.rollback()
                raise

    # ---------- matches ----------


    async def list_admin_review_matches(self, limit: int = 10) -> List[Match]:
        async with self._lock:
            cur = await self.conn.execute(
                """
                SELECT * FROM matches
                WHERE status='admin_review'
                ORDER BY round ASC, id ASC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cur.fetchall()
            return [dataclass_from_row(Match, r) for r in rows]

    async def clear_matches(self) -> None:
        async with self._lock:
            await self.conn.execute("DELETE FROM matches")
            await self.conn.commit()

    async def create_round_matches(self, round_no: int, pairs: List[Tuple[int, int]]) -> None:
        async with self._lock:
            for p1, p2 in pairs:
                await self.conn.execute(
                    """
                    INSERT INTO matches(round, p1_id, p2_id, winner_id, table_id, status, called_at, deadline_at, reported_by, score, p1_ready, p2_ready)
                    VALUES (?, ?, ?, NULL, NULL, 'ready', NULL, NULL, NULL, NULL, 0, 0)
                    """,
                    (round_no, p1, p2),
                )
            await self.conn.commit()

    async def get_match(self, match_id: int) -> Optional[Match]:
        async with self._lock:
            cur = await self.conn.execute("SELECT * FROM matches WHERE id=?", (match_id,))
            row = await cur.fetchone()
            return dataclass_from_row(Match, row) if row else None

    async def list_matches_by_round(self) -> Dict[int, List[Match]]:
        async with self._lock:
            cur = await self.conn.execute("SELECT * FROM matches WHERE round > 0 ORDER BY round, id")
            rows = await cur.fetchall()
            by: Dict[int, List[Match]] = {}
            for r in rows:
                m = dataclass_from_row(Match, r)
                by.setdefault(m.round, []).append(m)
            return by

    async def get_active_match_for_player(self, player_id: int) -> Optional[Match]:
        async with self._lock:
            cur = await self.conn.execute(
                """
                SELECT * FROM matches
                WHERE (p1_id=? OR p2_id=?)
                  AND status IN ('called','playing','reported','admin_review')
                ORDER BY round ASC, id ASC
                LIMIT 1
                """,
                (player_id, player_id),
            )
            row = await cur.fetchone()
            return dataclass_from_row(Match, row) if row else None

    async def table_is_busy(self, table_id: int) -> bool:
        async with self._lock:
            cur = await self.conn.execute(
                "SELECT COUNT(*) AS c FROM matches WHERE table_id=? AND status IN ('called','playing','reported')",
                (table_id,),
            )
            return int((await cur.fetchone())["c"]) > 0

    async def pick_ready_match_for_assignment(self) -> Optional[Match]:
        """
        Берём матч с самым ранним round, статус ready,
        и если called_at задан — это not_before (после delay), должен быть <= now.
        """
        now = now_utc_iso()
        async with self._lock:
            cur = await self.conn.execute(
                """
                SELECT * FROM matches
                WHERE status='ready'
                  AND (called_at IS NULL OR called_at <= ?)
                ORDER BY round ASC, id ASC
                LIMIT 1
                """,
                (now,),
            )
            row = await cur.fetchone()
            return dataclass_from_row(Match, row) if row else None

    async def set_match_called(self, match_id: int, table_id: int, called_at: str, deadline_at: str) -> None:
        async with self._lock:
            await self.conn.execute(
                """
                UPDATE matches
                SET status='called', table_id=?, called_at=?, deadline_at=?, p1_ready=0, p2_ready=0
                WHERE id=?
                """,
                (table_id, called_at, deadline_at, match_id),
            )
            await self.conn.commit()

    async def set_player_ready_flag(self, match_id: int, player_id: int) -> bool:
        async with self._lock:
            cur = await self.conn.execute(
                """
                UPDATE matches
                SET
                    p1_ready = CASE WHEN p1_id=? THEN 1 ELSE p1_ready END,
                    p2_ready = CASE WHEN p2_id=? THEN 1 ELSE p2_ready END
                WHERE id=? AND status='called' AND (? IN (p1_id, p2_id))
                """,
                (player_id, player_id, match_id, player_id),
            )
            await self.conn.commit()
            return (cur.rowcount or 0) > 0

    async def set_match_playing(self, match_id: int) -> None:
        async with self._lock:
            await self.conn.execute("UPDATE matches SET status='playing' WHERE id=?", (match_id,))
            await self.conn.commit()

    async def set_match_ready_delayed(self, match_id: int, not_before: str) -> None:
        async with self._lock:
            await self.conn.execute(
                """
                UPDATE matches
                SET status='ready', table_id=NULL, called_at=?, deadline_at=NULL, p1_ready=0, p2_ready=0
                WHERE id=? AND status='called'
                """,
                (not_before, match_id),
            )
            await self.conn.commit()

    async def set_match_admin_review_free_table(self, match_id: int) -> None:
        async with self._lock:
            await self.conn.execute(
                "UPDATE matches SET status='admin_review', table_id=NULL WHERE id=?",
                (match_id,),
            )
            await self.conn.commit()

    async def set_match_reported(self, match_id: int, winner_id: int, reported_by: int, score: str) -> None:
        async with self._lock:
            await self.conn.execute(
                """
                UPDATE matches
                SET status='reported', winner_id=?, reported_by=?, score=?
                WHERE id=? AND status='playing'
                """,
                (winner_id, reported_by, score, match_id),
            )
            await self.conn.commit()

    async def set_match_confirmed_closed(self, match_id: int) -> None:
        async with self._lock:
            await self.conn.execute(
                "UPDATE matches SET status='closed', table_id=NULL WHERE id=? AND status='confirmed'",
                (match_id,),
            )
            await self.conn.commit()

    async def set_match_confirmed(self, match_id: int) -> bool:
        async with self._lock:
            cur = await self.conn.execute(
                "UPDATE matches SET status='confirmed' WHERE id=? AND status='reported'",
                (match_id,),
            )
            await self.conn.commit()
            return (cur.rowcount or 0) > 0

    async def set_match_closed_with_winner(self, match_id: int, winner_id: int) -> None:
        async with self._lock:
            await self.conn.execute(
                """
                UPDATE matches
                SET status='closed', winner_id=?, table_id=NULL
                WHERE id=?
                """,
                (winner_id, match_id),
            )
            await self.conn.commit()

    async def list_called_expired(self) -> List[Match]:
        now = now_utc_iso()
        async with self._lock:
            cur = await self.conn.execute(
                """
                SELECT * FROM matches
                WHERE status='called'
                  AND deadline_at IS NOT NULL
                  AND deadline_at <= ?
                """,
                (now,),
            )
            rows = await cur.fetchall()
            return [dataclass_from_row(Match, r) for r in rows]

    async def round_is_complete(self, round_no: int) -> bool:
        async with self._lock:
            cur = await self.conn.execute(
                "SELECT COUNT(*) AS c FROM matches WHERE round=? AND status!='closed'",
                (round_no,),
            )
            return int((await cur.fetchone())["c"]) == 0

    async def list_winners_of_round(self, round_no: int) -> List[int]:
        async with self._lock:
            cur = await self.conn.execute(
                "SELECT winner_id FROM matches WHERE round=? AND status='closed' ORDER BY id",
                (round_no,),
            )
            rows = await cur.fetchall()
            return [int(r["winner_id"]) for r in rows if r["winner_id"] is not None]

    async def max_round(self) -> int:
        async with self._lock:
            cur = await self.conn.execute("SELECT MAX(round) AS r FROM matches WHERE round > 0")
            row = await cur.fetchone()
            return int(row["r"] or 0)

    async def round_exists(self, round_no: int) -> bool:
        async with self._lock:
            cur = await self.conn.execute(
                "SELECT COUNT(*) AS c FROM matches WHERE round=?",
                (round_no,),
            )
            return int((await cur.fetchone())["c"]) > 0

    async def assign_next_match_atomically(
        self,
        table_id: int,
        called_at: str,
        deadline_at: str,
    ) -> Optional[Match]:
        """
        Атомарно назначает первый доступный ready-матч на стол.
        """
        async with self._lock:
            try:
                await self.conn.execute("BEGIN IMMEDIATE")

                cur = await self.conn.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM matches
                    WHERE table_id=? AND status IN ('called', 'playing', 'reported')
                    """,
                    (table_id,),
                )
                if int((await cur.fetchone())["c"]) > 0:
                    await self.conn.rollback()
                    return None

                cur = await self.conn.execute(
                    """
                    SELECT m.*
                    FROM matches m
                    WHERE m.status='ready'
                      AND (m.called_at IS NULL OR m.called_at <= ?)
                      AND NOT EXISTS (
                        SELECT 1
                        FROM matches x
                        WHERE x.id != m.id
                          AND x.status IN ('called', 'playing', 'reported', 'admin_review')
                          AND (
                               x.p1_id IN (m.p1_id, m.p2_id)
                            OR x.p2_id IN (m.p1_id, m.p2_id)
                          )
                      )
                    ORDER BY m.round ASC, m.id ASC
                    LIMIT 1
                    """,
                    (called_at,),
                )
                row = await cur.fetchone()
                if not row:
                    await self.conn.rollback()
                    return None

                match = dataclass_from_row(Match, row)
                cur = await self.conn.execute(
                    """
                    UPDATE matches
                    SET status='called',
                        table_id=?,
                        called_at=?,
                        deadline_at=?,
                        p1_ready=0,
                        p2_ready=0
                    WHERE id=? AND status='ready'
                    """,
                    (table_id, called_at, deadline_at, match.id),
                )
                if (cur.rowcount or 0) == 0:
                    await self.conn.rollback()
                    return None

                await self.conn.commit()
                match.table_id = table_id
                match.status = "called"
                match.called_at = called_at
                match.deadline_at = deadline_at
                match.p1_ready = 0
                match.p2_ready = 0
                log.info("Assigned match %s to table %s", match.id, table_id)
                return match
            except Exception:
                await self.conn.rollback()
                raise
