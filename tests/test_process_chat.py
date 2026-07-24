"""Ядро оркестрации против реального process_chat/tick — repo/collector/llm замоканы."""
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.bitrix.links import FileLink  # noqa: F401  (для будущих дельт с файлами)
from src.digest import llm, scheduler
from src.digest.llm import CardDelta
from src.i18n import load_locales
from src.repo import CardRow, ChatRow, CursorRow
from src.telegram.send import SendResult

UTC = dt.timezone.utc
LOCALES = load_locales()
PROMPT = llm.load_prompt()

CARD = CardRow(id=1, bitrix_task_id=8017, chat_id=1, alias="Бишкек 8", active=True)
CARD2 = CardRow(id=2, bitrix_task_id=8018, chat_id=1, alias="Бишкек 9", active=True)
CUR = CursorRow(bitrix_task_id=8017, chat_id=1, last_history_id=20, last_message_id=200, last_comment_id=0)
DELTA_WITH_CHANGES = CardDelta(
    task_id=8017, alias="Бишкек 8", task_changes=["статус: 2 → 5"], comments=[],
    checklist_done=3, checklist_total=10, files=[], new_history_id=31, new_message_id=202,
)
DELTA_EMPTY = CardDelta(
    task_id=8017, alias="Бишкек 8", task_changes=[], comments=[],
    checklist_done=0, checklist_total=0, files=[], new_history_id=20, new_message_id=200,
)
DELTA_EMPTY_2 = CardDelta(
    task_id=8018, alias="Бишкек 9", task_changes=[], comments=[],
    checklist_done=0, checklist_total=0, files=[], new_history_id=5, new_message_id=50,
)


def make_chat(**over) -> ChatRow:
    base = dict(
        id=1, country="Кыргызстан", telegram_chat_id=-100, message_thread_id=7,
        digest_language="ru", digest_time=dt.time(9, 0), timezone="UTC",
        last_digest_date=None, last_posted_at=None, last_ping_at=None,
        restricted=False, active=True, created_at=dt.datetime(2026, 7, 1, tzinfo=UTC),
    )
    base.update(over)
    return ChatRow(**base)


def make_deps(send_fn, **settings_over) -> scheduler.Deps:
    settings = SimpleNamespace(
        bitrix_webhook_url="https://portal.example.com/rest/1/token/",
        openai_model="gpt-5-mini",
        weekly_ping_days=7,
        admin_chat_id=None,
    )
    for k, v in settings_over.items():
        setattr(settings, k, v)
    return scheduler.Deps(
        pool=object(),
        bx=SimpleNamespace(webhook_user_id=1),
        bot=object(),
        llm_client=object(),
        locales=LOCALES,
        settings=settings,
        prompt_template=PROMPT,
        send_fn=send_fn,
    )


def patch_repo(monkeypatch, *, cards=None, cursor=CUR):
    mocks = SimpleNamespace(
        list_active_cards=AsyncMock(return_value=cards or []),
        get_cursor=AsyncMock(return_value=cursor),
        mark_digest_run=AsyncMock(),
        mark_posted=AsyncMock(),
        mark_ping=AsyncMock(),
        advance_cursor=AsyncMock(),
        update_chat_telegram_id=AsyncMock(),
        deactivate_chat=AsyncMock(),
        card_exists=AsyncMock(return_value=False),
        add_card=AsyncMock(return_value="added"),
    )
    for name, mock in vars(mocks).items():
        monkeypatch.setattr(scheduler.repo, name, mock)
    # Дискавери подзадач (§7 фича 1) по умолчанию no-op: ни одна из этих тестов discovery не
    # проверяет, а без мока methods.list_subtasks ушёл бы в реальный bx.call и упал бы
    # AttributeError на тестовом bx=SimpleNamespace(...), засоряя errors всех остальных тестов.
    monkeypatch.setattr(scheduler.methods, "list_subtasks", AsyncMock(return_value=[]))
    return mocks


async def test_marks_run_even_when_send_raises(monkeypatch):
    chat = make_chat()
    repo_mocks = patch_repo(monkeypatch, cards=[CARD])
    monkeypatch.setattr(scheduler.collector, "collect_card_delta",
                        AsyncMock(return_value=DELTA_WITH_CHANGES))
    monkeypatch.setattr(scheduler.llm, "summarize", AsyncMock(return_value="ок"))

    send_fn = AsyncMock(side_effect=RuntimeError("boom"))
    deps = make_deps(send_fn)

    # чат создан 2026-07-01: держим прогон в пределах недели, чтобы не задеть ветку пинга
    errors, posted = await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    assert errors  # ошибка попала в сводку, а не улетела исключением
    assert send_fn.await_count == 1
    assert repo_mocks.mark_digest_run.await_count == 1
    repo_mocks.advance_cursor.assert_not_awaited()


async def test_cursor_advances_only_on_ok(monkeypatch):
    chat = make_chat()
    repo_mocks = patch_repo(monkeypatch, cards=[CARD])
    monkeypatch.setattr(scheduler.collector, "collect_card_delta",
                        AsyncMock(return_value=DELTA_WITH_CHANGES))
    monkeypatch.setattr(scheduler.llm, "summarize", AsyncMock(return_value="ок"))
    now = dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC)  # в пределах недели — пинг не мешает

    send_fn = AsyncMock(return_value=SendResult(ok=False))
    deps = make_deps(send_fn)

    errors, posted = await scheduler.process_chat(deps, chat, now)
    assert errors
    repo_mocks.advance_cursor.assert_not_awaited()
    repo_mocks.mark_posted.assert_not_awaited()

    repo_mocks.advance_cursor.reset_mock()
    repo_mocks.mark_posted.reset_mock()
    send_fn.return_value = SendResult(ok=True)

    await scheduler.process_chat(deps, chat, now)
    repo_mocks.advance_cursor.assert_awaited_once_with(deps.pool, 8017, chat.id, 31, 202, 0)
    repo_mocks.mark_posted.assert_awaited_once_with(deps.pool, chat.id)


async def test_forbidden_deactivates_and_marks_run(monkeypatch):
    chat = make_chat()
    repo_mocks = patch_repo(monkeypatch, cards=[CARD])
    monkeypatch.setattr(scheduler.collector, "collect_card_delta",
                        AsyncMock(return_value=DELTA_WITH_CHANGES))
    monkeypatch.setattr(scheduler.llm, "summarize", AsyncMock(return_value="ок"))
    send_fn = AsyncMock(return_value=SendResult(ok=False, forbidden=True))
    deps = make_deps(send_fn)

    errors, posted = await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    repo_mocks.deactivate_chat.assert_awaited_once_with(deps.pool, chat.id)
    assert any("выкинут" in e for e in errors)
    assert repo_mocks.mark_digest_run.await_count == 1


async def test_migrated_updates_chat_id(monkeypatch):
    chat = make_chat()
    repo_mocks = patch_repo(monkeypatch, cards=[CARD])
    monkeypatch.setattr(scheduler.collector, "collect_card_delta",
                        AsyncMock(return_value=DELTA_WITH_CHANGES))
    monkeypatch.setattr(scheduler.llm, "summarize", AsyncMock(return_value="ок"))
    send_fn = AsyncMock(return_value=SendResult(ok=True, migrated_to=-200))
    deps = make_deps(send_fn)

    await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    repo_mocks.update_chat_telegram_id.assert_awaited_once_with(deps.pool, chat.id, -200)


async def test_empty_chat_sends_nothing(monkeypatch):
    chat = make_chat()
    repo_mocks = patch_repo(monkeypatch, cards=[CARD])
    monkeypatch.setattr(scheduler.collector, "collect_card_delta",
                        AsyncMock(return_value=DELTA_EMPTY))
    summarize_mock = AsyncMock(return_value="ок")
    monkeypatch.setattr(scheduler.llm, "summarize", summarize_mock)
    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn)

    # чат создан 2026-07-01, прогон на следующий день — пинг ещё не наступил (§7 п.5)
    await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    send_fn.assert_not_awaited()
    summarize_mock.assert_not_awaited()
    assert repo_mocks.mark_digest_run.await_count == 1
    repo_mocks.mark_posted.assert_not_awaited()


async def test_ping_after_quiet_week(monkeypatch):
    chat = make_chat(last_posted_at=dt.datetime(2026, 7, 10, tzinfo=UTC))
    repo_mocks = patch_repo(monkeypatch, cards=[CARD])
    monkeypatch.setattr(scheduler.collector, "collect_card_delta",
                        AsyncMock(return_value=DELTA_EMPTY))
    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn)

    now = dt.datetime(2026, 7, 21, 4, 0, tzinfo=UTC)  # 11 дней тишины >= weekly_ping_days=7
    await scheduler.process_chat(deps, chat, now)

    send_fn.assert_awaited_once()
    text = send_fn.await_args.args[3]
    assert "2026-07-10" in text
    repo_mocks.mark_ping.assert_awaited_once_with(deps.pool, chat.id)
    repo_mocks.mark_posted.assert_not_awaited()


async def test_report_mark_run_false_skips_mark_digest_run(monkeypatch):
    """/report (§5): курсоры двигаются как обычно, но last_digest_date НЕ трогаем —
    дневной дайджест должен сработать сам по расписанию."""
    chat = make_chat()
    repo_mocks = patch_repo(monkeypatch, cards=[CARD])
    monkeypatch.setattr(scheduler.collector, "collect_card_delta",
                        AsyncMock(return_value=DELTA_WITH_CHANGES))
    monkeypatch.setattr(scheduler.llm, "summarize", AsyncMock(return_value="ок"))
    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn)

    errors, posted = await scheduler.process_chat(
        deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC), mark_run=False
    )

    assert not errors
    assert posted is True
    repo_mocks.mark_digest_run.assert_not_awaited()
    repo_mocks.advance_cursor.assert_awaited_once()  # курсор всё же двигается
    repo_mocks.mark_posted.assert_awaited_once_with(deps.pool, chat.id)


async def test_report_mark_run_false_skips_ping_even_when_due(monkeypatch):
    """/report не должен «съедать» недельный пинг: пинг-ветка целиком пропускается
    при mark_run=False, даже если по предикату is_ping_due он бы сработал."""
    chat = make_chat(last_posted_at=dt.datetime(2026, 7, 10, tzinfo=UTC))
    repo_mocks = patch_repo(monkeypatch, cards=[CARD])
    monkeypatch.setattr(scheduler.collector, "collect_card_delta",
                        AsyncMock(return_value=DELTA_EMPTY))
    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn)

    now = dt.datetime(2026, 7, 21, 4, 0, tzinfo=UTC)  # 11 дней тишины >= weekly_ping_days=7
    errors, posted = await scheduler.process_chat(deps, chat, now, mark_run=False)

    assert not errors
    assert posted is False
    send_fn.assert_not_awaited()  # никакого пинга
    repo_mocks.mark_ping.assert_not_awaited()
    repo_mocks.mark_digest_run.assert_not_awaited()
    repo_mocks.mark_posted.assert_not_awaited()


async def test_bad_prompt_placeholder_falls_back_to_render(monkeypatch):
    """Опечатка в плейсхолдере prompts/digest.txt не должна ронять дайджест целиком
    (§ фикс №5): build_prompt внутри try, KeyError -> fallback-рендер."""
    chat = make_chat()
    repo_mocks = patch_repo(monkeypatch, cards=[CARD])
    monkeypatch.setattr(scheduler.collector, "collect_card_delta",
                        AsyncMock(return_value=DELTA_WITH_CHANGES))
    summarize_mock = AsyncMock(return_value="ок")
    monkeypatch.setattr(scheduler.llm, "summarize", summarize_mock)
    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn)
    deps.prompt_template = "{nonexistent_placeholder}"

    errors, posted = await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    assert errors  # ошибка сборки промпта попала в сводку
    summarize_mock.assert_not_awaited()  # до LLM даже не дошли
    send_fn.assert_awaited_once()  # карточка всё равно ушла — fallback-рендером
    text = send_fn.await_args.args[3]
    assert "без выжимки" in text  # fallback_notice (ru)
    repo_mocks.mark_posted.assert_awaited_once_with(deps.pool, chat.id)


async def test_mixed_chat_single_send_contains_both_blocks(monkeypatch):
    """(д) Смешанный чат (изменения + no_changes) -> ОДНО сообщение чата, оба блока
    склеены пустой строкой внутри него (§7 п.7)."""
    chat = make_chat()
    repo_mocks = patch_repo(monkeypatch, cards=[CARD, CARD2])

    async def fake_collect(bx, card, cursor):
        return DELTA_WITH_CHANGES if card.bitrix_task_id == 8017 else DELTA_EMPTY_2

    monkeypatch.setattr(scheduler.collector, "collect_card_delta", fake_collect)
    monkeypatch.setattr(scheduler.llm, "summarize", AsyncMock(return_value="ок"))
    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn)

    await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    assert send_fn.await_count == 1  # одно сообщение на чат, не на карточку
    text = send_fn.await_args.args[3]
    assert "ок" in text
    assert "изменений нет" in text
    # курсор двигается только у карточки с изменениями — у no-changes курсор не трогаем (как раньше)
    repo_mocks.advance_cursor.assert_awaited_once_with(deps.pool, 8017, chat.id, 31, 202, 0)
    repo_mocks.mark_posted.assert_awaited_once_with(deps.pool, chat.id)


# --- Дайджест чата одним сообщением — сплит по границам блоков только при переполнении ---
# (владелец, §7 п.7-8): granularity сдвига курсора укрупнена с карточки до чанка.


DELTA2_WITH_CHANGES = CardDelta(
    task_id=8018, alias="Бишкек 9", task_changes=["статус: 1 → 2"], comments=[],
    checklist_done=1, checklist_total=5, files=[], new_history_id=15, new_message_id=60,
)


def _patch_two_cards_with_changes(monkeypatch):
    repo_mocks = patch_repo(monkeypatch, cards=[CARD, CARD2])

    async def fake_collect(bx, card, cursor):
        return DELTA_WITH_CHANGES if card.bitrix_task_id == 8017 else DELTA2_WITH_CHANGES

    monkeypatch.setattr(scheduler.collector, "collect_card_delta", fake_collect)
    monkeypatch.setattr(scheduler.llm, "summarize", AsyncMock(return_value="ок"))
    return repo_mocks


async def test_a_two_cards_with_changes_single_send_both_cursors_advance(monkeypatch):
    """(а) Две карточки с изменениями -> ОДИН send, оба advance_cursor после ok."""
    chat = make_chat()
    repo_mocks = _patch_two_cards_with_changes(monkeypatch)
    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn)

    await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    assert send_fn.await_count == 1
    text = send_fn.await_args.args[3]
    assert "Бишкек 8" in text and "Бишкек 9" in text
    assert repo_mocks.advance_cursor.await_count == 2
    repo_mocks.advance_cursor.assert_any_call(deps.pool, 8017, chat.id, 31, 202, 0)
    repo_mocks.advance_cursor.assert_any_call(deps.pool, 8018, chat.id, 15, 60, 0)
    repo_mocks.mark_posted.assert_awaited_once_with(deps.pool, chat.id)


async def test_b_send_not_ok_no_cursor_advances(monkeypatch):
    """(б) send не ok -> ни один курсор не сдвинут."""
    chat = make_chat()
    repo_mocks = _patch_two_cards_with_changes(monkeypatch)
    send_fn = AsyncMock(return_value=SendResult(ok=False))
    deps = make_deps(send_fn)

    errors, posted = await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    assert send_fn.await_count == 1
    repo_mocks.advance_cursor.assert_not_awaited()
    repo_mocks.mark_posted.assert_not_awaited()
    assert posted is False
    assert errors
    # (ревью, п.5) детализация: перечислены task_id ОБЕИХ карточек чанка, не только первой
    assert any("8017" in e and "8018" in e for e in errors)


async def test_c_two_chunks_first_ok_second_not_only_first_cursor_advances(monkeypatch):
    """(в) Два чанка, первый ok второй нет -> курсоры только первого."""
    chat = make_chat()
    repo_mocks = _patch_two_cards_with_changes(monkeypatch)
    monkeypatch.setattr(scheduler.render, "chunk_blocks", lambda texts, limit=4000: [[0], [1]])
    send_fn = AsyncMock(side_effect=[SendResult(ok=True), SendResult(ok=False)])
    deps = make_deps(send_fn)

    errors, posted = await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    assert send_fn.await_count == 2
    repo_mocks.advance_cursor.assert_awaited_once_with(deps.pool, 8017, chat.id, 31, 202, 0)
    assert posted is True  # ≥1 успешный чанк (§7 п.9)
    assert errors  # второй чанк не отправлен — попал в сводку


async def test_d_forbidden_on_first_chunk_stops_remaining_chunks(monkeypatch):
    """(г) forbidden на первом чанке -> второй не отправлен."""
    chat = make_chat()
    repo_mocks = _patch_two_cards_with_changes(monkeypatch)
    monkeypatch.setattr(scheduler.render, "chunk_blocks", lambda texts, limit=4000: [[0], [1]])
    send_fn = AsyncMock(return_value=SendResult(ok=False, forbidden=True))
    deps = make_deps(send_fn)

    errors, posted = await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    assert send_fn.await_count == 1  # второй чанк не отправлен
    repo_mocks.deactivate_chat.assert_awaited_once_with(deps.pool, chat.id)
    repo_mocks.advance_cursor.assert_not_awaited()
    assert posted is False


async def test_render_failure_for_one_card_is_isolated_in_multi_card_chat(monkeypatch):
    """(ревью) Рендер одной карточки падает в многокарточном чате: её блока нет в
    отправленном сообщении, курсор для неё не двигается; остальные карточки едут в том же
    (единственном) сообщении и их курсоры сдвигаются как обычно, ошибка попадает в errors."""
    chat = make_chat()
    repo_mocks = _patch_two_cards_with_changes(monkeypatch)

    real_card_message = scheduler.render.card_message

    def flaky_card_message(delta, summary, url, locales, lang):
        if delta.task_id == 8017:
            raise RuntimeError("boom render")
        return real_card_message(delta, summary, url, locales, lang)

    monkeypatch.setattr(scheduler.render, "card_message", flaky_card_message)
    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn)

    errors, posted = await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    assert send_fn.await_count == 1
    text = send_fn.await_args.args[3]
    assert "Бишкек 9" in text
    assert "Бишкек 8" not in text  # блок упавшей карточки отсутствует в отправленном тексте
    repo_mocks.advance_cursor.assert_awaited_once_with(deps.pool, 8018, chat.id, 15, 60, 0)
    assert posted is True
    assert any("8017" in e for e in errors)


async def test_advance_cursor_failure_for_one_card_does_not_block_others_in_chunk(monkeypatch):
    """(ревью, п.1) Сбой записи курсора одной карточки чанка не должен глушить запись
    курсоров остальных карточек уже доставленного сообщения — иначе их дельта уехала бы
    завтра повторно (дубль в дайджесте), хотя отправка была успешной."""
    chat = make_chat()
    _patch_two_cards_with_changes(monkeypatch)

    async def flaky_advance_cursor(pool, task_id, chat_id, *args):
        if task_id == 8017:
            raise RuntimeError("db boom")
        return None

    advance_cursor_mock = AsyncMock(side_effect=flaky_advance_cursor)
    monkeypatch.setattr(scheduler.repo, "advance_cursor", advance_cursor_mock)
    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn)

    errors, posted = await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    assert send_fn.await_count == 1
    assert advance_cursor_mock.await_count == 2  # обе попытки предприняты, несмотря на сбой первой
    advance_cursor_mock.assert_any_call(deps.pool, 8018, chat.id, 15, 60, 0)
    assert posted is True
    assert any("8017" in e and "курсор" in e for e in errors)


async def test_tick_isolates_chat_errors_and_reports_admin(monkeypatch):
    chat1 = make_chat(id=1, telegram_chat_id=-100)
    chat2 = make_chat(id=2, telegram_chat_id=-200, country="Казахстан")
    monkeypatch.setattr(scheduler.repo, "list_active_chats", AsyncMock(return_value=[chat1, chat2]))

    async def fake_process_chat(deps_arg, chat_arg, now_arg):
        if chat_arg.id == chat1.id:
            raise RuntimeError("boom1")
        return ["ошибка X"], True

    monkeypatch.setattr(scheduler, "process_chat", fake_process_chat)

    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn, admin_chat_id=42)

    now = dt.datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
    await scheduler.tick(deps, now)

    send_fn.assert_awaited_once()
    args = send_fn.await_args.args
    assert args[1] == 42
    assert "ошибк" in args[3].lower()


async def test_tick_isolates_invalid_timezone_and_continues(monkeypatch):
    """Невалидная timezone одного чата не должна глушить весь tick (§ фикс №2)."""
    chat1 = make_chat(id=1, telegram_chat_id=-100, timezone="Invalid/Zone")
    chat2 = make_chat(id=2, telegram_chat_id=-200, country="Казахстан")
    monkeypatch.setattr(scheduler.repo, "list_active_chats", AsyncMock(return_value=[chat1, chat2]))

    process_chat_mock = AsyncMock(return_value=([], False))
    monkeypatch.setattr(scheduler, "process_chat", process_chat_mock)

    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn, admin_chat_id=42)

    now = dt.datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
    await scheduler.tick(deps, now)  # не должен упасть на невалидной tz первого чата

    process_chat_mock.assert_awaited_once()
    assert process_chat_mock.await_args.args[1].id == chat2.id  # второй чат всё же обработан

    send_fn.assert_awaited_once()
    args = send_fn.await_args.args
    assert args[1] == 42
    assert "кыргызстан" in args[3].lower()  # ошибка первого чата (default country) попала в admin-сводку


async def test_admin_summary_escapes_html(monkeypatch):
    """country — произвольный пользовательский текст, str(e) может содержать <>&
    (§ фикс №3): без экранирования Telegram роняет can't parse entities."""
    chat = make_chat()
    monkeypatch.setattr(scheduler.repo, "list_active_chats", AsyncMock(return_value=[chat]))

    async def fake_process_chat(deps_arg, chat_arg, now_arg):
        return ["карточка <тест>: сбой"], True

    monkeypatch.setattr(scheduler, "process_chat", fake_process_chat)

    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn, admin_chat_id=42)

    now = dt.datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
    await scheduler.tick(deps, now)

    text = send_fn.await_args.args[3]
    assert "&lt;тест&gt;" in text
    assert "<тест>" not in text


# --- Авто-подхват подзадач (фича 1, §7): дискавери внутри process_chat до сбора дельты ---


async def test_discovery_adds_new_subtask_card(monkeypatch):
    """Новая подзадача (нет среди карточек чата вообще) -> add_card с alias
    «Родитель / Подзадача» и auto_from=parent.bitrix_task_id."""
    chat = make_chat()
    repo_mocks = patch_repo(monkeypatch, cards=[CARD])  # CARD — ручная (auto_from=None)
    monkeypatch.setattr(scheduler.methods, "list_subtasks", AsyncMock(return_value=[
        {"id": "73689", "title": "Подзадача", "status": "2"},
    ]))
    monkeypatch.setattr(scheduler.methods, "get_latest_history_id", AsyncMock(return_value=10))
    monkeypatch.setattr(scheduler.methods, "get_latest_comment_id", AsyncMock(return_value=0))
    monkeypatch.setattr(scheduler.collector, "collect_card_delta", AsyncMock(return_value=DELTA_EMPTY))
    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn)

    await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    repo_mocks.card_exists.assert_awaited_once_with(deps.pool, chat.id, 73689)
    repo_mocks.add_card.assert_awaited_once_with(
        deps.pool, chat.id, 73689, "Бишкек 8 / Подзадача", None, 10, 0, 0, auto_from=8017,
    )


async def test_discovery_skips_subtask_existing_active_or_inactive(monkeypatch):
    """Подзадача уже среди карточек чата (активная ИЛИ деактивированная) -> add_card НЕ
    вызывается — деактивированную реанимировать нельзя, партнёр мог снять её осознанно."""
    chat = make_chat()
    repo_mocks = patch_repo(monkeypatch, cards=[CARD])
    repo_mocks.card_exists.return_value = True
    monkeypatch.setattr(scheduler.methods, "list_subtasks", AsyncMock(return_value=[
        {"id": "73689", "title": "Подзадача", "status": "2"},
    ]))
    monkeypatch.setattr(scheduler.collector, "collect_card_delta", AsyncMock(return_value=DELTA_EMPTY))
    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn)

    await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    repo_mocks.add_card.assert_not_awaited()


async def test_discovery_error_is_isolated_and_run_continues(monkeypatch):
    """Сбой list_subtasks по одному родителю -> попадает в errors, но прогон чата
    доходит до конца (mark_digest_run всё равно вызван) — как у сбора дельты."""
    chat = make_chat()
    repo_mocks = patch_repo(monkeypatch, cards=[CARD])
    monkeypatch.setattr(scheduler.methods, "list_subtasks",
                        AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(scheduler.collector, "collect_card_delta", AsyncMock(return_value=DELTA_EMPTY))
    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn)

    errors, posted = await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    assert errors
    assert any("подзадач" in e for e in errors)
    repo_mocks.mark_digest_run.assert_awaited_once()
    repo_mocks.add_card.assert_not_awaited()


async def test_discovery_skips_auto_cards_as_roots():
    """Один уровень вложенности: карточка САМА авто-подхваченная (auto_from не None) не
    участвует в дискавери как родитель — иначе подзадачи подзадач дискаверились бы рекурсивно."""
    auto_card = CardRow(
        id=3, bitrix_task_id=73689, chat_id=1, alias="Бишкек 8 / Подзадача",
        active=True, auto_from=8017,
    )
    errors = await scheduler._discover_subtasks(
        make_deps(AsyncMock()), make_chat(), [auto_card]
    )
    assert errors == []


async def test_discovery_calls_list_subtasks_only_for_manual_cards(monkeypatch):
    chat = make_chat()
    auto_card = CardRow(
        id=3, bitrix_task_id=73689, chat_id=1, alias="Бишкек 8 / Подзадача",
        active=True, auto_from=8017,
    )
    repo_mocks = patch_repo(monkeypatch, cards=[auto_card])
    list_subtasks_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(scheduler.methods, "list_subtasks", list_subtasks_mock)
    monkeypatch.setattr(scheduler.collector, "collect_card_delta", AsyncMock(return_value=DELTA_EMPTY))
    send_fn = AsyncMock(return_value=SendResult(ok=True))
    deps = make_deps(send_fn)

    await scheduler.process_chat(deps, chat, dt.datetime(2026, 7, 2, 10, 0, tzinfo=UTC))

    list_subtasks_mock.assert_not_awaited()
    repo_mocks.add_card.assert_not_awaited()
