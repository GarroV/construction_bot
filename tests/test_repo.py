import datetime as dt
import os
import pytest
from src.db import create_pool, apply_migrations
from src import repo

pytestmark = pytest.mark.db
DSN = os.environ.get("TEST_POSTGRES_DSN")


@pytest.fixture
async def pool():
    if not DSN:
        pytest.skip("TEST_POSTGRES_DSN не задан")
    p = await create_pool(DSN)
    async with p.acquire() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await apply_migrations(p)
    yield p
    await p.close()


async def test_upsert_chat_is_idempotent(pool):
    a = await repo.upsert_chat(pool, -100, 7, "Кыргызстан", "ru")
    b = await repo.upsert_chat(pool, -100, 7, "Кыргызстан", "ru")
    assert a.id == b.id
    assert a.digest_language == "ru"


async def test_add_card_lifecycle(pool):
    chat = await repo.upsert_chat(pool, -100, 7, "Кыргызстан", "ru")

    r1 = await repo.add_card(pool, chat.id, 8017, "Бишкек 8", 555, 100, 200)
    r2 = await repo.add_card(pool, chat.id, 8017, "Бишкек 8", 555, 100, 200)
    assert (r1, r2) == ("added", "exists")

    cur = await repo.get_cursor(pool, 8017, chat.id)
    assert (cur.last_history_id, cur.last_message_id) == (100, 200)

    assert await repo.deactivate_card(pool, chat.id, 8017) is True
    assert await repo.deactivate_card(pool, chat.id, 999) is False
    assert await repo.list_active_cards(pool, chat.id) == []

    # повторный /add реактивирует и ПЕРЕинициализирует курсор (§5)
    r3 = await repo.add_card(pool, chat.id, 8017, "Бишкек 8", 555, 150, 250)
    assert r3 == "reactivated"
    cur = await repo.get_cursor(pool, 8017, chat.id)
    assert (cur.last_history_id, cur.last_message_id) == (150, 250)


async def test_cursor_advance_and_marks(pool):
    chat = await repo.upsert_chat(pool, -100, 7, "Кыргызстан", "ru")
    await repo.add_card(pool, chat.id, 8017, "Бишкек 8", 555, 0, 0)

    await repo.advance_cursor(pool, 8017, chat.id, 111, 222)
    cur = await repo.get_cursor(pool, 8017, chat.id)
    assert (cur.last_history_id, cur.last_message_id) == (111, 222)

    await repo.mark_digest_run(pool, chat.id, dt.date(2026, 7, 21))
    await repo.mark_posted(pool, chat.id)
    await repo.mark_ping(pool, chat.id)
    fresh = (await repo.list_active_chats(pool))[0]
    assert fresh.last_digest_date == dt.date(2026, 7, 21)
    assert fresh.last_posted_at is not None
    assert fresh.last_ping_at is not None
