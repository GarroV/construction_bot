"""Чистые предикаты §7 — без БД и сети."""
import datetime as dt
from types import SimpleNamespace

from src.digest.scheduler import is_digest_due, is_ping_due

UTC = dt.timezone.utc


def chat(**over):
    base = dict(
        id=1, timezone="Asia/Bishkek", digest_time=dt.time(9, 0),
        last_digest_date=None, last_posted_at=None, last_ping_at=None,
        created_at=dt.datetime(2026, 7, 1, tzinfo=UTC),
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_due_when_local_time_reached_and_not_run_today():
    # 04:00 UTC = 10:00 Бишкек (UTC+6) — время пришло
    now = dt.datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    assert is_digest_due(chat(), now) is True


def test_not_due_before_local_time():
    now = dt.datetime(2026, 7, 21, 2, 0, tzinfo=UTC)  # 08:00 Бишкек
    assert is_digest_due(chat(), now) is False


def test_not_due_twice_same_local_date():
    now = dt.datetime(2026, 7, 21, 5, 0, tzinfo=UTC)
    assert is_digest_due(chat(last_digest_date=dt.date(2026, 7, 21)), now) is False
    assert is_digest_due(chat(last_digest_date=dt.date(2026, 7, 20)), now) is True


def test_ping_due_only_with_cards_and_after_quiet_week():
    now = dt.datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    quiet = chat(last_posted_at=dt.datetime(2026, 7, 10, tzinfo=UTC))
    assert is_ping_due(quiet, now, 7, has_active_cards=True) is True
    assert is_ping_due(quiet, now, 7, has_active_cards=False) is False

    fresh_ping = chat(last_posted_at=dt.datetime(2026, 7, 10, tzinfo=UTC),
                      last_ping_at=dt.datetime(2026, 7, 19, tzinfo=UTC))
    assert is_ping_due(fresh_ping, now, 7, has_active_cards=True) is False  # пинг был недавно

    new_chat = chat(created_at=dt.datetime(2026, 7, 20, tzinfo=UTC))
    assert is_ping_due(new_chat, now, 7, has_active_cards=True) is False   # чату < 7 дней
