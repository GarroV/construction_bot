import json
from pathlib import Path

import pytest
import respx
import httpx
from src.bitrix.client import BitrixClient
from src.bitrix import methods, parse
from src.bitrix.links import FileLink

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


def test_parse_chat_messages_fills_file_names_from_own_message():
    """Имена вложений сообщения — для инлайн-упоминания «в контексте» в промпте LLM
    (фидбек владельца), НЕ только id для resolve_files."""
    data = json.loads((FIX / "chat_messages.json").read_text())

    msgs = parse.parse_chat_messages(data["messages"], data.get("users", {}))

    assert msgs[0].file_names == ("Нови сад2.png",)
    assert msgs[1].file_names == ()  # сообщение без вложений


@respx.mock
async def test_fetch_new_history_stops_at_cursor():
    page = {"result": {"list": [{"id": "30"}, {"id": "20"}, {"id": "10"}]}}  # desc
    respx.get(BASE + "tasks.task.history.list.json").respond(json=page)
    async with httpx.AsyncClient() as http:
        bx = BitrixClient(BASE, http, min_interval=0)

        fresh = await methods.fetch_new_history(bx, 8017, last_history_id=20)

    assert [r["id"] for r in fresh] == ["30"]


@respx.mock
async def test_fetch_new_chat_messages_uses_first_id():
    route = respx.get(BASE + "im.dialog.messages.get.json")
    route.respond(json={"result": {"messages": [{"id": 201, "author_id": 5, "text": "ok"}],
                                   "users": [{"id": 5, "name": "Иван"}]}})
    async with httpx.AsyncClient() as http:
        bx = BitrixClient(BASE, http, min_interval=0)

        msgs, users = await methods.fetch_new_chat_messages(bx, chat_id=42, last_message_id=200)

    assert [m["id"] for m in msgs] == [201]
    q = route.calls[0].request.url.params
    assert q["DIALOG_ID"] == "chat42" and q["FIRST_ID"] == "200"


# --- Fallback старых карточек (§13): strip_bbcode / parse_comments / parse_comment_files ---


def test_strip_bbcode_user_tag():
    assert parse.strip_bbcode("[USER=935]Суботко Виталий[/USER], привет") == "Суботко Виталий, привет"


def test_strip_bbcode_url_tag():
    assert parse.strip_bbcode("смотри [URL=https://x]тут[/URL]") == "смотри тут"


def test_strip_bbcode_nested_tags():
    text = "[QUOTE]Пётр Петров написал:\n[B]нужно[/B] согласовать[/QUOTE]\nСогласовано."
    assert parse.strip_bbcode(text) == "Пётр Петров написал:\nнужно согласовать\nСогласовано."


def test_strip_bbcode_known_singleton_tag_is_removed():
    """BR — известный самозакрывающийся BB-тег из вайтлиста, без пары."""
    assert parse.strip_bbcode("текст [BR] дальше") == "текст  дальше"


def test_strip_bbcode_empty_text_returns_empty():
    assert parse.strip_bbcode("") == ""


# --- Регрессия ревью (Critical): вайтлист, а не любое [слово] — иначе съедает легитимный текст ---


def test_strip_bbcode_preserves_bracketed_word_not_a_known_tag():
    assert parse.strip_bbcode("[важно] встретиться") == "[важно] встретиться"


def test_strip_bbcode_preserves_footnote_markers():
    assert parse.strip_bbcode("см. [1] и [2]") == "см. [1] и [2]"


def test_strip_bbcode_preserves_todo_marker():
    assert parse.strip_bbcode("[TODO] доделать") == "[TODO] доделать"


def test_strip_bbcode_removes_disk_file_tag_with_space_attribute():
    """[DISK FILE ID=n123] — реальная форма Bitrix для встроенных файлов, атрибут через
    пробел, а не «=» сразу после имени тега."""
    assert parse.strip_bbcode("смотри [DISK FILE ID=n123] тут") == "смотри  тут"


def test_parse_comments_sorts_and_cleans_bbcode():
    records = json.loads((FIX / "comments_page.json").read_text())

    msgs = parse.parse_comments(records)

    assert [m.id for m in msgs] == [100, 101, 103]  # сортировка по int(ID) asc, не по порядку в списке
    assert msgs[1].text == "Пётр Петров написал:\nнужно согласовать\nСогласовано."
    assert msgs[2].text == "Иван Иванов, согласен, сделаем к пятнице. Смотри план."
    assert all(m.file_ids == [] for m in msgs)  # файлы отдельно — parse_comment_files


def test_parse_comments_fills_file_names_from_own_attached_objects():
    """file_names — имена вложений СВОЕГО комментария (id=101 несёт план.pdf), а не
    общий пул ATTACHED_OBJECTS всех комментариев (тот собирает parse_comment_files)."""
    records = json.loads((FIX / "comments_page.json").read_text())

    msgs = parse.parse_comments(records)

    assert msgs[0].file_names == () and msgs[2].file_names == ()  # id=100, id=103 без вложений
    assert msgs[1].file_names == ("план.pdf",)  # id=101 — своё ATTACHED_OBJECTS


def test_parse_comment_files_names_only_no_secret_leak():
    records = json.loads((FIX / "comments_page.json").read_text())

    files = parse.parse_comment_files(records)

    assert files == [FileLink(name="план.pdf", url=None)]
    # инвариант §8: DOWNLOAD_URL/VIEW_URL (токен вебхука) не просачиваются наружу ни в имя, ни в url
    dump = "".join(f"{f.name}{f.url}" for f in files)
    assert "SUPER_SECRET_TOKEN" not in dump


@respx.mock
async def test_fetch_new_comments_cuts_by_cursor_and_sorts():
    records = json.loads((FIX / "comments_page.json").read_text())
    respx.get(BASE + "task.commentitem.getlist.json").respond(json={"result": records})
    async with httpx.AsyncClient() as http:
        bx = BitrixClient(BASE, http, min_interval=0)

        fresh = await methods.fetch_new_comments(bx, 8017, last_comment_id=100)

    assert [int(r["ID"]) for r in fresh] == [101, 103]  # id=100 отрезан курсором, сортировка asc


@respx.mock
async def test_get_latest_comment_id_returns_max():
    records = json.loads((FIX / "comments_page.json").read_text())
    respx.get(BASE + "task.commentitem.getlist.json").respond(json={"result": records})
    async with httpx.AsyncClient() as http:
        bx = BitrixClient(BASE, http, min_interval=0)

        latest = await methods.get_latest_comment_id(bx, 8017)

    assert latest == 103


@respx.mock
async def test_get_latest_comment_id_empty_list_returns_zero():
    respx.get(BASE + "task.commentitem.getlist.json").respond(json={"result": []})
    async with httpx.AsyncClient() as http:
        bx = BitrixClient(BASE, http, min_interval=0)

        latest = await methods.get_latest_comment_id(bx, 8017)

    assert latest == 0
