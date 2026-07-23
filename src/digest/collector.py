from src.bitrix import links, methods, parse
from src.digest.llm import CardDelta
from src.repo import CardRow, CursorRow


async def collect_card_delta(bx, card: CardRow, cursor: CursorRow) -> CardDelta:
    history = await methods.fetch_new_history(bx, card.bitrix_task_id, cursor.last_history_id)
    task = await methods.get_task(bx, card.bitrix_task_id)
    bitrix_chat_id = task.get("chatId") or (task.get("chat") or {}).get("id")

    if bitrix_chat_id:
        raw_msgs, users = await methods.fetch_new_chat_messages(
            bx, int(bitrix_chat_id), cursor.last_message_id
        )
        comments = parse.parse_chat_messages(raw_msgs, users)
        file_ids = [fid for m in comments for fid in m.file_ids]
        files = await links.resolve_files(bx, file_ids) if file_ids else []
        new_message_id = max((int(m["id"]) for m in raw_msgs), default=cursor.last_message_id)
        new_comment_id = cursor.last_comment_id
    else:
        # Старая карточка (§13 fallback): нет chatId -> im.dialog.messages.get недоступен,
        # комментарии читаем через task.commentitem.getlist, файлы — без disk.file.get
        # (ACCESS_DENIED на файлах старых карточек, см. parse_comment_files).
        raw_comments = await methods.fetch_new_comments(
            bx, card.bitrix_task_id, cursor.last_comment_id
        )
        comments = parse.parse_comments(raw_comments)
        files = parse.parse_comment_files(raw_comments)
        new_message_id = cursor.last_message_id
        new_comment_id = max(
            (int(r["ID"]) for r in raw_comments), default=cursor.last_comment_id
        )

    done, total = await methods.get_checklist_counts(bx, card.bitrix_task_id)

    return CardDelta(
        task_id=card.bitrix_task_id,
        alias=card.alias or str(task.get("title") or f"#{card.bitrix_task_id}"),
        task_changes=parse.parse_history_events(history),
        comments=comments,
        checklist_done=done,
        checklist_total=total,
        files=files,
        new_history_id=max((int(r["id"]) for r in history), default=cursor.last_history_id),
        new_message_id=new_message_id,
        new_comment_id=new_comment_id,
    )
