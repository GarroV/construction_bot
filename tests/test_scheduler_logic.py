"""Чистые предикаты §7 — без БД и сети."""
import datetime as dt
from types import SimpleNamespace

from src.digest.scheduler import is_digest_due, is_ping_due, safe_zoneinfo

UTC = dt.timezone.utc


def chat(**over):
    base = dict(
        id=1, timezone="Asia/Bishkek", digest_time=dt.time(9, 0),
        last_digest_date=None, last_posted_at=None, last_ping_at=None,
        created_at=dt.datetime(2026, 7, 1, tzinfo=UTC),
        country=None, telegram_chat_id=-100,
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


def test_due_with_invalid_timezone_falls_back_to_utc_deterministically():
    """Ревью: битая timezone чата (ручная правка в БД — легальный сценарий §6) не
    должна ронять is_digest_due исключением ZoneInfoNotFoundError — safe_zoneinfo
    деградирует на UTC, результат детерминирован (не «всегда False», не падение)."""
    broken = chat(timezone="Not/AZone")
    now_due = dt.datetime(2026, 7, 21, 10, 0, tzinfo=UTC)  # 10:00 UTC >= digest_time 09:00
    assert is_digest_due(broken, now_due) is True

    now_not_due = dt.datetime(2026, 7, 21, 2, 0, tzinfo=UTC)  # 02:00 UTC < digest_time 09:00
    assert is_digest_due(broken, now_not_due) is False


def test_safe_zoneinfo_falls_back_to_utc_and_does_not_raise():
    assert safe_zoneinfo("Not/AZone").key == "UTC"
    assert safe_zoneinfo("Asia/Bishkek").key == "Asia/Bishkek"  # валидная tz — как есть


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
