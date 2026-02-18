from dataclasses import dataclass
from pathlib import Path
from typing import Set

from dotenv import load_dotenv
import os


@dataclass(frozen=True)
class Config:
    BOT_TOKEN: str
    ADMIN_IDS: Set[int]
    DB_PATH: str


def _parse_admin_ids(raw: str) -> Set[int]:
    out: Set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


def load_config() -> Config:
    load_dotenv()

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is empty in .env")

    admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))

    db_path = os.getenv("DB_PATH", "").strip()
    if not db_path:
        db_path = str(Path("tournament.db").resolve())

    return Config(BOT_TOKEN=token, ADMIN_IDS=admin_ids, DB_PATH=db_path)
