import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from src.bitrix.links import FileLink
from src.bitrix.parse import ChatMessage

log = logging.getLogger(__name__)
_ATTEMPTS = 3
_MAX_TOKENS = 500  # ~150 слов + запас (§9)


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


async def summarize(client, model: str, prompt: str) -> str:
    for attempt in range(_ATTEMPTS):
        try:
            resp = await client.chat.completions.create(
                model=model,
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content or ""
        except Exception as e:  # сеть/5xx/лимиты — ретраим всё (§7 п.6)
            log.warning("OpenAI попытка %d/%d: %s", attempt + 1, _ATTEMPTS, e)
            if attempt < _ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)
    raise LlmUnavailable("OpenAI недоступен после ретраев")
