"""Ядра команд: repo и bitrix заменены фейками (юнит-тесты без сети и БД)."""
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from src.bitrix.client import BitrixError
from src.i18n import load_locales
from src.telegram import commands
from src.repo import CardRow, ChatRow


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


# auto_configured=True — большинство тестов ниже не про автонастройку из карточки (§5) и
# не поднимают llm_client/фейковые бб-комментарии; True гасит новую ветку в handle_add,
# как и было бы в реальности у чата, где /lang или /time уже вызывались хоть раз.
CHAT = SimpleNamespace(id=1, digest_language="ru", timezone="UTC",
                       digest_time=dt.time(9, 0), restricted=False, auto_configured=True)


def _chat_row(**over):
    """ChatRow real, а не SimpleNamespace — нужен там, где _ensure_chat_core делает
    dataclasses.replace(chat, ...) после автоопределения таймзоны (§5)."""
    base = dict(
        id=7, country=None, telegram_chat_id=-100, message_thread_id=None,
        digest_language="ru", digest_time=dt.time(9, 0), timezone="UTC",
        last_digest_date=None, last_posted_at=None, last_ping_at=None,
        restricted=False, active=True, created_at=dt.datetime(2026, 1, 1),
        auto_configured=True,
    )
    base.update(over)
    return ChatRow(**base)


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


# --- Автонастройка чата из карточки при первом /add (§5, дизайн владельца зафиксирован):
# язык (ru/en) и таймзона города через один LLM-вызов; ручное /lang//time побеждает навсегда.

CHAT_UNCONFIGURED = SimpleNamespace(id=1, digest_language="ru", timezone="UTC",
                                    digest_time=dt.time(9, 0), restricted=False,
                                    auto_configured=False)


def _autoconfigure_deps(**settings_over):
    settings = SimpleNamespace(default_language="ru", openai_model="gpt-5.6-terra")
    for k, v in settings_over.items():
        setattr(settings, k, v)
    return make_deps(llm_client=object(), settings=settings)


async def test_add_autoconfigures_language_and_timezone_from_new_card(monkeypatch):
    """§5: первый /add на чате без auto_configured детектит язык и таймзону ОДНИМ
    LLM-вызовом и применяет их к чату. Новая карточка (есть chatId) — комментарии для
    детекта берутся через im.dialog.messages.get (fetch_latest_chat_messages)."""
    deps = _autoconfigure_deps()
    monkeypatch.setattr(commands.methods, "get_task",
                        AsyncMock(return_value={"title": "Podgorica-2", "chatId": 42}))
    monkeypatch.setattr(commands.methods, "get_latest_history_id", AsyncMock(return_value=100))
    monkeypatch.setattr(commands.methods, "get_latest_chat_message_id", AsyncMock(return_value=200))
    monkeypatch.setattr(commands.methods, "get_latest_comment_id", AsyncMock(return_value=0))
    monkeypatch.setattr(commands.repo, "add_card", AsyncMock(return_value="added"))
    fetch_chat_msgs = AsyncMock(return_value=([{"id": 1}], {}))
    monkeypatch.setattr(commands.methods, "fetch_latest_chat_messages", fetch_chat_msgs)
    monkeypatch.setattr(commands.parse, "parse_chat_messages",
                        lambda raw, users: [SimpleNamespace(text="Хорошо, договорились")])
    detect = AsyncMock(return_value=("ru", "Europe/Podgorica"))
    monkeypatch.setattr(commands.llm, "detect_card_locale", detect)
    set_lang = AsyncMock()
    set_time = AsyncMock()
    set_auto = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_language", set_lang)
    monkeypatch.setattr(commands.repo, "set_chat_time", set_time)
    monkeypatch.setattr(commands.repo, "set_auto_configured", set_auto)

    reply = await commands.handle_add(deps, CHAT_UNCONFIGURED, "8017", user_id=555)

    assert "Podgorica-2" in reply  # /add ответ как обычно, без доп. сообщений
    fetch_chat_msgs.assert_awaited_once_with(deps.bx, 42, commands._LOCALE_COMMENT_LIMIT)
    detect.assert_awaited_once_with(
        deps.llm_client, deps.settings.openai_model, "Podgorica-2", ["Хорошо, договорились"]
    )
    set_lang.assert_awaited_once_with(deps.pool, 1, "ru")
    set_time.assert_awaited_once_with(deps.pool, 1, dt.time(9, 0), "Europe/Podgorica")
    set_auto.assert_awaited_once_with(deps.pool, 1, True)


async def test_add_autoconfigure_skipped_when_already_configured(monkeypatch):
    """auto_configured=True (ручная /lang или /time уже была, либо детект уже удался
    на прошлой карточке) -> детект НЕ вызывается вовсе."""
    deps = make_deps()
    monkeypatch.setattr(commands.methods, "get_task",
                        AsyncMock(return_value={"title": "Бишкек 8", "chatId": 42}))
    monkeypatch.setattr(commands.methods, "get_latest_history_id", AsyncMock(return_value=100))
    monkeypatch.setattr(commands.methods, "get_latest_chat_message_id", AsyncMock(return_value=200))
    monkeypatch.setattr(commands.methods, "get_latest_comment_id", AsyncMock(return_value=0))
    monkeypatch.setattr(commands.repo, "add_card", AsyncMock(return_value="added"))
    detect = AsyncMock()
    monkeypatch.setattr(commands.llm, "detect_card_locale", detect)

    await commands.handle_add(deps, CHAT, "8017", user_id=555)  # CHAT.auto_configured=True

    detect.assert_not_awaited()


async def test_add_autoconfigure_llm_failure_keeps_add_ok_and_settings_untouched(monkeypatch):
    """Тихая деградация (владелец): LLM недоступен/вернул мусор -> /add всё равно
    возвращает обычный add_ok, настройки чата не меняются вовсе (auto_configured
    остаётся False — новая попытка на следующей карточке)."""
    deps = _autoconfigure_deps()
    monkeypatch.setattr(commands.methods, "get_task",
                        AsyncMock(return_value={"title": "Бишкек 8", "chatId": 42}))
    monkeypatch.setattr(commands.methods, "get_latest_history_id", AsyncMock(return_value=100))
    monkeypatch.setattr(commands.methods, "get_latest_chat_message_id", AsyncMock(return_value=200))
    monkeypatch.setattr(commands.methods, "get_latest_comment_id", AsyncMock(return_value=0))
    monkeypatch.setattr(commands.repo, "add_card", AsyncMock(return_value="added"))
    monkeypatch.setattr(commands.methods, "fetch_latest_chat_messages",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(commands.parse, "parse_chat_messages", lambda raw, users: [])
    monkeypatch.setattr(commands.llm, "detect_card_locale",
                        AsyncMock(side_effect=commands.llm.LlmUnavailable("недоступен")))
    set_lang = AsyncMock()
    set_time = AsyncMock()
    set_auto = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_language", set_lang)
    monkeypatch.setattr(commands.repo, "set_chat_time", set_time)
    monkeypatch.setattr(commands.repo, "set_auto_configured", set_auto)

    reply = await commands.handle_add(deps, CHAT_UNCONFIGURED, "8017", user_id=555)

    assert "Бишкек 8" in reply and "8017" in reply  # add_ok, а не падение
    set_lang.assert_not_awaited()
    set_time.assert_not_awaited()
    set_auto.assert_not_awaited()


async def test_add_autoconfigure_db_failure_after_detect_keeps_add_ok_and_flag_false(monkeypatch):
    """Ревью: детект LLM прошёл успешно (язык+таймзона определены), но применение к БД
    подводит (`set_chat_time` кидает исключение, например обрыв соединения) — /add
    всё равно должен вернуть обычный add_ok, а `auto_configured` НЕ должен выставиться
    в True: `set_auto_configured` стоит ПОСЛЕ `set_chat_time` внутри одного try/except
    в `_auto_configure_from_card`, так что исключение из set_chat_time обрывает функцию
    до вызова set_auto_configured — флаг остаётся False, следующий /add повторит
    попытку целиком (см. фикс доков §5/§12: TRUE ставится только при полном успехе)."""
    deps = _autoconfigure_deps()
    monkeypatch.setattr(commands.methods, "get_task",
                        AsyncMock(return_value={"title": "Podgorica-2", "chatId": 42}))
    monkeypatch.setattr(commands.methods, "get_latest_history_id", AsyncMock(return_value=100))
    monkeypatch.setattr(commands.methods, "get_latest_chat_message_id", AsyncMock(return_value=200))
    monkeypatch.setattr(commands.methods, "get_latest_comment_id", AsyncMock(return_value=0))
    monkeypatch.setattr(commands.repo, "add_card", AsyncMock(return_value="added"))
    monkeypatch.setattr(commands.methods, "fetch_latest_chat_messages",
                        AsyncMock(return_value=([{"id": 1}], {})))
    monkeypatch.setattr(commands.parse, "parse_chat_messages",
                        lambda raw, users: [SimpleNamespace(text="Согласовано, работаем")])
    monkeypatch.setattr(commands.llm, "detect_card_locale",
                        AsyncMock(return_value=("ru", "Europe/Podgorica")))
    set_lang = AsyncMock()
    set_time = AsyncMock(side_effect=RuntimeError("БД недоступна"))
    set_auto = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_language", set_lang)
    monkeypatch.setattr(commands.repo, "set_chat_time", set_time)
    monkeypatch.setattr(commands.repo, "set_auto_configured", set_auto)

    reply = await commands.handle_add(deps, CHAT_UNCONFIGURED, "8017", user_id=555)

    assert "Podgorica-2" in reply and "8017" in reply  # add_ok, а не падение
    set_lang.assert_awaited_once_with(deps.pool, 1, "ru")  # успел выполниться до сбоя
    set_time.assert_awaited_once_with(deps.pool, 1, dt.time(9, 0), "Europe/Podgorica")
    set_auto.assert_not_awaited()  # не выставлен — следующий /add повторит попытку


async def test_add_autoconfigure_invalid_timezone_from_llm_is_skipped(monkeypatch):
    """LLM вернул несуществующую IANA-зону (город не в tzdata) — set_chat_time не
    вызывается, но язык и auto_configured применяются (сам детект в целом успешен).
    Заодно проверяет ветку старой карточки без chatId (task.commentitem.getlist)."""
    deps = _autoconfigure_deps()
    monkeypatch.setattr(commands.methods, "get_task",
                        AsyncMock(return_value={"title": "Тестовая стройка"}))  # нет chatId
    monkeypatch.setattr(commands.methods, "get_latest_history_id", AsyncMock(return_value=100))
    monkeypatch.setattr(commands.methods, "get_latest_chat_message_id", AsyncMock(return_value=0))
    monkeypatch.setattr(commands.methods, "get_latest_comment_id", AsyncMock(return_value=0))
    monkeypatch.setattr(commands.repo, "add_card", AsyncMock(return_value="added"))
    fetch_comments = AsyncMock(return_value=[])
    monkeypatch.setattr(commands.methods, "fetch_latest_comments", fetch_comments)
    monkeypatch.setattr(commands.parse, "parse_comments", lambda raw: [])
    monkeypatch.setattr(commands.llm, "detect_card_locale",
                        AsyncMock(return_value=("en", "Mars/Olympus")))
    set_lang = AsyncMock()
    set_time = AsyncMock()
    set_auto = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_language", set_lang)
    monkeypatch.setattr(commands.repo, "set_chat_time", set_time)
    monkeypatch.setattr(commands.repo, "set_auto_configured", set_auto)

    await commands.handle_add(deps, CHAT_UNCONFIGURED, "9001", user_id=1)

    fetch_comments.assert_awaited_once_with(deps.bx, 9001, commands._LOCALE_COMMENT_LIMIT)
    set_lang.assert_awaited_once_with(deps.pool, 1, "en")
    set_time.assert_not_awaited()
    set_auto.assert_awaited_once_with(deps.pool, 1, True)


async def test_add_autoconfigure_empty_timezone_from_llm_skips_set_chat_time(monkeypatch):
    """LLM не смог определить город (timezone=None после нормализации в llm.py) —
    set_chat_time не вызывается, язык и auto_configured всё равно применяются."""
    deps = _autoconfigure_deps()
    monkeypatch.setattr(commands.methods, "get_task",
                        AsyncMock(return_value={"title": "Стройка без города", "chatId": 7}))
    monkeypatch.setattr(commands.methods, "get_latest_history_id", AsyncMock(return_value=0))
    monkeypatch.setattr(commands.methods, "get_latest_chat_message_id", AsyncMock(return_value=0))
    monkeypatch.setattr(commands.methods, "get_latest_comment_id", AsyncMock(return_value=0))
    monkeypatch.setattr(commands.repo, "add_card", AsyncMock(return_value="added"))
    monkeypatch.setattr(commands.methods, "fetch_latest_chat_messages",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(commands.parse, "parse_chat_messages", lambda raw, users: [])
    monkeypatch.setattr(commands.llm, "detect_card_locale", AsyncMock(return_value=("en", None)))
    set_lang = AsyncMock()
    set_time = AsyncMock()
    set_auto = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_language", set_lang)
    monkeypatch.setattr(commands.repo, "set_chat_time", set_time)
    monkeypatch.setattr(commands.repo, "set_auto_configured", set_auto)

    await commands.handle_add(deps, CHAT_UNCONFIGURED, "4242", user_id=1)

    set_lang.assert_awaited_once_with(deps.pool, 1, "en")
    set_time.assert_not_awaited()
    set_auto.assert_awaited_once_with(deps.pool, 1, True)


async def test_collect_locale_comments_truncates_and_limits(monkeypatch):
    """Сырьё для LLM-детекта — не более _LOCALE_COMMENT_LIMIT комментариев, каждый
    обрезан до _LOCALE_COMMENT_TRUNCATE символов (реальный parse.parse_comments, не мок —
    strip_bbcode применяется по дороге)."""
    deps = make_deps()
    raw = [{"ID": i, "AUTHOR_NAME": "Иван", "POST_MESSAGE": "x" * 300} for i in range(1, 4)]
    monkeypatch.setattr(commands.methods, "fetch_latest_comments", AsyncMock(return_value=raw))

    result = await commands._collect_locale_comments(deps, 8017, None)

    assert len(result) == 3
    assert all(len(text) == commands._LOCALE_COMMENT_TRUNCATE for text in result)


async def test_remove_and_list(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(commands.repo, "deactivate_card", AsyncMock(return_value=False))
    assert "не отслеживается" in await commands.handle_remove(deps, CHAT, "8017")

    monkeypatch.setattr(commands.repo, "list_active_cards", AsyncMock(return_value=[
        CardRow(id=1, bitrix_task_id=8017, chat_id=1, alias="Бишкек 8", active=True),
    ]))
    listing = await commands.handle_list(deps, CHAT)
    assert "Бишкек 8" in listing and "8017" in listing


async def test_list_marks_auto_discovered_cards_with_hook(monkeypatch):
    """Фича 1 (§5): авто-подхваченная подзадача (auto_from не None) помечается «↳»
    перед alias в /list, ручная — обычным маркером «•»."""
    deps = make_deps()
    monkeypatch.setattr(commands.repo, "list_active_cards", AsyncMock(return_value=[
        CardRow(id=1, bitrix_task_id=8017, chat_id=1, alias="Бишкек 8", active=True),
        CardRow(id=2, bitrix_task_id=73689, chat_id=1, alias="Бишкек 8 / Подзадача",
                active=True, auto_from=8017),
    ]))

    listing = await commands.handle_list(deps, CHAT)

    assert "• Бишкек 8 (#8017)" in listing
    assert "↳ Бишкек 8 / Подзадача (#73689)" in listing


async def test_time_validation(monkeypatch):
    deps = make_deps()
    set_time = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_time", set_time)
    set_auto = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_auto_configured", set_auto)

    assert "Использование" in await commands.handle_time(deps, CHAT, "9:99 Asia/Bishkek")
    assert "Использование" in await commands.handle_time(deps, CHAT, "09:00 Mars/Olympus")
    # чат ещё на дефолтной UTC, таймзона не передана -> просим указать (§5)
    assert "таймзон" in (await commands.handle_time(deps, CHAT, "09:00")).lower()

    ok = await commands.handle_time(deps, CHAT, "09:00 Asia/Bishkek")
    assert "09:00" in ok and "Asia/Bishkek" in ok
    set_time.assert_awaited_once_with(deps.pool, 1, dt.time(9, 0), "Asia/Bishkek")
    # только успешный вызов ставит auto_configured — три invalid-попытки выше его не задели
    set_auto.assert_awaited_once_with(deps.pool, 1, True)


async def test_time_accepts_city_name(monkeypatch):
    """Фидбек владельца (скриншот): партнёр ответил на time-prompt «14:49 белград» —
    handle_time теперь резолвит город через resolve_tz, а не требует голый IANA."""
    deps = make_deps()
    set_time = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_time", set_time)
    monkeypatch.setattr(commands.repo, "set_auto_configured", AsyncMock())

    ok = await commands.handle_time(deps, CHAT, "09:00 белград")

    assert "09:00" in ok and "Europe/Belgrade" in ok
    set_time.assert_awaited_once_with(deps.pool, 1, dt.time(9, 0), "Europe/Belgrade")


async def test_time_still_accepts_iana_tz_directly(monkeypatch):
    deps = make_deps()
    set_time = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_time", set_time)
    monkeypatch.setattr(commands.repo, "set_auto_configured", AsyncMock())

    ok = await commands.handle_time(deps, CHAT, "09:00 Europe/Belgrade")

    assert "09:00" in ok and "Europe/Belgrade" in ok
    set_time.assert_awaited_once_with(deps.pool, 1, dt.time(9, 0), "Europe/Belgrade")


async def test_time_unresolvable_city_falls_back_to_usage(monkeypatch):
    deps = make_deps()
    set_time = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_time", set_time)
    set_auto = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_auto_configured", set_auto)

    reply = await commands.handle_time(deps, CHAT, "09:00 нарния")

    assert "Использование" in reply
    set_time.assert_not_awaited()
    set_auto.assert_not_awaited()  # невалидный ввод не должен блокировать будущую автонастройку


async def test_time_sets_auto_configured_to_block_future_card_autodetect(monkeypatch):
    """§5: ручная /time всегда побеждает будущее автоопределение из карточки — флаг
    ставится сразу после успешного применения."""
    deps = make_deps()
    monkeypatch.setattr(commands.repo, "set_chat_time", AsyncMock())
    set_auto = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_auto_configured", set_auto)

    await commands.handle_time(deps, CHAT, "09:00 Asia/Bishkek")

    set_auto.assert_awaited_once_with(deps.pool, 1, True)


async def test_lang(monkeypatch):
    deps = make_deps()
    set_lang = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_language", set_lang)
    set_auto = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_auto_configured", set_auto)

    assert "Использование" in await commands.handle_lang(deps, CHAT, "russian!")
    assert "ru" in await commands.handle_lang(deps, CHAT, "ru")
    set_lang.assert_awaited_once_with(deps.pool, 1, "ru")
    # только успешный вызов ставит auto_configured (§5: ручная настройка навсегда
    # побеждает автоопределение из карточки при /add) — invalid-попытка его не задела
    set_auto.assert_awaited_once_with(deps.pool, 1, True)


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
    monkeypatch.setattr(commands.repo, "upsert_chat", AsyncMock(return_value=(restricted_chat, False)))

    assert await commands.ensure_chat(deps, _member_msg(user_id=999)) is None


async def test_ensure_chat_restricted_allows_admin(monkeypatch):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[{"telegram_user_id": 1}])
    deps = make_deps(pool=pool)
    restricted_chat = SimpleNamespace(id=7, restricted=True, digest_language="ru")
    monkeypatch.setattr(commands.repo, "upsert_chat", AsyncMock(return_value=(restricted_chat, False)))

    assert await commands.ensure_chat(deps, _member_msg(user_id=1)) is restricted_chat


async def test_ensure_chat_open_chat_skips_whitelist(monkeypatch):
    pool = AsyncMock()
    deps = make_deps(pool=pool)
    open_chat = SimpleNamespace(id=7, restricted=False, digest_language="ru")
    monkeypatch.setattr(commands.repo, "upsert_chat", AsyncMock(return_value=(open_chat, False)))

    assert await commands.ensure_chat(deps, _member_msg(user_id=999)) is open_chat
    pool.fetch.assert_not_awaited()


def _new_chat_msg(title: str, user_id: int = 555):
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100, title=title),
        from_user=SimpleNamespace(id=user_id),
        is_topic_message=False,
        message_thread_id=None,
    )


async def test_ensure_chat_autodetects_timezone_from_title_on_first_contact(monkeypatch):
    """§5 (фидбек владельца, скриншот): Telegram не отдаёт таймзону юзера — при первом
    контакте (upsert_chat created=True) бот угадывает её из названия супергруппы и
    тихо проставляет через set_chat_time (без дополнительного ответа партнёру)."""
    pool = AsyncMock()
    deps = make_deps(pool=pool)
    new_chat = _chat_row(id=7, restricted=False)
    monkeypatch.setattr(commands.repo, "upsert_chat", AsyncMock(return_value=(new_chat, True)))
    set_time = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_time", set_time)

    result = await commands.ensure_chat(deps, _new_chat_msg("Сербия"))

    set_time.assert_awaited_once_with(deps.pool, 7, dt.time(9, 0), "Europe/Belgrade")
    # ChatRow заменён на месте (dataclasses.replace) — та же команда, если бы это был
    # голый /time без таймзоны, должна видеть УЖЕ определённую timezone, а не UTC.
    assert result.timezone == "Europe/Belgrade"
    assert result.digest_time == dt.time(9, 0)


async def test_ensure_chat_unresolvable_title_leaves_default_timezone(monkeypatch):
    """Тестовый чат «group_za_test» не резолвится ни в один город/страну — таймзона
    остаётся дефолтной UTC, set_chat_time не вызывается вовсе."""
    pool = AsyncMock()
    deps = make_deps(pool=pool)
    new_chat = _chat_row(id=7, restricted=False)
    monkeypatch.setattr(commands.repo, "upsert_chat", AsyncMock(return_value=(new_chat, True)))
    set_time = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_time", set_time)

    await commands.ensure_chat(deps, _new_chat_msg("group_za_test"))

    set_time.assert_not_awaited()


async def test_ensure_chat_existing_chat_never_triggers_autodetection(monkeypatch):
    """created=False (чат уже существовал) — автоопределение не трогает уже
    настроенную таймзону, даже если title сейчас резолвится."""
    pool = AsyncMock()
    deps = make_deps(pool=pool)
    existing_chat = _chat_row(id=7, restricted=False)
    monkeypatch.setattr(
        commands.repo, "upsert_chat", AsyncMock(return_value=(existing_chat, False))
    )
    set_time = AsyncMock()
    monkeypatch.setattr(commands.repo, "set_chat_time", set_time)

    await commands.ensure_chat(deps, _new_chat_msg("Сербия"))

    set_time.assert_not_awaited()


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
    monkeypatch.setattr(commands.repo, "upsert_chat", AsyncMock(return_value=(restricted_chat, False)))

    callback = _callback_obj(clicker_id=777, bot_id=42)
    assert await commands.ensure_chat_for_callback(deps, callback) is restricted_chat


async def test_ensure_chat_for_callback_denies_when_clicker_not_whitelisted(monkeypatch):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[{"telegram_user_id": 777}])
    deps = make_deps(pool=pool)
    restricted_chat = SimpleNamespace(id=7, restricted=True, digest_language="ru")
    monkeypatch.setattr(commands.repo, "upsert_chat", AsyncMock(return_value=(restricted_chat, False)))

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
