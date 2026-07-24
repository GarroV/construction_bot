from unittest.mock import AsyncMock

from src.bitrix.links import FileLink
from src.bitrix.parse import ChatMessage
from src.digest import collector
from src.repo import CardRow, CursorRow

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
    monkeypatch.setattr(collector.methods, "get_checklist_counts", AsyncMock(return_value=(3, 10)))
    monkeypatch.setattr(collector.links, "resolve_files",
                        AsyncMock(return_value=[FileLink(name="план.pdf", url="https://p/1")]))

    delta = await collector.collect_card_delta(object(), CARD, CUR)

    assert delta.has_changes
    assert delta.task_changes == ["статус: 2 → 5"]
    assert delta.comments[0].author == "Иван"
    assert (delta.new_history_id, delta.new_message_id) == (31, 202)
    assert delta.new_comment_id == CUR.last_comment_id  # ветка с чатом задачи курсор комментов не трогает


async def test_collect_empty_delta_keeps_cursor(monkeypatch):
    monkeypatch.setattr(collector.methods, "fetch_new_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(collector.methods, "get_task",
                        AsyncMock(return_value={"title": "Бишкек 8", "chatId": 42}))
    monkeypatch.setattr(collector.methods, "fetch_new_chat_messages", AsyncMock(return_value=([], {})))
    monkeypatch.setattr(collector.methods, "get_checklist_counts", AsyncMock(return_value=(3, 10)))

    delta = await collector.collect_card_delta(object(), CARD, CUR)

    assert not delta.has_changes
    assert (delta.new_history_id, delta.new_message_id) == (20, 200)  # курсор не прыгает
    assert delta.new_comment_id == CUR.last_comment_id


async def test_collect_card_delta_old_card_uses_comments_and_skips_chat(monkeypatch):
    """§13 fallback: у задачи нет chatId (старая карточка коробочного портала) ->
    комментарии идут через task.commentitem.getlist, файлы — без disk.file.get (§8:
    ACCESS_DENIED на файлах старых карточек), im.dialog.messages.get не вызывается вовсе."""
    bx = object()
    monkeypatch.setattr(collector.methods, "fetch_new_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(collector.methods, "get_task",
                        AsyncMock(return_value={"title": "Старая стройка"}))  # нет chatId
    fetch_comments = AsyncMock(return_value=[
        {"ID": "103", "AUTHOR_NAME": "Пётр", "POST_MESSAGE": "[USER=1]Иван[/USER], привет",
         "ATTACHED_OBJECTS": {"1": {"NAME": "план.pdf",
                                     "DOWNLOAD_URL": "secret", "VIEW_URL": "secret"}}},
    ])
    monkeypatch.setattr(collector.methods, "fetch_new_comments", fetch_comments)
    fetch_chat = AsyncMock()
    monkeypatch.setattr(collector.methods, "fetch_new_chat_messages", fetch_chat)
    monkeypatch.setattr(collector.methods, "get_checklist_counts", AsyncMock(return_value=(1, 2)))
    resolve_files = AsyncMock()
    monkeypatch.setattr(collector.links, "resolve_files", resolve_files)

    cur = CursorRow(bitrix_task_id=8017, chat_id=1, last_history_id=0, last_message_id=0,
                    last_comment_id=100)
    delta = await collector.collect_card_delta(bx, CARD, cur)

    assert delta.comments == [ChatMessage(id=103, author="Пётр", text="Иван, привет", file_ids=[],
                                          file_names=("план.pdf",))]
    assert delta.files == [FileLink(name="план.pdf", url=None)]
    assert delta.new_comment_id == 103
    assert delta.new_message_id == cur.last_message_id  # старая карточка: курсор чата не двигается
    fetch_comments.assert_awaited_once_with(bx, 8017, 100)
    fetch_chat.assert_not_awaited()
    resolve_files.assert_not_awaited()
