"""Смоук Фазы 0 (§13): python -m scripts.smoke_bitrix <task_id>
Проверяет (а)-(д) и сохраняет фикстуры в tests/fixtures/live/."""
import asyncio
import json
import sys
from pathlib import Path

import httpx

from src.bitrix.client import BitrixClient
from src.config import load_settings

OUT = Path("tests/fixtures/live")

_SECRET_KEYS = {"DOWNLOAD_URL", "urlDownload", "url_download", "downloadUrl"}


def _redact(value):
    """Recursively redact sensitive keys containing webhook tokens."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k in _SECRET_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def dump(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(_redact(data), ensure_ascii=False, indent=2))
    print(f"  фикстура: {OUT / name}.json")


async def main(task_id: int) -> None:
    s = load_settings()
    async with httpx.AsyncClient(timeout=30) as http:
        bx = BitrixClient(s.bitrix_webhook_url, http)

        task = (await bx.call("tasks.task.get", {"taskId": task_id, "select": ["ID", "TITLE", "CHAT_ID"]}))["task"]
        print(f"Задача: {task.get('title')!r}, chat_id={task.get('chatId')}")

        history = await bx.call("tasks.task.history.list", {"taskId": task_id})
        dump("history", history)
        types = {rec.get("field") for rec in history.get("list", [])}
        print(f"(б) типы событий в history: {sorted(t for t in types if t)}")
        print(f"    COMMENT в history: {'ДА' if 'COMMENT' in types else 'НЕТ'}")

        chat_id = task.get("chatId")
        if chat_id:
            msgs = await bx.call(
                "im.dialog.messages.get", {"DIALOG_ID": f"chat{chat_id}", "LIMIT": 20}
            )
            dump("chat_messages", msgs)
            print(f"(а) сообщений чата задачи получено: {len(msgs.get('messages', []))}")
            print("(д) образцы author_id (0 = системное?):",
                  sorted({m.get('author_id') for m in msgs.get('messages', [])}))
            files = [f for m in msgs.get("messages", []) for f in (m.get("files") or [])]
            if files:
                fid = files[0].get("id")
                file_info = await bx.call("disk.file.get", {"id": fid})
                dump("file", file_info)
                print(f"(в) disk.file.get по файлу из чата: DETAIL_URL={'ЕСТЬ' if file_info.get('DETAIL_URL') else 'НЕТ'}")
            else:
                print("(в) в последних сообщениях нет файлов — приложи файл в карточку и повтори")
        else:
            print("(а) у задачи НЕТ chat_id — старая карточка? см. §13 fallback")

        checklist = await bx.call("task.checklistitem.getlist", {"taskId": task_id})
        dump("checklist", checklist)
        print(f"чек-лист: {len(checklist)} пунктов")

        host = s.bitrix_webhook_url.split("/rest/")[0]
        url = f"{host}/company/personal/user/{bx.webhook_user_id}/tasks/task/view/{task_id}/"
        print(f"(г) проверь кликабельность руками под партнёрским аккаунтом: {url}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
