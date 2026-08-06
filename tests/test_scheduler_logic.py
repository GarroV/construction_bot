"""Чистые предикаты §7 — без БД и сети."""
import datetime as dt
from types import SimpleNamespace

from src.digest.scheduler import is_digest_due, is_ping_due, safe_zoneinfo

UTC = dt.timezone.utc
HOUR = dt.timedelta(hours=1)


def chat(**over):
    base = dict(
        id=1, timezone="Asia/Bishkek", digest_time=dt.time(9, 0),
        last_run_at=None, last_digest_date=None, last_posted_at=None, last_ping_at=None,
        created_at=dt.datetime(2026, 7, 1, tzinfo=UTC),
        country=None, telegram_chat_id=-100,
    )
    base.update(over)
    return SimpleNamespace(**base)


# --- is_digest_due: почасовой режим (§7) — интервал с последнего прогона, не время суток ---

def test_due_on_first_run_when_never_ran():
    now = dt.datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    assert is_digest_due(chat(last_run_at=None), now, HOUR) is True


def test_not_due_within_interval():
    now = dt.datetime(2026, 7, 21, 4, 30, tzinfo=UTC)  # прошло 30 мин < 1 ч
    ran = dt.datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    assert is_digest_due(chat(last_run_at=ran), now, HOUR) is False


def test_due_after_interval_elapsed():
    now = dt.datetime(2026, 7, 21, 5, 0, tzinfo=UTC)  # ровно 1 ч
    ran = dt.datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    assert is_digest_due(chat(last_run_at=ran), now, HOUR) is True


def test_time_of_day_no_longer_matters():
    """Ночью тоже due — молчание при отсутствии изменений (§7 п.5) само глушит поток,
    расписание больше не привязано к digest_time."""
    now = dt.datetime(2026, 7, 21, 2, 0, tzinfo=UTC)  # глубокая ночь
    ran = dt.datetime(2026, 7, 21, 0, 30, tzinfo=UTC)  # 1.5 ч назад
    assert is_digest_due(chat(last_run_at=ran), now, HOUR) is True


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
