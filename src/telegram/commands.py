import datetime as dt
import logging
import re
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src import repo
from src.bitrix import methods
from src.bitrix.client import BitrixError
from src.i18n import t
from src.telegram.capture import chat_title_of, thread_id_of

log = logging.getLogger(__name__)
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_LANG_RE = re.compile(r"^[a-z]{2}(-[a-z]{2})?$", re.IGNORECASE)


async def handle_add(deps, chat, args: str, user_id: int) -> str:
    lang = chat.digest_language
    if not args.strip().isdigit():
        return t(deps.locales, lang, "add_usage")
    task_id = int(args.strip())
    try:
        task = await methods.get_task(deps.bx, task_id)
    except BitrixError:
        return t(deps.locales, lang, "add_not_found", task_id=task_id)

    alias = str(task.get("title") or f"#{task_id}")
    bitrix_chat_id = task.get("chatId") or (task.get("chat") or {}).get("id")
    last_history_id = await methods.get_latest_history_id(deps.bx, task_id)
    last_message_id = await methods.get_latest_chat_message_id(
        deps.bx, int(bitrix_chat_id) if bitrix_chat_id else None
    )
    # курсор комментариев «с этого момента» (§5): иначе первый дайджест старой карточки
    # вываливает всю историю task.commentitem.getlist (сотни шт., см. §13 fallback).
    # task.commentitem.getlist — deprecated: на карточке нового типа может ответить ошибкой;
    # /add не должен из-за этого падать целиком (паттерн как в links.resolve_files).
    try:
        last_comment_id = await methods.get_latest_comment_id(deps.bx, task_id)
    except BitrixError as e:
        log.warning("get_latest_comment_id(%s) не удался: %s", task_id, e)
        last_comment_id = 0
    outcome = await repo.add_card(
        deps.pool, chat.id, task_id, alias, user_id,
        last_history_id, last_message_id, last_comment_id,
    )
    key = {"added": "add_ok", "exists": "add_exists", "reactivated": "add_reactivated"}[outcome]
    return t(deps.locales, lang, key, alias=alias, task_id=task_id)


async def handle_remove(deps, chat, args: str) -> str:
    lang = chat.digest_language
    if not args.strip().isdigit():
        return t(deps.locales, lang, "remove_usage")
    task_id = int(args.strip())
    removed = await repo.deactivate_card(deps.pool, chat.id, task_id)
    key = "remove_ok" if removed else "remove_not_tracked"
    return t(deps.locales, lang, key, task_id=task_id)


async def handle_list(deps, chat) -> str:
    lang = chat.digest_language
    cards = await repo.list_active_cards(deps.pool, chat.id)
    if not cards:
        return t(deps.locales, lang, "list_empty")
    lines = [t(deps.locales, lang, "list_header")]
    lines += [f"• {c.alias or '#'} (#{c.bitrix_task_id})" for c in cards]
    return "\n".join(lines)


async def handle_lang(deps, chat, args: str) -> str:
    code = args.strip().lower()
    if not _LANG_RE.match(code):
        return t(deps.locales, chat.digest_language, "lang_usage")
    await repo.set_chat_language(deps.pool, chat.id, code)
    return t(deps.locales, code, "lang_ok", code=code)


async def handle_time(deps, chat, args: str) -> str:
    lang = chat.digest_language
    parts = args.split()
    if not parts or not _TIME_RE.match(parts[0]):
        return t(deps.locales, lang, "time_usage")
    hh, mm = parts[0].split(":")
    new_time = dt.time(int(hh), int(mm))

    tz: str | None = None
    if len(parts) > 1:
        try:
            ZoneInfo(parts[1])
        except Exception:
            return t(deps.locales, lang, "time_usage")
        tz = parts[1]
    elif chat.timezone == "UTC":  # §5: дефолтная UTC без явной tz — просим указать
        return t(deps.locales, lang, "time_need_tz", time=parts[0])

    await repo.set_chat_time(deps.pool, chat.id, new_time, tz)
    return t(deps.locales, lang, "time_ok", time=parts[0], tz=tz or chat.timezone)


async def handle_membership(deps, telegram_chat_id: int, new_status: str) -> None:
    """§11: бот выкинут из чата -> деактивировать записи, планировщик чат пропускает."""
    if new_status not in {"kicked", "left"}:
        return
    rows = await deps.pool.fetch(
        "SELECT id FROM chats WHERE telegram_chat_id = $1 AND active", telegram_chat_id
    )
    for r in rows:
        await repo.deactivate_chat(deps.pool, r["id"])


async def ensure_chat(deps, message: Message):
    chat = await repo.upsert_chat(
        deps.pool,
        message.chat.id,
        thread_id_of(message),
        chat_title_of(message),
        deps.settings.default_language,
    )
    if not chat.restricted:
        return chat
    admins = {
        r["telegram_user_id"]
        for r in await deps.pool.fetch(
            "SELECT telegram_user_id FROM chat_admins WHERE chat_id = $1", chat.id
        )
    }
    if message.from_user and message.from_user.id in admins:
        return chat
    return None  # §6: restricted и не в вайтлисте


def _args_of(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""


def build_router(deps) -> Router:
    router = Router()

    def register(cmd: str, core):
        @router.message(Command(cmd))
        async def _handler(message: Message, _core=core):
            chat = await ensure_chat(deps, message)
            if chat is None:
                await message.reply(
                    t(deps.locales, deps.settings.default_language, "restricted_denied")
                )
                return
            if _core is handle_add:
                text = await _core(deps, chat, _args_of(message),
                                   message.from_user.id if message.from_user else 0)
            elif _core is handle_list:
                text = await _core(deps, chat)
            else:
                text = await _core(deps, chat, _args_of(message))
            await message.reply(text)

    register("add", handle_add)
    register("remove", handle_remove)
    register("list", handle_list)
    register("lang", handle_lang)
    register("time", handle_time)

    @router.my_chat_member()
    async def _membership(update):  # §11: kicked/left -> active=false
        await handle_membership(deps, update.chat.id, update.new_chat_member.status)

    return router
