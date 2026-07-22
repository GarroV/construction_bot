import json
from pathlib import Path

import pytest
import respx
import httpx
from src.bitrix.client import BitrixClient
from src.bitrix import methods, parse

FIX = Path("tests/fixtures")
BASE = "https://portal.bitrix24.ru/rest/123/abc/"


def test_parse_history_events_maps_known_fields():
    records = json.loads((FIX / "history_page.json").read_text())["list"]

    lines = parse.parse_history_events(records)

    assert "статус: 2 → 5" in lines
    assert any("Заказать плитку" in ln for ln in lines)   # CHECKLIST_ITEM_CREATE
    assert not any("COMMENT" in ln for ln in lines)        # комментарии не из истории


def test_parse_chat_messages_filters_system_and_sorts():
    data = json.loads((FIX / "chat_messages.json").read_text())

    msgs = parse.parse_chat_messages(data["messages"], data.get("users", {}))

    assert [m.id for m in msgs] == sorted(m.id for m in msgs)
    assert all(m.author != "" for m in msgs)
    assert not any("присоединился" in m.text for m in msgs)  # системное отфильтровано
    assert msgs[0].file_ids == [777]


@respx.mock
async def test_fetch_new_history_stops_at_cursor():
    page = {"result": {"list": [{"id": "30"}, {"id": "20"}, {"id": "10"}]}}  # desc
    respx.post(BASE + "tasks.task.history.list").respond(json=page)
    async with httpx.AsyncClient() as http:
        bx = BitrixClient(BASE, http, min_interval=0)

        fresh = await methods.fetch_new_history(bx, 8017, last_history_id=20)

    assert [r["id"] for r in fresh] == ["30"]


@respx.mock
async def test_fetch_new_chat_messages_uses_first_id():
    route = respx.post(BASE + "im.dialog.messages.get")
    route.respond(json={"result": {"messages": [{"id": 201, "author_id": 5, "text": "ok"}],
                                   "users": [{"id": 5, "name": "Иван"}]}})
    async with httpx.AsyncClient() as http:
        bx = BitrixClient(BASE, http, min_interval=0)

        msgs, users = await methods.fetch_new_chat_messages(bx, chat_id=42, last_message_id=200)

    assert [m["id"] for m in msgs] == [201]
    sent = json.loads(route.calls[0].request.content)
    assert sent["DIALOG_ID"] == "chat42" and sent["FIRST_ID"] == 200
