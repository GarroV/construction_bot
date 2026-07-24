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
