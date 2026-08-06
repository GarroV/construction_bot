"""Чистые предикаты §7 — без БД и сети. Часовой пояс чата — Asia/Bishkek (UTC+6),
поэтому локальные слоты 9/11/…/19 = 03/05/…/13 UTC."""
import datetime as dt
from types import SimpleNamespace

from src.digest.scheduler import is_digest_due, is_ping_due, safe_zoneinfo

UTC = dt.timezone.utc


def chat(**over):
    base = dict(
        id=1, timezone="Asia/Bishkek", digest_time=dt.time(9, 0),
        last_run_at=None, last_digest_date=None, last_posted_at=None, last_ping_at=None,
        created_at=dt.datetime(2026, 7, 1, tzinfo=UTC),
        country=None, telegram_chat_id=-100,
    )
    base.update(over)
    return SimpleNamespace(**base)


# --- is_digest_due: фиксированные локальные слоты 9/11/13/15/17/19 (§7) ---

def test_due_at_first_slot_when_never_ran():
    now = dt.datetime(2026, 7, 21, 3, 0, tzinfo=UTC)  # 09:00 Бишкек — первый слот
    assert is_digest_due(chat(last_run_at=None), now) is True


def test_not_due_before_first_slot():
    now = dt.datetime(2026, 7, 21, 2, 0, tzinfo=UTC)  # 08:00 Бишкек — до 09:00
    assert is_digest_due(chat(last_run_at=None), now) is False


def test_not_due_twice_in_same_slot():
    now = dt.datetime(2026, 7, 21, 3, 30, tzinfo=UTC)  # 09:30 Бишкек, слот 09
    ran = dt.datetime(2026, 7, 21, 3, 5, tzinfo=UTC)   # прогон уже был в 09:05
    assert is_digest_due(chat(last_run_at=ran), now) is False


def test_due_at_next_slot():
    now = dt.datetime(2026, 7, 21, 5, 0, tzinfo=UTC)   # 11:00 Бишкек — слот 11
    ran = dt.datetime(2026, 7, 21, 3, 5, tzinfo=UTC)   # прошлый прогон — в слоте 09
    assert is_digest_due(chat(last_run_at=ran), now) is True


def test_not_due_at_night_after_last_slot():
    now = dt.datetime(2026, 7, 21, 17, 0, tzinfo=UTC)  # 23:00 Бишкек
    ran = dt.datetime(2026, 7, 21, 13, 5, tzinfo=UTC)  # прогон в слоте 19 (19:05)
    assert is_digest_due(chat(last_run_at=ran), now) is False


def test_due_next_morning_after_night_quiet():
    now = dt.datetime(2026, 7, 22, 3, 0, tzinfo=UTC)   # 09:00 Бишкек следующего дня
    ran = dt.datetime(2026, 7, 21, 13, 5, tzinfo=UTC)  # последний прогон — вчера в 19:05
    assert is_digest_due(chat(last_run_at=ran), now) is True


def test_invalid_timezone_falls_back_to_utc_slots():
    """Битая tz (ручная правка в БД, §6) не роняет is_digest_due — safe_zoneinfo → UTC,
    слоты считаются по UTC."""
    broken = chat(timezone="Not/AZone", last_run_at=None)
    assert is_digest_due(broken, dt.datetime(2026, 7, 21, 9, 0, tzinfo=UTC)) is True   # 09:00 UTC
    assert is_digest_due(broken, dt.datetime(2026, 7, 21, 8, 0, tzinfo=UTC)) is False  # 08:00 UTC


def test_safe_zoneinfo_falls_back_to_utc_and_does_not_raise():
    assert safe_zoneinfo("Not/AZone").key == "UTC"
    assert safe_zoneinfo("Asia/Bishkek").key == "Asia/Bishkek"


def test_ping_due_only_with_cards_and_after_quiet_week():
    now = dt.datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    quiet = chat(last_posted_at=dt.datetime(2026, 7, 10, tzinfo=UTC))
    assert is_ping_due(quiet, now, 7, has_active_cards=True) is True
    assert is_ping_due(quiet, now, 7, has_active_cards=False) is False

    fresh_ping = chat(last_posted_at=dt.datetime(2026, 7, 10, tzinfo=UTC),
                      last_ping_at=dt.datetime(2026, 7, 19, tzinfo=UTC))
    assert is_ping_due(fresh_ping, now, 7, has_active_cards=True) is False

    new_chat = chat(created_at=dt.datetime(2026, 7, 20, tzinfo=UTC))
    assert is_ping_due(new_chat, now, 7, has_active_cards=True) is False
