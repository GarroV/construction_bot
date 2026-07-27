import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import openai

from src.bitrix.links import FileLink
from src.bitrix.parse import ChatMessage

log = logging.getLogger(__name__)
_ATTEMPTS = 3
# Бюджет ответа. Дефолт-модель gpt-5.6-terra — НЕ reasoning: весь бюджет идёт в текст
# выжимки (~250–350 слов, с запасом влезает). История: на reasoning-модели (gpt-5-mini)
# в насыщенный день скрытые рассуждения съедали весь бюджет 1500 → пустой content →
# fallback «сбой обработки». Запас держим щедрым на случай смены модели обратно.
_MAX_TOKENS = 2000


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
    stage_title: str | None = None  # первый незакрытый этап чек-листа (системная строка, не LLM)
    stage_done: int = 0
    stage_total: int = 0
    has_stages: bool = False  # есть ли у чек-листа иерархия этапов вообще

    @property
    def has_changes(self) -> bool:
        return bool(self.task_changes or self.comments or self.files)


def load_prompt(path: str = "prompts/digest.txt") -> str:
    return Path(path).read_text()


def _comment_line(m: ChatMessage) -> str:
    """"автор -> текст" плюс инлайн-пометка вложений своего сообщения (фидбек владельца:
    LLM должна упоминать файлы в контексте темы, а не безликим списком в конце)."""
    line = f"{m.author} -> {m.text}"
    if m.file_names:
        line += f" [вложения: {', '.join(m.file_names)}]"
    return line


def checklist_state_text(delta: CardDelta) -> str:
    """Человеческое состояние чек-листа для промпта (сырые числа вида «65/71» вводили
    LLM в заблуждение: 71 включает заголовки этапов, которые никто не отмечает).
    Логика зеркалит render._checklist_line, но без эмодзи и локализации — это сырьё
    для LLM, он переведёт сам."""
    if delta.stage_title is not None:
        return f"этап «{delta.stage_title}» ({delta.stage_done}/{delta.stage_total})"
    if delta.has_stages:
        return "закрыт (все этапы выполнены)"
    if delta.checklist_total == 0:
        return "отсутствует"
    if delta.checklist_done == delta.checklist_total:
        return "закрыт"
    return f"{delta.checklist_done}/{delta.checklist_total} выполнено"


def build_prompt(template: str, delta: CardDelta, language: str, date_str: str) -> str:
    return template.format(
        language=language,
        date=date_str,
        pizzeria_name=delta.alias,
        checklist_state=checklist_state_text(delta),
        task_changes="\n".join(delta.task_changes) or "-",
        comments="\n".join(_comment_line(m) for m in delta.comments) or "-",
        files="\n".join(f.name for f in delta.files) or "-",
    )


def build_overview_prompt(template: str, overview: CardDelta, language: str, date_str: str) -> str:
    """Промпт «сводка текущего состояния» (§5, «Отчёт по запросу всегда с содержимым»):
    та же форма, что build_prompt, но без task_changes/files-плейсхолдеров — prompts/
    overview.txt их не содержит (владелец: сводка не про ЧТО изменилось, а про ГДЕ
    стройка сейчас — по последним комментариям и чек-листу, независимо от курсора).
    Принимает CardDelta целиком (как build_prompt), а не россыпь полей — один и тот
    же паттерн для обоих промптов, меньше поверхность аргументов."""
    return template.format(
        language=language,
        date=date_str,
        pizzeria_name=overview.alias,
        checklist_state=checklist_state_text(overview),
        comments="\n".join(_comment_line(m) for m in overview.comments) or "-",
    )


def material_hash(prompt: str) -> str:
    """Ключ кэша LLM-выжимок (§7): sha256 финального промпта, а не сырых полей дельты —
    build_prompt/build_overview_prompt детерминированно включают в него ВСЁ сырьё
    (комментарии, изменения, чек-лист, язык, шаблон), так что совпадение хэша означает
    совпадение результата. `{date}` в промпте меняется каждые сутки — это уже само по
    себе ограничивает жизнь кэша одним днём; TTL 24h на стороне репозитория (§12) —
    вторая, независимая от содержимого промпта страховка (не единственный механизм
    инвалидации, а подстраховка на случай, если дата в промпте вдруг перестанет меняться)."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


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
            choice = resp.choices[0]
            content = choice.message.content
            if content is not None and content.strip():
                return content
            # Пустой ответ — отказ, не результат (не ретраить как успех). finish_reason в
            # логе — ключ к диагностике: 'length' = модель упёрлась в max_completion_tokens
            # (у reasoning-моделей его съедают скрытые рассуждения — это и был «сбой обработки»).
            warning = f"OpenAI вернул пустой ответ (finish_reason={choice.finish_reason})"
        except Exception as e:
            if _is_client_error(e):  # 4xx не транзиентен — ретраи не помогут (§7 п.6)
                log.warning("OpenAI 4xx, без ретраев: %s", e)
                raise LlmUnavailable(f"OpenAI отклонил запрос: {e}") from e
            warning = str(e)  # сеть/5xx/лимиты — ретраим всё
        log.warning("OpenAI попытка %d/%d: %s", attempt + 1, _ATTEMPTS, warning)
        if attempt < _ATTEMPTS - 1:
            await asyncio.sleep(2**attempt)
    raise LlmUnavailable("OpenAI недоступен после ретраев")


# --- Автонастройка чата из карточки при первом /add (§5, дизайн владельца зафиксирован):
# ОДИН structured-output вызов даёт и язык (ru/en), и IANA-таймзону города из названия.
# Держим отдельно от digest.txt/overview.txt: это не генеративный пользовательский текст,
# а жёсткая экстракция под фиксированную JSON-схему — заводить под неё отдельный файл
# промпта (как у digest/overview) избыточно, схема и текст меняются только вместе.

_LOCALE_MAX_TOKENS = 300  # reasoning-модели (gpt-5.x) едят бюджет на скрытые рассуждения
# даже под маленький JSON-ответ (см. _MAX_TOKENS выше) — щедрый, но не безразмерный запас.

_LOCALE_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {
            "type": "string",
            "enum": ["ru", "en"],
            "description": "'ru', если обсуждение преимущественно на русском, иначе 'en'.",
        },
        "timezone": {
            "type": "string",
            "description": (
                "IANA-таймзона города из названия карточки (например 'Europe/Podgorica'); "
                "пустая строка, если город неясен."
            ),
        },
    },
    "required": ["language", "timezone"],
    "additionalProperties": False,
}

_LOCALE_PROMPT_TEMPLATE = """Определи по названию карточки стройки и обсуждению в ней:
1. language — 'ru', если обсуждение преимущественно на русском языке, иначе 'en' (другие значения недопустимы).
2. timezone — IANA-таймзона города, упомянутого в названии карточки (например, «Podgorica-2» -> 'Europe/Podgorica'). Если город не определить однозначно — верни пустую строку.

Название карточки: {title}

Последние комментарии в обсуждении (может быть пусто):
{comments}
"""


def _build_locale_prompt(title: str, comments: list[str]) -> str:
    body = "\n".join(comments) if comments else "-"
    return _LOCALE_PROMPT_TEMPLATE.format(title=title, comments=body)


def _parse_locale_response(content: str) -> tuple[str, str | None] | None:
    """JSON-строка ответа -> (language, timezone|None); None — мусор (не JSON, не тот
    тип, недопустимый language). Пустая/пробельная timezone нормализуется в None —
    контракт промпта: "город неясен"."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    language = data.get("language")
    if language not in ("ru", "en"):
        return None
    timezone = str(data.get("timezone") or "").strip() or None
    return language, timezone


async def detect_card_locale(
    client, model: str, title: str, comments: list[str]
) -> tuple[str, str | None]:
    """Автонастройка чата из карточки (§5): один LLM-вызов structured-output ->
    (language, timezone). language всегда 'ru' или 'en' (схема enum); timezone — IANA-
    строка или None, если LLM не смог определить город. Ретраи и деградация — тот же
    паттерн, что summarize (3 попытки, экспоненциальный backoff); финальный отказ ИЛИ
    неразбираемый ответ -> LlmUnavailable — вызывающий (commands.handle_add) обязан
    тихо проглотить её и не менять настройки чата (владелец: /add не должен падать
    или тормозить из-за детекта)."""
    prompt = _build_locale_prompt(title, comments)
    for attempt in range(_ATTEMPTS):
        warning = None
        try:
            resp = await client.chat.completions.create(
                model=model,
                max_completion_tokens=_LOCALE_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "card_locale",
                        "schema": _LOCALE_SCHEMA,
                        "strict": True,
                    },
                },
            )
            choice = resp.choices[0]
            content = choice.message.content
            if content is not None and content.strip():
                parsed = _parse_locale_response(content)
                if parsed is not None:
                    return parsed
                warning = f"LLM вернул нераспознаваемый ответ детекта языка/таймзоны: {content!r}"
            else:
                warning = f"OpenAI вернул пустой ответ (finish_reason={choice.finish_reason})"
        except Exception as e:
            if _is_client_error(e):  # 4xx не транзиентен — ретраи не помогут
                log.warning("OpenAI 4xx (detect_card_locale), без ретраев: %s", e)
                raise LlmUnavailable(f"OpenAI отклонил запрос: {e}") from e
            warning = str(e)  # сеть/5xx/лимиты — ретраим всё
        log.warning("OpenAI detect_card_locale попытка %d/%d: %s", attempt + 1, _ATTEMPTS, warning)
        if attempt < _ATTEMPTS - 1:
            await asyncio.sleep(2**attempt)
    raise LlmUnavailable("OpenAI недоступен после ретраев (detect_card_locale)")
