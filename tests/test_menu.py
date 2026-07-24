"""Инлайн-меню (menu.py): клавиатуры, reply-роутинг по точному тексту промпта,
callback-диспетчер (права нажавшего, устойчивость к мусорному callback_data).
repo/ensure_chat*/process_chat заменены фейками — без сети и БД (как test_commands.py)."""
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.i18n import load_locales, t
from src.repo import CardRow
from src.telegram import menu

LOCALES = load_locales()
UTC = dt.timezone.utc


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


CHAT = SimpleNamespace(
    id=1, telegram_chat_id=-100, message_thread_id=7, digest_language="ru",
    timezone="UTC", last_posted_at=None, country=None,
)

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


def _callback(data: str, *, clicker_id: int = 555):
    """callback.message — сообщение БОТА (answer/edit_text — то, чем бот отвечает);
    callback.from_user — реально нажавший (используется только внутри
    ensure_chat_for_callback, которая в этих тестах замокана саму по себе — здесь
    просто нужен правдоподобный объект)."""
    message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())
    return SimpleNamespace(data=data, message=message, from_user=SimpleNamespace(id=clicker_id),
                           answer=AsyncMock())


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


# --- Reply-роутинг по ТОЧНОМУ тексту промпта (после ревью: не по первому символу) ---

async def test_route_reply_exact_add_prompt_ru_calls_handle_add_core(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    add_core = AsyncMock(return_value="карточка добавлена")
    monkeypatch.setattr(menu, "handle_add", add_core)

    message = _msg(reply_text=t(LOCALES, "ru", "menu_add_prompt"), text="42103", user_id=555)
    await menu.route_reply(deps, message)

    add_core.assert_awaited_once_with(deps, CHAT, "42103", 555)
    message.reply.assert_awaited_once_with("карточка добавлена")


async def test_route_reply_exact_time_prompt_ru_calls_handle_time_core(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    time_core = AsyncMock(return_value="время обновлено")
    monkeypatch.setattr(menu, "handle_time", time_core)

    message = _msg(reply_text=t(LOCALES, "ru", "menu_time_prompt"), text="09:00 Europe/Belgrade")
    await menu.route_reply(deps, message)

    time_core.assert_awaited_once_with(deps, CHAT, "09:00 Europe/Belgrade")
    message.reply.assert_awaited_once_with("время обновлено")


async def test_route_reply_exact_add_prompt_en_also_matches(monkeypatch):
    """Матч идёт по ВСЕМ языкам локалей, не только по языку текущего чата — партнёр
    мог получить prompt на en, а чат с тех пор переключили на ru."""
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    add_core = AsyncMock(return_value="ok")
    monkeypatch.setattr(menu, "handle_add", add_core)

    message = _msg(reply_text=t(LOCALES, "en", "menu_add_prompt"), text="42103")
    await menu.route_reply(deps, message)

    add_core.assert_awaited_once()


async def test_route_reply_digest_like_text_starting_with_marker_emoji_is_ignored(monkeypatch):
    """§ревью Important: alias карточки из Битрикса может начинаться с того же эмодзи,
    что и menu_add_prompt (➕) — но это НЕ точное совпадение промпта, значит игнор."""
    deps = make_deps()
    ensure_chat_mock = AsyncMock(return_value=CHAT)
    monkeypatch.setattr(menu, "ensure_chat", ensure_chat_mock)
    add_core = AsyncMock()
    monkeypatch.setattr(menu, "handle_add", add_core)

    message = _msg(reply_text="➕ Belgrade-2 (#42103)\nСтатус: в работе", text="42103")
    await menu.route_reply(deps, message)

    ensure_chat_mock.assert_not_awaited()
    add_core.assert_not_awaited()
    message.reply.assert_not_awaited()


async def test_route_reply_unknown_text_is_ignored(monkeypatch):
    deps = make_deps()
    ensure_chat_mock = AsyncMock(return_value=CHAT)
    monkeypatch.setattr(menu, "ensure_chat", ensure_chat_mock)

    message = _msg(reply_text="Обычное сообщение бота, не промпт", text="42103")
    await menu.route_reply(deps, message)

    ensure_chat_mock.assert_not_awaited()
    message.reply.assert_not_awaited()


async def test_route_reply_restricted_chat_denies(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=None))
    add_core = AsyncMock()
    monkeypatch.setattr(menu, "handle_add", add_core)

    message = _msg(reply_text=t(LOCALES, "ru", "menu_add_prompt"), text="42103")
    await menu.route_reply(deps, message)

    add_core.assert_not_awaited()
    message.reply.assert_awaited_once_with(t(LOCALES, "ru", "restricted_denied"))


# --- m:report: НЕ гонит отчёт — сначала выбор карточки (владелец, §5) ---

async def test_dispatch_callback_report_shows_pick_keyboard(monkeypatch):
    """Клика по «📊 Отчёт сейчас» больше не запускает прогон напрямую — сперва выбор
    карточки (report_pick), как у send_rm_keyboard/send_lang_keyboard."""
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=CARDS))
    process_chat_mock = AsyncMock()
    monkeypatch.setattr(menu, "process_chat", process_chat_mock)

    callback = _callback("m:report")
    await menu.dispatch_callback(deps, callback)

    callback.answer.assert_awaited_once_with()  # без текста — прогон ещё не запущен
    process_chat_mock.assert_not_awaited()
    callback.message.answer.assert_awaited_once()
    args, kwargs = callback.message.answer.await_args
    assert args[0] == t(LOCALES, "ru", "report_pick")
    kb = kwargs["reply_markup"]
    data = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert data == {"m:report:all", "m:report:8017", "m:report:8018", "m:cancel"}


# --- m:report:all: posted=False -> report_empty в топик; errors -> report_errors ---

async def test_dispatch_callback_report_all_posted_false_sends_report_empty(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu, "process_chat", AsyncMock(return_value=([], False)))

    callback = _callback("m:report:all")
    await menu.dispatch_callback(deps, callback)

    callback.answer.assert_awaited_once_with(t(LOCALES, "ru", "report_running"))
    deps.send_fn.assert_awaited_once_with(
        deps.bot, CHAT.telegram_chat_id, CHAT.message_thread_id,
        t(LOCALES, "ru", "report_empty_never"),  # CHAT.last_posted_at is None
    )


async def test_dispatch_callback_report_all_posted_true_sends_nothing_extra(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu, "process_chat", AsyncMock(return_value=([], True)))

    callback = _callback("m:report:all")
    await menu.dispatch_callback(deps, callback)

    deps.send_fn.assert_not_awaited()  # дайджест уже ушёл своим путём внутри process_chat


async def test_dispatch_callback_report_all_passes_only_task_id_none(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=CHAT))
    process_chat_mock = AsyncMock(return_value=([], True))
    monkeypatch.setattr(menu, "process_chat", process_chat_mock)

    callback = _callback("m:report:all")
    await menu.dispatch_callback(deps, callback)

    assert process_chat_mock.await_args.kwargs["only_task_id"] is None


# --- m:report:<id>: точечный отчёт по одной карточке ---

async def test_dispatch_callback_report_specific_card_runs_only_task_id(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=CARDS))
    process_chat_mock = AsyncMock(return_value=([], True))
    monkeypatch.setattr(menu, "process_chat", process_chat_mock)

    callback = _callback("m:report:8017")
    await menu.dispatch_callback(deps, callback)

    callback.answer.assert_awaited_once_with(t(LOCALES, "ru", "report_running"))
    process_chat_mock.assert_awaited_once()
    assert process_chat_mock.await_args.kwargs["only_task_id"] == 8017


async def test_dispatch_callback_report_garbage_task_id_is_safe(monkeypatch):
    """Мусорный суффикс (m:report:abc) — безопасно: часики сняты, прогон не запущен."""
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=CHAT))
    process_chat_mock = AsyncMock()
    monkeypatch.setattr(menu, "process_chat", process_chat_mock)

    callback = _callback("m:report:abc")
    await menu.dispatch_callback(deps, callback)  # не должно поднять исключение

    callback.answer.assert_awaited_once_with()
    process_chat_mock.assert_not_awaited()
    deps.send_fn.assert_not_awaited()


async def test_dispatch_callback_report_unknown_task_id_is_safe(monkeypatch):
    """Валидный по формату, но не активный в чате task_id (карточку успели убрать между
    показом клавиатуры и нажатием) — тоже безопасно игнорируется."""
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=CARDS))  # 8017/8018 только
    process_chat_mock = AsyncMock()
    monkeypatch.setattr(menu, "process_chat", process_chat_mock)

    callback = _callback("m:report:9999")
    await menu.dispatch_callback(deps, callback)

    callback.answer.assert_awaited_once_with()
    process_chat_mock.assert_not_awaited()


async def test_run_report_sends_report_errors_when_errors_present(monkeypatch):
    """Minor-фикс ревью: раньше об ошибках знал только лог, партнёр видел тишину."""
    deps = make_deps()
    monkeypatch.setattr(menu, "process_chat", AsyncMock(return_value=(["карточка #1: сбой"], True)))

    await menu.run_report(deps, CHAT)

    deps.send_fn.assert_awaited_once_with(
        deps.bot, CHAT.telegram_chat_id, CHAT.message_thread_id, t(LOCALES, "ru", "report_errors"),
    )


async def test_run_report_no_errors_sends_nothing_when_posted(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "process_chat", AsyncMock(return_value=([], True)))

    await menu.run_report(deps, CHAT)

    deps.send_fn.assert_not_awaited()


async def test_run_report_passes_only_task_id_through_to_process_chat(monkeypatch):
    deps = make_deps()
    process_chat_mock = AsyncMock(return_value=([], True))
    monkeypatch.setattr(menu, "process_chat", process_chat_mock)

    await menu.run_report(deps, CHAT, only_task_id=8017)

    assert process_chat_mock.await_args.kwargs["only_task_id"] == 8017


# --- report_empty: дата последнего дайджеста в таймзоне чата / ветка "никогда" ---

async def test_report_empty_includes_formatted_date_in_chat_timezone(monkeypatch):
    """Владелец: голое «изменений нет» неинформативно — нужна дата в ТАЙМЗОНЕ ЧАТА,
    формат «ДД.ММ ЧЧ:ММ»."""
    chat = SimpleNamespace(
        id=1, telegram_chat_id=-100, message_thread_id=7, digest_language="ru",
        timezone="Europe/Belgrade", country=None,
        last_posted_at=dt.datetime(2026, 7, 20, 12, 30, tzinfo=UTC),  # UTC+2 летом -> 14:30
    )
    deps = make_deps()
    monkeypatch.setattr(menu, "process_chat", AsyncMock(return_value=([], False)))

    await menu.run_report(deps, chat)

    text = deps.send_fn.await_args.args[3]
    assert text == t(LOCALES, "ru", "report_empty", date="20.07 14:30")


async def test_report_empty_never_branch_when_last_posted_at_is_none(monkeypatch):
    """last_posted_at IS NULL (чат только подключили) -> отдельный текст без даты."""
    chat = SimpleNamespace(
        id=1, telegram_chat_id=-100, message_thread_id=7, digest_language="ru",
        timezone="UTC", last_posted_at=None, country=None,
    )
    deps = make_deps()
    monkeypatch.setattr(menu, "process_chat", AsyncMock(return_value=([], False)))

    await menu.run_report(deps, chat)

    text = deps.send_fn.await_args.args[3]
    assert text == t(LOCALES, "ru", "report_empty_never")


async def test_report_empty_falls_back_to_utc_for_invalid_chat_timezone(monkeypatch):
    """Ревью (safe_zoneinfo): битая timezone чата в БД (ручная правка, легальный
    сценарий §6) не должна ронять /report ZoneInfoNotFoundError — вместо этого
    отчёт уходит с датой по UTC."""
    chat = SimpleNamespace(
        id=1, telegram_chat_id=-100, message_thread_id=7, digest_language="ru",
        timezone="Not/AZone", country=None,
        last_posted_at=dt.datetime(2026, 7, 20, 12, 30, tzinfo=UTC),
    )
    deps = make_deps()
    monkeypatch.setattr(menu, "process_chat", AsyncMock(return_value=([], False)))

    await menu.run_report(deps, chat)  # не должен поднять ZoneInfoNotFoundError

    text = deps.send_fn.await_args.args[3]
    assert text == t(LOCALES, "ru", "report_empty", date="20.07 12:30")  # UTC-фолбэк, без сдвига


# --- send_report_pick: клавиатура «По чему отчёт?» ---

async def test_send_report_pick_builds_keyboard_with_all_and_active_cards(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=CARDS))
    target = _msg(reply_text=None)

    await menu.send_report_pick(deps, target, CHAT)

    target.answer.assert_awaited_once()
    args, kwargs = target.answer.await_args
    assert args[0] == t(LOCALES, "ru", "report_pick")
    kb = kwargs["reply_markup"]
    flat = [btn for row in kb.inline_keyboard for btn in row]
    by_data = {btn.callback_data: btn.text for btn in flat}
    assert by_data["m:report:all"] == t(LOCALES, "ru", "btn_report_all")
    assert by_data["m:report:8017"] == "Бишкек 8 (#8017)"
    assert by_data["m:report:8018"] == "Бишкек 9 (#8018)"
    assert by_data["m:cancel"] == t(LOCALES, "ru", "btn_cancel")


async def test_send_report_pick_with_no_active_cards_still_offers_all_and_cancel(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=[]))
    target = _msg(reply_text=None)

    await menu.send_report_pick(deps, target, CHAT)

    kwargs = target.answer.await_args.kwargs
    data = {btn.callback_data for row in kwargs["reply_markup"].inline_keyboard for btn in row}
    assert data == {"m:report:all", "m:cancel"}


# --- /report (команда): без аргументов -> выбор; со ссылкой/ID -> отчёт точечный ---

async def test_cmd_report_no_args_shows_pick(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=CARDS))
    process_chat_mock = AsyncMock()
    monkeypatch.setattr(menu, "process_chat", process_chat_mock)

    message = _msg(reply_text=None, text="/report")
    await menu._cmd_report(deps, message)

    process_chat_mock.assert_not_awaited()
    message.answer.assert_awaited_once()
    args, kwargs = message.answer.await_args
    assert args[0] == t(LOCALES, "ru", "report_pick")


async def test_cmd_report_with_link_runs_direct_report(monkeypatch):
    """/report со ссылкой на карточку — отчёт сразу по ней (§5, _parse_task_ref)."""
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=CARDS))
    process_chat_mock = AsyncMock(return_value=([], True))
    monkeypatch.setattr(menu, "process_chat", process_chat_mock)

    url = "https://b24.dodoteam.ru/company/personal/user/1650/tasks/task/view/8017/"
    message = _msg(reply_text=None, text=f"/report {url}")
    await menu._cmd_report(deps, message)

    message.reply.assert_awaited_once_with(t(LOCALES, "ru", "report_running"))
    process_chat_mock.assert_awaited_once()
    assert process_chat_mock.await_args.kwargs["only_task_id"] == 8017


async def test_cmd_report_with_bare_id_runs_direct_report(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=CARDS))
    process_chat_mock = AsyncMock(return_value=([], True))
    monkeypatch.setattr(menu, "process_chat", process_chat_mock)

    message = _msg(reply_text=None, text="/report 8018")
    await menu._cmd_report(deps, message)

    process_chat_mock.assert_awaited_once()
    assert process_chat_mock.await_args.kwargs["only_task_id"] == 8018


async def test_cmd_report_unknown_card_replies_report_unknown_card(monkeypatch):
    """Аргумент разобрался в ID, но карточка не отслеживается в этом топике —
    remove_not_tracked-подобный ответ (report_unknown_card)."""
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=CARDS))  # 8017/8018 только
    process_chat_mock = AsyncMock()
    monkeypatch.setattr(menu, "process_chat", process_chat_mock)

    message = _msg(reply_text=None, text="/report 9999")
    await menu._cmd_report(deps, message)

    message.reply.assert_awaited_once_with(t(LOCALES, "ru", "report_unknown_card"))
    process_chat_mock.assert_not_awaited()


async def test_cmd_report_restricted_chat_denies(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat", AsyncMock(return_value=None))

    message = _msg(reply_text=None, text="/report")
    await menu._cmd_report(deps, message)

    message.reply.assert_awaited_once_with(t(LOCALES, "ru", "restricted_denied"))


# --- m:rm: кнопки по активным карточкам / пусто / мусорный суффикс ---

async def test_dispatch_callback_rm_builds_keyboard_from_active_cards(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=CARDS))

    callback = _callback("m:rm")
    await menu.dispatch_callback(deps, callback)

    callback.answer.assert_awaited_once()
    callback.message.answer.assert_awaited_once()
    kwargs = callback.message.answer.await_args.kwargs
    kb = kwargs["reply_markup"]
    data = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert data == {"m:rm:8017", "m:rm:8018", "m:cancel"}


async def test_dispatch_callback_rm_empty_sends_empty_text(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=CHAT))
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=[]))

    callback = _callback("m:rm")
    await menu.dispatch_callback(deps, callback)

    callback.message.answer.assert_awaited_once_with(t(LOCALES, "ru", "menu_rm_empty"))


async def test_dispatch_callback_rm_confirm_deactivates_card(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=CHAT))
    deactivate = AsyncMock(return_value=True)
    monkeypatch.setattr(menu.repo, "deactivate_card", deactivate)

    callback = _callback("m:rm:8017")
    await menu.dispatch_callback(deps, callback)

    deactivate.assert_awaited_once_with(deps.pool, CHAT.id, 8017)
    callback.message.edit_text.assert_awaited_once_with(t(LOCALES, "ru", "remove_ok", task_id=8017))


async def test_dispatch_callback_rm_garbage_suffix_answers_and_does_not_crash(monkeypatch):
    """Important-фикс ревью: m:rm:abc раньше кидал ValueError из int() ДО callback.answer()
    — часики висели у пользователя. Теперь answer() первым, парсинг в try/except."""
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=CHAT))
    deactivate = AsyncMock()
    monkeypatch.setattr(menu.repo, "deactivate_card", deactivate)

    callback = _callback("m:rm:abc")
    await menu.dispatch_callback(deps, callback)  # не должно поднять исключение

    callback.answer.assert_awaited_once()
    deactivate.assert_not_awaited()
    callback.message.edit_text.assert_not_awaited()


# --- m:lang:<code>: валидация суффикса против вайтлиста кнопок ---

async def test_dispatch_callback_lang_known_code_sets_language(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=CHAT))
    set_lang = AsyncMock()
    monkeypatch.setattr(menu.repo, "set_chat_language", set_lang)

    callback = _callback("m:lang:en")
    await menu.dispatch_callback(deps, callback)

    set_lang.assert_awaited_once_with(deps.pool, CHAT.id, "en")
    callback.message.answer.assert_awaited_once_with(t(LOCALES, "en", "lang_ok", code="en"))


async def test_dispatch_callback_lang_unknown_code_ignored(monkeypatch):
    """Minor-фикс ревью: суффикс m:lang:<code> принимаем только из вайтлиста кнопок."""
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=CHAT))
    set_lang = AsyncMock()
    monkeypatch.setattr(menu.repo, "set_chat_language", set_lang)

    callback = _callback("m:lang:xx")
    await menu.dispatch_callback(deps, callback)

    callback.answer.assert_awaited_once()
    set_lang.assert_not_awaited()
    callback.message.answer.assert_not_awaited()


# --- send_*-функции: общий код диалоговых флоу для голых команд И кнопок панели ---

async def test_send_add_prompt_sends_prompt_text():
    deps = make_deps()
    target = _msg(reply_text=None)

    await menu.send_add_prompt(deps, target, CHAT)

    target.answer.assert_awaited_once_with(t(LOCALES, "ru", "menu_add_prompt"))


async def test_send_time_prompt_sends_prompt_text():
    deps = make_deps()
    target = _msg(reply_text=None)

    await menu.send_time_prompt(deps, target, CHAT)

    target.answer.assert_awaited_once_with(t(LOCALES, "ru", "menu_time_prompt"))


async def test_send_lang_keyboard_sends_language_picker():
    deps = make_deps()
    target = _msg(reply_text=None)

    await menu.send_lang_keyboard(deps, target, CHAT)

    target.answer.assert_awaited_once()
    args, kwargs = target.answer.await_args
    assert args[0] == t(LOCALES, "ru", "menu_lang_pick")
    kb = kwargs["reply_markup"]
    data = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert "m:lang:ru" in data and "m:lang:en" in data


async def test_send_rm_keyboard_builds_keyboard_from_active_cards(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=CARDS))
    target = _msg(reply_text=None)

    await menu.send_rm_keyboard(deps, target, CHAT)

    kwargs = target.answer.await_args.kwargs
    kb = kwargs["reply_markup"]
    data = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert data == {"m:rm:8017", "m:rm:8018", "m:cancel"}


async def test_send_rm_keyboard_empty_sends_empty_text(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu.repo, "list_active_cards", AsyncMock(return_value=[]))
    target = _msg(reply_text=None)

    await menu.send_rm_keyboard(deps, target, CHAT)

    target.answer.assert_awaited_once_with(t(LOCALES, "ru", "menu_rm_empty"))


# --- Ограниченный чат: callback.answer(show_alert=True) вместо ответа ---

async def test_dispatch_callback_restricted_denies_with_alert(monkeypatch):
    deps = make_deps()
    monkeypatch.setattr(menu, "ensure_chat_for_callback", AsyncMock(return_value=None))

    callback = _callback("m:add")
    await menu.dispatch_callback(deps, callback)

    callback.answer.assert_awaited_once_with(
        t(LOCALES, "ru", "restricted_denied"), show_alert=True
    )
    callback.message.answer.assert_not_awaited()
