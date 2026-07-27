import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from src.bitrix.links import FileLink
from src.bitrix.parse import ChatMessage
from src.digest import llm
from src.digest.llm import CardDelta, LlmUnavailable

DELTA = CardDelta(
    task_id=8017, alias="Бишкек 8",
    task_changes=["статус: 2 → 5"],
    comments=[ChatMessage(id=1, author="Иван", text="Плитку согласовали", file_ids=[])],
    checklist_done=3, checklist_total=10,
    files=[FileLink(name="план.pdf", url="https://p/disk/1")],
    new_history_id=31, new_message_id=202,
)


def test_build_prompt_fills_placeholders():
    template = llm.load_prompt()
    prompt = llm.build_prompt(template, DELTA, language="ru", date_str="2026-07-21")
    assert "Бишкек 8" in prompt and "3/10" in prompt
    assert "Иван -> Плитку согласовали" in prompt
    assert "план.pdf" in prompt and "{" not in prompt  # все плейсхолдеры закрыты


def test_build_prompt_inlines_attachment_names_when_present():
    """Фидбек владельца: LLM должна упоминать вложения в контексте темы, а не
    безликим списком в конце — build_prompt помечает строку комментария file_names."""
    template = llm.load_prompt()
    delta = CardDelta(
        task_id=8017, alias="Бишкек 8", task_changes=[],
        comments=[ChatMessage(
            id=1, author="Иван", text="пароль указан", file_ids=[],
            file_names=("Нови сад2 1.png", "Нови сад2.png"),
        )],
        checklist_done=0, checklist_total=0, files=[],
        new_history_id=0, new_message_id=0,
    )

    prompt = llm.build_prompt(template, delta, language="ru", date_str="2026-07-21")

    assert "Иван -> пароль указан [вложения: Нови сад2 1.png, Нови сад2.png]" in prompt


def test_build_prompt_omits_attachment_marker_when_no_files():
    template = llm.load_prompt()
    prompt = llm.build_prompt(template, DELTA, language="ru", date_str="2026-07-21")

    # DELTA.comments[0].file_names пуст — строка комментария без маркера вложений
    assert "Плитку согласовали [вложения" not in prompt


# --- build_overview_prompt: сводка «текущее состояние» (§5, «Отчёт по запросу») ---

OVERVIEW = CardDelta(
    task_id=8017, alias="Бишкек 8", task_changes=[],
    comments=[ChatMessage(id=1, author="Иван", text="Плитку положили", file_ids=[])],
    checklist_done=3, checklist_total=10,
    files=[FileLink(name="план.pdf", url="https://p/disk/1")],
    new_history_id=20, new_message_id=200,
)


def test_build_overview_prompt_fills_placeholders():
    template = llm.load_prompt("prompts/overview.txt")
    prompt = llm.build_overview_prompt(template, OVERVIEW, language="ru", date_str="2026-07-24")

    assert "Бишкек 8" in prompt and "3/10" in prompt
    assert "Иван -> Плитку положили" in prompt
    assert "2026-07-24" in prompt
    assert "{" not in prompt  # все плейсхолдеры закрыты — в т.ч. отсутствующие task_changes/files


def test_build_overview_prompt_no_comments_uses_placeholder_dash():
    template = llm.load_prompt("prompts/overview.txt")
    empty = CardDelta(
        task_id=8017, alias="Бишкек 8", task_changes=[], comments=[],
        checklist_done=0, checklist_total=0, files=[],
        new_history_id=0, new_message_id=0,
    )

    prompt = llm.build_overview_prompt(template, empty, language="ru", date_str="2026-07-24")

    assert "Последние комментарии" in prompt


def _client_returning(text):
    resp = AsyncMock()
    resp.choices = [AsyncMock(message=AsyncMock(content=text))]
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    return client


async def test_summarize_returns_text():
    client = _client_returning("Сводка дня.")
    assert await llm.summarize(client, "gpt-5-mini", "prompt") == "Сводка дня."


async def test_summarize_retries_then_raises(monkeypatch):
    monkeypatch.setattr(llm.asyncio, "sleep", AsyncMock())
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("api down"))

    with pytest.raises(LlmUnavailable):
        await llm.summarize(client, "gpt-5-mini", "prompt")
    assert client.chat.completions.create.await_count == 3


async def test_summarize_empty_content_is_treated_as_failure(monkeypatch):
    """Пустой content — отказ модели, а не валидный дайджест: ретраим и в конце падаем."""
    monkeypatch.setattr(llm.asyncio, "sleep", AsyncMock())
    client = _client_returning("")

    with pytest.raises(LlmUnavailable):
        await llm.summarize(client, "gpt-5-mini", "prompt")
    assert client.chat.completions.create.await_count == 3


async def test_summarize_bad_request_fails_fast_without_retry(monkeypatch):
    """4xx от OpenAI — ошибка запроса (не транзиентная), ретраить бессмысленно."""
    monkeypatch.setattr(llm.asyncio, "sleep", AsyncMock())
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(400, request=req, json={"error": {"message": "bad"}})
    error = openai.BadRequestError("bad request", response=resp, body=None)
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(side_effect=error)

    with pytest.raises(LlmUnavailable):
        await llm.summarize(client, "gpt-5-mini", "prompt")
    assert client.chat.completions.create.await_count == 1


async def test_summarize_retries_rate_limit_then_succeeds(monkeypatch):
    """429 (RateLimitError) — транзиентный лимит, а не ошибка запроса: ретраим с backoff
    (§7 п.6, §11), не fail-fast как прочие 4xx."""
    monkeypatch.setattr(llm.asyncio, "sleep", AsyncMock())
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(429, request=req, json={"error": {"message": "rate limited"}})
    error = openai.RateLimitError("rate limited", response=resp, body=None)

    ok_resp = AsyncMock()
    ok_resp.choices = [AsyncMock(message=AsyncMock(content="Сводка дня."))]
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(side_effect=[error, error, ok_resp])

    result = await llm.summarize(client, "gpt-5-mini", "prompt")

    assert result == "Сводка дня."
    assert client.chat.completions.create.await_count == 3


async def test_summarize_uses_max_completion_tokens():
    """gpt-5 отвергает max_tokens с 400 — контракт требует max_completion_tokens."""
    client = _client_returning("Сводка дня.")
    await llm.summarize(client, "gpt-5-mini", "prompt")

    _, kwargs = client.chat.completions.create.call_args
    assert "max_completion_tokens" in kwargs
    assert "max_tokens" not in kwargs
    assert kwargs["max_completion_tokens"] == llm._MAX_TOKENS


# --- material_hash: ключ кэша LLM-выжимок (§7, кэш по хэшу сырья) ---


def test_material_hash_is_deterministic():
    prompt = llm.build_prompt(llm.load_prompt(), DELTA, language="ru", date_str="2026-07-21")

    assert llm.material_hash(prompt) == llm.material_hash(prompt)


def test_material_hash_changes_with_content():
    """Промпт детерминированно включает всё сырьё — любое изменение (новый комментарий,
    другая дата и т.п.) должно менять хэш, иначе кэш отдал бы устаревшую выжимку."""
    base = llm.build_prompt(llm.load_prompt(), DELTA, language="ru", date_str="2026-07-21")
    changed = llm.build_prompt(llm.load_prompt(), DELTA, language="ru", date_str="2026-07-22")

    assert llm.material_hash(base) != llm.material_hash(changed)


def test_material_hash_is_sha256_hex_digest():
    assert llm.material_hash("привет") == hashlib.sha256("привет".encode("utf-8")).hexdigest()


# --- detect_card_locale: автонастройка чата из карточки при первом /add (§5) ---


def _client_returning_json(payload: dict):
    """Structured-output ответ: content — JSON-СТРОКА (как реально отдаёт OpenAI при
    response_format=json_schema), не готовый dict."""
    return _client_returning(json.dumps(payload))


async def test_detect_card_locale_returns_ru_and_resolved_timezone():
    client = _client_returning_json({"language": "ru", "timezone": "Europe/Podgorica"})

    result = await llm.detect_card_locale(client, "gpt-5.6-terra", "Подгорица-2", ["Привет"])

    assert result == ("ru", "Europe/Podgorica")


async def test_detect_card_locale_returns_en_and_none_when_city_unclear():
    """Пустая строка timezone (LLM не смог определить город) нормализуется в None."""
    client = _client_returning_json({"language": "en", "timezone": ""})

    result = await llm.detect_card_locale(client, "gpt-5.6-terra", "New Site", ["Hello"])

    assert result == ("en", None)


async def test_detect_card_locale_passes_json_schema_response_format():
    client = _client_returning_json({"language": "ru", "timezone": ""})

    await llm.detect_card_locale(client, "gpt-5.6-terra", "Белград 2", [])

    _, kwargs = client.chat.completions.create.call_args
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    assert kwargs["max_completion_tokens"] == llm._LOCALE_MAX_TOKENS


async def test_detect_card_locale_retries_then_raises(monkeypatch):
    monkeypatch.setattr(llm.asyncio, "sleep", AsyncMock())
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("api down"))

    with pytest.raises(LlmUnavailable):
        await llm.detect_card_locale(client, "gpt-5.6-terra", "Бишкек 8", [])
    assert client.chat.completions.create.await_count == 3


async def test_detect_card_locale_garbage_json_retries_then_raises(monkeypatch):
    """Мусор вместо валидного JSON-ответа — не результат, а отказ (ретраим, как и
    пустой content у summarize)."""
    monkeypatch.setattr(llm.asyncio, "sleep", AsyncMock())
    client = _client_returning("это не json")

    with pytest.raises(LlmUnavailable):
        await llm.detect_card_locale(client, "gpt-5.6-terra", "Бишкек 8", [])
    assert client.chat.completions.create.await_count == 3


async def test_detect_card_locale_invalid_language_enum_retries_then_raises(monkeypatch):
    """language вне {'ru', 'en'} — тоже мусор, даже если JSON валиден."""
    monkeypatch.setattr(llm.asyncio, "sleep", AsyncMock())
    client = _client_returning_json({"language": "fr", "timezone": ""})

    with pytest.raises(LlmUnavailable):
        await llm.detect_card_locale(client, "gpt-5.6-terra", "Бишкек 8", [])
    assert client.chat.completions.create.await_count == 3


async def test_detect_card_locale_bad_request_fails_fast_without_retry(monkeypatch):
    monkeypatch.setattr(llm.asyncio, "sleep", AsyncMock())
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(400, request=req, json={"error": {"message": "bad"}})
    error = openai.BadRequestError("bad request", response=resp, body=None)
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(side_effect=error)

    with pytest.raises(LlmUnavailable):
        await llm.detect_card_locale(client, "gpt-5.6-terra", "Бишкек 8", [])
    assert client.chat.completions.create.await_count == 1


def test_checklist_state_text_variants():
    def d(**over):
        base = dict(task_id=1, alias="X", task_changes=[], comments=[],
                    checklist_done=0, checklist_total=0, files=[],
                    new_history_id=0, new_message_id=0)
        base.update(over)
        return CardDelta(**base)

    assert "этап «05 Delivery» (9/12)" in llm.checklist_state_text(
        d(has_stages=True, stage_title="05 Delivery", stage_done=9, stage_total=12))
    assert llm.checklist_state_text(d(has_stages=True, checklist_done=65, checklist_total=71)) == "закрыт (все этапы выполнены)"
    assert llm.checklist_state_text(d()) == "отсутствует"
    assert llm.checklist_state_text(d(checklist_done=9, checklist_total=9)) == "закрыт"
    assert llm.checklist_state_text(d(checklist_done=3, checklist_total=9)) == "3/9 выполнено"
    # сырые «65/71» больше не попадают в промпт закрытого чек-листа
    prompt = llm.build_prompt(llm.load_prompt(), d(has_stages=True, checklist_done=65, checklist_total=71), "ru", "2026-07-25")
    assert "65/71" not in prompt and "закрыт" in prompt
