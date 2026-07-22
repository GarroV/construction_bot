from unittest.mock import AsyncMock

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
