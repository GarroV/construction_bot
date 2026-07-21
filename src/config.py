from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bitrix_webhook_url: str
    telegram_bot_token: str
    openai_api_key: str
    openai_model: str = "gpt-5-mini"
    postgres_dsn: str
    scheduler_tick_minutes: int = 5
    default_language: str = "ru"
    admin_chat_id: int | None = None
    weekly_ping_days: int = 7
    dry_run: bool = False


def load_settings() -> Settings:
    return Settings()  # читает env; .env подхватывает docker compose
