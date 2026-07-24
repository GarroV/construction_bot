"""Инлайн-меню (кнопки) + «Отчёт сейчас» (§5 спеки: /report, /menu).

Зачем: партнёры не печатают адресные команды в группах (§«строгая адресация»,
commands.py:_addressed_to_me). Callback'и (InlineKeyboard + callback_query) вообще не
зависят от privacy mode/адресации — тыкать кнопки можно всегда. Единственный текстовый
ввод, который остаётся, — номер карточки/время, и он идёт reply-ем на сообщение бота:
reply на сообщение бота проходит privacy mode (это не «обычная переписка»).

Reply-роутинг (после ревью, Important): _reply_action узнаёт, ЧТО означает reply, по
ТОЧНОМУ совпадению текста prompt-сообщения с локализованными menu_add_prompt/
menu_time_prompt (по ВСЕМ языкам локалей) — а не по первому символу. Первый символ
prompt-сообщения — тот же эмодзи (➕/🕘), что и в начале alias'а карточки из Битрикса
вполне может встретиться в дайджест-сообщении; матч по первому символу коллизировал бы
с обычным дайджестом и увёл бы reply не туда. Точное совпадение всей строки такой
коллизии не подвержено.

Callback-путь и права (после ревью, Critical): callback.message — это сообщение БОТА
(автор кнопки), а не нажавшего пользователя. Права в restricted-чате проверяем через
ensure_chat_for_callback (commands.py) — она читает user_id из callback.from_user, а не
из callback.message.from_user.

Диалоговые флоу вместо usage-подсказок: владелец зафиксировал — голая команда без
аргументов (/add, /time, /lang, /remove) должна открывать диалог, а не показывать
«Использование: …» (та подсказка — только на непустые НЕвалидные аргументы).
send_add_prompt/send_time_prompt/send_lang_keyboard/send_rm_keyboard — общий код
для этого диалога: их вызывает и dispatch_callback (кнопки панели), и
commands.build_router (голая команда) — один текст, одна реализация (DRY).

Циклический импорт: menu.py импортирует точечно только «ядра» из commands.py
(ensure_chat, ensure_chat_for_callback, _addressed_to_me, handle_add, handle_time,
_args_of, _parse_task_ref) — их значения не нужны build_router. commands.py, наоборот, использует
build_panel_keyboard()/send_add_prompt/send_time_prompt/send_lang_keyboard/
send_rm_keyboard отсюда только внутри build_router() (локальный импорт по месту
вызова, а не на уровне модуля) — к моменту вызова оба модуля уже полностью загружены,
цикла не возникает.
"""
import datetime as dt
import logging
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src import repo
from src.digest.scheduler import process_chat
from src.i18n import t
from src.repo import CardRow, ChatRow
from src.telegram.commands import (
    _addressed_to_me,
    _args_of,
    _parse_task_ref,
    ensure_chat,
    ensure_chat_for_callback,
    handle_add,
    handle_time,
)

log = logging.getLogger(__name__)

_LANG_CODES = ("ru", "en", "es", "sl")


def _cards_line(cards: list[CardRow]) -> str:
    return "\n".join(f"• {c.alias or '#'} (#{c.bitrix_task_id})" for c in cards)


def render_panel_text(locales, lang: str, cards: list[CardRow]) -> str:
    body = _cards_line(cards) if cards else t(locales, lang, "menu_empty_cards")
    return t(locales, lang, "menu_title", cards=body)


def build_panel_keyboard(locales, lang: str) -> InlineKeyboardMarkup:
    def btn(key: str, data: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=t(locales, lang, key), callback_data=data)

    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("btn_add", "m:add"), btn("btn_rm", "m:rm")],
        [btn("btn_report", "m:report")],
        [btn("btn_time", "m:time"), btn("btn_lang", "m:lang")],
    ])


def build_rm_keyboard(locales, lang: str, cards: list[CardRow]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{c.alias or '#'} (#{c.bitrix_task_id})",
                              callback_data=f"m:rm:{c.bitrix_task_id}")]
        for c in cards
    ]
    rows.append([InlineKeyboardButton(text=t(locales, lang, "btn_cancel"), callback_data="m:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_report_keyboard(locales, lang: str, cards: list[CardRow]) -> InlineKeyboardMarkup:
    """[По всем] + по одной кнопке на каждую активную карточку + [Отмена] (владелец:
    «📊 Отчёт сейчас» должен давать выбор карточки, а не молча гнать по всему чату)."""
    rows = [[InlineKeyboardButton(text=t(locales, lang, "btn_report_all"), callback_data="m:report:all")]]
    rows += [
        [InlineKeyboardButton(text=f"{c.alias or '#'} (#{c.bitrix_task_id})",
                              callback_data=f"m:report:{c.bitrix_task_id}")]
        for c in cards
    ]
    rows.append([InlineKeyboardButton(text=t(locales, lang, "btn_cancel"), callback_data="m:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_lang_keyboard() -> InlineKeyboardMarkup:
    # Коды языков как подписи — без отдельного слоя переводимых названий языков.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=code.upper(), callback_data=f"m:lang:{code}") for code in _LANG_CODES]
    ])


# Диалоговые флоу для add/time/lang/remove (владелец зафиксировал: голая команда без
# аргументов — это диалог, не «Использование: …», см. commands.resolve_empty_args_flow).
# Общие функции — их вызывают И callback-хендлеры кнопок панели (dispatch_callback),
# И голые команды без аргументов (commands.build_router) — один код, один текст (DRY).
# target — Message, на которое зовём .answer() (либо сообщение пользователя с командой,
# либо callback.message — сообщение бота с кнопкой; у обоих есть .answer()).

async def send_add_prompt(deps, target: Message, chat: ChatRow) -> None:
    await target.answer(t(deps.locales, chat.digest_language, "menu_add_prompt"))


async def send_time_prompt(deps, target: Message, chat: ChatRow) -> None:
    await target.answer(t(deps.locales, chat.digest_language, "menu_time_prompt"))


async def send_lang_keyboard(deps, target: Message, chat: ChatRow) -> None:
    await target.answer(
        t(deps.locales, chat.digest_language, "menu_lang_pick"),
        reply_markup=build_lang_keyboard(),
    )


async def send_rm_keyboard(deps, target: Message, chat: ChatRow) -> None:
    lang = chat.digest_language
    cards = await repo.list_active_cards(deps.pool, chat.id)
    if not cards:
        await target.answer(t(deps.locales, lang, "menu_rm_empty"))
        return
    await target.answer(
        t(deps.locales, lang, "menu_rm_pick"),
        reply_markup=build_rm_keyboard(deps.locales, lang, cards),
    )


async def send_report_pick(deps, target: Message, chat: ChatRow) -> None:
    """«По чему отчёт?» — диалоговый флоу для голого /report И кнопки «📊 Отчёт сейчас»
    (владелец: кнопка не должна молча гнать отчёт по всему чату). Общий код, как у
    send_rm_keyboard/send_lang_keyboard выше (паттерн send_*, DRY)."""
    lang = chat.digest_language
    cards = await repo.list_active_cards(deps.pool, chat.id)
    await target.answer(
        t(deps.locales, lang, "report_pick"),
        reply_markup=build_report_keyboard(deps.locales, lang, cards),
    )


_REPORT_EMPTY_DATE_FMT = "%d.%m %H:%M"


def _report_empty_text(deps, chat: ChatRow) -> str:
    """report_empty с датой последнего дайджеста в таймзоне чата (владелец: голое
    «изменений нет» неинформативно — с какого момента?). last_posted_at IS NULL
    (чат подключили, но ни один дайджест ещё не ушёл) -> отдельный текст
    report_empty_never, без даты-заглушки."""
    lang = chat.digest_language
    if chat.last_posted_at is None:
        return t(deps.locales, lang, "report_empty_never")
    local = chat.last_posted_at.astimezone(ZoneInfo(chat.timezone))
    return t(deps.locales, lang, "report_empty", date=local.strftime(_REPORT_EMPTY_DATE_FMT))


async def run_report(deps, chat: ChatRow, only_task_id: int | None = None) -> None:
    """Общее ядро для /report (текстом) и m:report:*-кнопок (§5): тот же пайплайн, что и
    у планировщика, но mark_run=False — курсоры двигаются, last_digest_date нет.
    only_task_id — точечный отчёт по одной карточке (см. process_chat), None — по всем."""
    now_utc = dt.datetime.now(dt.timezone.utc)
    errors, posted = await process_chat(deps, chat, now_utc, mark_run=False, only_task_id=only_task_id)
    if not posted:
        await deps.send_fn(
            deps.bot, chat.telegram_chat_id, chat.message_thread_id,
            _report_empty_text(deps, chat),
        )
    if errors:
        log.warning("report: чат %s: %s", chat.id, errors)
        # правка ревью (Minor): раньше об ошибках узнавал только лог — партнёр видел
        # тишину и не понимал, что часть отчёта не собралась.
        await deps.send_fn(
            deps.bot, chat.telegram_chat_id, chat.message_thread_id,
            t(deps.locales, chat.digest_language, "report_errors"),
        )


async def _deny_restricted(deps, message: Message) -> None:
    await message.reply(t(deps.locales, deps.settings.default_language, "restricted_denied"))


async def _cmd_menu(deps, message: Message) -> None:
    if not _addressed_to_me(message, getattr(deps, "bot_username", "")):
        return
    chat = await ensure_chat(deps, message)
    if chat is None:
        await _deny_restricted(deps, message)
        return
    cards = await repo.list_active_cards(deps.pool, chat.id)
    await message.reply(
        render_panel_text(deps.locales, chat.digest_language, cards),
        reply_markup=build_panel_keyboard(deps.locales, chat.digest_language),
    )


async def _cmd_report(deps, message: Message) -> None:
    """Без аргументов — тот же выбор карточки, что и у кнопки «📊 Отчёт сейчас»
    (send_report_pick). С аргументом (число или ссылка на карточку, §5) — отчёт сразу
    по этой карточке; если она не отслеживается в топике — report_unknown_card, по
    аналогии с remove_not_tracked."""
    if not _addressed_to_me(message, getattr(deps, "bot_username", "")):
        return
    chat = await ensure_chat(deps, message)
    if chat is None:
        await _deny_restricted(deps, message)
        return
    args = _args_of(message)
    task_id = _parse_task_ref(args) if args.strip() else None
    if task_id is None:
        await send_report_pick(deps, message, chat)
        return
    cards = await repo.list_active_cards(deps.pool, chat.id)
    if not any(c.bitrix_task_id == task_id for c in cards):
        await message.reply(t(deps.locales, chat.digest_language, "report_unknown_card"))
        return
    await message.reply(t(deps.locales, chat.digest_language, "report_running"))
    await run_report(deps, chat, only_task_id=task_id)


async def dispatch_callback(deps, callback: CallbackQuery) -> None:
    """Роутер m:*-callback'ов. Всегда завершается callback.answer() (снять «часики»,
    §UX Telegram: не отвеченный callback висит крутилкой у пользователя) — причём
    answer() вызывается ПЕРВЫМ действием в каждой ветке, до разбора суффикса
    callback_data (после ревью, Important): мусорный data вида "m:rm:abc" не должен
    ронять хендлер исключением раньше, чем часики снимут."""
    message = callback.message
    # ensure_chat_for_callback, не ensure_chat (после ревью, Critical): callback.message
    # принадлежит боту, права нажавшего проверяются по callback.from_user.
    chat = await ensure_chat_for_callback(deps, callback)
    if chat is None:
        await callback.answer(
            t(deps.locales, deps.settings.default_language, "restricted_denied"),
            show_alert=True,
        )
        return

    data = callback.data or ""
    lang = chat.digest_language

    if data == "m:add":
        await callback.answer()
        await send_add_prompt(deps, message, chat)
    elif data == "m:time":
        await callback.answer()
        await send_time_prompt(deps, message, chat)
    elif data == "m:lang":
        await callback.answer()
        await send_lang_keyboard(deps, message, chat)
    elif data.startswith("m:lang:"):
        await callback.answer()
        code = data.split(":", 2)[2]
        if code not in _LANG_CODES:
            return  # неизвестный код языка — мусор, часики уже сняты (Minor-фикс)
        await repo.set_chat_language(deps.pool, chat.id, code)
        await message.answer(t(deps.locales, code, "lang_ok", code=code))
    elif data == "m:rm":
        await callback.answer()
        await send_rm_keyboard(deps, message, chat)
    elif data.startswith("m:rm:"):
        await callback.answer()
        try:
            task_id = int(data.split(":", 2)[2])
        except (IndexError, ValueError):
            return  # мусорный callback_data (например m:rm:abc) — молча игнорируем
        removed = await repo.deactivate_card(deps.pool, chat.id, task_id)
        key = "remove_ok" if removed else "remove_not_tracked"
        await message.edit_text(t(deps.locales, lang, key, task_id=task_id))
    elif data == "m:cancel":
        await callback.answer()
        await message.edit_text(t(deps.locales, lang, "menu_cancelled"))
    elif data == "m:report":
        # владелец: кнопка не должна молча гнать отчёт по всему чату — сперва выбор.
        await callback.answer()
        await send_report_pick(deps, message, chat)
    elif data == "m:report:all":
        await callback.answer(t(deps.locales, lang, "report_running"))
        await run_report(deps, chat)
    elif data.startswith("m:report:"):
        try:
            task_id = int(data.split(":", 2)[2])
        except (IndexError, ValueError):
            await callback.answer()
            return  # мусорный callback_data (например m:report:abc) — молча игнорируем (паттерн m:rm)
        cards = await repo.list_active_cards(deps.pool, chat.id)
        if not any(c.bitrix_task_id == task_id for c in cards):
            await callback.answer()
            return  # карточка убрана между показом клавиатуры и нажатием — тоже молча игнорируем
        await callback.answer(t(deps.locales, lang, "report_running"))
        await run_report(deps, chat, only_task_id=task_id)
    else:
        await callback.answer()  # неизвестный callback_data — просто снимаем часики


def _build_prompt_actions(locales: dict) -> dict[str, str]:
    """Точный текст промпта -> действие, по ВСЕМ языкам локалей (после ревью,
    Important): reply-роутинг матчит ровно эти строки целиком, не первый символ —
    alias карточки из Битрикса в обычном дайджест-сообщении вполне может начинаться
    с того же эмодзи (➕/🕘) и коллизировать с «коротким» маркером."""
    actions: dict[str, str] = {}
    for lang in locales:
        actions[t(locales, lang, "menu_add_prompt")] = "add"
        actions[t(locales, lang, "menu_time_prompt")] = "time"
    return actions


def _reply_action(deps, reply_text: str) -> str | None:
    return _build_prompt_actions(deps.locales).get(reply_text)


async def route_reply(deps, message: Message) -> None:
    """Reply на prompt-сообщение бота — единственный текстовый ввод в этой фиче.
    Работает БЕЗ строгой адресации (_addressed_to_me): сам факт reply на сообщение
    бота уже однозначно адресует его нам."""
    reply = getattr(message, "reply_to_message", None)
    reply_text = getattr(reply, "text", None) or ""
    action = _reply_action(deps, reply_text)
    if action == "add":
        core, args = handle_add, (message.text or "").strip()
    elif action == "time":
        core, args = handle_time, (message.text or "")
    else:
        return  # неизвестный/чужой reply — игнор

    chat = await ensure_chat(deps, message)
    if chat is None:
        await _deny_restricted(deps, message)
        return

    if core is handle_add:
        text = await core(deps, chat, args, message.from_user.id if message.from_user else 0)
    else:
        text = await core(deps, chat, args)
    await message.reply(text)


def _is_reply_to_bot(message: Message) -> bool:
    reply = getattr(message, "reply_to_message", None)
    bot = getattr(message, "bot", None)
    reply_user = getattr(reply, "from_user", None)
    return bool(reply and bot and reply_user and reply_user.id == bot.id)


def build_menu_router(deps) -> Router:
    router = Router()

    @router.message(Command("menu"))
    async def _menu_cmd(message: Message) -> None:
        await _cmd_menu(deps, message)

    @router.message(Command("report"))
    async def _report_cmd(message: Message) -> None:
        await _cmd_report(deps, message)

    @router.callback_query(F.data.startswith("m:"))
    async def _on_callback(callback: CallbackQuery) -> None:
        await dispatch_callback(deps, callback)

    @router.message(_is_reply_to_bot)
    async def _on_reply(message: Message) -> None:
        await route_reply(deps, message)

    return router
