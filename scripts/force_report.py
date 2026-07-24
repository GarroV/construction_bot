"""Операционный инструмент: принудительный отчёт по карточке во все подписанные чаты.

Запуск внутри контейнера бота (или локально с env):
    python -m scripts.force_report <bitrix_task_id> [--rewind N]

--rewind N — откатить курсор комментариев на N назад перед прогоном (по умолчанию 0):
удобно для демонстрации/отладки, чтобы в отчёт гарантированно попали последние N
комментариев. Курсор истории не откатывается. Семантика прогона — как у «Отчёта
сейчас» (§5): process_chat(mark_run=False) — дневное расписание не ломается.
"""
import argparse
import asyncio
import datetime as dt

import httpx
from openai import AsyncOpenAI

from src import db, repo
from src.bitrix.client import BitrixClient
from src.config import load_settings
from src.digest import llm as llm_mod
from src.digest.scheduler import Deps, dry_run_send, process_chat
from src.i18n import load_locales, t


async def main(task_id: int, rewind: int) -> None:
    s = load_settings()
    pool = await db.create_pool(s.postgres_dsn)
    await db.apply_migrations(pool)
    async with httpx.AsyncClient(timeout=60) as http:
        from aiogram import Bot

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

        rows = await pool.fetch(
            "SELECT c.chat_id FROM cards c JOIN chats ch ON ch.id = c.chat_id "
            "WHERE c.bitrix_task_id = $1 AND c.active AND ch.active",
            task_id,
        )
        if not rows:
            print(f"карточку #{task_id} не отслеживает ни один активный чат")
            return

        if rewind > 0:
            comments = await deps.bx.call("task.commentitem.getlist", {"taskId": task_id}) or []
            ids = sorted(int(c["ID"]) for c in comments)
            target = ids[-(rewind + 1)] if len(ids) > rewind else 0
            for r in rows:
                await pool.execute(
                    "UPDATE cursors SET last_comment_id = LEAST(last_comment_id, $3) "
                    "WHERE bitrix_task_id = $1 AND chat_id = $2",
                    task_id, r["chat_id"], target,
                )
            print(f"курсор комментариев ≤ {target} (последние {rewind} шт. попадут в отчёт)")

        chats = {c.id: c for c in await repo.list_active_chats(pool)}
        now = dt.datetime.now(dt.timezone.utc)
        for r in rows:
            chat = chats.get(r["chat_id"])
            if chat is None:
                continue
            errors, posted = await process_chat(deps, chat, now, mark_run=False)
            if not posted:
                await deps.send_fn(
                    deps.bot, chat.telegram_chat_id, chat.message_thread_id,
                    t(deps.locales, chat.digest_language, "report_empty"),
                )
            print(f"chat {chat.id} ({chat.country}): posted={posted}, errors={errors or 'нет'}")
        await deps.bot.session.close()
    await pool.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("task_id", type=int)
    p.add_argument("--rewind", type=int, default=0)
    a = p.parse_args()
    asyncio.run(main(a.task_id, a.rewind))
