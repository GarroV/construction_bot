from unittest.mock import AsyncMock

from src.bitrix.links import FileLink
from src.digest import collector
from src.repo import CardRow, CursorRow

CARD = CardRow(id=1, bitrix_task_id=8017, chat_id=1, alias="Бишкек 8", active=True)
CUR = CursorRow(bitrix_task_id=8017, chat_id=1, last_history_id=20, last_message_id=200)


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


async def test_collect_empty_delta_keeps_cursor(monkeypatch):
    monkeypatch.setattr(collector.methods, "fetch_new_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(collector.methods, "get_task",
                        AsyncMock(return_value={"title": "Бишкек 8", "chatId": 42}))
    monkeypatch.setattr(collector.methods, "fetch_new_chat_messages", AsyncMock(return_value=([], {})))
    monkeypatch.setattr(collector.methods, "get_checklist_counts", AsyncMock(return_value=(3, 10)))

    delta = await collector.collect_card_delta(object(), CARD, CUR)

    assert not delta.has_changes
    assert (delta.new_history_id, delta.new_message_id) == (20, 200)  # курсор не прыгает
