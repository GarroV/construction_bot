from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.bitrix.links import FileLink
from src.bitrix.methods import ChecklistSummary
from src.bitrix.parse import AttachedFile, ChatMessage
from src.digest import collector
from src.repo import CardRow, CursorRow

BX_BASE = "https://portal.bitrix24.ru/rest/123/abc/"
CARD = CardRow(id=1, bitrix_task_id=8017, chat_id=1, alias="Бишкек 8", active=True)
CUR = CursorRow(bitrix_task_id=8017, chat_id=1, last_history_id=20, last_message_id=200, last_comment_id=55)


async def test_collect_card_delta_assembles_everything(monkeypatch):
    monkeypatch.setattr(collector.methods, "fetch_new_history", AsyncMock(return_value=[
        {"id": "31", "field": "STATUS", "value": {"from": "2", "to": "5"}},
    ]))
    monkeypatch.setattr(collector.methods, "get_task",
                        AsyncMock(return_value={"title": "Бишкек 8", "chatId": 42}))
    monkeypatch.setattr(collector.methods, "fetch_new_chat_messages", AsyncMock(return_value=(
        [{"id": 202, "author_id": 5, "text": "ок", "files": [{"id": 777}]}],
        {"5": {"id": 5, "name": "Иван"}},
    )))
    monkeypatch.setattr(collector.methods, "get_checklist_summary", AsyncMock(return_value=(
        ChecklistSummary(done=3, total=10, has_stages=True,
                          stage_title="02 Store design", stage_done=1, stage_total=5)
    )))
    monkeypatch.setattr(collector.links, "resolve_files",
                        AsyncMock(return_value=[FileLink(name="план.pdf", url="https://p/1")]))

    delta = await collector.collect_card_delta(object(), CARD, CUR)

    assert delta.has_changes
    assert delta.task_changes == ["статус: 2 → 5"]
    assert delta.comments[0].author == "Иван"
    assert (delta.new_history_id, delta.new_message_id) == (31, 202)
    assert delta.new_comment_id == CUR.last_comment_id  # ветка с чатом задачи курсор комментов не трогает
    assert (delta.checklist_done, delta.checklist_total) == (3, 10)
    assert delta.has_stages is True
    assert (delta.stage_title, delta.stage_done, delta.stage_total) == ("02 Store design", 1, 5)
    # §8: пересылка файлов охватывает только ветку старой карточки (extract_comment_files) —
    # у новой карточки (files чата задачи, resolve_files) attachments остаётся пустым.
    assert delta.attachments == ()


async def test_collect_empty_delta_keeps_cursor(monkeypatch):
    monkeypatch.setattr(collector.methods, "fetch_new_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(collector.methods, "get_task",
                        AsyncMock(return_value={"title": "Бишкек 8", "chatId": 42}))
    monkeypatch.setattr(collector.methods, "fetch_new_chat_messages", AsyncMock(return_value=([], {})))
    monkeypatch.setattr(collector.methods, "get_checklist_summary", AsyncMock(return_value=(
        ChecklistSummary(done=3, total=10, has_stages=False, stage_title=None, stage_done=0, stage_total=0)
    )))

    delta = await collector.collect_card_delta(object(), CARD, CUR)

    assert not delta.has_changes
    assert (delta.new_history_id, delta.new_message_id) == (20, 200)  # курсор не прыгает
    assert delta.new_comment_id == CUR.last_comment_id


async def test_collect_card_delta_old_card_uses_comments_and_skips_chat(monkeypatch):
    """§13 fallback: у задачи нет chatId (старая карточка коробочного портала) ->
    комментарии идут через task.commentitem.getlist; файлы — без disk.file.get (§8:
    ACCESS_DENIED на файлах старых карточек), ссылка вместо этого ведёт на комментарий-источник
    (links.comment_url, фича 2); im.dialog.messages.get не вызывается вовсе."""
    bx = SimpleNamespace(webhook_url=BX_BASE, webhook_user_id=123)
    monkeypatch.setattr(collector.methods, "fetch_new_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(collector.methods, "get_task",
                        AsyncMock(return_value={"title": "Старая стройка"}))  # нет chatId
    fetch_comments = AsyncMock(return_value=[
        {"ID": "103", "AUTHOR_NAME": "Пётр", "POST_MESSAGE": "[USER=1]Иван[/USER], привет",
         "ATTACHED_OBJECTS": {"1": {"NAME": "план.pdf", "SIZE": "222",
                                     "DOWNLOAD_URL": "secret", "VIEW_URL": "secret"}}},
    ])
    monkeypatch.setattr(collector.methods, "fetch_new_comments", fetch_comments)
    fetch_chat = AsyncMock()
    monkeypatch.setattr(collector.methods, "fetch_new_chat_messages", fetch_chat)
    monkeypatch.setattr(collector.methods, "get_checklist_summary", AsyncMock(return_value=(
        ChecklistSummary(done=1, total=2, has_stages=False, stage_title=None, stage_done=0, stage_total=0)
    )))
    resolve_files = AsyncMock()
    monkeypatch.setattr(collector.links, "resolve_files", resolve_files)

    cur = CursorRow(bitrix_task_id=8017, chat_id=1, last_history_id=0, last_message_id=0,
                    last_comment_id=100)
    delta = await collector.collect_card_delta(bx, CARD, cur)

    assert delta.comments == [ChatMessage(id=103, author="Пётр", text="Иван, привет", file_ids=[],
                                          file_names=("план.pdf",))]
    assert delta.files == [FileLink(
        name="план.pdf",
        url="https://portal.bitrix24.ru/company/personal/user/123/tasks/task/view/8017/"
            "?commentId=103#com103",
    )]
    # §8: attachments несёт сырой DOWNLOAD_URL для загрузчика — но НЕ в FileLink.url
    # (тот уходит партнёру в тексте дайджеста, см. проверку ниже).
    assert delta.attachments == (AttachedFile(name="план.pdf", comment_id=103,
                                              download_url="secret", size=222),)
    assert "secret" not in delta.files[0].url
    assert delta.new_comment_id == 103
    assert delta.new_message_id == cur.last_message_id  # старая карточка: курсор чата не двигается
    fetch_comments.assert_awaited_once_with(bx, 8017, 100)
    fetch_chat.assert_not_awaited()
    resolve_files.assert_not_awaited()


# --- collect_card_overview: сводка «текущее состояние» (§5, «Отчёт по запросу») ---


async def test_collect_card_overview_new_chat_takes_latest_k_independent_of_cursor(monkeypatch):
    """Карточка с чатом задачи: последние k сообщений через fetch_latest_chat_messages
    (НЕ fetch_new_chat_messages) — курсор в вызов вообще не передаётся."""
    monkeypatch.setattr(collector.methods, "get_task",
                        AsyncMock(return_value={"title": "Бишкек 8", "chatId": 42}))
    fetch_latest = AsyncMock(return_value=(
        [{"id": 202, "author_id": 5, "text": "ок", "files": [{"id": 777}]}],
        {"5": {"id": 5, "name": "Иван"}},
    ))
    monkeypatch.setattr(collector.methods, "fetch_latest_chat_messages", fetch_latest)
    monkeypatch.setattr(collector.methods, "get_checklist_summary", AsyncMock(return_value=(
        ChecklistSummary(done=3, total=10, has_stages=False, stage_title=None, stage_done=0, stage_total=0)
    )))
    monkeypatch.setattr(collector.links, "resolve_files",
                        AsyncMock(return_value=[FileLink(name="план.pdf", url="https://p/1")]))

    bx = object()
    overview = await collector.collect_card_overview(bx, CARD, CUR, k=5)

    assert overview.has_changes
    assert overview.comments[0].author == "Иван"
    assert overview.task_changes == []  # обзору история изменений не нужна
    assert (overview.checklist_done, overview.checklist_total) == (3, 10)
    assert overview.attachments == ()  # §8: новая карточка вне охвата пересылки файлов
    fetch_latest.assert_awaited_once_with(bx, 42, 5)


async def test_collect_card_overview_keeps_cursor_values_unchanged(monkeypatch):
    """Курсоры НЕ двигаются: new_*_id возвращаются как есть из переданного cursor,
    независимо от того, что нашлось в комментариях (владелец: advance в report-пути
    должен быть безопасным no-op)."""
    monkeypatch.setattr(collector.methods, "get_task",
                        AsyncMock(return_value={"title": "Бишкек 8", "chatId": 42}))
    monkeypatch.setattr(collector.methods, "fetch_latest_chat_messages", AsyncMock(return_value=(
        [{"id": 9999, "author_id": 5, "text": "новое-новое"}], {"5": {"id": 5, "name": "Иван"}},
    )))
    monkeypatch.setattr(collector.methods, "get_checklist_summary", AsyncMock(return_value=(
        ChecklistSummary(done=0, total=0, has_stages=False, stage_title=None, stage_done=0, stage_total=0)
    )))

    overview = await collector.collect_card_overview(object(), CARD, CUR)

    assert overview.new_history_id == CUR.last_history_id
    assert overview.new_message_id == CUR.last_message_id
    assert overview.new_comment_id == CUR.last_comment_id


async def test_collect_card_overview_old_card_uses_latest_comments_and_comment_url(monkeypatch):
    """Старая карточка (нет chatId): fetch_latest_comments, файлы — ссылка на
    комментарий-источник (как у collect_card_delta), im.dialog.messages.get не зовётся."""
    bx = SimpleNamespace(webhook_url=BX_BASE, webhook_user_id=123)
    monkeypatch.setattr(collector.methods, "get_task",
                        AsyncMock(return_value={"title": "Старая стройка"}))  # нет chatId
    fetch_latest_comments = AsyncMock(return_value=[
        {"ID": "103", "AUTHOR_NAME": "Пётр", "POST_MESSAGE": "план готов",
         "ATTACHED_OBJECTS": {"1": {"NAME": "план.pdf", "SIZE": "333",
                                     "DOWNLOAD_URL": "secret", "VIEW_URL": "secret"}}},
    ])
    monkeypatch.setattr(collector.methods, "fetch_latest_comments", fetch_latest_comments)
    fetch_chat = AsyncMock()
    monkeypatch.setattr(collector.methods, "fetch_latest_chat_messages", fetch_chat)
    monkeypatch.setattr(collector.methods, "get_checklist_summary", AsyncMock(return_value=(
        ChecklistSummary(done=1, total=2, has_stages=False, stage_title=None, stage_done=0, stage_total=0)
    )))

    overview = await collector.collect_card_overview(bx, CARD, CUR, k=5)

    assert overview.comments[0].author == "Пётр"
    assert overview.files == [FileLink(
        name="план.pdf",
        url="https://portal.bitrix24.ru/company/personal/user/123/tasks/task/view/8017/"
            "?commentId=103#com103",
    )]
    # §8: attachments (для загрузчика) отделены от FileLink.url (партнёру)
    assert overview.attachments == (AttachedFile(name="план.pdf", comment_id=103,
                                                 download_url="secret", size=333),)
    assert "secret" not in overview.files[0].url
    fetch_latest_comments.assert_awaited_once_with(bx, 8017, 5)
    fetch_chat.assert_not_awaited()


async def test_collect_card_overview_no_comments_has_no_changes(monkeypatch):
    """0 комментариев за всё время -> has_changes False (process_chat решает падать на
    report_empty-текст для этой карточки, а не пытаться строить LLM-сводку из ничего)."""
    monkeypatch.setattr(collector.methods, "get_task",
                        AsyncMock(return_value={"title": "Бишкек 8", "chatId": 42}))
    monkeypatch.setattr(collector.methods, "fetch_latest_chat_messages",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(collector.methods, "get_checklist_summary", AsyncMock(return_value=(
        ChecklistSummary(done=0, total=0, has_stages=False, stage_title=None, stage_done=0, stage_total=0)
    )))

    overview = await collector.collect_card_overview(object(), CARD, CUR)

    assert not overview.has_changes
