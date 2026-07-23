import datetime as dt
import html
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from src import repo
from src.bitrix import links
from src.bitrix.client import BitrixClient
from src.digest import collector, llm, render
from src.i18n import t
from src.repo import ChatRow
from src.telegram.send import SendResult, send_html

log = logging.getLogger(__name__)
SendFn = Callable[..., Awaitable[SendResult]]


async def dry_run_send(bot, telegram_chat_id, thread_id, text) -> SendResult:
    print(f"--- DRY_RUN -> chat={telegram_chat_id} thread={thread_id} ---\n{text}\n")
    return SendResult(ok=True)


@dataclass
class Deps:
    pool: Any
    bx: BitrixClient
    bot: Any
    llm_client: Any
    locales: dict
    settings: Any
    prompt_template: str
    send_fn: SendFn = field(default=send_html)


def _chat_label(chat: ChatRow) -> str:
    return str(chat.country or chat.telegram_chat_id)


def is_digest_due(chat: ChatRow, now_utc: dt.datetime) -> bool:
    local = now_utc.astimezone(ZoneInfo(chat.timezone))
    if local.time() < chat.digest_time:
        return False
    return chat.last_digest_date is None or chat.last_digest_date < local.date()


def is_ping_due(chat: ChatRow, now_utc: dt.datetime, ping_days: int, has_active_cards: bool) -> bool:
    if not has_active_cards:
        return False
    anchor = max(x for x in (chat.last_posted_at, chat.last_ping_at, chat.created_at) if x)
    return (now_utc - anchor) >= dt.timedelta(days=ping_days)


async def process_chat(deps: Deps, chat: ChatRow, now_utc: dt.datetime) -> list[str]:
    errors: list[str] = []
    local_date = now_utc.astimezone(ZoneInfo(chat.timezone)).date()
    lang = chat.digest_language
    cards = await repo.list_active_cards(deps.pool, chat.id)

    deltas = []
    for card in cards:
        try:
            cursor = await repo.get_cursor(deps.pool, card.bitrix_task_id, chat.id)
            deltas.append((card, await collector.collect_card_delta(deps.bx, card, cursor)))
        except Exception as e:
            log.exception("сбор дельты %s/%s", chat.id, card.bitrix_task_id)
            errors.append(f"{_chat_label(chat)}: карточка #{card.bitrix_task_id}: {e}")

    posted = False
    if any(d.has_changes for _, d in deltas):  # §7 п.5: полностью пустой чат молчит
        for card, delta in deltas:
            try:
                url = links.task_url(deps.settings.bitrix_webhook_url,
                                     deps.bx.webhook_user_id, delta.task_id)
                if not delta.has_changes:
                    text = render.no_changes_line(delta.alias, url, deps.locales, lang)
                else:
                    summary = await _summarize_or_none(deps, delta, lang, str(local_date), errors, chat)
                    text = render.card_message(delta, summary, url, deps.locales, lang)

                result = await deps.send_fn(deps.bot, chat.telegram_chat_id,
                                            chat.message_thread_id, text)
                if result.migrated_to:
                    await repo.update_chat_telegram_id(deps.pool, chat.id, result.migrated_to)
                if result.forbidden:
                    await repo.deactivate_chat(deps.pool, chat.id)
                    errors.append(f"{_chat_label(chat)}: бот выкинут из чата — деактивирован")
                    break
                if not result.ok:
                    errors.append(f"{_chat_label(chat)}: не отправлено #{delta.task_id}")
                    continue  # курсор не двигаем — дельта уедет завтра (§7 п.8-9)
                posted = True
                if delta.has_changes:
                    await repo.advance_cursor(deps.pool, delta.task_id, chat.id,
                                              delta.new_history_id, delta.new_message_id)
            except Exception as e:  # изоляция цикла отправки (§7 п.9): чат не должен ретраиться каждые 5 мин
                log.exception("отправка карточки %s/%s", chat.id, delta.task_id)
                errors.append(f"{_chat_label(chat)}: карточка #{delta.task_id}: {e}")
                continue

    await repo.mark_digest_run(deps.pool, chat.id, local_date)  # всегда (§7 п.9)
    if posted:
        await repo.mark_posted(deps.pool, chat.id)
    elif is_ping_due(chat, now_utc, deps.settings.weekly_ping_days, bool(cards)):
        since = (chat.last_posted_at or chat.created_at).date()
        ping = t(deps.locales, lang, "weekly_ping", date=since.isoformat())
        result = await deps.send_fn(deps.bot, chat.telegram_chat_id, chat.message_thread_id, ping)
        if result.ok:
            await repo.mark_ping(deps.pool, chat.id)
    return errors


async def _summarize_or_none(deps, delta, lang, date_str, errors, chat) -> str | None:
    try:
        # build_prompt внутри try: опечатка в плейсхолдере prompts/digest.txt (владелец правит
        # его руками, §9) не должна ронять весь дайджест — трактуем как отказ LLM (fallback-рендер)
        prompt = llm.build_prompt(deps.prompt_template, delta, lang, date_str)
        return await llm.summarize(deps.llm_client, deps.settings.openai_model, prompt)
    except llm.LlmUnavailable as e:
        errors.append(f"{_chat_label(chat)}: LLM недоступен (#{delta.task_id}): {e}")
        return None  # render уйдёт в fallback (§7 п.6)
    except (KeyError, ValueError, IndexError) as e:
        errors.append(f"{_chat_label(chat)}: некорректный шаблон промпта (#{delta.task_id}): {e}")
        return None  # render уйдёт в fallback (§7 п.6)


async def tick(deps: Deps, now_utc: dt.datetime | None = None) -> None:
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    errors: list[str] = []
    for chat in await repo.list_active_chats(deps.pool):
        try:
            if not is_digest_due(chat, now_utc):  # невалидная tz одного чата не должна глушить остальные
                continue
            errors += await process_chat(deps, chat, now_utc)
        except Exception as e:
            log.exception("прогон чата %s", chat.id)
            errors.append(f"{_chat_label(chat)}: прогон упал: {e}")
    if errors and deps.settings.admin_chat_id:
        try:
            # экранируем перед отправкой (единая точка): country — произвольный
            # пользовательский текст, str(e) может содержать <>& — иначе Telegram
            # уронит сводку на "can't parse entities"
            lines = "\n".join(f"• {html.escape(e)}" for e in errors[:30])
            text = "⚠️ Ошибки прогона дайджеста:\n" + lines
            await deps.send_fn(deps.bot, deps.settings.admin_chat_id, None, render.clip(text))
        except Exception:  # падение admin-сводки не должно ронять tick
            log.exception("не удалось отправить admin-сводку")
