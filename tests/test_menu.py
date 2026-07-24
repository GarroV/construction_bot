"""Инлайн-меню (menu.py): клавиатуры, reply-роутинг по маркерам, /report-ветка callback'ов.
repo/ensure_chat/process_chat заменены фейками — без сети и БД (как test_commands.py)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.i18n import load_locales, t
from src.repo import CardRow
from src.telegram import menu

LOCALES = load_locales()


def make_deps(**over):
    deps = SimpleNamespace(
        pool=object(),
        bx=SimpleNamespace(),
        bot=object(),
        locales=LOCALES,
        settings=SimpleNamespace(default_language="ru"),
        send_fn=AsyncMock(),
        bot_username="",
    )
    for k, v in over.items():
        setattr(deps, k, v)
    return deps


CHAT = SimpleNamespace(id=1, telegram_chat_id=-100, message_thread_id=7, digest_language="ru")

CARDS = [
    CardRow(id=1, bitrix_task_id=8017, chat_id=1, alias="Бишкек 8", active=True),
    CardRow(id=2, bitrix_task_id=8018, chat_id=1, alias="Бишкек 9", active=True),
]


def _msg(reply_text: str | None, text: str = "", user_id: int = 555):
    reply_to = None
    if reply_text is not None:
        reply_to = SimpleNamespace(text=reply_text, from_user=SimpleNamespace(id=999))
    return SimpleNamespace(
        text=text,
        reply_to_message=reply_to,
        from_user=SimpleNamespace(id=user_id),
        reply=AsyncMock(),
        answer=AsyncMock(),
        edit_text=AsyncMock(),
    )


# --- Клавиатуры: тексты из локалей, callback_data по контракту m:* ---

def test_build_panel_keyboard_texts_and_callback_data():
    kb = menu.build_panel_keyboard(LOCALES, "ru")
    flat = [btn for row in kb.inline_keyboard for btn in row]
    by_data = {btn.callback_data: btn.text for btn in flat}

    assert by_data["m:add"] == t(LOCALES, "ru", "btn_add")
    assert by_data["m:rm"] == t(LOCALES, "ru", "btn_rm")
    assert by_data["m:report"] == t(LOCALES, "ru", "btn_report")
    assert by_data["m:time"] == t(LOCALES, "ru", "btn_time")
    assert by_data["m:lang"] == t(LOCALES, "ru", "btn_lang")


def test_render_panel_text_lists_cards_or_empty_placeholder():
    with_cards = menu.render_panel_text(LOCALES, "ru", CARDS)
    assert "Бишкек 8" in with_cards and "8017" in with_cards
    assert "Бишкек 9" in with_cards and "8018" in with_cards

    empty = menu.render_panel_text(LOCALES, "ru", [])
    assert t(LOCALES, "ru", "menu_empty_cards") in empty


def test_build_rm_keyboard_lists_active_cards_plus_cancel():
    kb = menu.build_rm_keyboard(LOCALES, "ru", CARDS)
    flat = [btn for row in kb.inline_keyboard for btn in row]
    by_data = {btn.callback_data: btn.text for btn in flat}

    assert by_data["m:rm:8017"] == "Бишкек 8 (#8017)"
    assert by_data["m:rm:8018"] == "Бишкек 9 (#8018)"
    assert by_data["m:cancel"] == t(LOCALES, "ru", "btn_cancel")


# --- Reply-роутинг по маркерам (хрупкий контракт: первый символ prompt-сообщения) ---

async def test_route_reply_add_marker_calls_handle_add_core(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    add_core = AsyncMock(return_value="карточка добавлена")
    monkeypatch.setattr(menu, "handle_add", add_core)

    message = _msg(reply_text=f"{menu._MARK_ADD} Ответь номером карточки", text="42103", user_id=555)
    await menu.route_reply(deps, message)

    add_core.assert_awaited_once_with(deps, CHAT, "42103", 555)
    message.reply.assert_awaited_once_with("карточка добавлена")


async def test_route_reply_time_marker_calls_handle_time_core(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    time_core = AsyncMock(return_value="время обновлено")
    monkeypatch.setattr(menu, "handle_time", time_core)

    message = _msg(reply_text=f"{menu._MARK_TIME} Ответь временем", text="09:00 Europe/Belgrade")
    await menu.route_reply(deps, message)

    time_core.assert_awaited_once_with(deps, CHAT, "09:00 Europe/Belgrade")
    message.reply.assert_awaited_once_with("время обновлено")


async def test_route_reply_unknown_marker_is_ignored(monkeypatch):
    deps = make_deps()
    ensure_chat_mock = AsyncMock(return_value=CHAT)
    monkeypatch.setattr(menu, "ensure_chat", ensure_chat_mock)

    # reply на чужое/обычное сообщение бота (без маркера) — не наш ввод, молчим
    message = _msg(reply_text="Обычное сообщение без маркера", text="42103")
    await menu.route_reply(deps, message)

    ensure_chat_mock.assert_not_awaited()
    message.reply.assert_not_awaited()


async def test_route_reply_restricted_chat_denies(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=None))
    add_core = AsyncMock()
    monkeypatch.setattr(menu, "handle_add", add_core)

    message = _msg(reply_text=f"{menu._MARK_ADD} Ответь номером", text="42103")
    await menu.route_reply(deps, message)

    add_core.assert_not_awaited()
    message.reply.assert_awaited_once_with(t(LOCALES, "ru", "restricted_denied"))


# --- m:report: posted=False -> report_empty в топик ---

async def test_dispatch_callback_report_posted_false_sends_report_empty(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu, "process_chat", AsyncMock(return_value=([], False)))

    message = _msg(reply_text=None)
    callback = SimpleNamespace(data="m:report", message=message, answer=AsyncMock())
    await menu.dispatch_callback(deps, callback)

    callback.answer.assert_awaited_once_with(t(LOCALES, "ru", "report_running"))
    deps.send_fn.assert_awaited_once_with(
        deps.bot, CHAT.telegram_chat_id, CHAT.message_thread_id, t(LOCALES, "ru", "report_empty"),
    )


async def test_dispatch_callback_report_posted_true_sends_nothing_extra(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu, "process_chat", AsyncMock(return_value=([], True)))

    message = _msg(reply_text=None)
    callback = SimpleNamespace(data="m:report", message=message, answer=AsyncMock())
    await menu.dispatch_callback(deps, callback)

    deps.send_fn.assert_not_awaited()  # дайджест уже ушёл своим путём внутри process_chat


# --- m:rm: кнопки по активным карточкам / пусто ---

async def test_dispatch_callback_rm_builds_keyboard_from_active_cards(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=CARDS))

    message = _msg(reply_text=None)
    callback = SimpleNamespace(data="m:rm", message=message, answer=AsyncMock())
    await menu.dispatch_callback(deps, callback)

    callback.answer.assert_awaited_once()
    message.answer.assert_awaited_once()
    kwargs = message.answer.await_args.kwargs
    kb = kwargs["reply_markup"]
    data = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert data == {"m:rm:8017", "m:rm:8018", "m:cancel"}


async def test_dispatch_callback_rm_empty_sends_empty_text(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=[]))

    message = _msg(reply_text=None)
    callback = SimpleNamespace(data="m:rm", message=message, answer=AsyncMock())
    await menu.dispatch_callback(deps, callback)

    message.answer.assert_awaited_once_with(t(LOCALES, "ru", "menu_rm_empty"))


async def test_dispatch_callback_rm_confirm_deactivates_card(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    deactivate = AsyncMock(return_value=True)
    monkeypatch.setattr(menu.repo, "deactivate_card", deactivate)

    message = _msg(reply_text=None)
    callback = SimpleNamespace(data="m:rm:8017", message=message, answer=AsyncMock())
    await menu.dispatch_callback(deps, callback)

    deactivate.assert_awaited_once_with(deps.pool, CHAT.id, 8017)
    message.edit_text.assert_awaited_once_with(t(LOCALES, "ru", "remove_ok", task_id=8017))


# --- Ограниченный чат: callback.answer(show_alert=True) вместо ответа ---

async def test_dispatch_callback_restricted_denies_with_alert(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=None))

    message = _msg(reply_text=None)
    callback = SimpleNamespace(data="m:add", message=message, answer=AsyncMock())
    await menu.dispatch_callback(deps, callback)

    callback.answer.assert_awaited_once_with(
        t(LOCALES, "ru", "restricted_denied"), show_alert=True
    )
    message.answer.assert_not_awaited()
