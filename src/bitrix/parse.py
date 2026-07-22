from dataclasses import dataclass

_FIELD_LABELS = {
    "STATUS": "статус",
    "DEADLINE": "дедлайн",
    "TITLE": "название",
    "RESPONSIBLE_ID": "ответственный",
}
_CHECKLIST_LABELS = {
    "CHECKLIST_ITEM_CREATE": "[+]",
    "CHECKLIST_ITEM_CHECK": "[✓]",
    "CHECKLIST_ITEM_UNCHECK": "[ ]",
    "CHECKLIST_ITEM_RENAME": "[~]",
    "CHECKLIST_ITEM_REMOVE": "[-]",
}


@dataclass(frozen=True)
class ChatMessage:
    id: int
    author: str
    text: str
    file_ids: list[int]


def parse_history_events(records: list[dict]) -> list[str]:
    lines: list[str] = []
    for rec in records:
        field = rec.get("field") or ""
        value = rec.get("value") or {}
        if field == "COMMENT":
            continue  # текст комментария берём из чата задачи (§7 п.2)
        if field in _CHECKLIST_LABELS:
            item = value.get("to") or value.get("from") or ""
            lines.append(f"чек-лист: {_CHECKLIST_LABELS[field]} {item}")
        elif field in _FIELD_LABELS:
            lines.append(f"{_FIELD_LABELS[field]}: {value.get('from')} → {value.get('to')}")
        elif field:
            lines.append(f"{field}: {value.get('from')} → {value.get('to')}")
    return lines


def parse_chat_messages(messages: list[dict], users: dict) -> list[ChatMessage]:
    by_id = _users_by_id(users)
    out = []
    for m in messages:
        author_id = int(m.get("author_id") or 0)
        if author_id == 0:  # системное сообщение (§7 п.2; признак сверен со смоуком)
            continue
        out.append(ChatMessage(
            id=int(m["id"]),
            author=by_id.get(author_id, f"user {author_id}"),
            text=str(m.get("text") or ""),
            file_ids=[int(f["id"]) for f in (m.get("files") or [])],
        ))
    return sorted(out, key=lambda m: m.id)


def _users_by_id(users) -> dict[int, str]:
    items = users.values() if isinstance(users, dict) else (users or [])
    return {int(u["id"]): str(u.get("name") or "") for u in items}
