from __future__ import annotations

from typing import List, Optional, Tuple

from db import Database, Match, Player


async def compute_podium(db: Database) -> Tuple[Optional[Player], Optional[Player], List[Player]]:
    """Возвращает (1 место, 2 место, [3 место...]).

    В олимпийке без матча за 3-е обычно два 3-х места (оба проигравших в полуфиналах).
    """
    max_r = await db.max_round()
    if max_r <= 0:
        return None, None, []

    final = await db.get_last_closed_match_of_round(max_r)
    if not final or not final.winner_id:
        return None, None, []

    first = await db.get_player_by_id(final.winner_id)
    runner_id = final.p2_id if final.winner_id == final.p1_id else final.p1_id
    second = await db.get_player_by_id(runner_id)

    third: List[Player] = []
    if max_r >= 2:
        semis = await db.list_matches_of_round(max_r - 1)
        for m in semis:
            if m.status != "closed" or not m.winner_id:
                continue
            loser_id = m.p2_id if m.winner_id == m.p1_id else m.p1_id
            p = await db.get_player_by_id(loser_id)
            if p:
                third.append(p)

    # уникализация (на всякий)
    uniq: dict[int, Player] = {p.id: p for p in third}
    return first, second, list(uniq.values())


def _fmt_match_line(p1: str, p2: str, m: Match, winner_name: Optional[str]) -> str:
    score = m.score or "-"
    if winner_name:
        return f"• R{m.round} Матч #{m.id}: {p1} vs {p2} — ✅ {winner_name} ({score})"
    return f"• R{m.round} Матч #{m.id}: {p1} vs {p2} — {m.status}"


async def render_stats_text(db: Database) -> str:
    st = await db.get_tournament_state()

    active = await db.list_active_matches_global()
    closed = await db.list_closed_matches(limit=30)

    lines: List[str] = ["📈 Статистика турнира", f"Состояние: {st.state}", ""]

    # --- сейчас играют / вызваны ---
    if active:
        lines.append("🎮 Сейчас идёт / вызвано:")
        for m in active:
            p1 = await db.get_player_by_id(m.p1_id)
            p2 = await db.get_player_by_id(m.p2_id)
            p1n = p1.name if p1 else f"#{m.p1_id}"
            p2n = p2.name if p2 else f"#{m.p2_id}"

            if m.status == "called":
                lines.append(
                    f"• Стол {m.table_id}: 📣 {p1n} vs {p2n} (дедлайн {m.deadline_at})"
                )
            elif m.status == "playing":
                lines.append(f"• Стол {m.table_id}: 🏓 {p1n} vs {p2n} (идёт)"
                             )
            elif m.status == "reported":
                lines.append(
                    f"• Стол {m.table_id}: 🏁 {p1n} vs {p2n} (ждёт подтверждения, {m.score or '-'})"
                )
            else:
                lines.append(f"• {p1n} vs {p2n} ({m.status})")
        lines.append("")
    else:
        lines.append("🎮 Сейчас нет активных матчей (вызванных/идущих).")
        lines.append("")

    # --- результаты ---
    if closed:
        lines.append("✅ Результаты (последние):")
        for m in closed:
            p1 = await db.get_player_by_id(m.p1_id)
            p2 = await db.get_player_by_id(m.p2_id)
            p1n = p1.name if p1 else f"#{m.p1_id}"
            p2n = p2.name if p2 else f"#{m.p2_id}"
            wname = None
            if m.winner_id:
                w = await db.get_player_by_id(m.winner_id)
                wname = w.name if w else f"#{m.winner_id}"
            lines.append(_fmt_match_line(p1n, p2n, m, wname))
        lines.append("")
    else:
        lines.append("✅ Пока нет завершённых матчей.")
        lines.append("")

    # --- итоги ---
    if st.state == "finished":
        first, second, third = await compute_podium(db)
        lines.append("🏆 Итоги турнира:")
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

    return "\n".join(lines).strip()
