import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import openai

from src.bitrix.links import FileLink
from src.bitrix.parse import ChatMessage

log = logging.getLogger(__name__)
_ATTEMPTS = 3
_MAX_TOKENS = 1500  # reasoning-модели (gpt-5) тратят часть бюджета на рассуждения


class LlmUnavailable(Exception):
    pass


@dataclass(frozen=True)
class CardDelta:
    task_id: int
    alias: str
    task_changes: list[str]
    comments: list[ChatMessage]
    checklist_done: int
    checklist_total: int
    files: list[FileLink]
    new_history_id: int
    new_message_id: int
    new_comment_id: int = 0  # курсор комментариев старой карточки без chatId (§13)

    @property
    def has_changes(self) -> bool:
        return bool(self.task_changes or self.comments or self.files)


def load_prompt(path: str = "prompts/digest.txt") -> str:
    return Path(path).read_text()


def build_prompt(template: str, delta: CardDelta, language: str, date_str: str) -> str:
    return template.format(
        language=language,
        date=date_str,
        pizzeria_name=delta.alias,
        checklist_done=delta.checklist_done,
        checklist_total=delta.checklist_total,
        task_changes="\n".join(delta.task_changes) or "-",
        comments="\n".join(f"{m.author} -> {m.text}" for m in delta.comments) or "-",
        files="\n".join(f.name for f in delta.files) or "-",
    )


def _is_client_error(e: Exception) -> bool:
    """4xx от OpenAI — ошибка запроса (промпт/параметры), ретраить бессмысленно.
    Исключение — 429 (RateLimitError): это транзиентный лимит, а не ошибка запроса,
    его штатно ретраим с backoff (§7 п.6, §11)."""
    if isinstance(e, openai.RateLimitError):
        return False
    if isinstance(e, openai.BadRequestError):
        return True
    return isinstance(e, openai.APIStatusError) and 400 <= e.status_code < 500


async def summarize(client, model: str, prompt: str) -> str:
    for attempt in range(_ATTEMPTS):
        warning = None
        try:
            resp = await client.chat.completions.create(
                model=model,
                max_completion_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            content = resp.choices[0].message.content
            if content is not None and content.strip():
                return content
            warning = "OpenAI вернул пустой ответ"  # пустой ответ — отказ, не результат (не ретраить как успех)
        except Exception as e:
            if _is_client_error(e):  # 4xx не транзиентен — ретраи не помогут (§7 п.6)
                log.warning("OpenAI 4xx, без ретраев: %s", e)
                raise LlmUnavailable(f"OpenAI отклонил запрос: {e}") from e
            warning = str(e)  # сеть/5xx/лимиты — ретраим всё
        log.warning("OpenAI попытка %d/%d: %s", attempt + 1, _ATTEMPTS, warning)
        if attempt < _ATTEMPTS - 1:
            await asyncio.sleep(2**attempt)
    raise LlmUnavailable("OpenAI недоступен после ретраев")
