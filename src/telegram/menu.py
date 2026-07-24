"""Инлайн-меню (кнопки) + «Отчёт сейчас» (§5 спеки: /report, /menu).

Зачем: партнёры не печатают адресные команды в группах (§«строгая адресация»,
commands.py:_addressed_to_me). Callback'и (InlineKeyboard + callback_query) вообще не
зависят от privacy mode/адресации — тыкать кнопки можно всегда. Единственный текстовый
ввод, который остаётся, — номер карточки/время, и он идёт reply-ем на сообщение бота:
reply на сообщение бота проходит privacy mode (это не «обычная переписка»).

Хрупкий контракт: reply-хендлер (_route_reply) узнаёт, ЧТО означает reply, по первому
символу текста prompt-сообщения (➕ = ждём номер карточки, 🕘 = ждём время+таймзону).
Если меняешь эмодзи в menu_add_prompt/menu_time_prompt (locales/*.json) — обнови и
_MARK_ADD/_MARK_TIME здесь, иначе reply-роутинг молча перестанет узнавать свои же
сообщения.

Циклический импорт: menu.py импортирует точечно только «ядра» из commands.py
(ensure_chat, _addressed_to_me, handle_add, handle_time, _args_of) — их значения
не нужны build_router. commands.py, наоборот, использует build_panel_keyboard() отсюда
только внутри build_router() (локальный импорт по месту вызова, а не на уровне модуля) —
к моменту вызова оба модуля уже полностью загружены, цикла не возникает.
"""
import datetime as dt
import logging

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
    ensure_chat,
    handle_add,
    handle_time,
)

log = logging.getLogger(__name__)

# Контракт с _route_reply: первый символ prompt-сообщения. См. docstring модуля.
_MARK_ADD = "➕"
_MARK_TIME = "🕘"
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


def build_lang_keyboard() -> InlineKeyboardMarkup:
    # Коды языков как подписи — без отдельного слоя переводимых названий языков.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=code.upper(), callback_data=f"m:lang:{code}") for code in _LANG_CODES]
    ])


async def run_report(deps, chat: ChatRow) -> None:
    """Общее ядро для /report (текстом) и m:report (кнопкой): тот же пайплайн, что и
    у планировщика, но mark_run=False (§5) — курсоры двигаются, last_digest_date нет."""
    now_utc = dt.datetime.now(dt.timezone.utc)
    errors, posted = await process_chat(deps, chat, now_utc, mark_run=False)
    if not posted:
        await deps.send_fn(
            deps.bot, chat.telegram_chat_id, chat.message_thread_id,
            t(deps.locales, chat.digest_language, "report_empty"),
        )
    if errors:
        log.warning("report: чат %s: %s", chat.id, errors)


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
    if not _addressed_to_me(message, getattr(deps, "bot_username", "")):
        return
    chat = await ensure_chat(deps, message)
    if chat is None:
        await _deny_restricted(deps, message)
        return
    await message.reply(t(deps.locales, chat.digest_language, "report_running"))
    await run_report(deps, chat)


async def dispatch_callback(deps, callback: CallbackQuery) -> None:
    """Роутер m:*-callback'ов. Всегда завершается callback.answer() (снять «часики»,
    §UX Telegram: не отвеченный callback висит крутилкой у пользователя)."""
    message = callback.message
    chat = await ensure_chat(deps, message)
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
        await message.answer(t(deps.locales, lang, "menu_add_prompt"))
    elif data == "m:time":
        await callback.answer()
        await message.answer(t(deps.locales, lang, "menu_time_prompt"))
    elif data == "m:lang":
        await callback.answer()
        await message.answer(t(deps.locales, lang, "menu_lang_pick"), reply_markup=build_lang_keyboard())
    elif data.startswith("m:lang:"):
        code = data.split(":", 2)[2]
        await repo.set_chat_language(deps.pool, chat.id, code)
        await callback.answer()
        await message.answer(t(deps.locales, code, "lang_ok", code=code))
    elif data == "m:rm":
        cards = await repo.list_active_cards(deps.pool, chat.id)
        await callback.answer()
        if not cards:
            await message.answer(t(deps.locales, lang, "menu_rm_empty"))
        else:
            await message.answer(
                t(deps.locales, lang, "menu_rm_pick"),
                reply_markup=build_rm_keyboard(deps.locales, lang, cards),
            )
    elif data.startswith("m:rm:"):
        task_id = int(data.split(":", 2)[2])
        removed = await repo.deactivate_card(deps.pool, chat.id, task_id)
        key = "remove_ok" if removed else "remove_not_tracked"
        await callback.answer()
        await message.edit_text(t(deps.locales, lang, key, task_id=task_id))
    elif data == "m:cancel":
        await callback.answer()
        await message.edit_text(t(deps.locales, lang, "menu_cancelled"))
    elif data == "m:report":
        await callback.answer(t(deps.locales, lang, "report_running"))
        await run_report(deps, chat)
    else:
        await callback.answer()  # неизвестный callback_data — просто снимаем часики


def _reply_marker(message: Message) -> str:
    reply = getattr(message, "reply_to_message", None)
    text = getattr(reply, "text", None) or ""
    return text[:1]


async def route_reply(deps, message: Message) -> None:
    """Reply на prompt-сообщение бота — единственный текстовый ввод в этой фиче.
    Работает БЕЗ строгой адресации (_addressed_to_me): сам факт reply на сообщение
    бота уже однозначно адресует его нам."""
    marker = _reply_marker(message)
    if marker == _MARK_ADD:
        core, args = handle_add, (message.text or "").strip()
    elif marker == _MARK_TIME:
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
