import datetime as dt
import html
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from src import repo
from src.bitrix import links, methods
from src.bitrix.client import BitrixClient, BitrixError
from src.digest import collector, llm, render
from src.i18n import t
from src.repo import CardRow, ChatRow
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
    bot_username: str = ""  # для строгой адресации команд в группах


def _chat_label(chat: ChatRow) -> str:
    return str(chat.country or chat.telegram_chat_id)


def safe_zoneinfo(tz: str, chat_label: str = "") -> ZoneInfo:
    """ZoneInfo(tz) с fallback на UTC (ревью: битая таймзона чата в БД — легальный
    сценарий §6, ручные правки/рассинхрон tz_aliases) не должна ронять ни тик, ни
    интерактивные пути (/report, кнопки, force_report) исключением ZoneInfoNotFoundError.
    Сигнал не глушим молча — логируем warning с исходным tz и лейблом чата, если он
    известен вызывающему (иначе tick тихо переехавший на UTC чат было бы невозможно
    отличить от корректно настроенного)."""
    try:
        return ZoneInfo(tz)
    except Exception:
        log.warning("невалидная таймзона %r у чата %s — fallback на UTC", tz, chat_label or "?")
        return ZoneInfo("UTC")


def is_digest_due(chat: ChatRow, now_utc: dt.datetime) -> bool:
    local = now_utc.astimezone(safe_zoneinfo(chat.timezone, _chat_label(chat)))
    if local.time() < chat.digest_time:
        return False
    return chat.last_digest_date is None or chat.last_digest_date < local.date()


def is_ping_due(chat: ChatRow, now_utc: dt.datetime, ping_days: int, has_active_cards: bool) -> bool:
    if not has_active_cards:
        return False
    anchor = max(x for x in (chat.last_posted_at, chat.last_ping_at, chat.created_at) if x)
    return (now_utc - anchor) >= dt.timedelta(days=ping_days)


async def _add_subtask_card(deps: Deps, chat: ChatRow, parent: CardRow, sub_id: int, sub_title: str) -> None:
    """Заводит авто-подхваченную карточку подзадачи с курсорами «с этого момента» (§5,
    как обычный /add) — первый прогон по ней не должен вываливать историю задачи."""
    last_history_id = await methods.get_latest_history_id(deps.bx, sub_id)
    # На этом портале у задач нет чатов (живой смоук §13: 42103 без chatId) — не спрашиваем
    # chatId подзадачи отдельным вызовом ради last_message_id, дискавери не должен дорожать
    # лишним обращением к API; фиксируем 0, как для карточек без чата в handle_add.
    last_message_id = 0
    try:
        last_comment_id = await methods.get_latest_comment_id(deps.bx, sub_id)
    except BitrixError as e:
        log.warning("get_latest_comment_id(%s) не удался при дискавери: %s", sub_id, e)
        last_comment_id = 0
    alias = f"{parent.alias or f'#{parent.bitrix_task_id}'} / {sub_title}"
    await repo.add_card(
        deps.pool, chat.id, sub_id, alias, None,
        last_history_id, last_message_id, last_comment_id, auto_from=parent.bitrix_task_id,
    )


async def _discover_subtasks(deps: Deps, chat: ChatRow, cards: list[CardRow]) -> list[str]:
    """Авто-подхват подзадач Битрикса (§7 фича 1): для каждой РУЧНОЙ активной карточки чата
    (auto_from IS NULL — авто-карточки сами родителями не становятся, иначе подзадачи подзадач
    дискаверились бы рекурсивно; уровень вложенности ровно один) ищем её подзадачи через
    tasks.task.list(PARENT_ID) и заводим карточку на каждую, которой ещё нет среди карточек
    этого чата — ни активной, ни деактивированной (партнёр мог снять её осознанно, реанимировать
    нельзя). Сбой по одному родителю логируется и попадает в errors, но не роняет прогон —
    тот же паттерн изоляции, что у сбора дельты чуть ниже."""
    errors: list[str] = []
    for card in cards:
        if card.auto_from is not None:
            continue
        try:
            subtasks = await methods.list_subtasks(deps.bx, card.bitrix_task_id)
            for sub in subtasks:
                sub_id = int(sub["id"])
                if await repo.card_exists(deps.pool, chat.id, sub_id):
                    continue
                await _add_subtask_card(deps, chat, card, sub_id, str(sub.get("title") or f"#{sub_id}"))
        except Exception as e:
            log.exception("дискавери подзадач %s/%s", chat.id, card.bitrix_task_id)
            errors.append(f"{_chat_label(chat)}: дискавери подзадач #{card.bitrix_task_id}: {e}")
    return errors


async def process_chat(
    deps: Deps, chat: ChatRow, now_utc: dt.datetime, mark_run: bool = True,
    only_task_id: int | None = None,
) -> tuple[list[str], bool]:
    """Прогон дайджеста чата. mark_run=False — «Отчёт сейчас» (/report, §5): курсоры
    двигаются как в обычном тике, но last_digest_date НЕ трогаем (дневной дайджест по
    расписанию должен сработать сам) и пинг-ветку пропускаем (иначе /report мог бы
    «съесть» недельный пинг за счёт mark_posted, который остаётся безусловным).

    only_task_id (§5, выбор карточки для отчёта — кнопка/команда с аргументом): если
    задан, обрабатывается ТОЛЬКО эта карточка чата — соседние карточки (в т.ч. их
    блоки «изменений нет») в сообщение вообще не попадают, а дискавери подзадач
    пропускается целиком (точечный отчёт должен быть быстрым, обход API подзадач ему
    не нужен). При None (обычный тик/полный отчёт) — поведение без изменений."""
    errors: list[str] = []
    local_date = now_utc.astimezone(safe_zoneinfo(chat.timezone, _chat_label(chat))).date()
    lang = chat.digest_language
    cards = await repo.list_active_cards(deps.pool, chat.id)
    if only_task_id is None:
        errors += await _discover_subtasks(deps, chat, cards)
    else:
        cards = [c for c in cards if c.bitrix_task_id == only_task_id]

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
        # Фаза 1: рендер блоков карточек — per-card try/except как раньше, сбой одной
        # карточки изолирован (её блок выпадает, попадает в errors, остальные едут дальше
        # в общем сообщении чата, §7 п.6 fallback-путь не затронут).
        blocks: list[tuple[llm.CardDelta, str]] = []
        for card, delta in deltas:
            try:
                url = links.task_url(deps.settings.bitrix_webhook_url,
                                     deps.bx.webhook_user_id, delta.task_id)
                if not delta.has_changes:
                    text = render.no_changes_line(delta.alias, url, deps.locales, lang)
                else:
                    summary = await _summarize_or_none(deps, delta, lang, str(local_date), errors, chat)
                    text = render.card_message(delta, summary, url, deps.locales, lang)
                blocks.append((delta, text))
            except Exception as e:
                log.exception("рендер карточки %s/%s", chat.id, delta.task_id)
                errors.append(f"{_chat_label(chat)}: карточка #{delta.task_id}: {e}")

        # Фаза 2: сплит по границам блоков (§7 п.7) — минимальное число сообщений; всё,
        # что влезло, уходит одним сообщением.
        chunks = render.chunk_blocks([text for _, text in blocks])

        # Фаза 3: последовательная отправка чанков. Granularity сдвига курсора укрупнена
        # с карточки до чанка (§7 п.8): курсоры ВСЕХ карточек чанка двигаются только после
        # успешной отправки сообщения, в которое вошёл их блок; сбой чанка не двигает ни
        # один из его курсоров (дельта уедет завтра), но не блокирует остальные чанки —
        # кроме forbidden, где чат деактивирован и дальнейшие чанки не имеют смысла.
        for chunk_indices in chunks:
            chunk_text = "\n\n".join(blocks[i][1] for i in chunk_indices)
            try:
                result = await deps.send_fn(deps.bot, chat.telegram_chat_id,
                                            chat.message_thread_id, chunk_text)
                if result.migrated_to:
                    await repo.update_chat_telegram_id(deps.pool, chat.id, result.migrated_to)
                if result.forbidden:
                    await repo.deactivate_chat(deps.pool, chat.id)
                    errors.append(f"{_chat_label(chat)}: бот выкинут из чата — деактивирован")
                    break
                if not result.ok:
                    task_ids = ", ".join(f"#{blocks[i][0].task_id}" for i in chunk_indices)
                    errors.append(f"{_chat_label(chat)}: не отправлен чанк дайджеста ({task_ids})")
                    continue  # курсоры чанка не двигаем — дельта уедет завтра (§7 п.8-9)
                posted = True
                for i in chunk_indices:
                    delta = blocks[i][0]
                    if not delta.has_changes:
                        continue
                    try:
                        # Свой try/except на карточку (ревью-фикс): без него сбой записи
                        # курсора первой карточки чанка глушил бы запись курсоров остальных
                        # карточек УЖЕ доставленного сообщения — их дельта уехала бы завтра
                        # повторно (дубль в дайджесте), хотя отправка была успешной.
                        await repo.advance_cursor(deps.pool, delta.task_id, chat.id,
                                                  delta.new_history_id, delta.new_message_id,
                                                  delta.new_comment_id)
                    except Exception as e:
                        log.exception("запись курсора %s/%s", chat.id, delta.task_id)
                        errors.append(f"{_chat_label(chat)}: курсор #{delta.task_id} не записан: {e}")
                        continue
            except Exception as e:  # изоляция отправки (§7 п.9): чат не должен ретраиться каждые 5 мин
                log.exception("отправка чанка дайджеста %s", chat.id)
                errors.append(f"{_chat_label(chat)}: отправка дайджеста: {e}")
                continue

    if mark_run:
        await repo.mark_digest_run(deps.pool, chat.id, local_date)  # всегда, кроме /report (§7 п.9)
    if posted:
        await repo.mark_posted(deps.pool, chat.id)
    elif mark_run and is_ping_due(chat, now_utc, deps.settings.weekly_ping_days, bool(cards)):
        since = (chat.last_posted_at or chat.created_at).date()
        ping = t(deps.locales, lang, "weekly_ping", date=since.isoformat())
        result = await deps.send_fn(deps.bot, chat.telegram_chat_id, chat.message_thread_id, ping)
        if result.ok:
            await repo.mark_ping(deps.pool, chat.id)
    return errors, posted


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
            chat_errors, _posted = await process_chat(deps, chat, now_utc)
            errors += chat_errors
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
