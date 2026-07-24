import datetime as dt
from dataclasses import dataclass

import asyncpg

_CHAT_COLS = (
    "id, country, telegram_chat_id, message_thread_id, digest_language, digest_time, "
    "timezone, last_digest_date, last_posted_at, last_ping_at, restricted, active, created_at"
)


@dataclass(frozen=True)
class ChatRow:
    id: int
    country: str | None
    telegram_chat_id: int
    message_thread_id: int | None
    digest_language: str
    digest_time: dt.time
    timezone: str
    last_digest_date: dt.date | None
    last_posted_at: dt.datetime | None
    last_ping_at: dt.datetime | None
    restricted: bool
    active: bool
    created_at: dt.datetime


@dataclass(frozen=True)
class CardRow:
    id: int
    bitrix_task_id: int
    chat_id: int
    alias: str | None
    active: bool
    auto_from: int | None = None  # NULL = добавлена вручную; иначе bitrix_task_id родителя (§7 фича 1)


@dataclass(frozen=True)
class CursorRow:
    bitrix_task_id: int
    chat_id: int
    last_history_id: int
    last_message_id: int
    last_comment_id: int


def _chat(r: asyncpg.Record) -> ChatRow:
    return ChatRow(**dict(r))


async def upsert_chat(
    pool, telegram_chat_id, message_thread_id, country, language
) -> tuple[ChatRow, bool]:
    """Возвращает (ChatRow, created) — created=True, только если запись чата заведена
    ИМЕННО этим вызовом (не существовала раньше). Нужно для автоопределения таймзоны
    из названия чата при первом контакте (§5): триггерить его на каждый upsert
    существующего чата было бы и накладно, и неверно семантически (тихо перезаписало
    бы таймзону, заданную партнёром через /time). `xmax = 0` — стандартный приём
    Postgres для различения INSERT/UPDATE внутри одного `... ON CONFLICT DO UPDATE
    RETURNING`: xmax остаётся нулевым только для строки, вставленной в этой же
    команде."""
    row = await pool.fetchrow(
        f"""
        INSERT INTO chats (telegram_chat_id, message_thread_id, country, digest_language)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (telegram_chat_id, message_thread_id)
        DO UPDATE SET active = TRUE
        RETURNING {_CHAT_COLS}, (xmax = 0) AS inserted
        """,
        telegram_chat_id, message_thread_id, country, language,
    )
    data = dict(row)
    created = data.pop("inserted")
    return ChatRow(**data), created


async def list_active_chats(pool) -> list[ChatRow]:
    rows = await pool.fetch(f"SELECT {_CHAT_COLS} FROM chats WHERE active ORDER BY id")
    return [_chat(r) for r in rows]


async def set_chat_language(pool, chat_id: int, code: str) -> None:
    await pool.execute("UPDATE chats SET digest_language = $2 WHERE id = $1", chat_id, code)


async def set_chat_time(pool, chat_id: int, digest_time: dt.time, timezone: str | None) -> None:
    if timezone is None:
        await pool.execute("UPDATE chats SET digest_time = $2 WHERE id = $1", chat_id, digest_time)
    else:
        await pool.execute(
            "UPDATE chats SET digest_time = $2, timezone = $3 WHERE id = $1",
            chat_id, digest_time, timezone,
        )


async def update_chat_telegram_id(pool, chat_id: int, new_telegram_chat_id: int) -> None:
    await pool.execute(
        "UPDATE chats SET telegram_chat_id = $2 WHERE id = $1", chat_id, new_telegram_chat_id
    )


async def deactivate_chat(pool, chat_id: int) -> None:
    await pool.execute("UPDATE chats SET active = FALSE WHERE id = $1", chat_id)


async def add_card(
    pool, chat_id, bitrix_task_id, alias, added_by,
    last_history_id, last_message_id, last_comment_id,
    auto_from: int | None = None,
) -> str:
    async with pool.acquire() as conn, conn.transaction():
        # Try to insert; if conflict, DO NOTHING returns None
        inserted = await conn.fetchrow(
            "INSERT INTO cards (bitrix_task_id, chat_id, alias, added_by, auto_from) "
            "VALUES ($1,$2,$3,$4,$5) "
            "ON CONFLICT (bitrix_task_id, chat_id) DO NOTHING RETURNING id",
            bitrix_task_id, chat_id, alias, added_by, auto_from,
        )
        if inserted:
            # New card was inserted
            await conn.execute(
                "INSERT INTO cursors (bitrix_task_id, chat_id, last_history_id, "
                "last_message_id, last_comment_id) VALUES ($1,$2,$3,$4,$5)",
                bitrix_task_id, chat_id, last_history_id, last_message_id, last_comment_id,
            )
            return "added"

        # Card already exists; check if active and handle reactivation
        existing = await conn.fetchrow(
            "SELECT id, active FROM cards WHERE bitrix_task_id = $1 AND chat_id = $2 FOR UPDATE",
            bitrix_task_id, chat_id,
        )
        if existing["active"]:
            return "exists"

        # Reactivate, reinitialize cursor and re-record auto_from (a manual /add on a
        # previously auto-discovered-but-removed task turns it back into a manual card,
        # since auto_from defaults to None here — discovery itself never reaches this
        # branch, it pre-checks existence via card_exists and skips known task ids).
        await conn.execute(
            "UPDATE cards SET active = TRUE, auto_from = $2 WHERE id = $1",
            existing["id"], auto_from,
        )
        await conn.execute(
            "UPDATE cursors SET last_history_id=$3, last_message_id=$4, last_comment_id=$5, "
            "updated_at=now() WHERE bitrix_task_id=$1 AND chat_id=$2",
            bitrix_task_id, chat_id, last_history_id, last_message_id, last_comment_id,
        )
        return "reactivated"


def _rows_affected(status: str) -> int:
    """asyncpg execute() возвращает строку вида "UPDATE N" — берём N. НЕ status.endswith("1")
    (прежняя реализация): для N=10 это дало бы False при реально снятых строках."""
    return int(status.rsplit(" ", 1)[-1])


async def deactivate_card(pool, chat_id: int, bitrix_task_id: int) -> bool:
    """Снимает карточку `bitrix_task_id` в чате; если она была РУЧНОЙ (или вообще
    что-то совпало), заодно снимает её авто-подхваченных детей (`auto_from = bitrix_task_id`)
    в этом же чате (§7 фича 1). True, если снята хотя бы одна строка."""
    status = await pool.execute(
        "UPDATE cards SET active = FALSE WHERE chat_id = $1 "
        "AND (bitrix_task_id = $2 OR auto_from = $2) AND active",
        chat_id, bitrix_task_id,
    )
    return _rows_affected(status) > 0


async def card_exists(pool, chat_id: int, bitrix_task_id: int) -> bool:
    """Существует ли связка (чат, задача) НЕЗАВИСИМО от active — дискавери подзадач (§7 фича 1)
    не должен реанимировать карточку, которую партнёр снял осознанно вручную."""
    row = await pool.fetchrow(
        "SELECT 1 FROM cards WHERE chat_id = $1 AND bitrix_task_id = $2", chat_id, bitrix_task_id
    )
    return row is not None


async def list_active_cards(pool, chat_id: int) -> list[CardRow]:
    rows = await pool.fetch(
        "SELECT id, bitrix_task_id, chat_id, alias, active, auto_from FROM cards "
        "WHERE chat_id = $1 AND active ORDER BY id",
        chat_id,
    )
    return [CardRow(**dict(r)) for r in rows]


async def get_cursor(pool, bitrix_task_id: int, chat_id: int) -> CursorRow:
    r = await pool.fetchrow(
        "SELECT bitrix_task_id, chat_id, last_history_id, last_message_id, last_comment_id "
        "FROM cursors WHERE bitrix_task_id = $1 AND chat_id = $2",
        bitrix_task_id, chat_id,
    )
    return CursorRow(**dict(r))


async def advance_cursor(
    pool, bitrix_task_id, chat_id, last_history_id, last_message_id, last_comment_id
) -> None:
    await pool.execute(
        "UPDATE cursors SET last_history_id=$3, last_message_id=$4, last_comment_id=$5, "
        "updated_at=now() WHERE bitrix_task_id=$1 AND chat_id=$2",
        bitrix_task_id, chat_id, last_history_id, last_message_id, last_comment_id,
    )


async def mark_digest_run(pool, chat_id: int, local_date: dt.date) -> None:
    await pool.execute("UPDATE chats SET last_digest_date = $2 WHERE id = $1", chat_id, local_date)


async def mark_posted(pool, chat_id: int) -> None:
    await pool.execute("UPDATE chats SET last_posted_at = now() WHERE id = $1", chat_id)


async def mark_ping(pool, chat_id: int) -> None:
    await pool.execute("UPDATE chats SET last_ping_at = now() WHERE id = $1", chat_id)
