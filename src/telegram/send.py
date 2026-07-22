import asyncio
from dataclasses import dataclass

from aiogram.exceptions import TelegramForbiddenError, TelegramMigrateToChat, TelegramRetryAfter

_ATTEMPTS = 3


@dataclass
class SendResult:
    ok: bool
    migrated_to: int | None = None
    forbidden: bool = False


async def send_html(bot, telegram_chat_id: int, thread_id: int | None, text: str) -> SendResult:
    chat_id = telegram_chat_id
    migrated_to: int | None = None
    for _ in range(_ATTEMPTS):
        kwargs = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True}
        if thread_id is not None:
            kwargs["message_thread_id"] = thread_id
        try:
            await bot.send_message(**kwargs)
            return SendResult(ok=True, migrated_to=migrated_to)
        except TelegramRetryAfter as e:          # 429 (§14)
            await asyncio.sleep(e.retry_after)
        except TelegramMigrateToChat as e:       # группа стала супергруппой (§14)
            chat_id = e.migrate_to_chat_id
            migrated_to = chat_id
        except TelegramForbiddenError:           # бот выкинут (§11)
            return SendResult(ok=False, forbidden=True)
    return SendResult(ok=False, migrated_to=migrated_to)
