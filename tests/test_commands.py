"""Ядра команд: repo и bitrix заменены фейками (юнит-тесты без сети и БД)."""
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from src.bitrix.client import BitrixError
from src.i18n import load_locales
from src.telegram import commands
from src.repo import CardRow


def make_deps(**over):
    deps = SimpleNamespace(
        pool=object(),
        bx=SimpleNamespace(),
        locales=load_locales(),
        settings=SimpleNamespace(default_language="ru"),
    )
    for k, v in over.items():
        setattr(deps, k, v)
    return deps


CHAT = SimpleNamespace(id=1, digest_language="ru", timezone="UTC",
                       digest_time=dt.time(9, 0), restricted=False)


async def test_add_happy_path(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(commands.methods, "get_task",
                        AsyncMock(return_value={"title": "Бишкек 8", "chatId": 42}))
    monkeypatch.setattr(commands.methods, "get_latest_history_id", AsyncMock(return_value=100))
    monkeypatch.setattr(commands.methods, "get_latest_chat_message_id", AsyncMock(return_value=200))
    add_card = AsyncMock(return_value="added")
    monkeypatch.setattr(commands.repo, "add_card", add_card)

    reply = await commands.handle_add(deps, CHAT, "8017", user_id=555)

    assert "Бишкек 8" in reply and "8017" in reply
    add_card.assert_awaited_once_with(deps.pool, 1, 8017, "Бишкек 8", 555, 100, 200)


async def test_add_rejects_bad_args_and_missing_task(monkeypatch):
    deps = make_deps()
    assert "Использование" in await commands.handle_add(deps, CHAT, "", user_id=1)
    assert "Использование" in await commands.handle_add(deps, CHAT, "abc", user_id=1)

    monkeypatch.setattr(commands.methods, "get_task",
                        AsyncMock(side_effect=BitrixError("TASK_NOT_FOUND")))
    assert "не найдена" in await commands.handle_add(deps, CHAT, "999", user_id=1)


async def test_remove_and_list(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(commands.repo, "deactivate_card", AsyncMock(return_value=False))
    assert "не отслеживается" in await commands.handle_remove(deps, CHAT, "8017")

    monkeypatch.setattr(commands.repo, "list_active_cards", AsyncMock(return_value=[
        CardRow(id=1, bitrix_task_id=8017, chat_id=1, alias="Бишкек 8", active=True),
    ]))
    listing = await commands.handle_list(deps, CHAT)
    assert "Бишкек 8" in listing and "8017" in listing


async def test_time_validation(monkeypatch):
    deps = make_deps()
    set_time = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_time", set_time)

    assert "Использование" in await commands.handle_time(deps, CHAT, "9:99 Asia/Bishkek")
    assert "Использование" in await commands.handle_time(deps, CHAT, "09:00 Mars/Olympus")
    # чат ещё на дефолтной UTC, таймзона не передана -> просим указать (§5)
    assert "таймзон" in (await commands.handle_time(deps, CHAT, "09:00")).lower()

    ok = await commands.handle_time(deps, CHAT, "09:00 Asia/Bishkek")
    assert "09:00" in ok and "Asia/Bishkek" in ok
    set_time.assert_awaited_once_with(deps.pool, 1, dt.time(9, 0), "Asia/Bishkek")


async def test_lang(monkeypatch):
    deps = make_deps()
    set_lang = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_language", set_lang)

    assert "Использование" in await commands.handle_lang(deps, CHAT, "russian!")
    assert "ru" in await commands.handle_lang(deps, CHAT, "ru")
    set_lang.assert_awaited_once_with(deps.pool, 1, "ru")


async def test_membership_kicked_deactivates_chats(monkeypatch):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
    deps = make_deps(pool=pool)
    deact = AsyncMock()
    monkeypatch.setattr(commands.repo, "deactivate_chat", deact)

    await commands.handle_membership(deps, -100, "kicked")
    assert deact.await_count == 2

    deact.reset_mock()
    await commands.handle_membership(deps, -100, "member")
    deact.assert_not_awaited()
