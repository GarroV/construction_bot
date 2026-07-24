from dataclasses import dataclass

from .client import BitrixClient

_PAGE = 50


@dataclass(frozen=True)
class ChecklistSummary:
    """Сводка по чек-листу задачи. Чек-листы Битрикса иерархичны: корни (PARENT_ID
    отсутствует/None/"0"/0) — этапы, дети — конкретные пункты. Корневые галочки люди не
    тыкают, поэтому статус этапа считаем ТОЛЬКО по его детям (см. docstring get_checklist_summary)."""

    done: int  # выполнено пунктов по ВСЕМ строкам чек-листа (этапы + дети) — как раньше
    total: int
    has_stages: bool  # есть ли дети хоть у одного корня (иерархия вообще есть)
    stage_title: str | None  # первый по SORT_INDEX корень с незакрытыми детьми; None,
    # если has_stages=True и у всех корней дети выполнены («все этапы закрыты»)
    stage_done: int
    stage_total: int


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


def _comment_records(res) -> list[dict]:
    """task.commentitem.getlist на живом коробочном портале отдаёт голый список без
    пагинации (проверено смоуком: 314 шт. на Belgrade-2); на всякий случай терпим и
    обёртку {"list": [...]}, как у tasks.task.history.list."""
    return res if isinstance(res, list) else (res.get("list", []) if isinstance(res, dict) else [])


async def fetch_new_comments(bx: BitrixClient, task_id: int, last_comment_id: int) -> list[dict]:
    """Fallback для старых карточек без chatId (§13): task.commentitem.getlist не
    поддерживает срез по курсору — клиентская резка по int(ID) > last_comment_id,
    сортировка по возрастанию id."""
    res = await bx.call("task.commentitem.getlist", {"taskId": task_id})
    fresh = [r for r in _comment_records(res) if int(r["ID"]) > last_comment_id]
    return sorted(fresh, key=lambda r: int(r["ID"]))


async def get_latest_comment_id(bx: BitrixClient, task_id: int) -> int:
    res = await bx.call("task.commentitem.getlist", {"taskId": task_id})
    return max((int(r["ID"]) for r in _comment_records(res)), default=0)


def _root_key(item: dict) -> str | None:
    """None у корня (PARENT_ID отсутствует/None/"0"/0), иначе строковый ID родителя."""
    parent_id = item.get("PARENT_ID")
    if parent_id is None or str(parent_id) == "0":
        return None
    return str(parent_id)


def _sort_key(item: dict) -> int:
    """SORT_INDEX как int; мусорное/отсутствующее значение -> 0 (не роняем сборку)."""
    try:
        return int(item.get("SORT_INDEX"))
    except (TypeError, ValueError):
        return 0


async def get_checklist_summary(bx: BitrixClient, task_id: int) -> ChecklistSummary:
    """Сводка по чек-листу: общие done/total по всем пунктам (как раньше) плюс —
    системной строкой, без LLM — первый незакрытый этап верхнего уровня. Статус этапа
    определяется ТОЛЬКО по его детям: живой пример (tests/fixtures/live/checklist.json)
    показывает, что владельцы чек-листа отмечают дочерние пункты, а корневую галочку
    этапа — нет, поэтому она не является надёжным сигналом."""
    items = await bx.call("task.checklistitem.getlist", {"taskId": task_id}) or []
    done = sum(1 for i in items if str(i.get("IS_COMPLETE")) == "Y")
    total = len(items)

    roots = [i for i in items if _root_key(i) is None]
    children_by_root: dict[str, list[dict]] = {}
    for i in items:
        parent = _root_key(i)
        if parent is not None:
            children_by_root.setdefault(parent, []).append(i)

    has_stages = any(children_by_root.get(str(r.get("ID"))) for r in roots)

    stage_title: str | None = None
    stage_done = 0
    stage_total = 0
    for root in sorted(roots, key=_sort_key):
        children = children_by_root.get(str(root.get("ID")), [])
        c_done = sum(1 for c in children if str(c.get("IS_COMPLETE")) == "Y")
        if children and c_done < len(children):
            stage_title = str(root.get("TITLE") or "")
            stage_done = c_done
            stage_total = len(children)
            break

    return ChecklistSummary(
        done=done, total=total, has_stages=has_stages,
        stage_title=stage_title, stage_done=stage_done, stage_total=stage_total,
    )


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
