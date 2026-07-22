from .client import BitrixClient

_PAGE = 50


async def get_task(bx: BitrixClient, task_id: int) -> dict:
    res = await bx.call(
        "tasks.task.get",
        {"taskId": task_id, "select": ["ID", "TITLE", "STATUS", "DEADLINE", "CHAT_ID"]},
    )
    return res["task"]


async def fetch_new_history(bx: BitrixClient, task_id: int, last_history_id: int) -> list[dict]:
    """Страницы desc; клиентская резка: стоп на id <= курсора (§7 п.1)."""
    fresh: list[dict] = []
    start = 0
    while True:
        res = await bx.call("tasks.task.history.list", {"taskId": task_id, "start": start})
        page = res.get("list", []) if isinstance(res, dict) else res
        if not page:
            return fresh
        for rec in page:
            if int(rec["id"]) <= last_history_id:
                return fresh
            fresh.append(rec)
        if len(page) < _PAGE:
            return fresh
        start += _PAGE


async def fetch_new_chat_messages(
    bx: BitrixClient, chat_id: int, last_message_id: int
) -> tuple[list[dict], dict]:
    messages: list[dict] = []
    users: dict = {}
    first_id = last_message_id
    while True:
        res = await bx.call(
            "im.dialog.messages.get",
            {"DIALOG_ID": f"chat{chat_id}", "FIRST_ID": first_id, "LIMIT": _PAGE},
        )
        batch = res.get("messages", [])
        if not batch:
            return messages, users
        messages.extend(batch)
        users = _merge_users(users, res.get("users"))
        first_id = max(int(m["id"]) for m in batch)
        if len(batch) < _PAGE:
            return messages, users


async def get_checklist_counts(bx: BitrixClient, task_id: int) -> tuple[int, int]:
    items = await bx.call("task.checklistitem.getlist", {"taskId": task_id}) or []
    done = sum(1 for i in items if str(i.get("IS_COMPLETE")) == "Y")
    return done, len(items)


async def get_latest_history_id(bx: BitrixClient, task_id: int) -> int:
    res = await bx.call("tasks.task.history.list", {"taskId": task_id})
    page = res.get("list", []) if isinstance(res, dict) else res
    return max((int(r["id"]) for r in page), default=0)


async def get_latest_chat_message_id(bx: BitrixClient, chat_id: int | None) -> int:
    if not chat_id:  # старая карточка без чата задачи (§5): комментарии не собираем
        return 0
    res = await bx.call("im.dialog.messages.get", {"DIALOG_ID": f"chat{chat_id}", "LIMIT": _PAGE})
    return max((int(m["id"]) for m in res.get("messages", [])), default=0)


def _merge_users(acc: dict, users) -> dict:
    if isinstance(users, dict):
        return {**acc, **users}
    for u in users or []:
        acc[str(u["id"])] = u
    return acc
