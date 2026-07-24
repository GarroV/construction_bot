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
    monkeypatch.setattr(commands.methods, "get_latest_comment_id", AsyncMock(return_value=0))
    add_card = AsyncMock(return_value="added")
    monkeypatch.setattr(commands.repo, "add_card", add_card)

    reply = await commands.handle_add(deps, CHAT, "8017", user_id=555)

    assert "Бишкек 8" in reply and "8017" in reply
    add_card.assert_awaited_once_with(deps.pool, 1, 8017, "Бишкек 8", 555, 100, 200, 0)


async def test_add_old_card_initializes_comment_cursor(monkeypatch):
    """§5: курсор комментариев старой карточки инициализируется «с этого момента» —
    первый дайджест не должен вываливать всю историю task.commentitem.getlist
    (314 шт. на живом смоуке, §13 fallback)."""
    deps = make_deps()
    monkeypatch.setattr(commands.methods, "get_task",
                        AsyncMock(return_value={"title": "Старая стройка"}))  # нет chatId
    monkeypatch.setattr(commands.methods, "get_latest_history_id", AsyncMock(return_value=50))
    monkeypatch.setattr(commands.methods, "get_latest_chat_message_id", AsyncMock(return_value=0))
    monkeypatch.setattr(commands.methods, "get_latest_comment_id", AsyncMock(return_value=314))
    add_card = AsyncMock(return_value="added")
    monkeypatch.setattr(commands.repo, "add_card", add_card)

    await commands.handle_add(deps, CHAT, "9001", user_id=1)

    add_card.assert_awaited_once_with(deps.pool, 1, 9001, "Старая стройка", 1, 50, 0, 314)


async def test_add_degrades_to_zero_comment_cursor_when_comment_api_errors(monkeypatch):
    """Important-фикс ревью: task.commentitem.getlist — deprecated метод; на карточке нового
    типа он может ответить ошибкой. /add не должен падать целиком (паттерн как в
    links.resolve_files) — деградация last_comment_id=0, а не пробрасывание BitrixError."""
    deps = make_deps()
    monkeypatch.setattr(commands.methods, "get_task",
                        AsyncMock(return_value={"title": "Бишкек 8", "chatId": 42}))
    monkeypatch.setattr(commands.methods, "get_latest_history_id", AsyncMock(return_value=100))
    monkeypatch.setattr(commands.methods, "get_latest_chat_message_id", AsyncMock(return_value=200))
    monkeypatch.setattr(commands.methods, "get_latest_comment_id",
                        AsyncMock(side_effect=BitrixError("NOT_FOUND", "deprecated method")))
    add_card = AsyncMock(return_value="added")
    monkeypatch.setattr(commands.repo, "add_card", add_card)

    reply = await commands.handle_add(deps, CHAT, "8017", user_id=555)

    assert "Бишкек 8" in reply and "8017" in reply  # add_ok, а не падение
    add_card.assert_awaited_once_with(deps.pool, 1, 8017, "Бишкек 8", 555, 100, 200, 0)


async def test_add_rejects_bad_args_and_missing_task(monkeypatch):
    deps = make_deps()
    # add_usage теперь без слова "Использование" — владелец зафиксировал новый текст
    # («Пришли номер карточки или ссылку на неё из Битрикса»), проверяем по сути.
    assert "номер" in await commands.handle_add(deps, CHAT, "", user_id=1)
    assert "номер" in await commands.handle_add(deps, CHAT, "фигня", user_id=1)

    monkeypatch.setattr(commands.methods, "get_task",
                        AsyncMock(side_effect=BitrixError("TASK_NOT_FOUND")))
    assert "не найдена" in await commands.handle_add(deps, CHAT, "999", user_id=1)


async def test_add_with_full_task_url_extracts_id(monkeypatch):
    """Владелец зафиксировал: /add принимает ID ИЛИ ссылку на карточку — партнёр
    копирует URL из браузера."""
    deps = make_deps()
    monkeypatch.setattr(commands.methods, "get_task",
                        AsyncMock(return_value={"title": "Бишкек 8", "chatId": 42}))
    monkeypatch.setattr(commands.methods, "get_latest_history_id", AsyncMock(return_value=100))
    monkeypatch.setattr(commands.methods, "get_latest_chat_message_id", AsyncMock(return_value=200))
    monkeypatch.setattr(commands.methods, "get_latest_comment_id", AsyncMock(return_value=0))
    add_card = AsyncMock(return_value="added")
    monkeypatch.setattr(commands.repo, "add_card", add_card)

    url = "https://b24.dodoteam.ru/company/personal/user/1650/tasks/task/view/42103/"
    reply = await commands.handle_add(deps, CHAT, url, user_id=555)

    assert "Бишкек 8" in reply and "42103" in reply
    add_card.assert_awaited_once_with(deps.pool, 1, 42103, "Бишкек 8", 555, 100, 200, 0)


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


def _member_msg(user_id):
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100, title="Кыргызстан"),
        from_user=SimpleNamespace(id=user_id),
        is_topic_message=False,
        message_thread_id=None,
    )


async def test_ensure_chat_restricted_denies_non_admin(monkeypatch):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[{"telegram_user_id": 1}])
    deps = make_deps(pool=pool)
    restricted_chat = SimpleNamespace(id=7, restricted=True, digest_language="ru")
    monkeypatch.setattr(commands.repo, "upsert_chat", AsyncMock(return_value=restricted_chat))

    assert await commands.ensure_chat(deps, _member_msg(user_id=999)) is None


async def test_ensure_chat_restricted_allows_admin(monkeypatch):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[{"telegram_user_id": 1}])
    deps = make_deps(pool=pool)
    restricted_chat = SimpleNamespace(id=7, restricted=True, digest_language="ru")
    monkeypatch.setattr(commands.repo, "upsert_chat", AsyncMock(return_value=restricted_chat))

    assert await commands.ensure_chat(deps, _member_msg(user_id=1)) is restricted_chat


async def test_ensure_chat_open_chat_skips_whitelist(monkeypatch):
    pool = AsyncMock()
    deps = make_deps(pool=pool)
    open_chat = SimpleNamespace(id=7, restricted=False, digest_language="ru")
    monkeypatch.setattr(commands.repo, "upsert_chat", AsyncMock(return_value=open_chat))

    assert await commands.ensure_chat(deps, _member_msg(user_id=999)) is open_chat
    pool.fetch.assert_not_awaited()


def _callback_obj(*, clicker_id: int, bot_id: int = 42):
    """callback.message.from_user — БОТ (автор сообщения с инлайн-кнопкой), не
    нажавшего; callback.from_user — реально нажавший пользователь. Ревью Critical:
    ensure_chat_for_callback обязана проверять права по нажавшему, а не по автору
    сообщения — иначе restricted-чат отваливается для любого админа (id бота никогда
    не будет в вайтлисте чата)."""
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100, title="Кыргызстан"),
        from_user=SimpleNamespace(id=bot_id),
        is_topic_message=False,
        message_thread_id=None,
    )
    return SimpleNamespace(message=message, from_user=SimpleNamespace(id=clicker_id))


async def test_ensure_chat_for_callback_checks_clicker_not_bot_author(monkeypatch):
    """Вайтлист содержит ТОЛЬКО нажавшего (777), не id бота (42). Со старым багом
    (проверка callback.message.from_user) это бы отказало даже вайтлистнутому админу."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[{"telegram_user_id": 777}])
    deps = make_deps(pool=pool)
    restricted_chat = SimpleNamespace(id=7, restricted=True, digest_language="ru")
    monkeypatch.setattr(commands.repo, "upsert_chat", AsyncMock(return_value=restricted_chat))

    callback = _callback_obj(clicker_id=777, bot_id=42)
    assert await commands.ensure_chat_for_callback(deps, callback) is restricted_chat


async def test_ensure_chat_for_callback_denies_when_clicker_not_whitelisted(monkeypatch):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[{"telegram_user_id": 777}])
    deps = make_deps(pool=pool)
    restricted_chat = SimpleNamespace(id=7, restricted=True, digest_language="ru")
    monkeypatch.setattr(commands.repo, "upsert_chat", AsyncMock(return_value=restricted_chat))

    callback = _callback_obj(clicker_id=999, bot_id=42)
    assert await commands.ensure_chat_for_callback(deps, callback) is None


async def test_start_returns_help(monkeypatch):
    deps = make_deps()
    reply = await commands.handle_start(deps, CHAT)
    assert "/add" in reply and "/time" in reply and "/list" in reply


def test_addressed_to_me_rules():
    group = SimpleNamespace(chat=SimpleNamespace(type="supergroup"), text="/add 42103")
    group_addr = SimpleNamespace(chat=SimpleNamespace(type="supergroup"),
                                 text="/add@Dodo_Construction_Bot 42103")
    group_other = SimpleNamespace(chat=SimpleNamespace(type="supergroup"),
                                  text="/add@other_bot 42103")
    private = SimpleNamespace(chat=SimpleNamespace(type="private"), text="/add 42103")

    assert commands._addressed_to_me(group, "dodo_construction_bot") is False
    assert commands._addressed_to_me(group_addr, "dodo_construction_bot") is True
    assert commands._addressed_to_me(group_other, "dodo_construction_bot") is False
    assert commands._addressed_to_me(private, "dodo_construction_bot") is True
    assert commands._addressed_to_me(group, "") is True  # username неизвестен -> не молчим


# --- _parse_task_ref: голое число ИЛИ ссылка на карточку из Битрикса -> ID ---

def test_parse_task_ref_plain_digits():
    assert commands._parse_task_ref("8017") == 8017
    assert commands._parse_task_ref("  8017  ") == 8017


def test_parse_task_ref_full_card_url():
    url = "https://b24.dodoteam.ru/company/personal/user/1650/tasks/task/view/42103/"
    assert commands._parse_task_ref(url) == 42103


def test_parse_task_ref_workgroups_card_url():
    url = "https://b24.dodoteam.ru/workgroups/group/25/tasks/task/view/42103/"
    assert commands._parse_task_ref(url) == 42103


def test_parse_task_ref_url_with_query_and_fragment_tail():
    url = "https://b24.dodoteam.ru/company/personal/user/1650/tasks/task/view/42103/?commentId=1#com1"
    assert commands._parse_task_ref(url) == 42103


def test_parse_task_ref_link_inside_surrounding_phrase():
    text = ("гляньте плз "
            "https://b24.dodoteam.ru/company/personal/user/1650/tasks/task/view/42103/ спасибо")
    assert commands._parse_task_ref(text) == 42103


def test_parse_task_ref_garbage_returns_none():
    assert commands._parse_task_ref("фигня") is None
    assert commands._parse_task_ref("") is None
    assert commands._parse_task_ref("   ") is None
    assert commands._parse_task_ref("https://b24.dodoteam.ru/tasks/list/") is None  # без /view/<id>


# --- resolve_empty_args_flow: пустые аргументы -> имя диалогового флоу (не usage) ---

def test_resolve_empty_args_flow_maps_dialog_commands_when_args_blank():
    assert commands.resolve_empty_args_flow("add", "") == "add"
    assert commands.resolve_empty_args_flow("time", "") == "time"
    assert commands.resolve_empty_args_flow("lang", "") == "lang"
    assert commands.resolve_empty_args_flow("remove", "") == "remove"
    assert commands.resolve_empty_args_flow("add", "   ") == "add"  # только пробелы — тоже пусто


def test_resolve_empty_args_flow_none_when_args_present_even_if_invalid():
    """Непустые (в т.ч. невалидные) аргументы -> None, ядро вызывается как обычно и
    само решает — usage-подсказка (/time 9:99, /add фигня) или успех."""
    assert commands.resolve_empty_args_flow("add", "8017") is None
    assert commands.resolve_empty_args_flow("add", "фигня") is None
    assert commands.resolve_empty_args_flow("time", "9:99") is None
    assert commands.resolve_empty_args_flow("lang", "russian!") is None


def test_resolve_empty_args_flow_none_for_commands_without_dialog():
    assert commands.resolve_empty_args_flow("list", "") is None
    assert commands.resolve_empty_args_flow("start", "") is None
    assert commands.resolve_empty_args_flow("help", "") is None
