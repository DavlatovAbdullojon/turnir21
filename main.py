import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import load_config
from db import Database
from handlers.admin import admin_router
from handlers.user import user_router
from services.matchmaking import MatchmakingService
from services.scheduler import SchedulerService


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    log = logging.getLogger("main")

    config = load_config()
    log.info("DB_PATH=%s", config.DB_PATH)

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()

    db = Database(config.DB_PATH)
    await db.connect()
    await db.init_schema()

    mm = MatchmakingService(bot=bot, db=db)
    scheduler = SchedulerService(bot=bot, db=db, matchmaking=mm)

    # aiogram 3.x DI (workflow_data)
    dp["db"] = db
    dp["mm"] = mm
    dp["config"] = config

    dp.include_router(user_router)
    dp.include_router(admin_router)

    log.info("Запуск фоновой проверки дедлайнов...")
    scheduler.start()

    try:
        log.info("Бот запущен. Polling...")
        await dp.start_polling(bot)
    finally:
        log.info("Остановка...")
        await scheduler.stop()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
