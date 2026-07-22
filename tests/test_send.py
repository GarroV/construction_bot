from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramMigrateToChat, TelegramRetryAfter
from src.telegram import send


def _method_stub():
    return AsyncMock()


async def test_send_ok_passes_thread_and_html():
    bot = AsyncMock()
    result = await send.send_html(bot, -100, 77, "<b>hi</b>")
    assert result.ok
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["message_thread_id"] == 77 and kwargs["parse_mode"] == "HTML"


async def test_send_without_thread_omits_param():
    bot = AsyncMock()
    await send.send_html(bot, -100, None, "hi")
    assert "message_thread_id" not in bot.send_message.await_args.kwargs


async def test_retry_after_then_success(monkeypatch):
    monkeypatch.setattr(send.asyncio, "sleep", AsyncMock())
    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=[
        TelegramRetryAfter(method=_method_stub(), message="429", retry_after=1), AsyncMock()
    ])
    result = await send.send_html(bot, -100, None, "hi")
    assert result.ok and bot.send_message.await_count == 2


async def test_migrate_updates_chat_id():
    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=[
        TelegramMigrateToChat(method=_method_stub(), message="migrate", migrate_to_chat_id=-200),
        AsyncMock(),
    ])
    result = await send.send_html(bot, -100, None, "hi")
    assert result.ok and result.migrated_to == -200


async def test_forbidden_reports_gone_chat():
    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=TelegramForbiddenError(method=_method_stub(), message="kicked"))
    result = await send.send_html(bot, -100, None, "hi")
    assert not result.ok and result.forbidden
