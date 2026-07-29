from src.bitrix import links, methods, parse
from src.digest.llm import CardDelta
from src.repo import CardRow, CursorRow

_OVERVIEW_COMMENTS = 5  # последние k комментариев для сводки «текущее состояние» (§5)


async def collect_card_delta(bx, card: CardRow, cursor: CursorRow) -> CardDelta:
    history = await methods.fetch_new_history(bx, card.bitrix_task_id, cursor.last_history_id)
    task = await methods.get_task(bx, card.bitrix_task_id)
    bitrix_chat_id = task.get("chatId") or (task.get("chat") or {}).get("id")

    attachments: tuple = ()
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
        # комментарии читаем через task.commentitem.getlist. Файлы — без disk.file.get
        # (ACCESS_DENIED на файлах старых карточек, см. extract_comment_files); вместо прямой
        # ссылки на файл строим ссылку на комментарий-источник (§8 фича 2, links.comment_url).
        # attachments (§8, пересылка): сырые DOWNLOAD_URL/SIZE — ТОЛЬКО для загрузчика,
        # в FileLink.url (партнёру) идёт исключительно ссылка на комментарий.
        raw_comments = await methods.fetch_new_comments(
            bx, card.bitrix_task_id, cursor.last_comment_id
        )
        comments = parse.parse_comments(raw_comments)
        attached = parse.extract_comment_files(raw_comments)
        files = [
            links.FileLink(
                name=a.name,
                url=links.comment_url(bx.webhook_url, bx.webhook_user_id,
                                      card.bitrix_task_id, a.comment_id),
            )
            for a in attached
        ]
        attachments = tuple(attached)
        new_message_id = cursor.last_message_id
        new_comment_id = max(
            (int(r["ID"]) for r in raw_comments), default=cursor.last_comment_id
        )

    summary = await methods.get_checklist_summary(bx, card.bitrix_task_id)

    return CardDelta(
        task_id=card.bitrix_task_id,
        alias=card.alias or str(task.get("title") or f"#{card.bitrix_task_id}"),
        task_changes=parse.parse_history_events(history),
        comments=comments,
        checklist_done=summary.done,
        checklist_total=summary.total,
        files=files,
        new_history_id=max((int(r["id"]) for r in history), default=cursor.last_history_id),
        new_message_id=new_message_id,
        new_comment_id=new_comment_id,
        stage_title=summary.stage_title,
        stage_done=summary.stage_done,
        stage_total=summary.stage_total,
        has_stages=summary.has_stages,
        attachments=attachments,
    )


async def collect_card_overview(bx, card: CardRow, cursor: CursorRow, k: int = _OVERVIEW_COMMENTS) -> CardDelta:
    """Сводка ТЕКУЩЕГО состояния карточки по явному запросу (§5, «Отчёт по запросу
    всегда с содержимым» — владелец: «если человек нажал что нужен дайджест — значит
    надо дайджест»). В отличие от collect_card_delta НЕ режет по курсору — последние
    `k` комментариев берутся независимо от того, что уже видел чат, чтобы напомнить
    состояние стройки, а не только «дельту с прошлого раза» (которой в этом сценарии
    и так нет, иначе collect_card_overview не вызвали бы).

    Курсоры НЕ двигаем: new_*_id возвращаются как есть из переданного `cursor` — вызывающая
    сторона (process_chat) для карточек без изменений всё равно не зовёт advance_cursor
    (has_changes у исходной дельты остаётся False), так что эти поля здесь чисто
    информационные (дают CardDelta ту же форму, что и у обычной дельты — для переиспользования
    render.card_message-подобной вёрстки и LLM-промпта)."""
    task = await methods.get_task(bx, card.bitrix_task_id)
    bitrix_chat_id = task.get("chatId") or (task.get("chat") or {}).get("id")

    attachments: tuple = ()
    if bitrix_chat_id:
        raw_msgs, users = await methods.fetch_latest_chat_messages(bx, int(bitrix_chat_id), k)
        comments = parse.parse_chat_messages(raw_msgs, users)
        file_ids = [fid for m in comments for fid in m.file_ids]
        files = await links.resolve_files(bx, file_ids) if file_ids else []
    else:
        # Старая карточка (§13 fallback) — та же логика источника файлов, что и в
        # collect_card_delta: ссылка на комментарий-источник, не на сам файл; attachments
        # (§8, пересылка) — сырые DOWNLOAD_URL/SIZE только для загрузчика.
        raw_comments = await methods.fetch_latest_comments(bx, card.bitrix_task_id, k)
        comments = parse.parse_comments(raw_comments)
        attached = parse.extract_comment_files(raw_comments)
        files = [
            links.FileLink(
                name=a.name,
                url=links.comment_url(bx.webhook_url, bx.webhook_user_id,
                                      card.bitrix_task_id, a.comment_id),
            )
            for a in attached
        ]
        attachments = tuple(attached)

    summary = await methods.get_checklist_summary(bx, card.bitrix_task_id)

    return CardDelta(
        task_id=card.bitrix_task_id,
        alias=card.alias or str(task.get("title") or f"#{card.bitrix_task_id}"),
        task_changes=[],  # обзору не нужна история изменений — только текущий срез
        comments=comments,
        checklist_done=summary.done,
        checklist_total=summary.total,
        files=files,
        new_history_id=cursor.last_history_id,
        new_message_id=cursor.last_message_id,
        new_comment_id=cursor.last_comment_id,
        stage_title=summary.stage_title,
        stage_done=summary.stage_done,
        stage_total=summary.stage_total,
        has_stages=summary.has_stages,
        attachments=attachments,
    )
