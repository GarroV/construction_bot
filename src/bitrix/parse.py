import re
from dataclasses import dataclass

from .links import FileLink

# Вайтлист реальных BB-тегов Битрикса (регистронезависимо). КРИТИЧНО: НЕ обобщать до
# произвольного [\w+] — иначе легитимный текст в скобках («[важно]», сноски «[1]», «[TODO]»)
# молча теряется перед LLM (нашлось ревью). Только эти теги подлежат удалению/снятию.
_BB_TAGS = (
    "B", "I", "U", "S", "URL", "IMG", "QUOTE", "CODE", "USER", "TABLE", "TR", "TD", "TH",
    "LIST", "SIZE", "COLOR", "FONT", "VIDEO", "DISK", "SPOILER", "CENTER", "LEFT", "RIGHT",
    "JUSTIFY", "P", "BR", "HR",
)
_BB_TAG_ALT = "|".join(_BB_TAGS)
# Парная BB-тег-пара: [TAG] или [TAG=атрибут] ... [/TAG] (то же имя в закрывающем, регистр
# не важен — задаёт и re.IGNORECASE, и обратные ссылки \1 под тем же флагом). Покрывает
# [USER=id]Имя[/USER], [URL=x]текст[/URL], [B]...[/B], [QUOTE]...[/QUOTE] и т.п. одним правилом.
_BB_PAIR_RE = re.compile(
    rf"\[({_BB_TAG_ALT})(?:[=\s][^\]]*)?\](.*?)\[/\1\]", re.IGNORECASE | re.DOTALL
)
# Одиночные теги вайтлиста без пары (открывающие/осиротевшие закрывающие, включая форму с
# атрибутом через пробел вида [DISK FILE ID=n123]) плюс маркер элемента списка [*] —
# вырезаются целиком. Всё остальное в квадратных скобках — не BB-код, не трогаем.
_BB_SINGLE_RE = re.compile(
    rf"\[/?(?:{_BB_TAG_ALT})(?:[=\s][^\]]*)?\]|\[\*\]", re.IGNORECASE
)

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
    file_names: tuple[str, ...] = ()  # имена вложений своего сообщения/комментария —
    # для упоминания «в контексте» в промпте LLM (📎-список внизу дайджеста собирает delta.files отдельно)


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
        files = m.get("files") or []
        out.append(ChatMessage(
            id=int(m["id"]),
            author=by_id.get(author_id, f"user {author_id}"),
            text=str(m.get("text") or ""),
            file_ids=[int(f["id"]) for f in files],
            file_names=tuple(str(f["name"]) for f in files if f.get("name")),
        ))
    return sorted(out, key=lambda m: m.id)


def _users_by_id(users) -> dict[int, str]:
    items = users.values() if isinstance(users, dict) else (users or [])
    return {int(u["id"]): str(u.get("name") or "") for u in items}


def strip_bbcode(text: str) -> str:
    """Fallback старых карточек (§13): task.commentitem.getlist отдаёт POST_MESSAGE с
    BB-кодами ([USER=id]Имя[/USER], [URL=x]текст[/URL], [B]...[/B], [QUOTE]...[/QUOTE]),
    в LLM должен идти чистый текст. Парные теги снимаются, содержимое остаётся (в т.ч.
    вложенные — цикл до неподвижной точки), одиночные без пары вырезаются целиком."""
    if not text:
        return ""
    result = text
    while True:
        stripped = _BB_PAIR_RE.sub(lambda m: m.group(2), result)
        if stripped == result:
            break
        result = stripped
    return _BB_SINGLE_RE.sub("", result)


def parse_comments(records: list[dict]) -> list[ChatMessage]:
    """task.commentitem.getlist -> ChatMessage (§13 fallback). file_ids сюда не попадают —
    их даёт parse_comment_files (id вложений комментариев — не то же пространство id,
    что file_ids чата задачи); file_names — только имена своих ATTACHED_OBJECTS, для
    упоминания вложений в контексте этого комментария в промпте LLM."""
    out = [
        ChatMessage(
            id=int(r["ID"]),
            author=str(r.get("AUTHOR_NAME") or ""),
            text=strip_bbcode(str(r.get("POST_MESSAGE") or "")),
            file_ids=[],
            file_names=_attached_object_names(r),
        )
        for r in records
    ]
    return sorted(out, key=lambda m: m.id)


def _attached_object_names(record: dict) -> tuple[str, ...]:
    attached = record.get("ATTACHED_OBJECTS") or {}
    items = attached.values() if isinstance(attached, dict) else attached
    return tuple(str(obj.get("NAME")) for obj in items if obj.get("NAME"))


def parse_comment_files(records: list[dict]) -> list[FileLink]:
    """ATTACHED_OBJECTS старых комментариев -> FileLink без ссылки (§8, §13): disk.file.get
    на файлы старых карточек отдаёт ACCESS_DENIED, постоянные ссылки не строим. Инвариант:
    DOWNLOAD_URL/VIEW_URL (несут токен вебхука) сюда не читаются и никогда не попадают наружу."""
    out: list[FileLink] = []
    for r in records:
        attached = r.get("ATTACHED_OBJECTS") or {}
        items = attached.values() if isinstance(attached, dict) else attached
        for obj in items:
            out.append(FileLink(name=str(obj.get("NAME") or ""), url=None))
    return out
