import asyncio
import logging

import httpx
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from openai import AsyncOpenAI

from src import db
from src.bitrix.client import BitrixClient
from src.config import load_settings
from src.digest import llm as llm_mod
from src.digest.scheduler import Deps, dry_run_send, tick
from src.i18n import load_locales
from src.telegram.commands import build_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


async def main() -> None:
    s = load_settings()
    pool = await db.create_pool(s.postgres_dsn)
    await db.apply_migrations(pool)

    async with httpx.AsyncClient(timeout=30) as http:
        deps = Deps(
            pool=pool,
            bx=BitrixClient(s.bitrix_webhook_url, http),
            bot=Bot(token=s.telegram_bot_token),
            llm_client=AsyncOpenAI(api_key=s.openai_api_key),
            locales=load_locales(),
            settings=s,
            prompt_template=llm_mod.load_prompt(),
        )
        if s.dry_run:
            deps.send_fn = dry_run_send
        deps.bot_username = (await deps.bot.get_me()).username or ""

        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(tick, "interval", minutes=s.scheduler_tick_minutes,
                          args=[deps], max_instances=1, coalesce=True)
        scheduler.start()

        # Меню команд: дискаверабилити + при нескольких ботах в чате клиент
        # подставляет @username из меню автоматически (адресация без коллизий)
        await deps.bot.set_my_commands([
            BotCommand(command="add", description="Подключить карточку: /add 42103"),
            BotCommand(command="list", description="Что отслеживается в этом топике"),
            BotCommand(command="remove", description="Снять карточку: /remove 42103"),
            BotCommand(command="time", description="Время дайджеста: /time 09:00 Europe/Belgrade"),
            BotCommand(command="lang", description="Язык дайджеста: /lang ru"),
            BotCommand(command="help", description="Как пользоваться ботом"),
        ])

        dp = Dispatcher()
        dp.include_router(build_router(deps))
        # privacy mode включён: до бота доходят только команды и reply (§14)
        await dp.start_polling(deps.bot)


if __name__ == "__main__":
    asyncio.run(main())
