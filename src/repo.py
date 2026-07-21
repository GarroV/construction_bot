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


@dataclass(frozen=True)
class CursorRow:
    bitrix_task_id: int
    chat_id: int
    last_history_id: int
    last_message_id: int


def _chat(r: asyncpg.Record) -> ChatRow:
    return ChatRow(**dict(r))


async def upsert_chat(pool, telegram_chat_id, message_thread_id, country, language) -> ChatRow:
    row = await pool.fetchrow(
        f"""
        INSERT INTO chats (telegram_chat_id, message_thread_id, country, digest_language)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (telegram_chat_id, message_thread_id)
        DO UPDATE SET active = TRUE
        RETURNING {_CHAT_COLS}
        """,
        telegram_chat_id, message_thread_id, country, language,
    )
    return _chat(row)


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
    pool, chat_id, bitrix_task_id, alias, added_by, last_history_id, last_message_id
) -> str:
    async with pool.acquire() as conn, conn.transaction():
        # Try to insert; if conflict, DO NOTHING returns None
        inserted = await conn.fetchrow(
            "INSERT INTO cards (bitrix_task_id, chat_id, alias, added_by) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (bitrix_task_id, chat_id) DO NOTHING RETURNING id",
            bitrix_task_id, chat_id, alias, added_by,
        )
        if inserted:
            # New card was inserted
            await conn.execute(
                "INSERT INTO cursors (bitrix_task_id, chat_id, last_history_id, last_message_id) "
                "VALUES ($1,$2,$3,$4)",
                bitrix_task_id, chat_id, last_history_id, last_message_id,
            )
            return "added"

        # Card already exists; check if active and handle reactivation
        existing = await conn.fetchrow(
            "SELECT id, active FROM cards WHERE bitrix_task_id = $1 AND chat_id = $2 FOR UPDATE",
            bitrix_task_id, chat_id,
        )
        if existing["active"]:
            return "exists"

        # Reactivate and reinitialize cursor
        await conn.execute("UPDATE cards SET active = TRUE WHERE id = $1", existing["id"])
        await conn.execute(
            "UPDATE cursors SET last_history_id=$3, last_message_id=$4, updated_at=now() "
            "WHERE bitrix_task_id=$1 AND chat_id=$2",
            bitrix_task_id, chat_id, last_history_id, last_message_id,
        )
        return "reactivated"


async def deactivate_card(pool, chat_id: int, bitrix_task_id: int) -> bool:
    status = await pool.execute(
        "UPDATE cards SET active = FALSE WHERE chat_id = $1 AND bitrix_task_id = $2 AND active",
        chat_id, bitrix_task_id,
    )
    return status.endswith("1")


async def list_active_cards(pool, chat_id: int) -> list[CardRow]:
    rows = await pool.fetch(
        "SELECT id, bitrix_task_id, chat_id, alias, active FROM cards "
        "WHERE chat_id = $1 AND active ORDER BY id",
        chat_id,
    )
    return [CardRow(**dict(r)) for r in rows]


async def get_cursor(pool, bitrix_task_id: int, chat_id: int) -> CursorRow:
    r = await pool.fetchrow(
        "SELECT bitrix_task_id, chat_id, last_history_id, last_message_id FROM cursors "
        "WHERE bitrix_task_id = $1 AND chat_id = $2",
        bitrix_task_id, chat_id,
    )
    return CursorRow(**dict(r))


async def advance_cursor(pool, bitrix_task_id, chat_id, last_history_id, last_message_id) -> None:
    await pool.execute(
        "UPDATE cursors SET last_history_id=$3, last_message_id=$4, updated_at=now() "
        "WHERE bitrix_task_id=$1 AND chat_id=$2",
        bitrix_task_id, chat_id, last_history_id, last_message_id,
    )


async def mark_digest_run(pool, chat_id: int, local_date: dt.date) -> None:
    await pool.execute("UPDATE chats SET last_digest_date = $2 WHERE id = $1", chat_id, local_date)


async def mark_posted(pool, chat_id: int) -> None:
    await pool.execute("UPDATE chats SET last_posted_at = now() WHERE id = $1", chat_id)


async def mark_ping(pool, chat_id: int) -> None:
    await pool.execute("UPDATE chats SET last_ping_at = now() WHERE id = $1", chat_id)
