from src.config import load_settings


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://portal.bitrix24.ru/rest/123/abc/")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "42:token")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://bot:bot@localhost:5433/botdb")
    monkeypatch.setenv("ADMIN_CHAT_ID", "100500")
    monkeypatch.setenv("DRY_RUN", "true")

    s = load_settings()

    assert s.bitrix_webhook_url == "https://portal.bitrix24.ru/rest/123/abc/"
    assert s.openai_model == "gpt-5-mini"          # дефолт
    assert s.scheduler_tick_minutes == 5           # дефолт
    assert s.default_language == "ru"              # дефолт
    assert s.weekly_ping_days == 7                 # дефолт
    assert s.admin_chat_id == 100500
    assert s.dry_run is True
