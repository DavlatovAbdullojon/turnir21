from typing import Dict, List

from db import Database, Match


def _round_title(round_no: int, max_round: int) -> str:
    dist = max_round - round_no
    if dist == 0:
        return "Финал"
    if dist == 1:
        return "Полуфинал"
    if dist == 2:
        return "Четвертьфинал"
    if dist == 3:
        return "1/8 финала"
    return f"Раунд {round_no}"


def _human_status(m: Match, winner_name: str | None = None) -> str:
    if m.status == "closed":
        extra = f", победитель: {winner_name}" if winner_name else ""
        return f"✅ завершён{extra} ({m.score or '-'})"
    if m.status == "called":
        return f"📣 вызван (стол {m.table_id}, дедлайн {m.deadline_at})"
    if m.status == "playing":
        return f"🏓 идёт (стол {m.table_id})"
    if m.status == "reported":
        return f"🏁 ждёт подтверждения ({m.score or '-'})"
    if m.status == "admin_review":
        return "🛠 на разборе у админа"
    if m.status == "ready":
        return "⏳ в очереди"
    return m.status


async def render_bracket_text(db: Database) -> str:
    by_round: Dict[int, List[Match]] = await db.list_matches_by_round()
    if not by_round:
        return "🧩 Сетка ещё не создана. Админ должен нажать 🚀 Старт турнира."

    max_round = max(by_round.keys())
    lines: List[str] = ["🧩 Сетка турнира\n"]

    for rnd in sorted(by_round.keys()):
        lines.append(f"{_round_title(rnd, max_round)} (R{rnd}):")
        for m in by_round[rnd]:
            p1 = await db.get_player_by_id(m.p1_id)
            p2 = await db.get_player_by_id(m.p2_id)
            p1n = p1.name if p1 else f"#{m.p1_id}"
            p2n = p2.name if p2 else f"#{m.p2_id}"

            wname = None
            if m.winner_id:
                w = await db.get_player_by_id(m.winner_id)
                wname = w.name if w else f"#{m.winner_id}"

            lines.append(f"  • Матч #{m.id}: {p1n} vs {p2n} — {_human_status(m, wname)}")
        lines.append("")

    return "\n".join(lines).strip()
