import os
import pytest
from src.db import create_pool, apply_migrations

pytestmark = pytest.mark.db
DSN = os.environ.get("TEST_POSTGRES_DSN")


@pytest.fixture
async def pool():
    if not DSN:
        pytest.skip("TEST_POSTGRES_DSN не задан")
    p = await create_pool(DSN)
    yield p
    await p.close()


async def test_migrations_apply_and_are_idempotent(pool):
    async with pool.acquire() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")

    applied_first = await apply_migrations(pool)
    applied_second = await apply_migrations(pool)

    assert applied_first == ["0001_init.sql", "0002_last_comment_id.sql", "0003_auto_from.sql"]
    assert applied_second == []  # повторный прогон ничего не применяет
    tables = {
        r["tablename"]
        for r in await pool.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    assert {"chats", "cards", "cursors", "chat_admins", "schema_migrations"} <= tables


async def test_chats_unique_treats_null_thread_as_equal(pool):
    """§12: UNIQUE NULLS NOT DISTINCT — два ряда с NULL thread_id не вставятся."""
    await apply_migrations(pool)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM chats WHERE telegram_chat_id = -1")
        await c.execute(
            "INSERT INTO chats (telegram_chat_id, message_thread_id, digest_language) "
            "VALUES (-1, NULL, 'ru')"
        )
        with pytest.raises(Exception):
            await c.execute(
                "INSERT INTO chats (telegram_chat_id, message_thread_id, digest_language) "
                "VALUES (-1, NULL, 'ru')"
            )
