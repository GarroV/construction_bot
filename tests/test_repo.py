import asyncio
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


async def test_add_card_concurrent_race_condition(pool):
    """Simulate 10 concurrent add_card calls for the same (chat_id, task_id) pair.
    Should result in exactly one 'added' and nine 'exists' without exceptions."""
    chat = await repo.upsert_chat(pool, -100, 7, "Кыргызстан", "ru")

    # Simulate 10 concurrent calls
    tasks = [
        repo.add_card(pool, chat.id, 8017, "Бишкек 8", 555, 100, 200)
        for _ in range(10)
    ]
    results = await asyncio.gather(*tasks)

    # Exactly one 'added', rest 'exists'
    assert results.count("added") == 1
    assert results.count("exists") == 9
    assert results.count("reactivated") == 0

    # Verify card exists exactly once
    cards = await repo.list_active_cards(pool, chat.id)
    assert len(cards) == 1
    assert cards[0].bitrix_task_id == 8017


async def test_chat_reactivation_on_upsert(pool):
    """Deactivate a chat, then upsert it again — should become active=True."""
    chat = await repo.upsert_chat(pool, -100, 7, "Кыргызстан", "ru")
    assert chat.active is True

    await repo.deactivate_chat(pool, chat.id)
    chats = await repo.list_active_chats(pool)
    assert len(chats) == 0

    # Upsert same chat again
    reactivated = await repo.upsert_chat(pool, -100, 7, "Кыргызстан", "ru")
    assert reactivated.id == chat.id
    assert reactivated.active is True

    chats = await repo.list_active_chats(pool)
    assert len(chats) == 1


async def test_set_chat_language(pool):
    """Test setting chat language updates correctly."""
    chat = await repo.upsert_chat(pool, -100, 7, "Кыргызстан", "ru")
    assert chat.digest_language == "ru"

    await repo.set_chat_language(pool, chat.id, "en")
    fresh = (await repo.list_active_chats(pool))[0]
    assert fresh.digest_language == "en"


async def test_set_chat_time_without_timezone(pool):
    """Test setting only digest_time when timezone is None."""
    chat = await repo.upsert_chat(pool, -100, 7, "Кыргызстан", "ru")
    original_tz = chat.timezone

    new_time = dt.time(14, 30)
    await repo.set_chat_time(pool, chat.id, new_time, None)
    fresh = (await repo.list_active_chats(pool))[0]
    assert fresh.digest_time == new_time
    assert fresh.timezone == original_tz  # Unchanged


async def test_set_chat_time_with_timezone(pool):
    """Test setting both digest_time and timezone."""
    chat = await repo.upsert_chat(pool, -100, 7, "Кыргызстан", "ru")

    new_time = dt.time(10, 15)
    await repo.set_chat_time(pool, chat.id, new_time, "Europe/Moscow")
    fresh = (await repo.list_active_chats(pool))[0]
    assert fresh.digest_time == new_time
    assert fresh.timezone == "Europe/Moscow"


async def test_update_chat_telegram_id(pool):
    """Test updating chat's telegram_chat_id."""
    chat = await repo.upsert_chat(pool, -100, 7, "Кыргызстан", "ru")
    assert chat.telegram_chat_id == -100

    await repo.update_chat_telegram_id(pool, chat.id, -200)
    fresh = (await repo.list_active_chats(pool))[0]
    assert fresh.telegram_chat_id == -200
